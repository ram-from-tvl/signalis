"""Single choke point for all agent LLM calls.

Every agent goes through `generate_json`/`generate_text` so the model id,
API key handling, and error behavior live in exactly one place. Agents never
construct a genai client (or the HF client) of their own.

Hugging Face Inference Providers is the primary provider (chosen for
practical reliability over Gemini's restrictive free-tier daily quota). If
HF fails (missing token, transport error, unparsable response), the same
call is retried once against Gemini, so a transient HF outage does not stop
the pipeline from producing real, model-generated reasoning. This priority
order is a deliberate choice, not a fallback-of-convenience — see
docs/DECISIONS.md.
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
    models wrap JSON in prose, markdown code fences, or <tool_call> tags
    (some HF-served models emit a text-form tool call instead of using the
    tool_calls field, despite tool_choice) despite instructions.
    """
    text = text.strip()
    if text.startswith("<tool_call>"):
        text = text[len("<tool_call>") :]
        text = text.split("</tool_call>", 1)[0]
        text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    else:
        # HF's <tool_call> convention wraps the call as {"name": ..., "arguments": {...}}
        if isinstance(parsed, dict) and "arguments" in parsed and "name" in parsed:
            return parsed["arguments"]
        return parsed

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and "arguments" in parsed and "name" in parsed:
            return parsed["arguments"]
        return parsed
    return None


class LLMError(RuntimeError):
    """Raised when every configured LLM provider fails or returns unparsable output."""


_clients: dict[str, genai.Client] = {}


def _get_client(api_key: str) -> genai.Client:
    """One cached client per distinct API key, not a single process-global
    client — a single cache slot would silently keep serving whichever key
    last built it, so a rotation loop retrying "key 1" after key 2 succeeded
    would actually reuse key 2's client and never really retry key 1."""
    client = _clients.get(api_key)
    if client is None:
        client = genai.Client(api_key=api_key)
        _clients[api_key] = client
    return client


def _call_gemini_json_with_key(
    api_key: str, system_instruction: str, prompt: str, response_schema: dict[str, Any], temperature: float
) -> dict[str, Any]:
    client = _get_client(api_key)
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


def _call_gemini_json(
    system_instruction: str, prompt: str, response_schema: dict[str, Any], temperature: float
) -> dict[str, Any]:
    """Try every configured Gemini API key in order, moving to the next only
    on a genuine quota/auth failure from the current one."""
    settings = get_settings()
    keys = settings.gemini_api_keys
    if not keys:
        raise LLMError("GEMINI_API_KEY is not configured")

    last_error: LLMError | None = None
    for key in keys:
        try:
            return _call_gemini_json_with_key(key, system_instruction, prompt, response_schema, temperature)
        except LLMError as exc:
            last_error = exc
            quota_or_auth = "RESOURCE_EXHAUSTED" in str(exc) or "PERMISSION_DENIED" in str(exc) or "UNAUTHENTICATED" in str(exc)
            if not quota_or_auth:
                raise
    raise last_error


def _call_hf_json_with_token(
    token: str, system_instruction: str, prompt: str, response_schema: dict[str, Any], temperature: float
) -> dict[str, Any]:
    settings = get_settings()
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
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMError(f"Hugging Face call failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Hugging Face returned a non-JSON response body: {exc}") from exc

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Hugging Face returned an unexpected response: {data}") from exc

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        arguments = tool_calls[0]["function"]["arguments"]
    else:
        # Model answered in free text instead of calling the tool. Try to
        # recover a JSON object from the content rather than failing outright.
        arguments = _extract_json_object(message.get("content") or "")
        if arguments is None:
            raise LLMError(f"Hugging Face returned no usable tool call or JSON: {data}")
        return arguments

    try:
        return json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Hugging Face returned non-JSON arguments: {exc}") from exc


