"""Tests for dated Alembic revision id helper."""

from __future__ import annotations

import re

from src.webapp.alembic_cli import dated_rev_id

DATED_REV_RE = re.compile(r"^\d{8}_[0-9a-f]{6}$")


def test_dated_rev_id_shape() -> None:
    rev = dated_rev_id()
    assert DATED_REV_RE.match(rev), rev


def test_dated_rev_id_unique_enough() -> None:
    assert len({dated_rev_id() for _ in range(20)}) == 20
