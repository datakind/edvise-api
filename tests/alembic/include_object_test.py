"""Unit tests for webapp.alembic_filters.include_object exclusions."""

from __future__ import annotations

from webapp.alembic_filters import include_object


def test_include_object_excludes_users() -> None:
    assert include_object(object(), "users", "table", False, None) is False


def test_include_object_excludes_backup_tables() -> None:
    assert (
        include_object(object(), "inst_custom_to_legacy_backup", "table", False, None)
        is False
    )
    assert (
        include_object(
            object(), "schema_registry_custom_ext_backup", "table", False, None
        )
        is False
    )


def test_include_object_allows_api_tables() -> None:
    assert include_object(object(), "job", "table", False, None) is True
    assert include_object(object(), "inst", "table", False, None) is True


def test_include_object_ignores_non_table_types() -> None:
    assert include_object(object(), "users", "index", False, None) is True
