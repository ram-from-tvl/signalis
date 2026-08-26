"""TrueForge's native per-tool human-approval primitive, wired into the
product: when an agent's MCP tool call is gated by require_approval_for_tools,
the paused turn is persisted here so a marketer can review and decide it
through the UI rather than the turn just hanging until a curl script resumes
it. See docs/DECISIONS.md ("TrueForge agent harness integration") for why
this was previously demonstrated but not wired in, and why it now is.
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow

if TYPE_CHECKING:
    from app.models.lead import Lead


class ToolApprovalRequest(Base):
    __tablename__ = "tool_approval_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    trueforge_agent_name: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    turn_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    tool_input: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    lead: Mapped["Lead"] = relationship()
