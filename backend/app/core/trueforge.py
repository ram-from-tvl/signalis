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
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.llm import _extract_json_object

logger = logging.getLogger("signalis.trueforge")

_TURN_POLL_INTERVAL_SECONDS = 1.0
_TURN_POLL_TIMEOUT_SECONDS = 90.0


class TrueForgeError(RuntimeError):
    """Raised when the TrueForge harness is unreachable or a turn fails."""


def _base_url() -> str:
    return get_settings().trueforge_url.rstrip("/")


def ensure_agent(name: str, *, model: str, instructions: str, mcp_servers: list[dict] | None = None) -> None:
    """Create the named TrueForge agent if it does not already exist.

    Agents are immutable by name once created (per TrueForge's API), so this
    treats "already exists" as success rather than trying to update in place.
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


def _poll_turn_to_completion(session_id: str, turn_id: str, *, label: str) -> str:
    """Poll a turn until it reaches a terminal state and return its raw
    output content (still a string; callers decide whether to parse JSON).

    Shared by both the create-a-session path (run_turn) and the
    reuse-an-existing-session path (run_followup_turn) so the polling
    semantics — timeout, error handling, the "requires_action" case this
    client doesn't handle — live in exactly one place.
    """
    deadline = time.monotonic() + _TURN_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            poll = httpx.get(f"{_base_url()}/api/v1/sessions/{session_id}/turns/{turn_id}", timeout=15.0)
            poll.raise_for_status()
            state = poll.json()["data"]["state"]
            if not isinstance(state, dict):
                raise TypeError(f"expected turn state to be an object, got {type(state).__name__}")

            status = state.get("status")
            if status == "done":
                output = state.get("output")
                if output is None:
                    raise TrueForgeError(
                        f"TrueForge turn for {label} finished with no output "
                        f"(required_actions={state.get('required_actions')})"
                    )
                if not isinstance(output, dict):
                    raise TypeError(f"expected turn output to be an object, got {type(output).__name__}")
                return output.get("content", "")
            if status == "error":
                raise TrueForgeError(f"TrueForge turn for {label} failed: {state.get('message')}")
            if status == "requires_action":
                raise TrueForgeError(
                    f"TrueForge turn for {label} requires an approval this client does not handle"
                )
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise TrueForgeError(f"Failed to poll TrueForge turn for {label}: {exc}") from exc
        time.sleep(_TURN_POLL_INTERVAL_SECONDS)

    raise TrueForgeError(f"TrueForge turn for {label} timed out after {_TURN_POLL_TIMEOUT_SECONDS}s")


def run_turn(agent_name: str, message: str) -> tuple[dict[str, Any], str]:
    """Create a brand-new TrueForge session, run one user-message turn on
    it, and return the parsed JSON object from the model's final response
    together with the session_id that produced it.

    The session_id is returned (rather than discarded) so callers can
    persist it and later resume genuine conversational memory of this turn
    via run_followup_turn — TrueForge keeps this session's state in its own
    store independent of this call returning.

    Raises TrueForgeError on any transport failure, a turn that ends in
    error/needs-approval-forever, or non-JSON model output.
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
    content = _poll_turn_to_completion(session_id, turn_id, label=agent_name)
    parsed = _extract_json_object(content)
    if parsed is None:
        raise TrueForgeError(f"TrueForge agent {agent_name} returned non-JSON output: {content!r}")
    return parsed, session_id


def run_followup_turn(session_id: str, message: str) -> dict[str, Any]:
    """Run one more user-message turn on an EXISTING TrueForge session and
    return the parsed JSON object from the model's response.

    Unlike run_turn, this never calls POST /api/v1/sessions — it posts
    straight to /api/v1/sessions/{session_id}/turns, so the model genuinely
    continues the conversation it already had (its own prior reasoning is
    still in context, held by TrueForge's own session store), rather than
    being re-fed a fresh restatement of that context in a brand-new session.

    Raises TrueForgeError under the same conditions as run_turn, plus if
    the session_id no longer exists on the TrueForge side (e.g. its store
    was cleared).
    """
    turn_id = _start_turn(session_id, message, label=f"session {session_id}")
    content = _poll_turn_to_completion(session_id, turn_id, label=f"session {session_id}")
    parsed = _extract_json_object(content)
    if parsed is None:
        raise TrueForgeError(f"TrueForge session {session_id} returned non-JSON output: {content!r}")
    return parsed
