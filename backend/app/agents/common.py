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
from app.core.trueforge import TrueForgeError, ensure_agent, run_turn
from app.models import AgentRun

logger = logging.getLogger("signalis.agents")


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
) -> tuple[dict[str, Any], str | None]:
    """Run one agent's reasoning step through the TrueForge harness so the
    real agent loop (model calls, MCP tool discovery/execution, context
    management) genuinely executes this step, rather than a bare model call.

    Falls back to a direct Gemini/Hugging-Face call (app.core.llm) if
    TrueForge is disabled or unreachable, so the pipeline still produces
    real model reasoning when the local harness sidecar is not running —
    this is a transport fallback, not a second reasoning path: both routes
    execute the same prompt and schema.

    Returns (parsed_result, trueforge_session_id). The session_id is None
    whenever the fallback path was used (there is no TrueForge session in
    that case) — callers persist it on the AgentRun so a later marketer
    follow-up question can be answered as a real continuation turn on the
    same TrueForge session, with genuine conversational memory of this run's
    own output, rather than a fresh one-shot call re-fed the context.
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
            result, session_id = run_turn(trueforge_agent_name, prompt)
            return result, session_id
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
        result = generate_json(
            system_instruction=fallback_instruction,
            prompt=prompt,
            response_schema=response_schema,
            temperature=temperature,
        )
        return result, None
    except LLMError as exc:
        raise LLMError(f"Agent {trueforge_agent_name} reasoning failed: {exc}") from exc
