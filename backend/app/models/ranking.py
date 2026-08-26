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
    # Each entry is a self-contained snapshot, not a pointer to live rows:
    # {"lead_id": str, "rank": int, "reasoning": str, "name": str, "company": str,
    #  "title": str, "stage": str, "confidence": float}
    # Baking the stage/confidence/name in at write time means a snapshot never
    # drifts if the lead is later reclassified, and reading it back needs no
    # per-entry Lead/StageClassification lookups.
    summary: Mapped[str] = mapped_column(Text, default="")
    # {"status": "delegated"|"partial"|"evidence_unavailable"|"not_delegated",
    #  "used": bool, "subagent_count": int | None, "expected_count": int,
    #  "subagents": list[dict]} — see app.agents.prioritization.run_prioritization.
    # This snapshot is a pipeline-wide run (agent_run_id points at a run with
    # lead_id=None), so the existing lead-scoped Agent Trace UI never
    # surfaces it; baking it directly onto the ranking row is what makes it
    # reachable from the ranking API response the frontend actually reads.
    subagent_delegation: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
