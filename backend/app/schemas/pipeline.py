from __future__ import annotations

from pydantic import BaseModel

from app.schemas.classification import OutreachPlanOut, StageClassificationOut


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
