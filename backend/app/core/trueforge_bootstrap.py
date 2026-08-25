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

    if not settings.hf_token:
        print("HF_TOKEN is not set; skipping Hugging Face provider registration.", file=sys.stderr)
    else:
        print("Registering Hugging Face fallback model provider...")
        _put_or_post(
            "PUT",
            f"{base}/api/v1/settings/model-providers",
            {
                "manifest": {
                    "type": "custom",
                    "name": "huggingface",
                    "base_url": "https://router.huggingface.co/v1",
                    "auth": {"api_key": settings.hf_token},
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

    print("Done. Individual agents register automatically on first pipeline run.")


if __name__ == "__main__":
    main()
