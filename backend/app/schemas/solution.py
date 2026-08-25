from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, Field


class SolutionCreate(BaseModel):
    name: str
    problem_solved: str
    value_props: list[str] = Field(default_factory=list)
    icp_filters: dict = Field(default_factory=dict)
    differentiators: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)


class SolutionOut(SolutionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime.datetime
