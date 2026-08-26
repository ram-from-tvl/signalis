from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow


class PipelineRanking(Base):
    """One snapshot produced by the Prioritization/Ranking Agent: an ordered
    list of leads across the whole pipeline, each with a rank and a
    plain-language reason, so a rep knows who to contact first. Snapshots are
    append-only like stage_classifications/outreach_plans — re-running the
    agent creates a new row rather than mutating the previous ranking."""

    __tablename__ = "pipeline_rankings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    ranked_leads: Mapped[list] = mapped_column(JSON, default=list)
    # Self-contained snapshot per entry: {lead_id, rank, reasoning, name,
    # company, title, stage, confidence} — baked in at write time so it
    # never drifts if the lead is later reclassified.
    summary: Mapped[str] = mapped_column(Text, default="")
    # {status, used, subagent_count, expected_count, subagents} — see
    # app.agents.prioritization.run_prioritization. Baked onto the ranking
    # row (rather than left on the pipeline-wide AgentRun, lead_id=None) so
    # it's reachable from the ranking API response the frontend reads.
    subagent_delegation: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
