"""Remote MCP server exposing Exa semantic/company-focused web search.

Run standalone (`python -m app.mcp_tools.exa_server`) so TrueForge can reach
it as a `remote` MCP server over HTTP, the same way `research_server.py` is
reached. Complements the Tavily-backed research server with a differently
sourced, neural/semantic search over a dedicated company index rather than
duplicating broad web search.
"""
from __future__ import annotations

import logging

import httpx
from mcp.server import MCPServer

from app.core.config import get_settings

logger = logging.getLogger("signalis.exa_server")

server = MCPServer(
    name="signalis-exa",
    instructions="Semantic/company-focused web search via Exa, complementing the Tavily-backed research server.",
)

_EXA_SEARCH_URL = "https://api.exa.ai/search"
_MAX_RESULTS = 5
_REQUEST_TIMEOUT_SECONDS = 10.0
# Generous enough for any real company name or focus phrase, but bounded so a
# runaway/malicious input can't blow up the query sent to Exa or waste quota.
_MAX_INPUT_LENGTH = 200


@server.tool()
def search_company_semantic(company_name: str, focus: str | None = None) -> dict:
    """Semantic web search for a company via Exa, optionally narrowed by
    `focus` (e.g. "funding" or "hiring").

    Never raises: if EXA_API_KEY is unset, or the call fails or times out,
    returns a normalized "not queried" shape instead of crashing the agent
    turn.
    """
    company_name_clean = (company_name or "").strip()
    if not company_name_clean:
        return {
            "company_name": company_name,
            "results": [],
            "queried": False,
            "reason": "company_name is required and must not be blank",
        }
    if len(company_name_clean) > _MAX_INPUT_LENGTH:
        return {
            "company_name": company_name,
            "results": [],
            "queried": False,
            "reason": f"company_name exceeds the {_MAX_INPUT_LENGTH}-character limit",
        }

    focus_clean = (focus or "").strip() or None
    if focus_clean and len(focus_clean) > _MAX_INPUT_LENGTH:
        return {
            "company_name": company_name,
            "results": [],
            "queried": False,
            "reason": f"focus exceeds the {_MAX_INPUT_LENGTH}-character limit",
        }

    settings = get_settings()
    if not settings.exa_api_key:
        return {
            "company_name": company_name,
            "results": [],
            "queried": False,
            "reason": "EXA_API_KEY is not configured",
        }

    query = f"{company_name_clean} company news, product, or hiring"
    if focus_clean:
        query = f"{query} (focus: {focus_clean})"

    try:
        response = httpx.post(
            _EXA_SEARCH_URL,
            headers={"Authorization": f"Bearer {settings.exa_api_key}"},
            json={
                "query": query,
                "numResults": _MAX_RESULTS,
                "type": "neural",
                "category": "company",
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Exa search call failed for %r: %s", company_name, exc)
        return {
            "company_name": company_name,
            "results": [],
            "queried": False,
            "reason": f"Exa request failed: {exc}",
        }
    except ValueError as exc:  # response.json() decode failure
        logger.warning("Exa search returned a non-JSON response for %r: %s", company_name, exc)
        return {
            "company_name": company_name,
            "results": [],
            "queried": False,
            "reason": f"Exa returned a non-JSON response: {exc}",
        }

    try:
        if "results" not in data or not isinstance(data["results"], list):
            raise TypeError("'results' field is missing or not a list")
        raw_results = data["results"]
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("text") or _first_highlight(item),
            }
            for item in raw_results[:_MAX_RESULTS]
        ]
    except (AttributeError, TypeError) as exc:
        logger.warning("Exa search returned an unexpected response shape for %r: %s", company_name, exc)
        return {
            "company_name": company_name,
            "results": [],
            "queried": False,
            "reason": f"Exa returned an unexpected response shape: {exc}",
        }

    return {"company_name": company_name, "results": results, "queried": True}


def _first_highlight(item: dict) -> str:
    highlights = item.get("highlights")
    if isinstance(highlights, list) and highlights:
        return str(highlights[0])
    return ""


def create_app():
    return server.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8793)
