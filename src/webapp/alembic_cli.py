"""Alembic CLI wrapper that generates YYYYMMDD_<6hex> revision ids.

Alembic has no config for custom revision-id format; it always uses
``uuid4().hex[-12:]`` unless ``--rev-id`` is passed. This entrypoint patches
``alembic.util.rev_id`` before delegating to the normal Alembic CLI so:

    uv run edvise-alembic revision -m "add_foo_column"

creates ids like ``20260805_a1b2c3`` (same shape as the baseline).

Explicit ``--rev-id`` still wins (Alembic won't call ``util.rev_id()``).
"""

from __future__ import annotations

import datetime as dt
import secrets
import sys


def dated_rev_id() -> str:
    """Return ``YYYYMMDD_<6 hex chars>`` (e.g. ``20260805_a1b2c3``)."""
    return f"{dt.date.today():%Y%m%d}_{secrets.token_hex(3)}"


def main(argv: list[str] | None = None) -> None:
    import alembic.config
    import alembic.util

    alembic.util.rev_id = dated_rev_id  # type: ignore[assignment]
    alembic.config.main(argv=list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    main()
