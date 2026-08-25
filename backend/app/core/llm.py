"""Single choke point for all Gemini calls.

Every agent goes through `generate_json` so the model id, API key handling,
and error behavior live in exactly one place. Agents never construct a
genai client of their own.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from google import genai
from google.genai import types

from app.core.config import get_settings

logger = logging.getLogger("signalis.llm")

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0


class LLMError(RuntimeError):
    """Raised when the Gemini API call fails or returns unparsable output."""


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not configured")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def generate_json(
    *,
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any],
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Call Gemini with a JSON response schema and return the parsed object.

    Raises LLMError on any transport failure or on a response that fails to
    parse as JSON, so callers can decide how to surface the failure rather
    than silently falling back to fabricated reasoning.
    """
    settings = get_settings()
    client = _get_client()
    response = None
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=temperature,
                ),
            )
            last_error = None
            break
        except Exception as exc:  # network/auth/quota failures from the SDK
            last_error = exc
            is_last_attempt = attempt == _MAX_RETRIES - 1
            transient = "RESOURCE_EXHAUSTED" in str(exc) or "UNAVAILABLE" in str(exc)
            if is_last_attempt or not transient:
                break
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))

    if last_error is not None:
        logger.error("Gemini call failed: %s", last_error)
        raise LLMError(f"Gemini call failed: {last_error}") from last_error

    text = getattr(response, "text", None)
    if not text:
        raise LLMError("Gemini returned an empty response")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Gemini returned non-JSON output: {exc}") from exc


def generate_text(*, system_instruction: str, prompt: str, temperature: float = 0.4) -> str:
    settings = get_settings()
    client = _get_client()
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
            ),
        )
    except Exception as exc:
        logger.error("Gemini call failed: %s", exc)
        raise LLMError(f"Gemini call failed: {exc}") from exc

    text = getattr(response, "text", None)
    if not text:
        raise LLMError("Gemini returned an empty response")
    return text
