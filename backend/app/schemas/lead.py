from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.classification import OutreachPlanOut, StageClassificationOut


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    raw_source: str
    raw_payload: dict
    event_type: str
    intent_stage_hint: str
    occurred_at: datetime.datetime
    extracted_at: datetime.datetime


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    company: str
    title: str
    company_size: str
    industry: str
    geography: str
    email: str
    created_at: datetime.datetime


class LeadListItem(BaseModel):
    lead: LeadOut
    latest_classification: StageClassificationOut | None = None
    latest_plan_status: str | None = None


class LeadDetail(BaseModel):
    lead: LeadOut
    signals: list[SignalOut]
    classification_history: list[StageClassificationOut]
    latest_plan: OutreachPlanOut | None = None


class AppendSignalsRequest(BaseModel):
    signals: list[dict]
