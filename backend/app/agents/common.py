"""Shared helpers used by every agent node.

Keeps agent_runs persistence logic (the audit-trail table backing the trace
view) in one place so each agent focuses on its own prompt/schema.
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMError, generate_json
from app.core.trueforge import (
    PendingToolApproval,
    TrueForgeError,
    TrueForgeTurnExecutedError,
    ensure_agent,
    resume_turn,
    run_turn,
)
from app.models import AgentRun

logger = logging.getLogger("signalis.agents")

# TrueForge agents are registered under fixed, shared names (e.g.
# "signalis-persona-fit"), not per-request/per-lead identities. Model
# rotation makes ensure_agent(model=candidate) -> run_turn(...) a genuine
# two-step, retryable sequence against that shared name, so two concurrent
# requests for the same agent could otherwise interleave: request A's
# ensure_agent(model=B) could be immediately followed by request B's
# ensure_agent(model=A) before A's run_turn fires, making A silently run on
# B's model. A per-agent-name lock serializes ensure_agent+run_turn for a
# given agent within this single-process deployment (no multi-worker
# uvicorn, no external process pool — see README), which is sufficient to
# close the race for how this app actually runs.
_agent_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_agent_locks_guard = threading.Lock()


def _lock_for_agent(agent_name: str) -> threading.Lock:
    with _agent_locks_guard:
        return _agent_locks[agent_name]


class AgentPausedForToolApproval(Exception):
    """Raised by run_agent_reasoning when a TrueForge turn paused on a
    require_approval_for_tools gate instead of producing a final answer.
    Only agents with a gated MCP attachment (currently Persona Fit) trigger
    this; app.services.pipeline catches it to persist a ToolApprovalRequest.
    """

    def __init__(self, pending: list[PendingToolApproval]):
        self.pending = pending
        super().__init__(
            f"Turn paused awaiting approval for {len(pending)} tool call(s): "
            f"{[p.tool_name for p in pending]}"
        )


def start_run(db: Session, *, lead_id: str | None, agent_name: str, input_summary: str) -> AgentRun:
    run = AgentRun(
        lead_id=lead_id,
        agent_name=agent_name,
        input_summary=input_summary,
        status="running",
        started_at=datetime.datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_run(
    db: Session,
    run: AgentRun,
    *,
    output: dict[str, Any],
    reasoning: str,
    status: str = "completed",
    trueforge_session_id: str | None = None,
) -> AgentRun:
    run.output = output
    run.reasoning = reasoning
    run.status = status
    run.completed_at = datetime.datetime.utcnow()
    if trueforge_session_id is not None:
        run.trueforge_session_id = trueforge_session_id
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _trueforge_models_to_try(model: str) -> list[str]:
    """The caller-requested model first (so an explicit override is always
    honored), then every other configured trueforge_models entry, without
    duplicates — so a quota failure on one HF-backed provider rotates to the
    next registered provider/key before falling through to the Python
    direct-call path."""
    settings = get_settings()
    models = [model]
    for candidate in settings.trueforge_models:
        if candidate not in models:
            models.append(candidate)
    return models


def run_agent_reasoning(
    *,
    trueforge_agent_name: str,
    model: str,
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any],
    temperature: float = 0.3,
    mcp_servers: list[dict] | None = None,
    skills: list[dict] | None = None,
    fallback_style_guidance: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Run one agent's reasoning step through TrueForge (real agent loop:
    model calls, MCP tool discovery/execution, context management) rather
    than a bare model call.

    Tries every configured TrueForge model/provider in order (see
    Settings.trueforge_models) before falling back to a direct Gemini/
    Hugging-Face call — so a quota failure on one HF key rotates to another
    registered HF provider first, and only falls through to the tool-less
    Python path if every TrueForge-registered provider fails.

    Returns (parsed_result, trueforge_session_id); session_id is None on
    the fallback path (no TrueForge session exists there), and is what
    later lets a follow-up question continue this exact session.

    `skills` are name-only TrueForge skill references (fallback path can't
    load them). `fallback_style_guidance`, if given, is appended to the
    fallback instruction so craft guidance that now lives in a skill isn't
    lost when TrueForge is unavailable.

    Raises AgentPausedForToolApproval instead of returning if the turn
    paused on a require_approval_for_tools gate — never falls back in that
    case, since a pause isn't a transport failure and retrying through the
    tool-less fallback would just skip the approval gate.
    """
    settings = get_settings()
    if settings.trueforge_enabled:
        last_trueforge_error: TrueForgeError | None = None
        for candidate_model in _trueforge_models_to_try(model):
            try:
                with _lock_for_agent(trueforge_agent_name):
                    ensure_agent(
                        trueforge_agent_name,
                        model=candidate_model,
                        instructions=(
                            f"{system_instruction}\n\nRespond with a single JSON object matching this "
                            f"JSON schema exactly, and nothing else: {json.dumps(response_schema)}"
                        ),
                        mcp_servers=mcp_servers,
                        skills=skills,
                    )
                    result, session_id = run_turn(trueforge_agent_name, prompt)
                if isinstance(result, list):
                    raise AgentPausedForToolApproval(result)
                return result, session_id
            except TrueForgeTurnExecutedError as exc:
                # The turn genuinely ran (model reasoning + any real MCP tool
                # calls already executed) before failing at output
                # resolution — retrying on a different model would re-run
                # those side effects, so stop rotating and fall through to
                # the direct-LLM path below exactly as before this feature.
                last_trueforge_error = exc
                logger.warning(
                    "TrueForge turn for %s executed but failed to resolve on model %s; "
                    "not rotating models (would repeat side effects), falling back to direct LLM call: %s",
                    trueforge_agent_name,
                    candidate_model,
                    exc,
                )
                break
            except TrueForgeError as exc:
                # A pre-execution failure (agent registration, session/turn
                # creation, or poll transport) — nothing ran, so it's safe
                # to try the next configured model.
                last_trueforge_error = exc
                logger.warning(
                    "TrueForge call failed for %s on model %s, trying next configured model: %s",
                    trueforge_agent_name,
                    candidate_model,
                    exc,
                )
        else:
            logger.warning(
                "TrueForge call failed for %s on every configured model, falling back to direct LLM call: %s",
                trueforge_agent_name,
                last_trueforge_error,
            )

    fallback_instruction = system_instruction
    if mcp_servers:
        # Neutralize tool-referencing instructions — the fallback path has
        # no tool access, so leaving them in makes the model try to "use"
        # tools that don't exist and answer incoherently.
        fallback_instruction = (
            f"{system_instruction}\n\nNo external tools are available for this request. "
            "Answer using only the information given in the prompt, and do not reference "
            "or attempt to call any tool."
        )
    if fallback_style_guidance and fallback_style_guidance not in fallback_instruction:
        # `not in` avoids double-injecting when a caller (e.g.
        # run_outreach_planner on a skill-registration failure) already
        # folded this same guidance into system_instruction.
        fallback_instruction = f"{fallback_instruction}\n\n{fallback_style_guidance}"

    try:
        result = generate_json(
            system_instruction=fallback_instruction,
            prompt=prompt,
            response_schema=response_schema,
            temperature=temperature,
        )
        return result, None
    except LLMError as exc:
        raise LLMError(f"Agent {trueforge_agent_name} reasoning failed: {exc}") from exc


