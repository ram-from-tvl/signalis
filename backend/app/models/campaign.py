from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import new_uuid, utcnow

if TYPE_CHECKING:
    from app.models.persona import Persona
    from app.models.solution import Solution


class Campaign(Base):
    """A targeting config a lead is assigned to: one persona + one solution.

    A GTM team runs several campaigns at once (e.g. "CTOs — Q3 platform
    push" and "VP Marketing — enterprise upsell"), each scoring its own
    leads against its own persona/ICP rather than the whole pipeline
    sharing one global "active" persona. See docs/DECISIONS.md for the
    "singleton persona" problem this replaces.
    """

    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), nullable=False)
    solution_id: Mapped[str] = mapped_column(ForeignKey("solutions.id"), nullable=False)
    # Exactly one campaign should have is_default=True at a time — the
    # landing target for leads ingested without an explicit campaign, and
    # the one auto-created from pre-existing data on first migration.
    # Enforced at the application layer (set_default_campaign), not a DB
    # constraint, since SQLite has no partial-unique-index support here.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    persona: Mapped["Persona"] = relationship()
    solution: Mapped["Solution"] = relationship()
