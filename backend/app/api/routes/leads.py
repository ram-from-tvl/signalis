from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.entities import Lead, OutreachPlan, Signal, StageClassification
from app.schemas.schemas import (
    AgentRunOut,
    AppendSignalsRequest,
    IngestionReportOut,
    LeadDetail,
    LeadListItem,
    LeadOut,
)
from app.services.ingestion import parse_date

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.get("", response_model=list[LeadListItem])
def list_leads(db: Session = Depends(get_db)):
    leads = db.execute(select(Lead).order_by(Lead.created_at.desc())).scalars().all()
    items = []
    for lead in leads:
        latest = db.execute(
            select(StageClassification)
            .where(StageClassification.lead_id == lead.id, StageClassification.superseded_by_id.is_(None))
            .order_by(StageClassification.created_at.desc())
        ).scalars().first()
        latest_plan = db.execute(
            select(OutreachPlan)
            .where(OutreachPlan.lead_id == lead.id)
            .order_by(OutreachPlan.created_at.desc())
        ).scalars().first()
        items.append(
            LeadListItem(
                lead=LeadOut.model_validate(lead),
                latest_classification=latest,
                latest_plan_status=latest_plan.status if latest_plan else None,
            )
        )
    return items


@router.get("/{lead_id}", response_model=LeadDetail)
def get_lead_detail(lead_id: str, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    history = db.execute(
        select(StageClassification)
        .where(StageClassification.lead_id == lead_id)
        .order_by(StageClassification.created_at.desc())
    ).scalars().all()
    latest_plan = db.execute(
        select(OutreachPlan)
        .where(OutreachPlan.lead_id == lead_id)
        .order_by(OutreachPlan.created_at.desc())
    ).scalars().first()
    return LeadDetail(
        lead=LeadOut.model_validate(lead),
        signals=lead.signals,
        classification_history=history,
        latest_plan=latest_plan,
    )


@router.get("/{lead_id}/trace", response_model=list[AgentRunOut])
def get_lead_trace(lead_id: str, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return sorted(lead.agent_runs, key=lambda r: r.started_at)


@router.post("/{lead_id}/signals", response_model=IngestionReportOut)
def append_signal(lead_id: str, payload: AppendSignalsRequest, db: Session = Depends(get_db)):
    """Simulates a new signal arriving live for an existing lead (e.g. a
    fresh pricing page visit). Callers should follow up with a pipeline
    trigger to re-classify."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")

    created = 0
    skipped = []
    for i, raw in enumerate(payload.signals):
        if not isinstance(raw, dict):
            skipped.append(f"Signal {i}: not an object; skipped.")
            continue
        source = raw.get("raw_source", "website")
        occurred_at = parse_date(raw.get("occurred_at")) or datetime.datetime.utcnow()
        signal = Signal(
            lead_id=lead.id,
            raw_source=source,
            raw_payload=raw.get("payload", raw),
            event_type="unknown",
            intent_stage_hint="early",
            occurred_at=occurred_at,
        )
        db.add(signal)
        created += 1
    db.commit()

    return IngestionReportOut(
        rows_parsed=len(payload.signals),
        rows_skipped=len(skipped),
        skipped_reasons=skipped,
        leads_created=0,
        leads_updated=1 if created else 0,
        signals_created=created,
    )
