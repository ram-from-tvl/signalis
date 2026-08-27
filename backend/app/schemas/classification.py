from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict


class StageClassificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str
    stage: str
    confidence: float
    justification: str
    persona_fit_result: dict
    requires_approval: bool
    approval_status: str
    created_at: datetime.datetime
    superseded_by_id: str | None = None


class OutreachPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str
    stage_classification_id: str
    touchpoints: list
    channels: list
    messaging_examples: list
    status: str
    created_at: datetime.datetime
    approved_at: datetime.datetime | None = None
    approved_by: str | None = None
    verified_email: str | None = None
    email_verification_status: str | None = None


class ApprovalActionRequest(BaseModel):
    action: str  # approve | reject | edit
    notes: str = ""
    approved_by: str = "marketer"
    edited_touchpoints: list | None = None
