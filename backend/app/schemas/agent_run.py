from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    lead_id: str | None
    agent_name: str
    input_summary: str
    output: dict
    reasoning: str
    status: str
    started_at: datetime.datetime
    completed_at: datetime.datetime | None = None
