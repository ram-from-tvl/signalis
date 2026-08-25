from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import ApprovalEvent, OutreachPlan, StageClassification
from app.schemas import ApprovalActionRequest, OutreachPlanOut, StageClassificationOut

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

VALID_ACTIONS = {"approve", "reject", "edit"}


@router.post("/plans/{plan_id}", response_model=OutreachPlanOut)
def act_on_plan(plan_id: str, payload: ApprovalActionRequest, db: Session = Depends(get_db)):
    plan = db.get(OutreachPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Outreach plan not found")
    if payload.action not in VALID_ACTIONS:
        raise HTTPException(400, f"action must be one of {sorted(VALID_ACTIONS)}")

    if payload.action == "approve":
        plan.status = "approved"
        plan.approved_by = payload.approved_by
        plan.approved_at = datetime.datetime.utcnow()
    elif payload.action == "reject":
        plan.status = "rejected"
    elif payload.action == "edit":
        if payload.edited_touchpoints is not None:
            plan.touchpoints = payload.edited_touchpoints
            plan.messaging_examples = [
                t.get("message_copy", "") for t in payload.edited_touchpoints
            ]
        plan.status = "approved"
        plan.approved_by = payload.approved_by
        plan.approved_at = datetime.datetime.utcnow()

    db.add(plan)
    event = ApprovalEvent(outreach_plan_id=plan.id, action=payload.action, notes=payload.notes)
    db.add(event)
    db.commit()
    db.refresh(plan)
    return plan


@router.post("/classifications/{classification_id}", response_model=StageClassificationOut)
def act_on_classification(
    classification_id: str, payload: ApprovalActionRequest, db: Session = Depends(get_db)
):
    classification = db.get(StageClassification, classification_id)
    if not classification:
        raise HTTPException(404, "Stage classification not found")
    if payload.action not in {"approve", "reject"}:
        raise HTTPException(400, "action must be 'approve' or 'reject'")

    classification.approval_status = "approved" if payload.action == "approve" else "rejected"
    db.add(classification)
    event = ApprovalEvent(
        stage_classification_id=classification.id, action=payload.action, notes=payload.notes
    )
    db.add(event)
    db.commit()
    db.refresh(classification)
    return classification
