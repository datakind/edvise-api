"""Baseline API-owned tables.

Revision ID: 20260803_baseline
Revises:
Create Date: 2026-08-03

Creates API-owned tables to match SQLAlchemy models in webapp.database.
Does not create `users` (edvise-ui / Laravel DDL ownership).

On existing Cloud SQL environments: run `alembic stamp head` instead of
`upgrade` for the first cutover (tables already exist via create_all).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from webapp.database import Base

# revision identifiers, used by Alembic.
revision: str = "20260803_baseline"
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
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in API_TABLES]
    Base.metadata.create_all(bind=bind, tables=tables)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in reversed(API_TABLES)]
    Base.metadata.drop_all(bind=bind, tables=tables)
