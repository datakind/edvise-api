"""Golden snapshot tests for validation error formatter.

These tests compare formatted output against expected "golden" files to catch
UX regressions. The expected outputs are stored in fixtures and should be
updated when intentional formatting changes are made.

To update snapshots, set UPDATE_SNAPSHOTS=1 environment variable or use
pytest --update-snapshots flag.
"""

import pytest
import os
from pathlib import Path
from unittest.mock import patch

try:
    import pandas as pd
    from pandera.errors import SchemaErrors, SchemaError

    HAS_PANDERA = True
except ImportError:
    HAS_PANDERA = False
    pd = None  # type: ignore
    SchemaErrors = None  # type: ignore
    SchemaError = None  # type: ignore

from .validation import HardValidationError
from .validation_error_formatter import format_validation_error


# ============================================================================
# Snapshot Update Control
# ============================================================================


def _should_update_snapshots() -> bool:
    """Check if snapshots should be updated (via env var or pytest flag)."""
    # Check environment variable
    if os.getenv("UPDATE_SNAPSHOTS") == "1":
        return True
    # Check pytest config option (if available)
    try:
        config = pytest.config  # type: ignore
        if hasattr(config, "option") and hasattr(config.option, "update_snapshots"):
            return getattr(config.option, "update_snapshots", False)
    except (AttributeError, RuntimeError):
        pass
    return False


# ============================================================================
# Fixture Paths
# ============================================================================

# Directory for golden snapshot files
_SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "validation_error_snapshots"


def _get_snapshot_path(test_name: str) -> Path:
    """Get path to snapshot file for a test."""
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return _SNAPSHOT_DIR / f"{test_name}.txt"


def _read_snapshot(test_name: str) -> str:
    """Read expected snapshot content."""
    snapshot_path = _get_snapshot_path(test_name)
    if snapshot_path.exists():
        return snapshot_path.read_text(encoding="utf-8")
    return ""


def _write_snapshot(test_name: str, content: str) -> None:
    """Write snapshot content to file."""
    snapshot_path = _get_snapshot_path(test_name)
    snapshot_path.write_text(content, encoding="utf-8")


def _normalize_snapshot(content: str) -> str:
    """Normalize snapshot content for comparison.

    Handles:
    - Platform newlines (\r\n → \n)
    - Trailing whitespace on each line
    - Trailing empty lines
    - Does NOT collapse internal whitespace (to catch real UX regressions)
    """
    # Normalize newlines: \r\n → \n
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    lines = content.splitlines()
    # Remove trailing whitespace from each line (but preserve internal whitespace)
    normalized = [line.rstrip() for line in lines]

    # Remove trailing empty lines
    while normalized and not normalized[-1]:
        normalized.pop()

    # Return with single trailing newline (or empty string if no content)
    return "\n".join(normalized) + "\n" if normalized else ""


