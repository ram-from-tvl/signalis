"""Persona Fit Agent.

Given a lead's firmographic data and the marketer-defined persona + solution
ICP, decides whether the lead is a full fit, partial fit, or mismatch, and
flags any missing data that limited the assessment.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.common import finish_run, run_agent_reasoning, start_run
from app.core.config import get_settings
from app.core.llm import LLMError
from app.models import Lead, Persona, Solution

_ENRICHMENT_MCP_SERVER = [
    {
        "name": "signalis-enrichment",
        "enable_tools": ["@all"],
        "require_approval_for_tools": [],
    }
]

SYSTEM_INSTRUCTION = """You are the Persona Fit Agent inside a B2B sales intelligence system.
You are given a lead's firmographic profile, a target persona definition, and a solution's ideal
customer profile (ICP) filters. Before deciding fit, use the classify_company_industry and
estimate_company_size_band tools to enrich the lead's company data whenever the lead's industry
or company size is missing or you want to verify a stated value. Decide whether the lead is a
full_fit, partial_fit, or mismatch against the persona and ICP, and explain your reasoning in
plain, specific language a sales rep could sanity-check in five seconds. Always call out any lead
fields that are missing or blank and explain how that limited your confidence in the assessment.
Be honest about ambiguity rather than forcing a confident-sounding answer when data is thin."""


def run_persona_fit(
    db: Session, lead: Lead, persona: Persona | None, solution: Solution | None
) -> dict[str, Any]:
    input_summary = f"Persona fit for {lead.name} ({lead.company}) vs persona/solution ICP"
    run = start_run(db, lead_id=lead.id, agent_name="persona_fit", input_summary=input_summary)

    persona_desc = (
        f"role={persona.role}, seniority={persona.seniority}, industry={persona.industry}, "
        f"company_size_band={persona.company_size_band}, geography={persona.geography}, "
        f"custom_traits={persona.custom_traits}"
        if persona
        else "No target persona has been defined yet."
    )
    solution_desc = (
        f"name={solution.name}, problem_solved={solution.problem_solved}, "
        f"icp_filters={solution.icp_filters}"
        if solution
        else "No solution/ICP has been defined yet."
    )

    prompt = (
        f"Lead profile: name={lead.name}, title={lead.title or '(missing)'}, "
        f"company={lead.company}, company_size={lead.company_size or '(missing)'}, "
        f"industry={lead.industry or '(missing)'}, geography={lead.geography or '(missing)'}.\n\n"
        f"Target persona: {persona_desc}\n\n"
        f"Solution ICP: {solution_desc}\n\n"
        "Assess fit."
    )

    schema = {
        "type": "object",
        "properties": {
            "fit": {"type": "string", "enum": ["full_fit", "partial_fit", "mismatch"]},
            "reasoning": {"type": "string"},
            "missing_data": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["fit", "reasoning", "missing_data"],
    }

    try:
        result, session_id = run_agent_reasoning(
            trueforge_agent_name="signalis-persona-fit",
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=schema,
            temperature=0.2,
            mcp_servers=_ENRICHMENT_MCP_SERVER,
        )
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    finish_run(db, run, output=result, reasoning=result.get("reasoning", ""), trueforge_session_id=session_id)
    return result
