"""HTTP client for the local TrueForge agent harness.

Every agent's reasoning runs as a TrueForge session/turn rather than a bare
model API call: TrueForge owns the agent loop (model calls, MCP tool
discovery/execution, context management) and this client only starts turns
and reads back their structured output. TrueForge must already be running
locally (see README) before agents can execute.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.llm import _extract_json_object

logger = logging.getLogger("signalis.trueforge")

_TURN_POLL_INTERVAL_SECONDS = 1.0
_TURN_POLL_TIMEOUT_SECONDS = 90.0


class TrueForgeError(RuntimeError):
    """Raised when the TrueForge harness is unreachable or a turn fails."""


@dataclass
class PendingToolApproval:
    """One tool call TrueForge paused a turn on, awaiting a human decision.

    Carries everything a caller needs to persist a pending-approval record
    and later resume the turn: which session/turn/thread it belongs to,
    which tool call id to resume with, and the tool name/input so a human
    reviewer can actually make an informed decision.
    """

    session_id: str
    turn_id: str
    thread_id: str
    tool_call_id: str
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)


def _base_url() -> str:
    return get_settings().trueforge_url.rstrip("/")


def ensure_agent(
    name: str,
    *,
    model: str,
    instructions: str,
    mcp_servers: list[dict] | None = None,
    skills: list[dict] | None = None,
) -> None:
    """Create the named TrueForge agent, or update its manifest in place if
    it already exists.

    Agent names are unique but the manifest is mutable via
    `PUT /api/v1/agents/{agent_id}`; without updating in place, config
    changes (MCP servers, require_approval_for_tools, skills) would never
    take effect on an agent registered by an earlier run, since create
    silently no-ops on 409.

    `skills` are name-only references to skills already registered via
    `ensure_skill`; attaching any requires the agent's sandbox enabled, so a
    non-empty `skills` also flips `config.sandbox.enabled`.

    `skills=None` ("no opinion", e.g. this call's registration failed) is
    distinct from `skills=[]` ("explicitly no skills"): on update,
    `skills=None` preserves the existing manifest's skills/sandbox config
    instead of overwriting them, so a transient registration failure can't
    silently strip a previously-attached skill — see `_update_agent`.
    """
    manifest: dict[str, Any] = {
        "model": {"name": model},
        "instructions": instructions,
        "config": {"sandbox": {"enabled": bool(skills)}},
    }
    if mcp_servers:
        manifest["mcp_servers"] = mcp_servers
    if skills:
        manifest["skills"] = skills

    try:
        resp = httpx.post(
            f"{_base_url()}/api/v1/agents",
            json={"name": name, "manifest": manifest},
            timeout=15.0,
        )
        if resp.status_code == 201:
            logger.info("Registered TrueForge agent %s", name)
            return
        if resp.status_code == 409 and "already exists" in resp.text.lower():
            _update_agent(name, manifest, skills_explicit=skills is not None)
            return
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise TrueForgeError(f"Failed to register TrueForge agent {name}: {exc}") from exc


def _start_turn(session_id: str, message: str, *, label: str) -> str:
    """POST one user-message turn onto an already-existing session and
    return the new turn's id."""
    try:
        turn_resp = httpx.post(
            f"{_base_url()}/api/v1/sessions/{session_id}/turns",
            json={"stream": False, "input": [{"type": "user.message", "content": message}]},
            timeout=30.0,
        )
        turn_resp.raise_for_status()
        return turn_resp.json()["data"]["id"]
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TrueForgeError(f"Failed to start TrueForge turn for {label}: {exc}") from exc


def _update_agent(name: str, manifest: dict[str, Any], *, skills_explicit: bool) -> None:
    """PUT-updates an already-registered agent's manifest so config changes
    actually take effect instead of no-op'ing on 409.

    `skills_explicit=False` (caller passed `skills=None`) preserves the
    existing agent's current skills/sandbox config instead of overwriting
    it with a skills-less manifest — see `ensure_agent`.
    """
    try:
        get_resp = httpx.get(f"{_base_url()}/api/v1/agents", timeout=15.0)
        get_resp.raise_for_status()
        agents = get_resp.json()["data"]
        if not isinstance(agents, list):
            raise TrueForgeError(f"TrueForge agent list response had non-list 'data': {agents!r}")
        match = next((a for a in agents if a.get("name") == name), None)
        if match is None:
            raise TrueForgeError(f"Agent {name} reported as already existing but not found in agent list")
        agent_id = match["id"]

        if not skills_explicit:
            existing_manifest = match.get("manifest") or {}
            existing_skills = existing_manifest.get("skills")
            existing_sandbox = (existing_manifest.get("config") or {}).get("sandbox", {}).get("enabled")
            if existing_skills:
                manifest["skills"] = existing_skills
            if existing_sandbox is not None:
                manifest.setdefault("config", {}).setdefault("sandbox", {})["enabled"] = existing_sandbox

        put_resp = httpx.put(
            f"{_base_url()}/api/v1/agents/{agent_id}",
            json={"manifest": manifest},
            timeout=15.0,
        )
        put_resp.raise_for_status()
        logger.info("Updated TrueForge agent %s manifest", name)
    except TrueForgeError:
        raise
    except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
        # Also catches JSON-decode/shape errors on the response so a
        # malformed dependency reply can't escape as a raw exception and
        # bypass run_agent_reasoning's except-TrueForgeError fallback.
        raise TrueForgeError(f"Failed to update TrueForge agent {name}: {exc}") from exc


def ensure_skill(
    name: str, *, repo_url: str, path: str, ref: str, description: str
) -> None:
    """Register a git-backed TrueForge skill if it does not already exist.

    TrueForge skills are git-backed only (SkillManifest.type is a
    "git"-only enum; no raw-content registration shape). Only name/
    description load into an agent's base context; SKILL.md content is
    fetched from the repo on demand when the model decides it's needed.

    Treats "already exists" (409) as success, matching `ensure_agent`.
    """
    manifest: dict[str, Any] = {
        "type": "git",
        "name": name,
        "url": repo_url,
        "path": path,
        "ref": ref,
        "description": description,
    }

    try:
        resp = httpx.post(
            f"{_base_url()}/api/v1/settings/skills",
            json={"manifest": manifest},
            timeout=15.0,
        )
        if resp.status_code == 201:
            logger.info("Registered TrueForge skill %s", name)
            return
        if resp.status_code == 409 and "already exists" in resp.text.lower():
            return
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise TrueForgeError(f"Failed to register TrueForge skill {name}: {exc}") from exc


def _poll_turn_to_done(agent_name: str, session_id: str, turn_id: str) -> dict[str, Any]:
    """Polls a turn until it reaches a terminal `status == "done"` state and
    returns that state's raw dict. Shared by `run_turn`, `run_followup_turn`,
    and `resume_turn` so there is exactly one poll loop.

    Note: TrueForge's `"done"` status means "the turn finished running" —
    it does NOT mean "the turn produced a final answer". A turn paused on a
    tool-approval gate also reports `status == "done"` with `output: null`
    and a populated `required_actions` list; the caller distinguishes that
    case from a genuine final answer.
    """
    deadline = time.monotonic() + _TURN_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            poll = httpx.get(f"{_base_url()}/api/v1/sessions/{session_id}/turns/{turn_id}", timeout=15.0)
            poll.raise_for_status()
            state = poll.json()["data"]["state"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise TrueForgeError(f"Failed to poll TrueForge turn for {agent_name}: {exc}") from exc

        if not isinstance(state, dict):
            raise TrueForgeError(f"TrueForge turn poll for {agent_name} returned non-object state: {state!r}")

        status = state.get("status")
        if status == "done":
            return state
        if status == "error":
            raise TrueForgeError(f"TrueForge turn for {agent_name} failed: {state.get('message')}")
        time.sleep(_TURN_POLL_INTERVAL_SECONDS)

    raise TrueForgeError(f"TrueForge turn for {agent_name} timed out after {_TURN_POLL_TIMEOUT_SECONDS}s")


def _extract_pending_approvals(
    agent_name: str, session_id: str, turn_id: str, state: dict[str, Any]
) -> list[PendingToolApproval]:
    """Builds one PendingToolApproval per tool call named in a "done" turn
    state's `required_actions` (`tool.approval_required` entries), looking
    up each call's tool name/arguments from the paused turn's `output`
    (which still carries the `model.message` with `tool_calls`, even though
    the turn has no final answer yet)."""
    tool_calls_by_id: dict[str, dict[str, Any]] = {}
    output = state.get("output") or {}
    for call in output.get("tool_calls", []) or []:
        tool_calls_by_id[call["id"]] = call

    pending: list[PendingToolApproval] = []
    for action in state.get("required_actions", []) or []:
        if action.get("type") != "tool.approval_required":
            continue
        thread_id = action.get("thread_id", "main")
        for ref in action.get("tool_calls", []) or []:
            call_id = ref["id"]
            call = tool_calls_by_id.get(call_id, {})
            function = call.get("function", {})
            tool_name = function.get("name", "unknown")
            raw_args = function.get("arguments", "{}")
            try:
                tool_input = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                tool_input = {"raw_arguments": raw_args}
            pending.append(
                PendingToolApproval(
                    session_id=session_id,
                    turn_id=turn_id,
                    thread_id=thread_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
            )
    return pending


def _resolve_turn_result(
    agent_name: str, session_id: str, turn_id: str, state: dict[str, Any]
) -> dict[str, Any] | list[PendingToolApproval]:
    """Given a "done" turn state, either returns the parsed final JSON
    answer, or a list of PendingToolApproval if the turn paused awaiting
    tool approval instead of producing one."""
    try:
        required_actions = state.get("required_actions") or []
        if required_actions:
            pending = _extract_pending_approvals(agent_name, session_id, turn_id, state)
            if pending:
                return pending
            # A required_actions entry TrueForge didn't tag as tool.approval_required
            # (e.g. mcp.auth_required, tool.response_required) — this client only
            # knows how to resume tool-approval pauses.
            raise TrueForgeError(
                f"TrueForge turn for {agent_name} paused on an unsupported required action: {required_actions}"
            )

        output = state.get("output")
        if output is None:
            raise TrueForgeError(f"TrueForge turn for {agent_name} finished with no output and no required actions")
        content = output.get("content", "")
        parsed = _extract_json_object(content)
        if parsed is None:
            raise TrueForgeError(f"TrueForge agent {agent_name} returned non-JSON output: {content!r}")
        return parsed
    except TrueForgeError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise TrueForgeError(f"TrueForge turn for {agent_name} returned a malformed response: {exc}") from exc


def _get_session_events(session_id: str) -> list[dict[str, Any]]:
    """Fetch and validate the raw event list for a session.

    Raises TrueForgeError for any failure mode: transport/HTTP failure,
    non-JSON body, a JSON body missing the expected "data" key, or a "data"
    value that isn't a list of dicts. `_extract_subagent_delegations` calls
    `.get()` on every element assuming it's a dict, so an unexpected shape
    must be caught here rather than surfacing as an uncaught TypeError/
    AttributeError deeper in the pipeline.
    """
    try:
        resp = httpx.get(f"{_base_url()}/api/v1/sessions/{session_id}/events", timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise TrueForgeError(f"Failed to fetch TrueForge session events for {session_id}: {exc}") from exc

    try:
        body = resp.json()
    except json.JSONDecodeError as exc:
        raise TrueForgeError(
            f"TrueForge session events response for {session_id} was not valid JSON: {exc}"
        ) from exc

    try:
        data = body["data"]
    except (KeyError, TypeError) as exc:
        raise TrueForgeError(
            f"TrueForge session events response for {session_id} is missing the expected 'data' field: {exc}"
        ) from exc

    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise TrueForgeError(
            f"TrueForge session events response for {session_id} had an unexpected shape "
            f"(expected a list of objects): {data!r}"
        )
    return data


def _extract_subagent_delegations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair up ``thread.created``/``thread.done`` events on a session's event
    stream into one summary entry per genuine subagent delegation TrueForge's
    ``create_sub_agent`` system tool performed during a turn.

    This is the same "prove it happened via TrueForge's own session data"
    verification method used elsewhere in this codebase (see
    docs/DECISIONS.md), applied to subagent delegation specifically: a
    subagent's existence is only trusted if it shows up as a real
    thread.created/thread.done pair on the session TrueForge itself recorded,
    not merely because the root model's final answer claims it delegated.
    """
    created: dict[str, dict[str, Any]] = {}
    done: dict[str, dict[str, Any]] = {}
    for item in events:
        event = item.get("event", {})
        thread_id = event.get("thread_id")
        if not thread_id:
            continue
        if event.get("type") == "thread.created":
            created[thread_id] = event
        elif event.get("type") == "thread.done":
            done[thread_id] = event

    delegations = []
    for thread_id, created_event in created.items():
        done_event = done.get(thread_id)
        agent_info = created_event.get("agent_info", {})
        entry: dict[str, Any] = {
            "thread_id": thread_id,
            "name": agent_info.get("name"),
            "input": agent_info.get("input"),
            "status": (done_event or {}).get("state", {}).get("status") if done_event else "incomplete",
        }
        if done_event:
            try:
                started = datetime.datetime.fromisoformat(created_event["created_at"].replace("Z", "+00:00"))
                finished = datetime.datetime.fromisoformat(done_event["created_at"].replace("Z", "+00:00"))
                entry["latency_seconds"] = round((finished - started).total_seconds(), 3)
            except (KeyError, ValueError):
                entry["latency_seconds"] = None
        delegations.append(entry)
    return delegations


def run_turn(
    agent_name: str, message: str, *, with_delegations: bool = False
) -> (
    tuple[dict[str, Any] | list[PendingToolApproval], str]
    | tuple[dict[str, Any] | list[PendingToolApproval], str, list[dict[str, Any]] | None]
):
    """Create a new TrueForge session, run one user-message turn, and return
    (result, session_id) — result is the parsed JSON answer, or a list of
    `PendingToolApproval` if the turn paused on a `require_approval_for_tools`
    gate. session_id is always returned so callers can persist it and later
    continue the conversation via run_followup_turn/resume_turn.

    If `with_delegations` is True, also fetches session events and returns
    a third element: subagent delegations TrueForge recorded for this turn
    (see `_extract_subagent_delegations`), or `None` if that fetch failed —
    a sentinel distinct from `[]` (no delegation occurred). This never
    raises: a metadata-fetch failure after a successful turn must not
    discard a valid result and force a rerun.

    Raises TrueForgeError on any transport failure for the turn itself, a
    turn that ends in error, or non-JSON model output when no approval was
    pending.
    """
    try:
        session_resp = httpx.post(
            f"{_base_url()}/api/v1/sessions",
            json={"agent": {"name": agent_name}},
            timeout=15.0,
        )
        session_resp.raise_for_status()
        session_id = session_resp.json()["data"]["id"]
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TrueForgeError(f"Failed to create TrueForge session for {agent_name}: {exc}") from exc

    turn_id = _start_turn(session_id, message, label=agent_name)
    state = _poll_turn_to_done(agent_name, session_id, turn_id)
    result = _resolve_turn_result(agent_name, session_id, turn_id, state)

    if with_delegations:
        try:
            events = _get_session_events(session_id)
        except TrueForgeError as exc:
            logger.warning(
                "TrueForge turn for %s completed successfully, but fetching session "
                "events for delegation evidence failed; returning the successful "
                "result with delegation evidence marked unavailable: %s",
                agent_name,
                exc,
            )
            return result, session_id, None
        delegations = _extract_subagent_delegations(events)
        return result, session_id, delegations
    return result, session_id


def run_followup_turn(session_id: str, message: str) -> dict[str, Any] | list[PendingToolApproval]:
    """Run one more turn on an EXISTING session and return the parsed JSON
    answer, or a list of PendingToolApproval if it paused on an approval
    gate. Posts directly to /sessions/{id}/turns (never creates a new
    session), so the model continues its own prior reasoning rather than
    being re-fed a fresh restatement of it.

    Raises TrueForgeError under the same conditions as run_turn, plus if
    session_id no longer exists on the TrueForge side.
    """
    label = f"session {session_id}"
    turn_id = _start_turn(session_id, message, label=label)
    state = _poll_turn_to_done(label, session_id, turn_id)
    return _resolve_turn_result(label, session_id, turn_id, state)


def resume_turn(
    agent_name: str,
    *,
    session_id: str,
    thread_id: str,
    tool_call_id: str,
    approve: bool,
    deny_reason: str | None = None,
) -> dict[str, Any] | list[PendingToolApproval]:
    """Resumes a turn paused on a tool-approval gate by posting a new turn
    with `previous_turn_id: "auto"` and a `user.tool_approval` input item,
    then polls it to completion exactly like `run_turn` does.

    Returns the parsed final JSON answer once the resumed turn completes, or
    another list of PendingToolApproval if a further tool call in the same
    turn also needs approval (e.g. multiple gated tools called back to
    back).
    """
    approval: dict[str, Any] = {"status": "allow"} if approve else {"status": "deny"}
    if not approve and deny_reason:
        approval["reason"] = deny_reason

    try:
        turn_resp = httpx.post(
            f"{_base_url()}/api/v1/sessions/{session_id}/turns",
            json={
                "stream": False,
                "previous_turn_id": "auto",
                "input": [
                    {
                        "type": "user.tool_approval",
                        "thread_id": thread_id,
                        "tool_call_id": tool_call_id,
                        "approval": approval,
                    }
                ],
            },
            timeout=30.0,
        )
        turn_resp.raise_for_status()
        turn_id = turn_resp.json()["data"]["id"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise TrueForgeError(f"Failed to resume TrueForge turn for {agent_name}: {exc}") from exc

    state = _poll_turn_to_done(agent_name, session_id, turn_id)
    return _resolve_turn_result(agent_name, session_id, turn_id, state)
