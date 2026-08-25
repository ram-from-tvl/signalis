from __future__ import annotations

import datetime

from pydantic import BaseModel


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
