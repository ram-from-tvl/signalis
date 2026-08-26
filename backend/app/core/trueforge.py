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


def ensure_agent(name: str, *, model: str, instructions: str, mcp_servers: list[dict] | None = None) -> None:
    """Create the named TrueForge agent, or update its manifest in place if
    it already exists.

    TrueForge agent names are unique, but the manifest itself is mutable via
    `PUT /api/v1/agents/{agent_id}` — this matters for config (like
    `require_approval_for_tools`) that needs to take effect on an agent that
    was already registered by an earlier run.
    """
    manifest: dict[str, Any] = {
        "model": {"name": model},
        "instructions": instructions,
        "config": {"sandbox": {"enabled": False}},
    }
    if mcp_servers:
        manifest["mcp_servers"] = mcp_servers

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
            _update_agent(name, manifest)
            return
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise TrueForgeError(f"Failed to register TrueForge agent {name}: {exc}") from exc


def _update_agent(name: str, manifest: dict[str, Any]) -> None:
    """PUT-updates an already-registered agent's manifest so config changes
    (e.g. require_approval_for_tools) actually take effect, rather than
    silently no-op'ing because the agent already existed."""
    try:
        get_resp = httpx.get(f"{_base_url()}/api/v1/agents", timeout=15.0)
        get_resp.raise_for_status()
        agents = get_resp.json()["data"]
        match = next((a for a in agents if a["name"] == name), None)
        if match is None:
            raise TrueForgeError(f"Agent {name} reported as already existing but not found in agent list")
        agent_id = match["id"]

        put_resp = httpx.put(
            f"{_base_url()}/api/v1/agents/{agent_id}",
            json={"manifest": manifest},
            timeout=15.0,
        )
        put_resp.raise_for_status()
        logger.info("Updated TrueForge agent %s manifest", name)
    except (httpx.HTTPError, KeyError) as exc:
        raise TrueForgeError(f"Failed to update TrueForge agent {name}: {exc}") from exc


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
        except (httpx.HTTPError, KeyError) as exc:
            raise TrueForgeError(f"Failed to poll TrueForge turn for {agent_name}: {exc}") from exc

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
    except (httpx.HTTPError, KeyError) as exc:
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
    except (httpx.HTTPError, KeyError) as exc:
        raise TrueForgeError(f"Failed to resume TrueForge turn for {agent_name}: {exc}") from exc

    state = _poll_turn_to_done(agent_name, session_id, turn_id)
    return _resolve_turn_result(agent_name, session_id, turn_id, state)
