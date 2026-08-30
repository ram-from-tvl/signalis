from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.persona import PersonaOut
from app.schemas.solution import SolutionOut


class CampaignCreate(BaseModel):
    name: str
    persona_id: str
    solution_id: str
    is_default: bool = False


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    persona_id: str
    solution_id: str
    is_default: bool
    created_at: datetime.datetime


class CampaignDetail(CampaignOut):
    persona: PersonaOut
    solution: SolutionOut
    lead_count: int = 0
