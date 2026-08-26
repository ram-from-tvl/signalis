from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict


class ToolApprovalRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str | None
    agent_run_id: str | None
    trueforge_agent_name: str
    tool_name: str
    tool_input: dict
    status: str
    created_at: datetime.datetime
    resolved_at: datetime.datetime | None = None


class ToolApprovalActionRequest(BaseModel):
    reason: str = ""  # optional, shown to the agent when denying
