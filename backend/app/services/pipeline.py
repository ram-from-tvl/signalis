"""Runs the LangGraph agent pipeline for a lead and persists results.

This is the boundary between the stateless agent graph (app.agents.graph)
and the database: it resolves the lead's campaign persona/solution,
invokes the graph, and writes the resulting StageClassification and
OutreachPlan rows, marking any prior ones as superseded so full history is
retained.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.common import AgentPausedForToolApproval
from app.agents.graph import get_pipeline_graph
from app.core.config import get_settings
from app.models import (
    AgentRun,
    Campaign,
    Lead,
    OutreachPlan,
    Signal,
    StageClassification,
    ToolApprovalRequest,
)


class NoCampaignConfigured(Exception):
    """Raised when a lead has no campaign assigned and no default campaign
    exists to fall back to (e.g. a lead was ingested before any
    persona/solution was ever saved). The caller should surface this as a
    clear "assign a campaign first" error rather than silently scoring the
    lead against nothing, or against whatever config happens to exist."""


def _resolve_campaign(db: Session, lead: Lead) -> Campaign:
    """Every lead is scored against its own campaign's persona/solution —
    not a single system-wide "active" persona (see docs/DECISIONS.md's
    "singleton persona" writeup). A lead with no campaign_id (pre-dating
    the Campaign model, or created without one) falls back to whichever
    Campaign is marked is_default, so ingestion and demo-seed flows that
    don't specify a campaign keep working."""
    if lead.campaign_id:
        campaign = db.get(Campaign, lead.campaign_id)
        if campaign is not None:
            return campaign
    default_campaign = db.execute(
        select(Campaign).where(Campaign.is_default.is_(True))
    ).scalars().first()
    if default_campaign is None:
        raise NoCampaignConfigured(
            f"Lead {lead.id} has no campaign assigned, and no default campaign exists. "
            "Create a persona, a solution, and a campaign before running the pipeline."
        )
    return default_campaign


class PipelinePausedForApproval(Exception):
    """Raised by run_pipeline_for_lead when a node's TrueForge turn paused
    on a require_approval_for_tools gate (currently only from Persona Fit).
    Carries the persisted ToolApprovalRequest row(s) so the API layer can
    report exactly what's pending instead of an ordinary failure.

    Approving the pending tool call resumes and completes the Persona Fit
    step, but doesn't auto-continue the rest of the graph — the marketer
    clicks "Regenerate Plan" to run the remaining steps with the now-
    unblocked result already on record.
    """

    def __init__(self, requests: list[ToolApprovalRequest]):
        self.requests = requests
        super().__init__(f"Pipeline paused awaiting {len(requests)} tool approval(s)")


def _unclassified_signals(lead: Lead) -> list[Signal]:
    """Signals not yet processed by the Signal Extraction Agent (event_type
    still 'unknown' with no extraction run attached). Re-running the graph
    only re-extracts what is new, but every node still re-evaluates the full
    signal history for the buying stage decision."""
    return [s for s in lead.signals if s.extracted_by_agent_run_id is None]


def run_pipeline_for_lead(db: Session, lead: Lead) -> dict[str, Any]:
    settings = get_settings()
    campaign = _resolve_campaign(db, lead)
    persona = campaign.persona
    solution = campaign.solution
    new_signals = _unclassified_signals(lead)

    graph = get_pipeline_graph()
    try:
        final_state = graph.invoke(
            {
                "db": db,
                "lead": lead,
                "persona": persona,
                "solution": solution,
                "raw_signals": new_signals,
            }
        )
    except AgentPausedForToolApproval as exc:
        # run_persona_fit left this run "running" when it raised; at most
        # one is in flight per lead since the graph runs synchronously.
        agent_run = db.execute(
            select(AgentRun)
            .where(AgentRun.lead_id == lead.id, AgentRun.agent_name == "persona_fit", AgentRun.status == "running")
            .order_by(AgentRun.started_at.desc())
        ).scalars().first()

        requests = []
        for pending in exc.pending:
            request = ToolApprovalRequest(
                lead_id=lead.id,
                agent_run_id=agent_run.id if agent_run else None,
                trueforge_agent_name="signalis-persona-fit",
                session_id=pending.session_id,
                turn_id=pending.turn_id,
                thread_id=pending.thread_id,
                tool_call_id=pending.tool_call_id,
                tool_name=pending.tool_name,
                tool_input=pending.tool_input,
                status="pending",
            )
            db.add(request)
            requests.append(request)
        db.commit()
        for request in requests:
            db.refresh(request)
        raise PipelinePausedForApproval(requests) from exc

    stage_result = final_state["stage_result"]
    requires_approval = final_state["requires_approval"]

    # Supersede any previous non-superseded classification for this lead.
    existing = db.execute(
        select(StageClassification).where(
            StageClassification.lead_id == lead.id,
            StageClassification.superseded_by_id.is_(None),
        )
    ).scalars().all()

    approval_status = "pending_approval" if requires_approval else "auto_approved"
    classification = StageClassification(
        lead_id=lead.id,
        stage=stage_result["stage"],
        confidence=stage_result["confidence"],
        justification=stage_result["justification"],
        persona_fit_result=final_state["persona_fit_result"],
        based_on_agent_run_id=final_state.get("persona_fit_agent_run_id"),
        requires_approval=requires_approval,
        approval_status=approval_status,
    )
    db.add(classification)
    db.flush()

    for prior in existing:
        prior.superseded_by_id = classification.id
        db.add(prior)

    # Any previously active plan for this lead is superseded by the new one.
    prior_plans = db.execute(
        select(OutreachPlan).where(
            OutreachPlan.lead_id == lead.id,
            OutreachPlan.status.in_(["pending_approval", "approved"]),
        )
    ).scalars().all()
    for prior_plan in prior_plans:
        prior_plan.status = "superseded"
        db.add(prior_plan)

    plan_result = final_state.get("plan_result") or {"touchpoints": [], "channels": [], "summary": ""}
    plan = OutreachPlan(
        lead_id=lead.id,
        stage_classification_id=classification.id,
        touchpoints=plan_result.get("touchpoints", []),
        channels=plan_result.get("channels", []),
        messaging_examples=[t.get("message_copy", "") for t in plan_result.get("touchpoints", [])],
        status="pending_approval",
        verified_email=plan_result.get("verified_email"),
        email_verification_status=plan_result.get("email_verification_status"),
        email_verification_reason=plan_result.get("email_verification_reason"),
    )
    db.add(plan)
    db.commit()
    db.refresh(classification)
    db.refresh(plan)

    return {
        "classification": classification,
        "plan": plan,
        "explainability": final_state.get("explainability_result", {}),
        "confidence_threshold": settings.confidence_approval_threshold,
    }
