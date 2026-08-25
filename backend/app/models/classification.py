"""The approval-workflow trio: a stage classification, the outreach plan
generated from it, and the approve/reject events logged against either one.

See docs/DATA_SCHEMA.md for why these are append-only history tables rather
than mutated in place.
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow

if TYPE_CHECKING:
    from app.models.lead import Lead


class StageClassification(Base):
    __tablename__ = "stage_classifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String, nullable=False)  # early|mid|late
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    justification: Mapped[str] = mapped_column(Text, default="")
    persona_fit_result: Mapped[dict] = mapped_column(JSON, default=dict)
    based_on_agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )
    requires_approval: Mapped[bool] = mapped_column(default=False)
    approval_status: Mapped[str] = mapped_column(String, default="auto_approved")
    # auto_approved | pending_approval | approved | rejected
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    superseded_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("stage_classifications.id"), nullable=True
    )

    lead: Mapped["Lead"] = relationship(back_populates="stage_classifications")


class OutreachPlan(Base):
    __tablename__ = "outreach_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), nullable=False)
    stage_classification_id: Mapped[str] = mapped_column(
        ForeignKey("stage_classifications.id"), nullable=False
    )
    touchpoints: Mapped[list] = mapped_column(JSON, default=list)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    messaging_examples: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="pending_approval")
    # pending_approval | approved | rejected | superseded
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)

    lead: Mapped["Lead"] = relationship(back_populates="outreach_plans")


class ApprovalEvent(Base):
    __tablename__ = "approval_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    outreach_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("outreach_plans.id"), nullable=True
    )
    stage_classification_id: Mapped[str | None] = mapped_column(
        ForeignKey("stage_classifications.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)  # approve|reject|edit
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
