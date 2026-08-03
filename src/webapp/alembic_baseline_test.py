"""Regression checks for the Alembic baseline (API-owned tables only)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

API_TABLES = {
    "inst",
    "apikey",
    "account_history",
    "file",
    "batch",
    "file_batch_association_table",
    "model",
    "schema_registry",
    "job",
}


@pytest.fixture()
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, Path]:
    db_path = tmp_path / "alembic_test.db"
    monkeypatch.setenv("ENV", "LOCAL")
    monkeypatch.setenv("ALEMBIC_SQLITE_URL", f"sqlite:///{db_path}")
    # Clear override if present in the environment.
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return cfg, db_path


def test_alembic_upgrade_creates_api_tables_without_users(
    alembic_cfg: tuple[Config, Path],
) -> None:
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")

    tables = set(inspect(create_engine(f"sqlite:///{db_path}")).get_table_names())
    assert API_TABLES.issubset(tables)
    assert "users" not in tables
    assert "alembic_version" in tables


def test_alembic_upgrade_is_noop_after_stamp(
    alembic_cfg: tuple[Config, Path],
) -> None:
    cfg, db_path = alembic_cfg
    # Pretend tables already exist (existing Cloud SQL cutover path).
    from webapp.database import Base

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[name] for name in API_TABLES]
    )

    command.stamp(cfg, "head")
    before = set(inspect(engine).get_table_names())
    command.upgrade(cfg, "head")
    after = set(inspect(engine).get_table_names())
    assert before == after
    assert "users" not in after
