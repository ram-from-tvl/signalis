"""Minimal, additive-only schema patcher for the no-Alembic setup.

`Base.metadata.create_all` handles brand-new tables but never alters an
existing one, so a column added to an already-existing table needs an
explicit ALTER TABLE. This module does that one kind of change (add a
nullable column if missing) idempotently, plus one one-time data backfill
(see `backfill_default_campaign`).
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger("signalis.db.migrations")

# (table, column, SQL type) for every column added after that table's first
# release. Add a new entry here whenever a nullable column is added to an
# existing table instead of a new one.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("agent_runs", "trueforge_session_id", "VARCHAR"),
    ("outreach_plans", "verified_email", "VARCHAR"),
    ("outreach_plans", "email_verification_status", "VARCHAR"),
    ("outreach_plans", "email_verification_reason", "VARCHAR"),
    ("leads", "campaign_id", "VARCHAR"),
]


def run_startup_migrations(engine: Engine) -> None:
    """Add any missing nullable columns listed in _ADDITIVE_COLUMNS.
    Idempotent no-op if they already exist.

    The inspect-then-ALTER sequence isn't atomic: if two backend processes
    start concurrently, both can see a column as absent and both attempt
    the ALTER, and the loser gets a duplicate-column error. Caught and
    treated as success, since the desired end-state is reached either way.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, column, sql_type in _ADDITIVE_COLUMNS:
            if table not in existing_tables:
                # create_all will create this table (with the column
                # already present) on this same startup; nothing to patch.
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            if column in existing_columns:
                continue
            logger.info("Adding missing column %s.%s", table, column)
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
                logger.info(
                    "Column %s.%s was added by a concurrent process; continuing.", table, column
                )


def backfill_default_campaign(session: Session) -> None:
    """One-time data backfill for the Campaign model: any lead with a null
    campaign_id (i.e. every lead that existed before campaigns did) gets
    assigned to a default campaign, auto-created from whatever persona/
    solution happened to be most-recently-saved under the old singleton
    behavior. Idempotent: a no-op once every lead has a campaign_id, and
    safe to call on every startup.

    Deliberately data-driven rather than schema-driven (unlike
    run_startup_migrations above), since it has to read existing rows to
    decide what to do — it belongs in the same "runs once at startup,
    always safe to re-run" family, just a different kind of migration.
    """
    from app.models import Campaign, Lead, Persona, Solution

    leads_without_campaign = session.execute(
        select(Lead).where(Lead.campaign_id.is_(None))
    ).scalars().all()
    if not leads_without_campaign:
        return

    default_campaign = session.execute(
        select(Campaign).where(Campaign.is_default.is_(True))
    ).scalars().first()

    if default_campaign is None:
        persona = session.execute(select(Persona).order_by(Persona.created_at.desc())).scalars().first()
        solution = session.execute(select(Solution).order_by(Solution.created_at.desc())).scalars().first()
        if persona is None or solution is None:
            # Nothing to backfill from yet (fresh install, no persona/
            # solution saved) — leave campaign_id null; the pipeline
            # already handles a lead with no campaign by falling back to
            # whatever default campaign exists at run time, or erroring
            # clearly if none does yet.
            return
        logger.info(
            "Creating default Campaign from pre-existing persona/solution for %d lead(s) without one",
            len(leads_without_campaign),
        )
        default_campaign = Campaign(
            name="Default",
            persona_id=persona.id,
            solution_id=solution.id,
            is_default=True,
        )
        session.add(default_campaign)
        session.flush()

    for lead in leads_without_campaign:
        lead.campaign_id = default_campaign.id
        session.add(lead)
    session.commit()
