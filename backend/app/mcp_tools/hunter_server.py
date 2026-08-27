"""Remote MCP server exposing Hunter.io email finding and verification.

Run standalone (`python -m app.mcp_tools.hunter_server`) so TrueForge can
reach it as a `remote` MCP server over HTTP, the same way `research_server.py`
and `exa_server.py` are reached. Gives the Outreach Planner a way to check
whether a lead's email is actually deliverable before generating copy for it.
"""
from __future__ import annotations

import logging

import httpx
from mcp.server import MCPServer

from app.core.config import get_settings

logger = logging.getLogger("signalis.hunter_server")

server = MCPServer(
    name="signalis-hunter",
    instructions="Email finder and verifier via Hunter.io, so outreach copy is only generated for a deliverable address.",
)

_HUNTER_BASE_URL = "https://api.hunter.io/v2"
_REQUEST_TIMEOUT_SECONDS = 10.0
# Generous enough for any real name/domain/email, but bounded so a runaway
# or malicious input can't waste Hunter.io's limited monthly credits.
_MAX_INPUT_LENGTH = 200


def _blank_or_oversized(value: str, field_name: str) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return f"{field_name} is required and must not be blank"
    if len(cleaned) > _MAX_INPUT_LENGTH:
        return f"{field_name} exceeds the {_MAX_INPUT_LENGTH}-character limit"
    return None


@server.tool()
def find_email(first_name: str, last_name: str, domain: str) -> dict:
    """Find a person's likely email address at a company domain via
    Hunter.io's Email Finder.

    Never raises: if HUNTER_API_KEY is unset, or the call fails or times
    out, returns a normalized "not queried" shape instead of crashing the
    agent turn.
    """
    for value, field_name in ((first_name, "first_name"), (last_name, "last_name"), (domain, "domain")):
        error = _blank_or_oversized(value, field_name)
        if error:
            return {"email": None, "score": None, "queried": False, "reason": error}

    settings = get_settings()
    if not settings.hunter_api_key:
        return {"email": None, "score": None, "queried": False, "reason": "HUNTER_API_KEY is not configured"}

    try:
        response = httpx.get(
            f"{_HUNTER_BASE_URL}/email-finder",
            headers={"X-API-KEY": settings.hunter_api_key},
            params={"domain": domain.strip(), "first_name": first_name.strip(), "last_name": last_name.strip()},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Hunter email-finder call failed for %r@%r: %s", first_name, domain, exc)
        return {"email": None, "score": None, "queried": False, "reason": f"Hunter request failed: {exc}"}
    except ValueError as exc:
        logger.warning("Hunter email-finder returned a non-JSON response: %s", exc)
        return {"email": None, "score": None, "queried": False, "reason": f"Hunter returned a non-JSON response: {exc}"}

    try:
        data = body["data"]
        if not isinstance(data, dict):
            raise TypeError("'data' field is not an object")
    except (KeyError, TypeError) as exc:
        logger.warning("Hunter email-finder returned an unexpected response shape: %s", exc)
        return {
            "email": None,
            "score": None,
            "queried": False,
            "reason": f"Hunter returned an unexpected response shape: {exc}",
        }

    return {"email": data.get("email"), "score": data.get("score"), "queried": True, "reason": None}


@server.tool()
def verify_email(email: str) -> dict:
    """Verify whether an email address is deliverable via Hunter.io's
    Email Verifier.

    Never raises: if HUNTER_API_KEY is unset, or the call fails or times
    out, returns a normalized "not queried" shape instead of crashing the
    agent turn.
    """
    error = _blank_or_oversized(email, "email")
    if error:
        return {"email": email, "status": None, "score": None, "queried": False, "reason": error}
    if "@" not in email:
        return {
            "email": email,
            "status": None,
            "score": None,
            "queried": False,
            "reason": "email does not look like a valid address (missing '@')",
        }

    settings = get_settings()
    if not settings.hunter_api_key:
        return {
            "email": email,
            "status": None,
            "score": None,
            "queried": False,
            "reason": "HUNTER_API_KEY is not configured",
        }

    try:
        response = httpx.get(
            f"{_HUNTER_BASE_URL}/email-verifier",
            headers={"X-API-KEY": settings.hunter_api_key},
            params={"email": email.strip()},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Hunter email-verifier call failed for %r: %s", email, exc)
        return {
            "email": email,
            "status": None,
            "score": None,
            "queried": False,
            "reason": f"Hunter request failed: {exc}",
        }
    except ValueError as exc:
        logger.warning("Hunter email-verifier returned a non-JSON response: %s", exc)
        return {
            "email": email,
            "status": None,
            "score": None,
            "queried": False,
            "reason": f"Hunter returned a non-JSON response: {exc}",
        }

    try:
        data = body["data"]
        if not isinstance(data, dict):
            raise TypeError("'data' field is not an object")
    except (KeyError, TypeError) as exc:
        logger.warning("Hunter email-verifier returned an unexpected response shape: %s", exc)
        return {
            "email": email,
            "status": None,
            "score": None,
            "queried": False,
            "reason": f"Hunter returned an unexpected response shape: {exc}",
        }

    return {
        "email": email,
        "status": data.get("status"),
        "score": data.get("score"),
        "queried": True,
        "reason": None,
    }


def create_app():
    return server.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8794)
