"""HTTP client for the local TrueForge agent harness.

Every agent's reasoning runs as a TrueForge session/turn rather than a bare
model API call: TrueForge owns the agent loop (model calls, MCP tool
discovery/execution, context management) and this client only starts turns
and reads back their structured output. TrueForge must already be running
locally (see README) before agents can execute.
"""
from __future__ import annotations

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

    TrueForge agent names are unique (immutable *names*), but the manifest
    itself is mutable via `PUT /api/v1/agents/{agent_id}` — this matters for
    config that needs to take effect on an agent that was already registered
    by an earlier run, e.g. `require_approval_for_tools`, which MCP servers
    are attached, or this PR's `skills` attachment and
    `config.sandbox.enabled` change: without updating in place, an agent
    registered by any prior run (the common case on a long-lived local
    TrueForge instance) would keep running with its old manifest forever,
    since the create-time POST silently no-ops on 409.

    `skills` is a list of name-only references (e.g. `[{"name": "outreach-
    copywriting-style-guide"}]`) to skills already registered via
    `ensure_skill`. Per TrueForge's manifest schema, attaching skills
    requires the agent's sandbox to be enabled, so passing a non-empty
    `skills` here also flips `config.sandbox.enabled` to `True` for this
    agent.

    `skills=None` ("caller has no opinion" — e.g. this call's skill
    registration failed and the caller doesn't know whether skills were
    previously attached) is deliberately distinct from `skills=[]`
    ("caller explicitly wants no skills attached"). On first-time creation
    there is no existing manifest to preserve, so both behave the same (no
    skills key, sandbox disabled). On an update to an already-existing
    agent, `skills=None` instead *preserves* whatever skills/sandbox config
    the existing manifest already has, so a transient skill-registration
    failure on one call can never silently strip skills a previous,
    successful call had already attached — see `_update_agent`.
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


def _update_agent(name: str, manifest: dict[str, Any], *, skills_explicit: bool) -> None:
    """PUT-updates an already-registered agent's manifest so config changes
    (e.g. require_approval_for_tools, a newly attached MCP server, or this
    PR's skills attachment / sandbox enablement) actually take effect,
    rather than silently no-op'ing because the agent already existed.

    `skills_explicit=False` means the caller passed `skills=None` to
    `ensure_agent` — it has no opinion on skills this call (typically
    because skill registration failed upstream), so this preserves the
    existing agent's current `skills`/`config.sandbox.enabled` instead of
    overwriting them with the caller's skills-less manifest. Without this,
    a single failed skill-registration attempt on an agent that was
    previously successfully attached to a skill would silently regress it
    back to skill-less on the very next run.
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
        # ValueError covers `get_resp.json()` raising `json.JSONDecodeError`
        # on a malformed (but 2xx) response body from the list-agents
        # endpoint; TypeError/AttributeError cover an unexpected response
        # shape, e.g. `data` not being a list, or an entry not being a dict
        # (`for a in agents` / `a.get("name")` above). Without catching
        # these too, a malformed dependency response escapes as a raw
        # ValueError/TypeError/AttributeError instead of TrueForgeError, so
        # `run_agent_reasoning`'s `except TrueForgeError` never triggers and
        # the direct-LLM fallback never runs.
        raise TrueForgeError(f"Failed to update TrueForge agent {name}: {exc}") from exc


def ensure_skill(
    name: str, *, repo_url: str, path: str, ref: str, description: str
) -> None:
    """Register a git-backed TrueForge skill if it does not already exist.

    TrueForge skills are name/description references whose full instructional
    content ("SKILL.md") lives in a git repository TrueForge clones into an
    agent's sandbox on demand — there is no "post raw content" registration
    shape in the real API (verified against the live OpenAPI schema at
    /api/v1/docs: `SkillManifest.type` is a `"git"`-only enum and `url` is
    regex-constrained to a GitHub/GitLab HTTPS URL). Only the skill's
    name/description are loaded into an agent's base context; the full
    `SKILL.md` content is fetched from the repo only when the model decides
    the task needs it.

    Like `ensure_agent`, this treats "already exists" as success rather than
    trying to update in place, matching TrueForge's actual conflict response
    (HTTP 409, "Skill name already exists").
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
    returns that state's raw dict. Shared by `run_turn` and `resume_turn` so
    there is exactly one poll loop.

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


def run_turn(agent_name: str, message: str) -> dict[str, Any] | list[PendingToolApproval]:
    """Run one user-message turn against a named TrueForge agent.

    Returns the parsed JSON object from the model's final response, or — if
    the turn paused on a `require_approval_for_tools` gate — a list of
    `PendingToolApproval` describing the tool call(s) awaiting a human
    decision. Callers that don't expect a pause (most agents, which have no
    approval-gated tools) will simply never see the list case.

    Raises TrueForgeError on any transport failure, a turn that ends in
    error, or non-JSON model output when no approval was pending.
    """
    try:
        session_resp = httpx.post(
            f"{_base_url()}/api/v1/sessions",
            json={"agent": {"name": agent_name}},
            timeout=15.0,
        )
        session_resp.raise_for_status()
        session_id = session_resp.json()["data"]["id"]

        turn_resp = httpx.post(
            f"{_base_url()}/api/v1/sessions/{session_id}/turns",
            json={"stream": False, "input": [{"type": "user.message", "content": message}]},
            timeout=30.0,
        )
        turn_resp.raise_for_status()
        turn_id = turn_resp.json()["data"]["id"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise TrueForgeError(f"Failed to start TrueForge turn for {agent_name}: {exc}") from exc

    state = _poll_turn_to_done(agent_name, session_id, turn_id)
    return _resolve_turn_result(agent_name, session_id, turn_id, state)


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