def run_agent_reasoning_with_delegations(
    *,
    trueforge_agent_name: str,
    model: str,
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any],
    temperature: float = 0.3,
    fallback_instruction: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None, bool]:
    """Like `run_agent_reasoning`, but also returns TrueForge subagent
    delegations (`create_sub_agent` calls) the turn performed, read from
    TrueForge's session events — see `_extract_subagent_delegations`.

    Subagent delegation has no fallback equivalent, so callers whose
    instruction mandates a `create_sub_agent` call must pass
    `fallback_instruction` — a version telling the model no delegation is
    available on the direct-LLM path, so it doesn't try to call a
    nonexistent tool or produce a non-delegated result indistinguishable
    from a genuine one.

    Returns (output, delegations, is_delegated); is_delegated is True only
    for a genuine TrueForge turn.
    """
    settings = get_settings()
    if settings.trueforge_enabled:
        last_trueforge_error: TrueForgeError | None = None
        for candidate_model in _trueforge_models_to_try(model):
            try:
                with _lock_for_agent(trueforge_agent_name):
                    ensure_agent(
                        trueforge_agent_name,
                        model=candidate_model,
                        instructions=(
                            f"{system_instruction}\n\nRespond with a single JSON object matching this "
                            f"JSON schema exactly, and nothing else: {json.dumps(response_schema)}"
                        ),
                    )
                    output, _session_id, delegations = run_turn(trueforge_agent_name, prompt, with_delegations=True)
                return output, delegations, True
            except TrueForgeTurnExecutedError as exc:
                # The turn (and any subagent delegations it fanned out to)
                # already ran — rotating to another model would re-run that
                # fan-out, so stop here and fall through to the direct-LLM
                # path exactly as before this feature.
                last_trueforge_error = exc
                logger.warning(
                    "TrueForge turn for %s executed but failed to resolve on model %s; "
                    "not rotating models (would repeat side effects), falling back to direct LLM call "
                    "(subagent delegation unavailable on the fallback path): %s",
                    trueforge_agent_name,
                    candidate_model,
                    exc,
                )
                break
            except TrueForgeError as exc:
                last_trueforge_error = exc
                logger.warning(
                    "TrueForge call failed for %s on model %s, trying next configured model: %s",
                    trueforge_agent_name,
                    candidate_model,
                    exc,
                )
        else:
            logger.warning(
                "TrueForge call failed for %s on every configured model, falling back to direct LLM call "
                "(subagent delegation unavailable on the fallback path): %s",
                trueforge_agent_name,
                last_trueforge_error,
            )

    effective_instruction = fallback_instruction if fallback_instruction is not None else system_instruction
    try:
        output = generate_json(
            system_instruction=effective_instruction,
            prompt=prompt,
            response_schema=response_schema,
            temperature=temperature,
        )
        return output, [], False
    except LLMError as exc:
        raise LLMError(f"Agent {trueforge_agent_name} reasoning failed: {exc}") from exc


def resume_agent_reasoning(
    *,
    trueforge_agent_name: str,
    session_id: str,
    thread_id: str,
    tool_call_id: str,
    approve: bool,
    deny_reason: str | None = None,
) -> dict[str, Any]:
    """Resumes a TrueForge turn previously paused by run_agent_reasoning
    raising AgentPausedForToolApproval, after a human decision.

    Raises AgentPausedForToolApproval again if the resumed turn immediately
    hits another gated tool call, so the caller can persist a new pending
    request instead of silently dropping it.
    """
    try:
        result = resume_turn(
            trueforge_agent_name,
            session_id=session_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            approve=approve,
            deny_reason=deny_reason,
        )
    except TrueForgeError as exc:
        raise TrueForgeError(f"Failed to resume agent {trueforge_agent_name}: {exc}") from exc

    if isinstance(result, list):
        raise AgentPausedForToolApproval(result)
    return result
