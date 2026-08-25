"""Single choke point for all agent LLM calls.

Every agent goes through `generate_json` so the model id, API key handling,
and error behavior live in exactly one place. Agents never construct a
genai client (or the HF fallback client) of their own.

Gemini is the primary provider. If Gemini fails after retries (rate limit,
quota, transport error), the same call is retried once against a Hugging
Face Inference Providers model using OpenAI-compatible tool calling, so a
transient Gemini outage does not stop the pipeline from producing real,
model-generated reasoning.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from google import genai
from google.genai import types

from app.core.config import get_settings

logger = logging.getLogger("signalis.llm")

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0
_HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort recovery of a JSON object from free-text model output.

    Tries the whole string first, then the widest {...} span, since some
    models wrap JSON in prose or markdown code fences despite instructions.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


class LLMError(RuntimeError):
    """Raised when every configured LLM provider fails or returns unparsable output."""


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not configured")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _call_gemini_json(
    system_instruction: str, prompt: str, response_schema: dict[str, Any], temperature: float
) -> dict[str, Any]:
    client = _get_client()
    settings = get_settings()
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
        raise LLMError(f"Gemini call failed: {last_error}") from last_error

    text = getattr(response, "text", None)
    if not text:
        raise LLMError("Gemini returned an empty response")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Gemini returned non-JSON output: {exc}") from exc


def _call_hf_fallback_json(
    system_instruction: str, prompt: str, response_schema: dict[str, Any], temperature: float
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.hf_token:
        raise LLMError("HF_TOKEN is not configured, no fallback available")

    tool_name = "submit_result"
    # This provider only supports tool_choice "auto"/"none" (not "required"),
    # so the model can technically still answer in free text. The system
    # instruction is strengthened to make that the unlikely path, and a
    # free-text JSON extraction is tried as a second line of defense below.
    forced_instruction = (
        f"{system_instruction}\n\nYou MUST respond by calling the `{tool_name}` tool exactly once "
        "with the complete result. Do not respond with plain text."
    )
    payload = {
        "model": settings.hf_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": forced_instruction},
            {"role": "user", "content": prompt},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "Submit the structured result for this task.",
                    "parameters": response_schema,
                },
            }
        ],
        "tool_choice": "auto",
    }

    try:
        resp = httpx.post(
            _HF_ROUTER_URL,
            headers={"Authorization": f"Bearer {settings.hf_token}"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMError(f"Hugging Face fallback call failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Hugging Face fallback returned a non-JSON response body: {exc}") from exc

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Hugging Face fallback returned an unexpected response: {data}") from exc

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        arguments = tool_calls[0]["function"]["arguments"]
    else:
        # Model answered in free text instead of calling the tool. Try to
        # recover a JSON object from the content rather than failing outright.
        arguments = _extract_json_object(message.get("content") or "")
        if arguments is None:
            raise LLMError(f"Hugging Face fallback returned no usable tool call or JSON: {data}")
        return arguments

    try:
        return json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Hugging Face fallback returned non-JSON arguments: {exc}") from exc


def generate_json(
    *,
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any],
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Return a structured object from the primary provider, falling back to
    Hugging Face Inference Providers if Gemini is unavailable.

    Raises LLMError only if both providers fail, so callers can decide how to
    surface the failure rather than silently falling back to fabricated
    reasoning.
    """
    try:
        return _call_gemini_json(system_instruction, prompt, response_schema, temperature)
    except LLMError as gemini_error:
        logger.warning("Gemini call failed, falling back to Hugging Face: %s", gemini_error)
        try:
            return _call_hf_fallback_json(system_instruction, prompt, response_schema, temperature)
        except LLMError as hf_error:
            logger.error("Hugging Face fallback also failed: %s", hf_error)
            raise LLMError(
                f"All LLM providers failed. Gemini: {gemini_error}. Hugging Face: {hf_error}"
            ) from hf_error


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
