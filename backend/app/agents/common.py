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
            return run_turn(trueforge_agent_name, prompt)
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


def run_agent_reasoning_with_delegations(
    *,
    trueforge_agent_name: str,
    model: str,
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any],
    temperature: float = 0.3,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Like `run_agent_reasoning`, but also returns the list of genuine
    TrueForge subagent delegations (`create_sub_agent` calls) the root
    agent's turn performed, as recorded on TrueForge's own session event
    stream — see `app.core.trueforge._extract_subagent_delegations`.

    Used by agents (currently only Prioritization) whose system instruction
    asks the model to delegate parallel per-item work to subagents, so the
    caller can persist real evidence that delegation happened rather than
    just trusting the model's own narration of what it did. Only available
    on the TrueForge path — dynamic subagent delegation is a TrueForge
    runtime feature with no equivalent in the direct Gemini/HF fallback, so
    when TrueForge is disabled/unreachable this falls back to
    `run_agent_reasoning` with an empty delegations list, exactly like every
    other agent's fallback behavior (same prompt/schema, just without the
    TrueForge session wrapper or subagent capability).
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
            output, delegations = run_turn(trueforge_agent_name, prompt, with_delegations=True)
            return output, delegations
        except TrueForgeError as exc:
            logger.warning(
                "TrueForge call failed for %s, falling back to direct LLM call "
                "(subagent delegation unavailable on the fallback path): %s",
                trueforge_agent_name,
                exc,
            )

    try:
        output = generate_json(
            system_instruction=system_instruction,
            prompt=prompt,
            response_schema=response_schema,
            temperature=temperature,
        )
        return output, []
    except LLMError as exc:
        raise LLMError(f"Agent {trueforge_agent_name} reasoning failed: {exc}") from exc
