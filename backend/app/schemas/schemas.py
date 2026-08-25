from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, Field


class PersonaCreate(BaseModel):
    role: str
    seniority: str
    industry: str
    company_size_band: str
    geography: str
    custom_traits: dict = Field(default_factory=dict)


class PersonaOut(PersonaCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime.datetime


class SolutionCreate(BaseModel):
    name: str
    problem_solved: str
    value_props: list[str] = Field(default_factory=list)
    icp_filters: dict = Field(default_factory=dict)
    differentiators: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class SolutionOut(SolutionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime.datetime


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


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str | None
    agent_name: str
    input_summary: str
    output: dict
    reasoning: str
    status: str
    started_at: datetime.datetime
    completed_at: datetime.datetime | None = None


class LeadListItem(BaseModel):
    lead: LeadOut
    latest_classification: StageClassificationOut | None = None
    latest_plan_status: str | None = None


class LeadDetail(BaseModel):
    lead: LeadOut
    signals: list[SignalOut]
    classification_history: list[StageClassificationOut]
    latest_plan: OutreachPlanOut | None = None


class PipelineRunRequest(BaseModel):
    lead_ids: list[str] | None = None  # None = run for all leads


class PipelineRunResult(BaseModel):
    lead_id: str
    classification: StageClassificationOut
    plan: OutreachPlanOut
    explainability_narrative: str
    latency_seconds: float


class PipelineRunResponse(BaseModel):
    results: list[PipelineRunResult]
    errors: list[dict]


class AppendSignalsRequest(BaseModel):
    signals: list[dict]


class IngestionReportOut(BaseModel):
    rows_parsed: int
    rows_skipped: int
    skipped_reasons: list[str]
    leads_created: int
    leads_updated: int
    signals_created: int


class ApprovalActionRequest(BaseModel):
    action: str  # approve | reject | edit
    notes: str = ""
    approved_by: str = "marketer"
    edited_touchpoints: list | None = None


class DashboardStats(BaseModel):
    total_leads: int
    stage_distribution: dict[str, int]
    plans_generated: int
    plans_pending_approval: int
    average_confidence: float
    average_agent_latency_seconds: float
    manual_minutes_per_lead_estimate: float
    agent_seconds_per_lead_actual: float


class RankedLeadEntry(BaseModel):
    lead_id: str
    rank: int
    reasoning: str
    name: str
    company: str
    title: str
    stage: str
    confidence: float


class PipelineRankingOut(BaseModel):
    id: str
    summary: str
    ranked_leads: list[RankedLeadEntry]
    created_at: datetime.datetime
