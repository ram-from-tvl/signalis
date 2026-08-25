"""Outreach Planner Agent.

Generates a 1-2 week outreach micro-plan tailored to a lead's buying stage,
persona fit, and the solution's positioning. Callable repeatedly for the
same lead so that a new signal (e.g. a fresh pricing page visit) produces
an updated plan reflecting current state, not a stale one.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.models.entities import Lead, Persona, Solution

SYSTEM_INSTRUCTION = """You are the Outreach Planner Agent inside a B2B sales intelligence
system. Given a lead's buying stage, confidence, persona fit, and the solution's value
proposition and differentiators, produce a concrete 1-2 week outreach micro-plan. Sequence 3-5
touchpoints with a suggested day_offset (0 = today), a channel drawn from the solution's
available channels, a content_theme, and example message copy specific to this lead's company,
title, and stage — never generic boilerplate. Early-stage leads should get educational,
low-pressure touches; late-stage leads should get direct, action-oriented touches (e.g. proposing
a specific next call). Tailor language to the persona's seniority and role."""


def run_outreach_planner(
    db: Session,
    lead: Lead,
    stage: str,
    confidence: float,
    persona_fit: dict[str, Any],
    persona: Persona | None,
    solution: Solution | None,
) -> dict[str, Any]:
    input_summary = f"Outreach plan for {lead.name} ({lead.company}) at stage={stage}"
    run = start_run(db, lead_id=lead.id, agent_name="outreach_planner", input_summary=input_summary)

    channels = solution.channels if solution and solution.channels else ["email"]
    solution_desc = (
        f"name={solution.name}, problem_solved={solution.problem_solved}, "
        f"value_props={solution.value_props}, differentiators={solution.differentiators}"
        if solution
        else "No solution has been defined; use generic B2B SaaS value language."
    )
    persona_desc = (
        f"role={persona.role}, seniority={persona.seniority}" if persona else "No persona defined."
    )

    prompt = (
        f"Lead: {lead.name}, {lead.title or 'unknown title'} at {lead.company} "
        f"({lead.industry or 'unknown industry'}, {lead.company_size or 'unknown size'}).\n"
        f"Buying stage: {stage} (confidence {confidence:.2f}).\n"
        f"Persona fit: {persona_fit.get('fit', 'unknown')} — {persona_fit.get('reasoning', '')}\n"
        f"Target persona: {persona_desc}\n"
        f"Solution: {solution_desc}\n"
        f"Available channels: {channels}\n\n"
        "Generate the outreach micro-plan."
    )

    schema = {
        "type": "object",
        "properties": {
            "touchpoints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "day_offset": {"type": "integer"},
                        "channel": {"type": "string"},
                        "content_theme": {"type": "string"},
                        "message_copy": {"type": "string"},
                    },
                    "required": ["day_offset", "channel", "content_theme", "message_copy"],
                },
            },
            "channels": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["touchpoints", "channels", "summary"],
    }

    try:
        result = run_agent_reasoning(
            trueforge_agent_name="signalis-outreach-planner",
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=schema,
            temperature=0.4,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    finish_run(db, run, output=result, reasoning=result.get("summary", ""))
    return result
