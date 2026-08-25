from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow


class Solution(Base):
    __tablename__ = "solutions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    problem_solved: Mapped[str] = mapped_column(Text, nullable=False)
    value_props: Mapped[list] = mapped_column(JSON, default=list)
    icp_filters: Mapped[dict] = mapped_column(JSON, default=dict)
    differentiators: Mapped[list] = mapped_column(JSON, default=list)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
