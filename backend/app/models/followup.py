"""A marketer's follow-up Q&A exchanges against one AgentRun's reasoning.

Append-only, same philosophy as stage_classifications/outreach_plans (see
docs/DATA_SCHEMA.md): every question/answer pair is its own row rather than
mutating a single "conversation" blob in place, so a marketer's full
follow-up history for a trace entry survives a page reload and is
independently inspectable/auditable after the fact.
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun


class AgentRunFollowup(Base):
    # Shape (id, agent_run_id, question, answer, created_at) is hand-mirrored
    # in two other places with no shared contract or codegen — keep in sync
    # with app/schemas/agent_run.py::AgentRunFollowupOut and
    # frontend/src/types/api.ts::AgentRunFollowup. See docs/DECISIONS.md for
    # why this is hand-duplicated rather than generated.
    __tablename__ = "agent_run_followups"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    agent_run: Mapped["AgentRun"] = relationship(back_populates="followups")
