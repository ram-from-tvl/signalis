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
    by an earlier run, e.g. this PR's `skills` attachment and
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
    (e.g. this PR's skills attachment / sandbox enablement) actually take
    effect, rather than silently no-op'ing because the agent already
    existed.

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
        match = next((a for a in agents if a["name"] == name), None)
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
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        # ValueError covers `get_resp.json()` raising `json.JSONDecodeError`
        # on a malformed (but 2xx) response body from the list-agents
        # endpoint; TypeError covers an unexpected response shape, e.g.
        # `data` not being a list (`for a in agents` / `a["name"]` above).
        # Without catching these too, a malformed dependency response
        # escapes as a raw ValueError/TypeError instead of TrueForgeError,
        # so `run_agent_reasoning`'s `except TrueForgeError` never triggers
        # and the direct-LLM fallback never runs.
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
