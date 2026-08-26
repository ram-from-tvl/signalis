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

_ENRICHMENT_MCP_SERVER = [
    {
        "name": "signalis-enrichment",
        "enable_tools": ["@all"],
        "require_approval_for_tools": ["classify_company_industry", "estimate_company_size_band"],
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
    """Finds a persona_fit AgentRun for this lead that already completed
    (typically via resume_persona_fit after a marketer approved a paused
    tool call) but whose result has not yet been folded into any
    StageClassification — i.e. "Regenerate Plan" hasn't run to completion
    since it finished.

    Without this, every "Regenerate Plan" click starts the whole graph from
    scratch, and the graph's persona_fit node unconditionally calls
    run_persona_fit again — re-registering the same require_approval_for_tools
    gate and re-running the same enrichment tool calls, so a lead that just
    got unblocked by an approval hits the identical approval gate again
    instead of proceeding to buying-stage/plan generation with the result
    that was already approved. Reusing the completed run's output here is
    what actually lets the marketer's approval decision stick."""
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
    TrueForge paused the turn on the enrichment tool's approval gate — the
    caller (app.services.pipeline) is responsible for catching that,
    persisting a ToolApprovalRequest, and marking this AgentRun accordingly
    rather than letting it look like a normal failure.

    Returns (result, agent_run_id). agent_run_id lets the caller record
    which AgentRun backs a StageClassification (StageClassification.
    based_on_agent_run_id) so a later "Regenerate Plan" click can tell this
    particular result has already been consumed and knows to run a fresh
    Persona Fit pass instead of reusing it forever.

    If a prior run already completed for this lead (most commonly: a
    marketer approved a previously-paused tool call, and resume_persona_fit
    finished that run) and its result hasn't been consumed by a
    classification yet, that result is reused instead of starting a brand
    new TrueForge turn — see _reusable_completed_run."""
    reusable = _reusable_completed_run(db, lead)
    if reusable is not None:
        return reusable.output, reusable.id

    input_summary = f"Persona fit for {lead.name} ({lead.company}) vs persona/solution ICP"
    run = start_run(db, lead_id=lead.id, agent_name="persona_fit", input_summary=input_summary)

    prompt = _build_prompt(lead, persona, solution)

    try:
        result = run_agent_reasoning(
            trueforge_agent_name=TRUEFORGE_AGENT_NAME,
            model=get_settings().trueforge_model,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt=prompt,
            response_schema=RESPONSE_SCHEMA,
            temperature=0.2,
            mcp_servers=_ENRICHMENT_MCP_SERVER,
        )
    except AgentPausedForToolApproval:
        # Leave the AgentRun in "running" status — it is neither completed
        # nor failed yet. The tool-approval endpoint calls finish_run once a
        # human resolves the pending request (approve -> completed with the
        # real result, reject -> failed).
        raise
    except LLMError as exc:
        finish_run(db, run, output={"error": str(exc)}, reasoning=str(exc), status="failed")
        raise

    finish_run(db, run, output=result, reasoning=result.get("reasoning", ""))
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
    """Resumes a previously paused Persona Fit AgentRun after a human has
    approved or rejected the pending enrichment tool call, and finishes the
    run exactly as run_persona_fit would have if it had never paused.

    A denial (approve=False) always finishes the run as "failed", even if
    the resumed TrueForge turn still produced a normal-looking JSON result
    (model-dependent: some models answer anyway after a denial instead of
    erroring out). The tool-approval endpoint's reject action promises the
    marketer that rejecting a tool call fails the run — that must hold
    regardless of how the resumed model happens to respond to the denial,
    not just in the "running" branch of a caller-side status check."""
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
        finish_run(db, run, output=result, reasoning=result.get("reasoning", ""))
    else:
        finish_run(
            db,
            run,
            output=result,
            reasoning=deny_reason or "Tool call rejected by marketer.",
            status="failed",
        )
    return result
