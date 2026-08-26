"""TrueForge's native per-tool human-approval primitive, wired into the
product: when an agent's MCP tool call is gated by require_approval_for_tools,
the paused turn is persisted here so a marketer can review and decide it
through the UI rather than the turn just hanging until a curl script resumes
it. See docs/DECISIONS.md ("TrueForge agent harness integration") for why
this was previously demonstrated but not wired in, and why it now is.
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Literal, get_args

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow

if TYPE_CHECKING:
    from app.models.lead import Lead


# Single source of truth for the status contract, shared by the ORM column
# comment below and app.schemas.tool_approval.ToolApprovalRequestOut (which
# imports this same alias rather than redeclaring the literal set). The
# frontend's ToolApprovalStatus union in frontend/src/types/api.ts is a
# manual mirror of these exact four values — keep them in sync if this
# changes; there is no shared codegen in this repo (see docs/DECISIONS.md).
#
# "claimed" is a short-lived transitional state: the approve/reject endpoint
# atomically claims a pending row (a conditional UPDATE ... WHERE status =
# 'pending') before doing any TrueForge I/O, so a second concurrent decision
# on the same row can never also see it as "pending" and race to resolve it
# independently. See app.api.routes.tool_approvals._claim_pending_request.
ToolApprovalStatus = Literal["pending", "claimed", "approved", "rejected"]
TOOL_APPROVAL_STATUSES: tuple[str, ...] = get_args(ToolApprovalStatus)


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
    # See ToolApprovalStatus above for the closed set of valid values:
    # pending|claimed|approved|rejected.
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    lead: Mapped["Lead"] = relationship()
