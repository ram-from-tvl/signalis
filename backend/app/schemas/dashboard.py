from __future__ import annotations

from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_leads: int
    stage_distribution: dict[str, int]
    plans_generated: int
    plans_pending_approval: int
    average_confidence: float
    average_agent_latency_seconds: float
    manual_minutes_per_lead_estimate: float
    agent_seconds_per_lead_actual: float
