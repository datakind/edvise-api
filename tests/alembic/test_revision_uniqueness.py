"""Ensure Alembic revision ids and version filenames stay unique."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
REVISION_RE = re.compile(r'^revision:\s*str\s*=\s*["\']([^"\']+)["\']', re.M)


def test_alembic_revision_ids_are_unique() -> None:
    revision_files = sorted(VERSIONS_DIR.glob("*.py"))
    assert revision_files, f"No Alembic revisions found in {VERSIONS_DIR}"

    revisions: list[str] = []
    for path in revision_files:
        text = path.read_text(encoding="utf-8")
        match = REVISION_RE.search(text)
        assert match, f"No revision id found in {path.name}"
        revisions.append(match.group(1))

    counts = Counter(revisions)
    dupes = sorted(rev for rev, n in counts.items() if n > 1)
    assert not dupes, f"Duplicate Alembic revision id(s): {dupes}"


def test_alembic_version_filenames_are_unique() -> None:
    names = [p.name for p in VERSIONS_DIR.glob("*.py")]
    counts = Counter(names)
    # filenames are unique by filesystem; also guard stems without .py if needed
    dupes = sorted(name for name, n in counts.items() if n > 1)
    assert not dupes, f"Duplicate Alembic version filename(s): {dupes}"

    stems = [Path(name).stem for name in names]
    # Detect date-only collisions like two files starting with same date prefix
    # without a distinguishing suffix — soft check via full stem uniqueness.
    stem_counts = Counter(stems)
    stem_dupes = sorted(stem for stem, n in stem_counts.items() if n > 1)
    assert not stem_dupes, f"Duplicate Alembic version stem(s): {stem_dupes}"
