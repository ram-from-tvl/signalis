"""SQLAlchemy ORM models for Signalis.

See docs/DATA_SCHEMA.md for the authoritative description of each table,
its columns, and the rationale behind key design choices (e.g. why
stage_classifications and outreach_plans are append-only history tables
rather than mutated in place).
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    role: Mapped[str] = mapped_column(String, nullable=False)
    seniority: Mapped[str] = mapped_column(String, nullable=False)
    industry: Mapped[str] = mapped_column(String, nullable=False)
    company_size_band: Mapped[str] = mapped_column(String, nullable=False)
    geography: Mapped[str] = mapped_column(String, nullable=False)
    custom_traits: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)


class Solution(Base):
    __tablename__ = "solutions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    problem_solved: Mapped[str] = mapped_column(Text, nullable=False)
    value_props: Mapped[list] = mapped_column(JSON, default=list)
    icp_filters: Mapped[dict] = mapped_column(JSON, default=dict)
    differentiators: Mapped[list] = mapped_column(JSON, default=list)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, default="")
    company_size: Mapped[str] = mapped_column(String, default="")
    industry: Mapped[str] = mapped_column(String, default="")
    geography: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), nullable=False)
    raw_source: Mapped[str] = mapped_column(String, nullable=False)  # crm | website | email | linkedin
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    event_type: Mapped[str] = mapped_column(String, default="unknown")
    intent_stage_hint: Mapped[str] = mapped_column(String, default="early")
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    extracted_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    extracted_by_agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )

    lead: Mapped["Lead"] = relationship(back_populates="signals")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="completed")  # running|completed|failed
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    lead: Mapped["Lead"] = relationship(back_populates="agent_runs")


class StageClassification(Base):
    __tablename__ = "stage_classifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
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
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    superseded_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("stage_classifications.id"), nullable=True
    )

    lead: Mapped["Lead"] = relationship(back_populates="stage_classifications")


class OutreachPlan(Base):
    __tablename__ = "outreach_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), nullable=False)
    stage_classification_id: Mapped[str] = mapped_column(
        ForeignKey("stage_classifications.id"), nullable=False
    )
    touchpoints: Mapped[list] = mapped_column(JSON, default=list)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    messaging_examples: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="pending_approval")
    # pending_approval | approved | rejected | superseded
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    approved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)

    lead: Mapped["Lead"] = relationship(back_populates="outreach_plans")


class ApprovalEvent(Base):
    __tablename__ = "approval_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    outreach_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("outreach_plans.id"), nullable=True
    )
    stage_classification_id: Mapped[str | None] = mapped_column(
        ForeignKey("stage_classifications.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)  # approve|reject|edit
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)


class PipelineRanking(Base):
    """One snapshot produced by the Prioritization/Ranking Agent: an ordered
    list of leads across the whole pipeline, each with a rank and a
    plain-language reason, so a rep knows who to contact first. Snapshots are
    append-only like stage_classifications/outreach_plans — re-running the
    agent creates a new row rather than mutating the previous ranking."""

    __tablename__ = "pipeline_rankings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    ranked_leads: Mapped[list] = mapped_column(JSON, default=list)
    # Each entry is a self-contained snapshot, not a pointer to live rows:
    # {"lead_id": str, "rank": int, "reasoning": str, "name": str, "company": str,
    #  "title": str, "stage": str, "confidence": float}
    # Baking the stage/confidence/name in at write time means a snapshot never
    # drifts if the lead is later reclassified, and reading it back needs no
    # per-entry Lead/StageClassification lookups.
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
