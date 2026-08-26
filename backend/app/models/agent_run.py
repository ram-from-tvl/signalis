from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow

if TYPE_CHECKING:
    from app.models.followup import AgentRunFollowup
    from app.models.lead import Lead


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="completed")  # running|completed|failed
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    # The TrueForge session that produced this run's reasoning. Nullable
    # because the direct Gemini/Hugging-Face fallback path (used when
    # TrueForge is disabled or unreachable) has no TrueForge session at all.
    # When present, a marketer's follow-up question can be answered as a
    # real continuation turn on this exact session (see
    # app/api/routes/agent_followups.py) instead of a stateless one-shot
    # call that has to be re-fed this run's context from scratch.
    trueforge_session_id: Mapped[str | None] = mapped_column(String, nullable=True)

    lead: Mapped["Lead"] = relationship(back_populates="agent_runs")
    followups: Mapped[list["AgentRunFollowup"]] = relationship(
        back_populates="agent_run", order_by="AgentRunFollowup.created_at"
    )
