from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # Whether a follow-up question can be asked against this run's TrueForge
    # session. False on the direct-Gemini fallback path (no session
    # created). The raw session id is not exposed to the frontend.
    can_ask_followup: bool = False

    @model_validator(mode="before")
    @classmethod
    def _derive_can_ask_followup(cls, data: Any) -> Any:
        session_id = getattr(data, "trueforge_session_id", None)
        if isinstance(data, dict):
            session_id = data.get("trueforge_session_id", session_id)
        if isinstance(data, dict):
            data = {**data, "can_ask_followup": bool(session_id)}
        else:
            data.can_ask_followup = bool(session_id)
        return data


class AgentRunFollowupCreate(BaseModel):
    # Bounded before reaching TrueForge — forwarded into a synchronous, paid
    # LLM turn that can hold an API worker for up to the 90s poll timeout.
    question: str = Field(..., min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question must not be empty")
        return value


class AgentRunFollowupOut(BaseModel):
    # Keep in sync with app/models/followup.py::AgentRunFollowup and
    # frontend/src/types/api.ts::AgentRunFollowup (hand-mirrored, no codegen).
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_run_id: str
    question: str
    answer: str
    created_at: datetime.datetime
