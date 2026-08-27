"""Minimal, additive-only schema patcher for the no-Alembic setup.

`Base.metadata.create_all` handles brand-new tables but never alters an
existing one, so a column added to an already-existing table needs an
explicit ALTER TABLE. This module does that one kind of change (add a
nullable column if missing) idempotently.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

logger = logging.getLogger("signalis.db.migrations")

# (table, column, SQL type) for every column added after that table's first
# release. Add a new entry here whenever a nullable column is added to an
# existing table instead of a new one.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("agent_runs", "trueforge_session_id", "VARCHAR"),
    ("outreach_plans", "verified_email", "VARCHAR"),
    ("outreach_plans", "email_verification_status", "VARCHAR"),
    ("outreach_plans", "email_verification_reason", "VARCHAR"),
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
