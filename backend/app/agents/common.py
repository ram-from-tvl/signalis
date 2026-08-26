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

    This is distinct from LLMError (a genuine failure) and a normal dict
    return (a genuine success) — callers that don't expect a pause (every
    agent except Persona Fit, at this build's scope) will simply never
    trigger it, since none of their MCP server attachments set
    require_approval_for_tools. Persona Fit's caller (app.services.pipeline)
    catches this specifically to persist a ToolApprovalRequest and surface a
    "paused, awaiting approval" outcome instead of crashing the pipeline run.
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
) -> AgentRun:
    run.output = output
    run.reasoning = reasoning
    run.status = status
    run.completed_at = datetime.datetime.utcnow()
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
) -> dict[str, Any]:
    """Run one agent's reasoning step through the TrueForge harness so the
    real agent loop (model calls, MCP tool discovery/execution, context
    management) genuinely executes this step, rather than a bare model call.

    Falls back to a direct Gemini/Hugging-Face call (app.core.llm) if
    TrueForge is disabled or unreachable, so the pipeline still produces
    real model reasoning when the local harness sidecar is not running —
    this is a transport fallback, not a second reasoning path: both routes
    execute the same prompt and schema.

    Raises AgentPausedForToolApproval instead of returning if the TrueForge
    turn paused on a require_approval_for_tools gate — this only happens for
    agents whose mcp_servers config actually sets that field (Persona Fit,
    at this build's scope). No fallback happens in that case: a pause is not
    a transport failure, so it must not be silently retried through the
    tool-less direct LLM path, which would just skip the approval gate
    entirely.
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
            )
            result = run_turn(trueforge_agent_name, prompt)
            if isinstance(result, list):
                raise AgentPausedForToolApproval(result)
            return result
        except TrueForgeError as exc:
            logger.warning(
                "TrueForge call failed for %s, falling back to direct LLM call: %s",
                trueforge_agent_name,
                exc,
            )

    fallback_instruction = system_instruction
    if mcp_servers:
        # The direct fallback path has no tool access (only the TrueForge
        # path does), so any tool-referencing instructions must be
        # neutralized here — otherwise the model tries to "use" tools that
        # do not exist in this call and answers incoherently instead of
        # following the response schema.
        fallback_instruction = (
            f"{system_instruction}\n\nNo external tools are available for this request. "
            "Answer using only the information given in the prompt, and do not reference "
            "or attempt to call any tool."
        )

    try:
        return generate_json(
            system_instruction=fallback_instruction,
            prompt=prompt,
            response_schema=response_schema,
            temperature=temperature,
        )
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
    raising AgentPausedForToolApproval, after a human has approved or
    rejected the pending tool call.

    Only handles the case where the resumed turn reaches a final answer or
    a genuine TrueForge error. If a further tool call in the same turn also
    needs approval (multiple gated tool calls back to back), this raises
    AgentPausedForToolApproval again so the caller can persist a new pending
    request rather than silently dropping it — the caller (the tool-approval
    endpoint) is expected to treat that the same way it treats the first
    pause.
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
