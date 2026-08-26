"""Tests for the additive-column schema patcher in app/db/migrations.py,
including the concurrent-startup race where two backend processes both see
a column as missing and both attempt the same ALTER TABLE."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.db.migrations import _ADDITIVE_COLUMNS, run_startup_migrations


@pytest.fixture()
def engine_with_table_missing_column():
    """An engine with agent_runs created WITHOUT trueforge_session_id, so
    run_startup_migrations has real work to do."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE agent_runs (id VARCHAR PRIMARY KEY, lead_id VARCHAR, "
                "agent_name VARCHAR, input_summary TEXT, status VARCHAR)"
            )
        )
    return engine


def test_run_startup_migrations_adds_missing_column(engine_with_table_missing_column):
    engine = engine_with_table_missing_column
    run_startup_migrations(engine)

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    assert "trueforge_session_id" in columns


def test_run_startup_migrations_is_idempotent_when_called_twice(engine_with_table_missing_column):
    """Simulates the simplest form of the race: the migration function is
    called twice against the same already-migrated DB (e.g. by two backend
    processes starting concurrently) and neither call should raise."""
    engine = engine_with_table_missing_column

    run_startup_migrations(engine)
    # Second call: the column now already exists, so this exercises the
    # normal "already present, skip" path (not a real race, but confirms
    # the baseline is safe before the harder concurrent-race test below).
    run_startup_migrations(engine)

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    assert "trueforge_session_id" in columns


def test_run_startup_migrations_survives_concurrent_alter_table_race(
    engine_with_table_missing_column,
):
    """Directly simulates the race Qodo flagged: two processes both inspect
    the schema, both see the column missing, and both attempt ALTER TABLE.
    The second ALTER TABLE must not crash the process — SQLite raises a
    duplicate-column OperationalError for the loser, and that must be
    caught and treated as success since the desired end-state (column
    exists) was already achieved by the winner.

    This is done by calling the real ALTER TABLE once out-of-band (winning
    the race) and then invoking run_startup_migrations, which will
    independently decide the column is missing (since we call it via a
    patched inspector state) and attempt to add it again, hitting the
    duplicate-column error for real.
    """
    engine = engine_with_table_missing_column

    # Winner: add the column directly, out-of-band, before the migration
    # function runs at all.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs ADD COLUMN trueforge_session_id VARCHAR"))

    # Loser: run_startup_migrations is tricked into believing the column is
    # still missing (as it would if its own schema inspection ran before
    # the winner's ALTER TABLE committed), so it attempts the same
    # ALTER TABLE and hits SQLite's real duplicate-column error.
    from sqlalchemy import inspect as sqlalchemy_inspect

    real_inspect = sqlalchemy_inspect

    class _StaleInspector:
        """Wraps the real inspector but always reports the column as
        missing, regardless of the table's actual current state."""

        def __init__(self, real):
            self._real = real

        def get_table_names(self):
            return self._real.get_table_names()

        def get_columns(self, table):
            cols = self._real.get_columns(table)
            return [c for c in cols if c["name"] != "trueforge_session_id"]

    with patch("app.db.migrations.inspect") as mock_inspect:
        mock_inspect.return_value = _StaleInspector(real_inspect(engine))
        # Must not raise, even though the column already exists and the
        # ALTER TABLE this triggers will hit a real duplicate-column error.
        run_startup_migrations(engine)

    inspector = real_inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    assert "trueforge_session_id" in columns


def test_run_startup_migrations_reraises_other_operational_errors(engine_with_table_missing_column):
    """Only the duplicate-column error should be swallowed by
    run_startup_migrations — any other OperationalError (e.g. a genuinely
    broken ALTER TABLE, unrelated to the concurrent-startup race) must
    still propagate rather than being silently absorbed."""
    from sqlalchemy.exc import OperationalError

    engine = engine_with_table_missing_column

    class _FakeConnection:
        def execute(self, *args, **kwargs):
            raise OperationalError("statement", {}, Exception("disk I/O error"))

    class _FakeBeginCtx:
        def __enter__(self):
            return _FakeConnection()

        def __exit__(self, *exc_info):
            return False

    with patch.object(engine, "begin", return_value=_FakeBeginCtx()):
        with pytest.raises(OperationalError, match="disk I/O error"):
            run_startup_migrations(engine)


def test_additive_columns_list_is_nonempty():
    """Sanity check that this test file's premise (there is at least one
    additive column to migrate) still holds."""
    assert len(_ADDITIVE_COLUMNS) >= 1
