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
    # Forwarded verbatim into a synchronous, paid LLM turn that can hold an
    # API worker for up to the TrueForge turn polling timeout (90s, see
    # app/core/trueforge.py) — an unbounded question string lets a single
    # request both balloon cost and tie up a worker for the max duration.
    # 2000 chars is generous for a genuine follow-up question (comfortably
    # multiple paragraphs) while still rejecting pasted-document-sized abuse
    # at the validation layer, before ever reaching TrueForge.
    question: str = Field(..., min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question must not be empty")
        return value


class AgentRunFollowupOut(BaseModel):
    # Shape is hand-mirrored in two other places with no shared contract or
    # codegen — keep in sync with app/models/followup.py::AgentRunFollowup
    # and frontend/src/types/api.ts::AgentRunFollowup. See docs/DECISIONS.md
    # for why this is hand-duplicated rather than generated.
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_run_id: str
    question: str
    answer: str
    created_at: datetime.datetime
