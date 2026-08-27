"""One-time TrueForge provider bootstrap.

Registers the Gemini model provider, the Hugging Face fallback model
provider, the Daytona sandbox provider, and the enrichment MCP server with a
running TrueForge instance, using the same credentials Signalis itself uses
from the root .env file. Individual agents are registered lazily by
app.agents.common.run_agent_reasoning on first use, so this script only
needs to run once after TrueForge starts (or whenever its local SQLite store
is reset).

Usage: python -m app.core.trueforge_bootstrap
"""
from __future__ import annotations

import sys

import httpx

from app.core.config import get_settings


def _put_or_post(method: str, url: str, json_body: dict) -> None:
    resp = httpx.request(method, url, json=json_body, timeout=15.0)
    if resp.status_code >= 400:
        print(f"  warning: {method} {url} -> {resp.status_code} {resp.text}", file=sys.stderr)
    else:
        print(f"  ok: {method} {url}")


def main() -> None:
    settings = get_settings()
    base = settings.trueforge_url.rstrip("/")

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set; skipping Gemini provider registration.", file=sys.stderr)
    else:
        print("Registering Gemini model provider...")
        _put_or_post(
            "PUT",
            f"{base}/api/v1/settings/model-providers",
            {
                "manifest": {
                    "type": "google-gemini",
                    "auth": {"api_key": settings.gemini_api_key},
                    "models": [
                        {"model_id": settings.gemini_model, "name": "gemini-2-5-flash", "properties": {}}
                    ],
                }
            },
        )

    hf_tokens = settings.hf_tokens
    if not hf_tokens:
        print("HF_TOKEN is not set; skipping Hugging Face provider registration.", file=sys.stderr)
    else:
        # One named provider per configured HF token (huggingface,
        # huggingface-2, huggingface-3, ...) so run_agent_reasoning can
        # rotate trueforge_model to a different provider/key when one's
        # quota is exhausted (see Settings.trueforge_models), rather than
        # only having a single HF identity registered with TrueForge.
        for i, token in enumerate(hf_tokens):
            provider_name = "huggingface" if i == 0 else f"huggingface-{i + 1}"
            print(f"Registering Hugging Face model provider '{provider_name}'...")
            _put_or_post(
                "PUT",
                f"{base}/api/v1/settings/model-providers",
                {
                    "manifest": {
                        "type": "custom",
                        "name": provider_name,
                        "base_url": "https://router.huggingface.co/v1",
                        "auth": {"api_key": token},
                        "models": [{"model_id": settings.hf_model, "name": "qwen3-4b", "properties": {}}],
                    }
                },
            )

    if not settings.daytona_api_key:
        print("DAYTONA_API_KEY is not set; skipping Daytona sandbox provider registration.", file=sys.stderr)
    else:
        print("Registering Daytona sandbox provider...")
        _put_or_post(
            "PUT",
            f"{base}/api/v1/settings/sandbox-providers",
            {
                "manifest": {
                    "type": "daytona",
                    "auth": {"api_key": settings.daytona_api_key},
                    "exec_timeout_ms": 30000,
                    "auto_stop_interval_in_minutes": 15,
                    "auto_archive_interval_in_minutes": 60,
                    "auto_delete_interval_in_minutes": 0,
                }
            },
        )

    print("Registering the enrichment MCP server...")
    _put_or_post(
        "PUT",
        f"{base}/api/v1/settings/mcp-servers",
        {
            "manifest": {
                "type": "remote",
                "name": "signalis-enrichment",
                "url": "http://127.0.0.1:8791/mcp",
                "description": "Firmographic enrichment tools: industry classification and company size estimation",
            }
        },
    )

    print("Registering the research MCP server...")
    _put_or_post(
        "PUT",
        f"{base}/api/v1/settings/mcp-servers",
        {
            "manifest": {
                "type": "remote",
                "name": "signalis-research",
                "url": "http://127.0.0.1:8792/mcp",
                "description": "Live web-research tool: recent company news, funding, and hiring signals via Tavily",
            }
        },
    )

    print("Registering the Exa search MCP server...")
    _put_or_post(
        "PUT",
        f"{base}/api/v1/settings/mcp-servers",
        {
            "manifest": {
                "type": "remote",
                "name": "signalis-exa",
                "url": "http://127.0.0.1:8793/mcp",
                "description": "Semantic/company-focused web search via Exa, complementing the Tavily-backed research server",
            }
        },
    )

    print("Registering the Hunter.io email MCP server...")
    _put_or_post(
        "PUT",
        f"{base}/api/v1/settings/mcp-servers",
        {
            "manifest": {
                "type": "remote",
                "name": "signalis-hunter",
                "url": "http://127.0.0.1:8794/mcp",
                "description": "Email finder and verifier via Hunter.io, so outreach copy is only generated for a deliverable address",
            }
        },
    )

    print("Registering the outreach copywriting style guide skill...")
    # Also registered lazily on the Outreach Planner's first run; doing it
    # here too avoids relying on registration order at cold start.
    from app.agents.outreach_planner import _ensure_style_guide_skill

    if _ensure_style_guide_skill() is not None:
        print("  ok: outreach-copywriting-style-guide skill registered")
    else:
        print("  warning: could not register outreach-copywriting-style-guide skill", file=sys.stderr)

    print("Done. Individual agents register automatically on first pipeline run.")


if __name__ == "__main__":
    main()
