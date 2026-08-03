"""Shared Alembic table filters (safe to import without Alembic context)."""

from __future__ import annotations

# Tables Alembic must never create/drop/alter via autogenerate or baseline.
# Laravel-only tables (teams, sessions, etc.) are not in Base.metadata, so
# autogenerate will not see them; users is on AccountTable and must be excluded.
EXCLUDED_TABLES = frozenset({"users"})


def include_object(object, name, type_, reflected, compare_to):  # noqa: A002, ANN001
    if type_ == "table":
        if name in EXCLUDED_TABLES:
            return False
        if name and name.endswith("_backup"):
            return False
    return True
