"""Central application configuration.

All environment-dependent values (API keys, model names, DB location, thresholds)
are read here exactly once so the rest of the codebase never touches os.environ
directly. This is the single place to change the Gemini model id or the
confidence threshold that drives the human-approval checkpoint.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_api_key_1: str = ""
    gemini_model: str = "gemini-3.6-flash"
    hf_token: str = ""
    hf_token_1: str = ""
    hf_token_2: str = ""
    hf_model: str = "Qwen/Qwen3-4B-Instruct-2507:nscale"
    daytona_api_key: str = ""
    daytona_api_url: str = "https://app.daytona.io/api"
    daytona_sandbox_id: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""
    hunter_api_key: str = ""
    trueforge_url: str = "http://localhost:8790"
    trueforge_enabled: bool = True
    trueforge_model: str = "huggingface/qwen3-4b"
    trueforge_model_1: str = "huggingface-2/qwen3-4b"
    trueforge_model_2: str = "huggingface-3/qwen3-4b"
    trueforge_model_fallback: str = "google-gemini/gemini-3-6-flash"
    database_url: str = f"sqlite:///{BACKEND_DIR / 'signalis.db'}"

    # Below this confidence, a stage classification requires human approval
    # before any downstream outreach plan is treated as "ready".
    confidence_approval_threshold: float = 0.5

    # Rolling window (days) the Buying Stage Orchestrator uses when weighing
    # signal recency for a lead.
    signal_window_days: int = 30

    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    app_env: str = "development"

    @property
    def hf_tokens(self) -> list[str]:
        """All configured HF tokens, in order, blanks excluded. Multiple
        keys let callers rotate to the next one when a given key's quota is
        exhausted (HTTP 402), rather than failing the whole call."""
        return [t for t in (self.hf_token, self.hf_token_1, self.hf_token_2) if t]

    @property
    def gemini_api_keys(self) -> list[str]:
        return [k for k in (self.gemini_api_key, self.gemini_api_key_1) if k]

    @property
    def trueforge_models(self) -> list[str]:
        """TrueForge-registered models to try in order for every agent turn,
        each backed by a distinct HF token (so one key's quota running out
        rotates to the next registered HF provider), with Gemini registered
        last as a distinct-provider fallback. Only includes an entry for
        each additional key actually configured (via hf_tokens)."""
        models = [self.trueforge_model]
        extra_models = [self.trueforge_model_1, self.trueforge_model_2]
        for _extra_token, extra_model in zip(self.hf_tokens[1:], extra_models):
            models.append(extra_model)
        if self.trueforge_model_fallback not in models:
            models.append(self.trueforge_model_fallback)
        return models


@lru_cache
def get_settings() -> Settings:
    return Settings()
