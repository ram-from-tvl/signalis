"""Runs the LangGraph agent pipeline for a lead and persists results.

This is the boundary between the stateless agent graph (app.agents.graph)
and the database: it resolves the active persona/solution, invokes the
graph, and writes the resulting StageClassification and OutreachPlan rows,
marking any prior ones as superseded so full history is retained.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.graph import get_pipeline_graph
from app.core.config import get_settings
from app.models import Lead, OutreachPlan, Persona, Signal, Solution, StageClassification


def _latest_persona(db: Session) -> Persona | None:
    return db.execute(select(Persona).order_by(Persona.created_at.desc())).scalars().first()


def _latest_solution(db: Session) -> Solution | None:
    return db.execute(select(Solution).order_by(Solution.created_at.desc())).scalars().first()


def _unclassified_signals(lead: Lead) -> list[Signal]:
    """Signals not yet processed by the Signal Extraction Agent (event_type
    still 'unknown' with no extraction run attached). Re-running the graph
    only re-extracts what is new, but every node still re-evaluates the full
    signal history for the buying stage decision."""
    return [s for s in lead.signals if s.extracted_by_agent_run_id is None]


def run_pipeline_for_lead(db: Session, lead: Lead) -> dict[str, Any]:
    settings = get_settings()
    persona = _latest_persona(db)
    solution = _latest_solution(db)
    new_signals = _unclassified_signals(lead)

    graph = get_pipeline_graph()
    final_state = graph.invoke(
        {
            "db": db,
            "lead": lead,
            "persona": persona,
            "solution": solution,
            "raw_signals": new_signals,
        }
    )

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
