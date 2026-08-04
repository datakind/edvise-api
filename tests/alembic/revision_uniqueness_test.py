"""Ensure Alembic revision ids and version filenames stay unique."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
# Match Alembic mako output (`revision: str = "..."`) and plain `revision = "..."`.
REVISION_RE = re.compile(r'^revision(?::\s*str)?\s*=\s*["\']([^"\']+)["\']', re.M)


def _revision_files() -> list[Path]:
    files = sorted(VERSIONS_DIR.glob("*.py"))
    assert files, f"No Alembic revisions found in {VERSIONS_DIR}"
    return files


def _revision_id(path: Path) -> str:
    match = REVISION_RE.search(path.read_text(encoding="utf-8"))
    assert match, (
        f"No revision id found in {path.name} "
        f'(expected `revision: str = "..."` or `revision = "..."`)'
    )
    return match.group(1)


def test_alembic_revision_ids_are_unique() -> None:
    revisions = [_revision_id(path) for path in _revision_files()]
    counts = Counter(revisions)
    dupes = sorted(rev for rev, n in counts.items() if n > 1)
    assert not dupes, f"Duplicate Alembic revision id(s): {dupes}"


def test_alembic_revision_id_matches_filename_prefix() -> None:
    """Filename should start with the revision id (Alembic / our baseline convention)."""
    mismatches: list[str] = []
    for path in _revision_files():
        rev = _revision_id(path)
        if not path.stem.startswith(rev):
            mismatches.append(f"{path.name} (revision={rev})")
    assert not mismatches, "Revision id does not match filename prefix: " + ", ".join(
        mismatches
    )