# ============================================================================
# Snapshot Test Cases
# ============================================================================


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_missing_required_columns() -> None:
    """Snapshot test: Missing required columns error."""
    error = HardValidationError(
        missing_required=["student_id", "grade", "age"],
        raw_to_canon={
            "Student ID": "student_id",
            "Grade": "grade",
            "Age": "age",
        },
        canon_to_raw={
            "student_id": "Student ID",
            "grade": "Grade",
            "age": "Age",
        },
        merged_specs={
            "student_id": {"description": "Unique student identifier"},
            "grade": {"description": "Student grade (A-F)"},
            "age": {"description": "Student age"},
        },
    )

    result = format_validation_error(error)
    normalized_result = _normalize_snapshot(result)

    snapshot_name = "missing_required_columns"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        # Update mode: write snapshot
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        # Missing snapshot: fail with instructions
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_extra_columns_ambiguous() -> None:
    """Snapshot test: Extra columns with ambiguous raw names."""
    error = HardValidationError(
        extra_columns=["student_id"],
        raw_to_canon={
            "Student ID": "student_id",
            "StudentID": "student_id",  # Multiple raw names normalize to same canonical
            "STUDENT-ID": "student_id",
        },
        canon_to_raw={
            "student_id": "Student ID",  # First encountered
        },
        merged_specs={},
    )

    result = format_validation_error(error)
    normalized_result = _normalize_snapshot(result)

    # Assert exact ambiguity rule phrase: with 3+ matches, should show "X (and N similar)"
    assert "(and" in result and "similar" in result, (
        f"Expected ambiguity phrase '(and N similar)' for 3+ matches, "
        f"but got: {result[:200]}"
    )
    # Verify the exact format: "X (and N similar)"
    assert (
        "Student ID (and 2 similar)" in result or "student_id (and 2 similar)" in result
    ), f"Expected 'Student ID (and 2 similar)' format, but got: {result[:200]}"

    snapshot_name = "extra_columns_ambiguous"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_mixed_types_multiple_columns() -> None:
    """Snapshot test: Mixed error types across multiple columns."""
    error = HardValidationError(
        failure_cases=[
            {
                "column": "student_id",
                "index": 0,
                "check": "str_length(3, None)",
                "failure_case": "AB",  # Too short
            },
            {
                "column": "grade",
                "index": 0,
                "check": "isin(['A', 'B', 'C', 'D', 'F'])",
                "failure_case": "X",  # Invalid value
            },
            {
                "column": "age",
                "index": 1,
                "check": "nullable",
                "failure_case": None,  # Null value
            },
            {
                "column": "score",
                "index": 2,
                "check": "greater_than(0)",
                "failure_case": -5,  # Negative value
            },
        ],
        raw_to_canon={
            "Student ID": "student_id",
            "Grade": "grade",
            "Age": "age",
            "Score": "score",
        },
        canon_to_raw={
            "student_id": "Student ID",
            "grade": "Grade",
            "age": "Age",
            "score": "Score",
        },
        merged_specs={
            "student_id": {
                "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}],
            },
            "grade": {
                "checks": [{"type": "isin", "args": [["A", "B", "C", "D", "F"]]}],
            },
            "age": {
                "nullable": False,
            },
            "score": {
                "checks": [{"type": "ge", "kwargs": {"ge": 0}}],
            },
        },
    )

    result = format_validation_error(error)
    normalized_result = _normalize_snapshot(result)

    snapshot_name = "mixed_types_multiple_columns"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_multiple_rows_same_column() -> None:
    """Snapshot test: Multiple rows with errors in the same column."""
    error = HardValidationError(
        failure_cases=[
            {
                "column": "grade",
                "index": 0,
                "check": "isin(['A', 'B', 'C', 'D', 'F'])",
                "failure_case": "X",
            },
            {
                "column": "grade",
                "index": 1,
                "check": "isin(['A', 'B', 'C', 'D', 'F'])",
                "failure_case": "Y",
            },
            {
                "column": "grade",
                "index": 2,
                "check": "isin(['A', 'B', 'C', 'D', 'F'])",
                "failure_case": "Z",
            },
            {
                "column": "grade",
                "index": 3,
                "check": "isin(['A', 'B', 'C', 'D', 'F'])",
                "failure_case": "W",
            },
        ],
        raw_to_canon={"Grade": "grade"},
        canon_to_raw={"grade": "Grade"},
        merged_specs={
            "grade": {
                "checks": [{"type": "isin", "args": [["A", "B", "C", "D", "F"]]}],
            },
        },
    )

    result = format_validation_error(error)
    normalized_result = _normalize_snapshot(result)

    snapshot_name = "multiple_rows_same_column"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_schema_level_errors() -> None:
    """Snapshot test: Schema-level errors (no column)."""
    error = HardValidationError(
        failure_cases=[
            {
                # No "column" field - schema-level error
                "check": "non_empty_dataframe",
                "failure_case": "DataFrame is empty",
            },
            {
                # No "column" field - schema-level error
                "check": "row_count",
                "failure_case": "Row count mismatch",
            },
        ],
        raw_to_canon={},
        canon_to_raw={},
        merged_specs={},
    )

    result = format_validation_error(error)
    normalized_result = _normalize_snapshot(result)

    snapshot_name = "schema_level_errors"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_truncation_many_errors() -> None:
    """Snapshot test: Truncation behavior with many errors.

    Uses a fixed limit (10) explicitly set in the test to ensure maximum
    stability. The snapshot represents the intended UX, not whatever the
    global constant happens to be.
    """
    # Use fixed limit explicitly set in test for maximum stability
    # This snapshot represents UX with max 10 examples, regardless of global constant
    # Generate enough errors to trigger truncation (10 + 5 = 15)
    num_errors = 15  # Guaranteed to exceed the fixed limit of 10

    failure_cases = []
    for i in range(num_errors):
        failure_cases.append(
            {
                "column": "value",
                "index": i,
                "check": "greater_than(0)",
                "failure_case": -1,
            }
        )

    error = HardValidationError(
        failure_cases=failure_cases,
        raw_to_canon={"Value": "value"},
        canon_to_raw={"value": "Value"},
        merged_specs={
            "value": {
                "checks": [{"type": "ge", "kwargs": {"ge": 0}}],
            },
        },
    )

    # Patch MAX_ERROR_EXAMPLES to 10 for this test to ensure snapshot stability
    with patch("src.webapp.validation_error_formatter.MAX_ERROR_EXAMPLES", 10):
        result = format_validation_error(error)

    normalized_result = _normalize_snapshot(result)

    snapshot_name = "truncation_many_errors"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_pii_masking_mixed() -> None:
    """Snapshot test: PII masking with mixed PII and non-PII columns.

    Uses distinctive canary values for PII to avoid false positives from
    substring matches in other parts of the output.
    """
    # Use distinctive canary values that won't appear as substrings elsewhere
    pii_email = "canary_pii_email_123@example.com"
    pii_name = "CANARY_PII_NAME_123"
    pii_ssn = "CANARY_PII_SSN_123456789"

    error = HardValidationError(
        failure_cases=[
            {
                "column": "student_name",
                "index": 0,
                "check": "str_length(3, None)",
                "failure_case": pii_name,  # PII - should be masked
            },
            {
                "column": "course_name",
                "index": 0,
                "check": "str_length(3, None)",
                "failure_case": "XY",  # NOT PII - should be shown
            },
            {
                "column": "email",
                "index": 1,
                "check": "str_length(5, None)",
                "failure_case": pii_email,  # PII - should be masked
            },
            {
                "column": "ssn",
                "index": 2,
                "check": "str_length(9, None)",
                "failure_case": pii_ssn,  # PII - should be masked
            },
            {
                "column": "grade",
                "index": 1,
                "check": "isin(['A', 'B', 'C'])",
                "failure_case": "X",  # NOT PII - should be shown
            },
        ],
        raw_to_canon={
            "Student Name": "student_name",
            "Course Name": "course_name",
            "Email": "email",
            "SSN": "ssn",
            "Grade": "grade",
        },
        canon_to_raw={
            "student_name": "Student Name",
            "course_name": "Course Name",
            "email": "Email",
            "ssn": "SSN",
            "grade": "Grade",
        },
        merged_specs={
            "student_name": {
                "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}],
            },
            "course_name": {
                "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}],
            },
            "email": {
                "checks": [{"type": "str_length", "kwargs": {"min_value": 5}}],
            },
            "ssn": {
                "checks": [{"type": "str_length", "kwargs": {"min_value": 9}}],
            },
            "grade": {
                "checks": [{"type": "isin", "args": [["A", "B", "C"]]}],
            },
        },
    )

    result = format_validation_error(error)
    normalized_result = _normalize_snapshot(result)

    # Negative assertion: raw PII values must NOT appear in output
    # Using distinctive canaries to avoid false positives from substring matches
    assert pii_email not in result, f"PII leakage: email '{pii_email}' found in output"
    assert pii_name not in result, f"PII leakage: name '{pii_name}' found in output"
    assert pii_ssn not in result, f"PII leakage: SSN '{pii_ssn}' found in output"
    # Non-PII values should be shown
    assert "XY" in result, "Non-PII value 'XY' should be shown (not masked)"
    assert "X" in result, "Non-PII value 'X' should be shown (not masked)"

    snapshot_name = "pii_masking_mixed"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_snapshot_complete_error_flow() -> None:
    """Snapshot test: Complete error flow with all error types."""
    error = HardValidationError(
        missing_required=["student_id"],
        extra_columns=["extra_col"],
        failure_cases=[
            {
                "column": "grade",
                "index": 0,
                "check": "isin(['A', 'B', 'C'])",
                "failure_case": "X",
            },
            {
                "column": "age",
                "index": 1,
                "check": "nullable",
                "failure_case": None,
            },
        ],
        raw_to_canon={
            "Student ID": "student_id",
            "Grade": "grade",
            "Age": "age",
            "Extra Col": "extra_col",
        },
        canon_to_raw={
            "student_id": "Student ID",
            "grade": "Grade",
            "age": "Age",
            "extra_col": "Extra Col",
        },
        merged_specs={
            "student_id": {"description": "Student identifier"},
            "grade": {
                "checks": [{"type": "isin", "args": [["A", "B", "C"]]}],
            },
            "age": {
                "nullable": False,
            },
        },
    )

    result = format_validation_error(error)
    normalized_result = _normalize_snapshot(result)

    snapshot_name = "complete_error_flow"
    expected = _read_snapshot(snapshot_name)

    if _should_update_snapshots():
        _write_snapshot(snapshot_name, normalized_result)
        pytest.skip(f"Updated snapshot: {snapshot_name}.txt")

    if not expected:
        pytest.fail(
            f"Snapshot file missing: {_get_snapshot_path(snapshot_name)}\n"
            f"To create/update snapshots, run with UPDATE_SNAPSHOTS=1 environment variable:\n"
            f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}\n"
            f"\nCurrent output:\n{normalized_result}"
        )

    expected_normalized = _normalize_snapshot(expected)
    assert normalized_result == expected_normalized, (
        f"Snapshot mismatch for {snapshot_name}.\n"
        f"Expected:\n{expected_normalized}\n\n"
        f"Got:\n{normalized_result}\n\n"
        f"To update snapshot, run with UPDATE_SNAPSHOTS=1:\n"
        f"  UPDATE_SNAPSHOTS=1 pytest {__file__}::{snapshot_name}"
    )