def _call_hf_text_with_token(token: str, system_instruction: str, prompt: str, temperature: float) -> str:
    settings = get_settings()
    payload = {
        "model": settings.hf_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        resp = httpx.post(
            _HF_ROUTER_URL,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMError(f"Hugging Face call failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Hugging Face returned a non-JSON response body: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Hugging Face returned an unexpected response: {data}") from exc

    if not content:
        raise LLMError("Hugging Face returned an empty response")
    return content


def _is_quota_or_auth_error(exc: LLMError) -> bool:
    text = str(exc)
    return "402" in text or "429" in text or "401" in text or "403" in text


def _call_hf_json(
    system_instruction: str, prompt: str, response_schema: dict[str, Any], temperature: float
) -> dict[str, Any]:
    """Try every configured HF token in order, moving to the next only on a
    genuine quota/auth failure (402/429/401/403) from the current one."""
    tokens = get_settings().hf_tokens
    if not tokens:
        raise LLMError("HF_TOKEN is not configured")

    last_error: LLMError | None = None
    for token in tokens:
        try:
            return _call_hf_json_with_token(token, system_instruction, prompt, response_schema, temperature)
        except LLMError as exc:
            last_error = exc
            if not _is_quota_or_auth_error(exc):
                raise
    raise last_error


def _call_hf_text(system_instruction: str, prompt: str, temperature: float) -> str:
    """Try every configured HF token in order, same rotation contract as
    _call_hf_json."""
    tokens = get_settings().hf_tokens
    if not tokens:
        raise LLMError("HF_TOKEN is not configured")

    last_error: LLMError | None = None
    for token in tokens:
        try:
            return _call_hf_text_with_token(token, system_instruction, prompt, temperature)
        except LLMError as exc:
            last_error = exc
            if not _is_quota_or_auth_error(exc):
                raise
    raise last_error


def generate_json(
    *,
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any],
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Return a structured object from the primary provider (Hugging Face),
    falling back to Gemini if HF is unavailable.

    Raises LLMError only if both providers fail, so callers can decide how to
    surface the failure rather than silently falling back to fabricated
    reasoning.
    """
    try:
        return _call_hf_json(system_instruction, prompt, response_schema, temperature)
    except LLMError as hf_error:
        logger.warning("Hugging Face call failed, falling back to Gemini: %s", hf_error)
        try:
            return _call_gemini_json(system_instruction, prompt, response_schema, temperature)
        except LLMError as gemini_error:
            logger.error("Gemini fallback also failed: %s", gemini_error)
            raise LLMError(
                f"All LLM providers failed. Hugging Face: {hf_error}. Gemini: {gemini_error}"
            ) from gemini_error


def _call_gemini_text_with_key(api_key: str, system_instruction: str, prompt: str, temperature: float) -> str:
    settings = get_settings()
    client = _get_client(api_key)
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
        raise LLMError(f"Gemini call failed: {exc}") from exc

    text = getattr(response, "text", None)
    if not text:
        raise LLMError("Gemini returned an empty response")
    return text


def _call_gemini_text(system_instruction: str, prompt: str, temperature: float) -> str:
    """Try every configured Gemini API key in order, same rotation contract
    as _call_gemini_json."""
    keys = get_settings().gemini_api_keys
    if not keys:
        raise LLMError("GEMINI_API_KEY is not configured")

    last_error: LLMError | None = None
    for key in keys:
        try:
            return _call_gemini_text_with_key(key, system_instruction, prompt, temperature)
        except LLMError as exc:
            last_error = exc
            if not _is_quota_or_auth_error(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                raise
    raise last_error


def generate_text(*, system_instruction: str, prompt: str, temperature: float = 0.4) -> str:
    """Return a free-text completion from the primary provider (Hugging
    Face), falling back to Gemini if HF is unavailable. Same priority order
    and error-surfacing contract as generate_json."""
    try:
        return _call_hf_text(system_instruction, prompt, temperature)
    except LLMError as hf_error:
        logger.warning("Hugging Face call failed, falling back to Gemini: %s", hf_error)
        try:
            return _call_gemini_text(system_instruction, prompt, temperature)
        except LLMError as gemini_error:
            logger.error("Gemini fallback also failed: %s", gemini_error)
            raise LLMError(
                f"All LLM providers failed. Hugging Face: {hf_error}. Gemini: {gemini_error}"
            ) from gemini_error
