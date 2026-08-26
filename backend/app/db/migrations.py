"""Minimal, additive-only schema patcher for the no-Alembic setup.

This project has no migration framework (see docs/DATA_SCHEMA.md): the
schema is created via `Base.metadata.create_all`, which is sufficient for
brand-new tables but does nothing for a column added to a table that
already exists in a previously-created `signalis.db` — SQLAlchemy never
alters existing tables. Every schema change so far has been an entirely new
table, which `create_all` already handles for free; adding
`agent_runs.trueforge_session_id` to an existing table is the first change
that needs an actual `ALTER TABLE`, so this module exists to do that one
kind of change (add a nullable column if missing) idempotently, without
pulling in Alembic for a single-column patch.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("signalis.db.migrations")

# (table, column, SQL type) for every column added after that table's first
# release. Add a new entry here whenever a nullable column is added to an
# existing table instead of a new one.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("agent_runs", "trueforge_session_id", "VARCHAR"),
]


def run_startup_migrations(engine: Engine) -> None:
    """Add any missing nullable columns listed in _ADDITIVE_COLUMNS.

    Safe to call on every startup: it inspects current columns first and
    only issues ALTER TABLE for ones that are actually missing, so it is a
    no-op on a database that already has them (including a brand-new one,
    where create_all already created the column as part of the table).
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
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
