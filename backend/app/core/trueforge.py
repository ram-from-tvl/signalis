"""HTTP client for the local TrueForge agent harness.

Every agent's reasoning runs as a TrueForge session/turn rather than a bare
model API call: TrueForge owns the agent loop (model calls, MCP tool
discovery/execution, context management) and this client only starts turns
and reads back their structured output. TrueForge must already be running
locally (see README) before agents can execute.
"""
from __future__ import annotations

import datetime
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


def _get_session_events(session_id: str) -> list[dict[str, Any]]:
    try:
        resp = httpx.get(f"{_base_url()}/api/v1/sessions/{session_id}/events", timeout=15.0)
        resp.raise_for_status()
        return resp.json()["data"]
    except (httpx.HTTPError, KeyError) as exc:
        raise TrueForgeError(f"Failed to fetch TrueForge session events for {session_id}: {exc}") from exc


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


def run_turn(agent_name: str, message: str, *, with_delegations: bool = False) -> dict[str, Any] | tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one user-message turn against a named TrueForge agent and return
    the parsed JSON object from the model's final response.

    If ``with_delegations`` is True, also fetches the session's event stream
    and returns ``(parsed_output, subagent_delegations)``, where
    ``subagent_delegations`` is a list of genuine ``create_sub_agent``
    delegations TrueForge itself recorded for this turn (empty if the model
    chose not to delegate) — see ``_extract_subagent_delegations``.

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

        turn_resp = httpx.post(
            f"{_base_url()}/api/v1/sessions/{session_id}/turns",
            json={"stream": False, "input": [{"type": "user.message", "content": message}]},
            timeout=30.0,
        )
        turn_resp.raise_for_status()
        turn_id = turn_resp.json()["data"]["id"]
    except (httpx.HTTPError, KeyError) as exc:
        raise TrueForgeError(f"Failed to start TrueForge turn for {agent_name}: {exc}") from exc

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
            output = state.get("output")
            if output is None:
                raise TrueForgeError(
                    f"TrueForge turn for {agent_name} finished with no output "
                    f"(required_actions={state.get('required_actions')})"
                )
            content = output.get("content", "")
            parsed = _extract_json_object(content)
            if parsed is None:
                raise TrueForgeError(f"TrueForge agent {agent_name} returned non-JSON output: {content!r}")
            if with_delegations:
                delegations = _extract_subagent_delegations(_get_session_events(session_id))
                return parsed, delegations
            return parsed
        if status == "error":
            raise TrueForgeError(f"TrueForge turn for {agent_name} failed: {state.get('message')}")
        if status == "requires_action":
            raise TrueForgeError(
                f"TrueForge turn for {agent_name} requires an approval this client does not handle"
            )
        time.sleep(_TURN_POLL_INTERVAL_SECONDS)

    raise TrueForgeError(f"TrueForge turn for {agent_name} timed out after {_TURN_POLL_TIMEOUT_SECONDS}s")
