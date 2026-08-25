from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.classification import OutreachPlan, StageClassification


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, default="")
    company_size: Mapped[str] = mapped_column(String, default="")
    industry: Mapped[str] = mapped_column(String, default="")
    geography: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    signals: Mapped[list["Signal"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="Signal.occurred_at"
    )
    stage_classifications: Mapped[list["StageClassification"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    outreach_plans: Mapped[list["OutreachPlan"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), nullable=False)
    raw_source: Mapped[str] = mapped_column(String, nullable=False)  # crm | website | email | linkedin
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    event_type: Mapped[str] = mapped_column(String, default="unknown")
    intent_stage_hint: Mapped[str] = mapped_column(String, default="early")
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    extracted_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    extracted_by_agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )

    lead: Mapped["Lead"] = relationship(back_populates="signals")
