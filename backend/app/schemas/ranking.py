from __future__ import annotations

import datetime
from typing import Any

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


class SubagentDelegationOut(BaseModel):
    """Real evidence of TrueForge subagent delegation for this ranking run
    (see app.agents.prioritization.run_prioritization), surfaced directly on
    the ranking API response since the pipeline-wide AgentRun this evidence
    also lives on (lead_id=None) is unreachable through the lead-scoped
    Agent Trace UI."""

    status: str  # "delegated" | "partial" | "evidence_unavailable" | "not_delegated"
    used: bool
    subagent_count: int | None
    expected_count: int
    subagents: list[dict[str, Any]]


class PipelineRankingOut(BaseModel):
    id: str
    summary: str
    ranked_leads: list[RankedLeadEntry]
    subagent_delegation: SubagentDelegationOut | None = None
    created_at: datetime.datetime
