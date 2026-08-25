from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.entities import AgentRun, Lead, OutreachPlan, StageClassification
from app.schemas.schemas import DashboardStats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Illustrative manual-effort baseline: rough time an SDR spends per lead
# researching signals across systems and hand-drafting an outreach sequence.
# Documented and justified in docs/TIME_SAVINGS.md.
MANUAL_MINUTES_PER_LEAD_ESTIMATE = 25.0


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats(db: Session = Depends(get_db)):
    total_leads = db.execute(select(func.count(Lead.id))).scalar_one()

    latest_classifications = db.execute(
        select(StageClassification).where(StageClassification.superseded_by_id.is_(None))
    ).scalars().all()

    stage_distribution: dict[str, int] = {"early": 0, "mid": 0, "late": 0}
    for c in latest_classifications:
        stage_distribution[c.stage] = stage_distribution.get(c.stage, 0) + 1

    avg_confidence = (
        sum(c.confidence for c in latest_classifications) / len(latest_classifications)
        if latest_classifications
        else 0.0
    )

    plans_generated = db.execute(select(func.count(OutreachPlan.id))).scalar_one()
    plans_pending = db.execute(
        select(func.count(OutreachPlan.id)).where(OutreachPlan.status == "pending_approval")
    ).scalar_one()

    # Excludes lead_id IS NULL runs (currently just the pipeline-wide
    # Prioritization/Ranking agent) — that agent reasons about the whole
    # pipeline in one call, not one lead, so mixing its latency into the
    # per-lead average would skew "agent seconds per lead" below.
    completed_runs = db.execute(
        select(AgentRun).where(
            AgentRun.status == "completed",
            AgentRun.completed_at.is_not(None),
            AgentRun.lead_id.is_not(None),
        )
    ).scalars().all()
    if completed_runs:
        latencies = [
            (r.completed_at - r.started_at).total_seconds()
            for r in completed_runs
            if r.completed_at
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    else:
        avg_latency = 0.0

    # "Agent seconds per lead" approximates full-pipeline latency as five
    # sequential agent calls at the observed average per-call latency.
    agent_seconds_per_lead = avg_latency * 5 if avg_latency else 0.0

    return DashboardStats(
        total_leads=total_leads,
        stage_distribution=stage_distribution,
        plans_generated=plans_generated,
        plans_pending_approval=plans_pending,
        average_confidence=round(avg_confidence, 3),
        average_agent_latency_seconds=round(avg_latency, 3),
        manual_minutes_per_lead_estimate=MANUAL_MINUTES_PER_LEAD_ESTIMATE,
        agent_seconds_per_lead_actual=round(agent_seconds_per_lead, 2),
    )
