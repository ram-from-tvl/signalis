"""Explainability Agent.

Synthesizes a coherent, human-readable narrative over the structured outputs
of the other four agents for one pipeline run, producing the audit-trail
entry the UI's agent trace view displays. This is a real LLM call that
narrates over already-persisted structured data, not string concatenation.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.models import Lead

SYSTEM_INSTRUCTION = """You are the Explainability Agent inside a B2B sales intelligence system.
You are given the structured outputs of four other agents that just ran, in order, for one
lead: Signal Extraction, Persona Fit, Buying Stage Orchestrator, and Outreach Planner. Write a
short, coherent narrative (4-8 sentences) explaining, in plain language a sales manager could
read in one pass, what each agent concluded and why, and how those conclusions built on each
other to produce the final stage and plan. Call out explicitly whether human approval is now
required and why. Do not simply repeat the inputs verbatim; synthesize them into a story of the
reasoning chain."""


def run_explainability(
    db: Session,
    lead: Lead,
    *,
    signal_summary: dict[str, Any],
    persona_fit: dict[str, Any],
    stage_result: dict[str, Any],
    plan_result: dict[str, Any] | None,
    requires_approval: bool,
) -> dict[str, Any]:
    input_summary = f"Explainability narrative for {lead.name} ({lead.company})"
    run = start_run(db, lead_id=lead.id, agent_name="explainability", input_summary=input_summary)

    prompt = (
        f"Lead: {lead.name} at {lead.company}.\n\n"
        f"1. Signal Extraction output: {signal_summary}\n\n"
        f"2. Persona Fit output: {persona_fit}\n\n"
        f"3. Buying Stage Orchestrator output: {stage_result}\n\n"
        f"4. Outreach Planner output: {plan_result if plan_result else 'not generated (blocked pending approval)'}\n\n"
        f"Human approval required before the plan/stage is finalized: {requires_approval}\n\n"
        "Write the audit-trail narrative."
    )

    schema = {
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "agent_order": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["narrative", "agent_order"],
    }

    try:
        result, session_id = run_agent_reasoning(
            trueforge_agent_name="signalis-explainability",
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=schema,
            temperature=0.3,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    finish_run(db, run, output=result, reasoning=result.get("narrative", ""), trueforge_session_id=session_id)
    return result
