from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


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
    # Whether this run has a live TrueForge session a marketer can ask a
    # follow-up question against (see AgentRunFollowupOut). False when the
    # fallback-to-direct-Gemini path was used for this run, since that path
    # never creates a TrueForge session. The raw session id itself is an
    # internal TrueForge implementation detail and is intentionally not
    # exposed to the frontend.
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
            # ORM object: attach the derived attribute so from_attributes
            # pickup works the same way it does for every other field.
            data.can_ask_followup = bool(session_id)
        return data


class AgentRunFollowupCreate(BaseModel):
    question: str


class AgentRunFollowupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_run_id: str
    question: str
    answer: str
    created_at: datetime.datetime
