from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict

from app.models.tool_approval import ToolApprovalStatus


class ToolApprovalRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str | None
    agent_run_id: str | None
    trueforge_agent_name: str
    tool_name: str
    tool_input: dict
    status: ToolApprovalStatus
    created_at: datetime.datetime
    resolved_at: datetime.datetime | None = None


class ToolApprovalResolutionOut(BaseModel):
    """Response body for approve/reject: the resolved request itself, plus
    (when the resumed turn immediately hit another approval gate) the new
    pending request created for it. The client must check `followup` rather
    than assuming every successful response means the pipeline is fully
    unblocked — see docs/DECISIONS.md and the frontend's toolApprovalsApi."""

    model_config = ConfigDict(from_attributes=True)
    resolved: ToolApprovalRequestOut
    followup: ToolApprovalRequestOut | None = None


class ToolApprovalActionRequest(BaseModel):
    reason: str = ""  # optional, shown to the agent when denying
