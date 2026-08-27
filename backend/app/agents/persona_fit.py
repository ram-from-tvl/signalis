"""Persona Fit Agent.

Given a lead's firmographic data and the marketer-defined persona + solution
ICP, decides whether the lead is a full fit, partial fit, or mismatch, and
flags any missing data that limited the assessment.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.common import (
    AgentPausedForToolApproval,
    finish_run,
    resume_agent_reasoning,
    run_agent_reasoning,
    start_run,
)
from app.core.config import get_settings
from app.core.llm import LLMError
from app.models import AgentRun, Lead, Persona, Solution, StageClassification

TRUEFORGE_AGENT_NAME = "signalis-persona-fit"

_MCP_SERVERS = [
    {
        "name": "signalis-enrichment",
        "enable_tools": ["@all"],
        "require_approval_for_tools": ["classify_company_industry", "estimate_company_size_band"],
    },
    {
        "name": "signalis-research",
        "enable_tools": ["@all"],
        "require_approval_for_tools": [],
    },
    {
        "name": "signalis-exa",
        "enable_tools": ["@all"],
        "require_approval_for_tools": [],
    },
]

SYSTEM_INSTRUCTION = """You are the Persona Fit Agent inside a B2B sales intelligence system.
You are given a lead's firmographic profile, a target persona definition, and a solution's ideal
customer profile (ICP) filters. Before deciding fit, use the classify_company_industry and
estimate_company_size_band tools to enrich the lead's company data whenever the lead's industry
or company size is missing or you want to verify a stated value. You also have a
search_company_news tool that returns real, current web results (recent news, funding, hiring
signals) about the lead's company; call it when that kind of external context would meaningfully
sharpen your fit assessment (for example, a recent funding round or hiring surge that speaks to
company size or growth stage) — it is not mandatory on every lead, since recent news is not
always relevant or available. You also have search_company_semantic (Exa), a differently-sourced
semantic/company-focused search; use it as a second read when Tavily's results are thin or
ambiguous, not as a mandatory second call on every lead. Decide whether the lead is a full_fit, partial_fit, or mismatch
against the persona and ICP, and explain your reasoning in plain, specific language a sales rep
could sanity-check in five seconds. Always call out any lead fields that are missing or blank and
explain how that limited your confidence in the assessment. Be honest about ambiguity rather than
forcing a confident-sounding answer when data is thin."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "string", "enum": ["full_fit", "partial_fit", "mismatch"]},
        "reasoning": {"type": "string"},
        "missing_data": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["fit", "reasoning", "missing_data"],
}


def _reusable_completed_run(db: Session, lead: Lead) -> AgentRun | None:
    """Finds a persona_fit AgentRun that already completed (typically via
    resume_persona_fit after an approved tool call) but hasn't yet been
    consumed by a StageClassification. Without this, a "Regenerate Plan"
    click after an approval would hit the same approval gate again instead
    of proceeding with the already-approved result."""
    run = db.execute(
        select(AgentRun)
        .where(
            AgentRun.lead_id == lead.id,
            AgentRun.agent_name == "persona_fit",
            AgentRun.status == "completed",
        )
        .order_by(AgentRun.started_at.desc())
    ).scalars().first()
    if run is None:
        return None

    already_consumed = db.execute(
        select(StageClassification.id).where(StageClassification.based_on_agent_run_id == run.id)
    ).scalars().first()
    if already_consumed is not None:
        return None

    return run


def run_persona_fit(
    db: Session, lead: Lead, persona: Persona | None, solution: Solution | None
) -> tuple[dict[str, Any], str | None]:
    """Runs the Persona Fit agent. May raise AgentPausedForToolApproval if
    the turn paused on the enrichment tool's approval gate — the caller
    (app.services.pipeline) persists a ToolApprovalRequest for that case.

    Returns (result, agent_run_id); the run id lets the caller record which
    AgentRun backs a StageClassification. Reuses a prior completed run's
    output instead of starting a new turn if one exists unconsumed — see
    _reusable_completed_run."""
    reusable = _reusable_completed_run(db, lead)
    if reusable is not None:
        return reusable.output, reusable.id

    input_summary = f"Persona fit for {lead.name} ({lead.company}) vs persona/solution ICP"
    run = start_run(db, lead_id=lead.id, agent_name="persona_fit", input_summary=input_summary)

    prompt = _build_prompt(lead, persona, solution)

    try:
        result, session_id = run_agent_reasoning(
            trueforge_agent_name=TRUEFORGE_AGENT_NAME,
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=RESPONSE_SCHEMA,
            temperature=0.2,
            mcp_servers=_MCP_SERVERS,
        )
    except AgentPausedForToolApproval:
        # Left "running" — the tool-approval endpoint calls finish_run once
        # a human resolves the pending request.
        raise
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    finish_run(db, run, output=result, reasoning=result.get("reasoning", ""), trueforge_session_id=session_id)
    return result, run.id


def _build_prompt(lead: Lead, persona: Persona | None, solution: Solution | None) -> str:
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
    return (
        f"Lead profile: name={lead.name}, title={lead.title or '(missing)'}, "
        f"company={lead.company}, company_size={lead.company_size or '(missing)'}, "
        f"industry={lead.industry or '(missing)'}, geography={lead.geography or '(missing)'}.\n\n"
        f"Target persona: {persona_desc}\n\n"
        f"Solution ICP: {solution_desc}\n\n"
        "Assess fit."
    )


def resume_persona_fit(
    db: Session,
    run: AgentRun,
    *,
    session_id: str,
    thread_id: str,
    tool_call_id: str,
    approve: bool,
    deny_reason: str | None = None,
) -> dict[str, Any]:
    """Resumes a previously paused Persona Fit AgentRun after a human
    decision, finishing the run as run_persona_fit would have.

    A denial always finishes the run as "failed", even if the resumed turn
    produced a normal-looking result (some models answer anyway after a
    denial instead of erroring out)."""
    try:
        result = resume_agent_reasoning(
            trueforge_agent_name=TRUEFORGE_AGENT_NAME,
            session_id=session_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            approve=approve,
            deny_reason=deny_reason,
        )
    except AgentPausedForToolApproval:
        raise
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    if approve:
        finish_run(db, run, output=result, reasoning=result.get("reasoning", ""), trueforge_session_id=session_id)
    else:
        finish_run(
            db,
            run,
            output=result,
            reasoning=deny_reason or "Tool call rejected by marketer.",
            status="failed",
            trueforge_session_id=session_id,
        )
    return result
