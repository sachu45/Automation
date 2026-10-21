"""Scenario 21: migrations build a complete schema on a fresh database and match the models."""

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.models import Base
from tests.conftest import run_migrations


def test_fresh_database_migrates_and_matches_models(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    run_migrations(url)
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    expected = set(Base.metadata.tables)
    assert expected <= tables, f"missing tables: {expected - tables}"
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False, "render_as_batch": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], f"models and migrations drifted: {diff}"
    engine.dispose()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'twice.db').as_posix()}"
    run_migrations(url)
    run_migrations(url)  # second upgrade is a no-op, never destructive
