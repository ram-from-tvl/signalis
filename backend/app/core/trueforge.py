"""HTTP client for the local TrueForge agent harness.

Every agent's reasoning runs as a TrueForge session/turn rather than a bare
model API call: TrueForge owns the agent loop (model calls, MCP tool
discovery/execution, context management) and this client only starts turns
and reads back their structured output. TrueForge must already be running
locally (see README) before agents can execute.
"""
from __future__ import annotations

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
    """Create the named TrueForge agent, or update its manifest in place if
    it already exists.

    TrueForge agent names are unique, but the manifest itself is mutable via
    `PUT /api/v1/agents/{agent_id}` — this matters because agent config
    (like which MCP servers are attached) needs to take effect even when the
    agent was already registered by an earlier run.
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
    (e.g. a newly attached MCP server) actually take effect, rather than
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


def run_turn(agent_name: str, message: str) -> dict[str, Any]:
    """Run one user-message turn against a named TrueForge agent and return
    the parsed JSON object from the model's final response.

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
            return parsed
        if status == "error":
            raise TrueForgeError(f"TrueForge turn for {agent_name} failed: {state.get('message')}")
        if status == "requires_action":
            raise TrueForgeError(
                f"TrueForge turn for {agent_name} requires an approval this client does not handle"
            )
        time.sleep(_TURN_POLL_INTERVAL_SECONDS)

    raise TrueForgeError(f"TrueForge turn for {agent_name} timed out after {_TURN_POLL_TIMEOUT_SECONDS}s")
