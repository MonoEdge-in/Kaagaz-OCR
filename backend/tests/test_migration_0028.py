"""Migration 0028 (stuck reading, 16 Oct 2026) without a database: the progress CHECK allows
every step the pipeline writes, and the waiting mark, so a progress write can never end the
worker. Against a real PostgreSQL: test_migration_0028_db.py."""

import importlib.util
import typing
from pathlib import Path
from types import ModuleType

from kaagaz.db.documents import WAITING_FOR_READER
from kaagaz.pipeline import Step


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0028_progress_steps.py"
    spec = importlib.util.spec_from_file_location("migration_0028", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_the_check_allows_every_step_the_pipeline_writes() -> None:
    assert set(typing.get_args(Step)) == set(_migration().STEPS)


def test_the_check_allows_the_waiting_mark_exactly() -> None:
    migration = _migration()
    assert WAITING_FOR_READER == {"step": migration.WAITING}
    assert '\'{"step": "waiting"}\'::jsonb' in migration.UPGRADE[1]
