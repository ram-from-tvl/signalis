from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, Field


class PersonaCreate(BaseModel):
    role: str
    seniority: str
    industry: str
    company_size_band: str
    geography: str
    custom_traits: dict = Field(default_factory=dict)


class PersonaOut(PersonaCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime.datetime
