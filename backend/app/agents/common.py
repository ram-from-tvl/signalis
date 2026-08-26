"""Shared helpers used by every agent node.

Keeps agent_runs persistence logic (the audit-trail table backing the trace
view) in one place so each agent focuses on its own prompt/schema.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMError, generate_json
from app.core.trueforge import PendingToolApproval, TrueForgeError, ensure_agent, resume_turn, run_turn
from app.models import AgentRun

logger = logging.getLogger("signalis.agents")


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

    Falls back to a direct Gemini/Hugging-Face call if TrueForge is
    disabled or unreachable — same prompt and schema, a transport fallback
    not a second reasoning path.

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
        try:
            ensure_agent(
                trueforge_agent_name,
                model=model,
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
        except TrueForgeError as exc:
            logger.warning(
                "TrueForge call failed for %s, falling back to direct LLM call: %s",
                trueforge_agent_name,
                exc,
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
        try:
            ensure_agent(
                trueforge_agent_name,
                model=model,
                instructions=(
                    f"{system_instruction}\n\nRespond with a single JSON object matching this "
                    f"JSON schema exactly, and nothing else: {json.dumps(response_schema)}"
                ),
            )
            output, _session_id, delegations = run_turn(trueforge_agent_name, prompt, with_delegations=True)
            return output, delegations, True
        except TrueForgeError as exc:
            logger.warning(
                "TrueForge call failed for %s, falling back to direct LLM call "
                "(subagent delegation unavailable on the fallback path): %s",
                trueforge_agent_name,
                exc,
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
