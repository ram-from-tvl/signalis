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
    gemini_model: str = "gemini-2.5-flash"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
