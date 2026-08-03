"""Baseline API-owned tables.

Revision ID: 20260803_596894
Revises:
Create Date: 2026-08-03

Creates API-owned tables to match SQLAlchemy models in webapp.database.
Does not create `users` (edvise-ui / Laravel DDL ownership).

On existing Cloud SQL environments: run `alembic stamp head` instead of
`upgrade` for the first cutover (tables already exist via create_all).

Prefer generating future revisions with `alembic revision -m "..."` so
Alembic assigns a unique revision id (random hex by default).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from webapp.database import Base

# revision identifiers, used by Alembic.
revision: str = "20260803_596894"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

API_TABLES = (
    "inst",
    "apikey",
    "account_history",
    "file",
    "batch",
    "file_batch_association_table",
    "model",
    "schema_registry",
    "job",
)


def upgrade() -> None:
    # Uses live Base.metadata (not frozen DDL). Safe for stamp-first cutover on
    # existing DBs. Before the first post-cutover ALTER migration, either keep
    # ORM models matching this baseline until that revision ships, or replace
    # this body with explicit op.create_table DDL frozen at cutover.
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in API_TABLES]
    Base.metadata.create_all(bind=bind, tables=tables)


def downgrade() -> None:
    raise NotImplementedError(
        "Baseline downgrade is refused — dropping API tables on shared Cloud SQL "
        "is unsafe. Restore from backup instead."
    )
