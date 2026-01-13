"""Comprehensive tests for validation_error_formatter module."""

import pytest
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

try:
    import pandas as pd
    import numpy as np

    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    pd = None  # type: ignore
    np = None  # type: ignore

from .validation import HardValidationError
from .validation_error_formatter import (
    format_validation_error,
    _format_missing_required,
    _format_extra_columns,
    _normalize_failure_cases,
    _group_failure_cases_by_column,
    _is_pii_column,
    _mask_pii_value,
    _format_column_validation_errors,
    _format_schema_validation_errors,
    _format_check_error,
    _sanitize_string,
    _get_canon_to_raw_mapping,
    MAX_VALUE_LENGTH,
    MAX_MESSAGE_LENGTH,
    PII_HIGH_RISK_INDICATORS,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def sample_error() -> HardValidationError:
    """Create a sample HardValidationError for testing."""
    return HardValidationError(
        missing_required=["student_id", "grade"],
        extra_columns=[],
        schema_errors=None,
        failure_cases=None,
        raw_to_canon={"Student ID": "student_id", "Grade": "grade"},
        canon_to_raw={"student_id": "Student ID", "grade": "Grade"},
        merged_specs={
            "student_id": {
                "description": "A unique identifier for each student",
                "checks": [{"type": "str_length", "kwargs": {"min_value": 1}}],
            },
            "grade": {
                "description": "Student grade",
                "checks": [],
            },
        },
    )


@pytest.fixture
def error_with_failure_cases() -> HardValidationError:
    """Create HardValidationError with failure cases."""
    return HardValidationError(
        missing_required=[],
        extra_columns=[],
        schema_errors=None,
        failure_cases=[
            {
                "column": "student_id",
                "index": 0,
                "check": "str_length",
                "failure_case": "AB",  # Too short
            },
            {
                "column": "grade",
                "index": 1,
                "check": "isin",
                "failure_case": "X",
            },
        ],
        raw_to_canon={"Student ID": "student_id", "Grade": "grade"},
        canon_to_raw={"student_id": "Student ID", "grade": "Grade"},
        merged_specs={
            "student_id": {
                "description": "A unique identifier",
                "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}],
            },
            "grade": {
                "description": "Student grade",
                "checks": [{"type": "isin", "args": [["A", "B", "C", "D", "F"]]}],
            },
        },
    )


@pytest.fixture
def error_with_pii() -> HardValidationError:
    """Create HardValidationError with PII in failure cases."""
    return HardValidationError(
        missing_required=[],
        extra_columns=[],
        schema_errors=None,
        failure_cases=[
            {
                "column": "student_id",
                "index": 0,
                "check": "str_length",
                "failure_case": "STU-12345-ABCDEF",
            },
            {
                "column": "email",
                "index": 1,
                "check": "matches",
                "failure_case": "john.doe@example.com",
            },
        ],
        raw_to_canon={"Student ID": "student_id", "Email": "email"},
        canon_to_raw={"student_id": "Student ID", "email": "Email"},
        merged_specs={
            "student_id": {
                "description": "Student identifier",
                "checks": [{"type": "str_length", "kwargs": {"min_value": 10}}],
            },
            "email": {
                "description": "Email address",
                "checks": [{"type": "matches", "args": [r"^[^@]+@[^@]+\.[^@]+$"]}],
            },
        },
    )


# ============================================================================
# Tests for _sanitize_string
# ============================================================================


def test_sanitize_string_normal() -> None:
    """Test sanitize_string with normal string."""
    result = _sanitize_string("normal_string")
    assert result == "normal_string"


def test_sanitize_string_with_newlines() -> None:
    """Test sanitize_string removes newlines."""
    result = _sanitize_string("line1\nline2\rline3")
    assert result == "line1 line2 line3"
    assert "\n" not in result
    assert "\r" not in result


def test_sanitize_string_truncates_long() -> None:
    """Test sanitize_string truncates very long strings."""
    long_string = "a" * 500
    result = _sanitize_string(long_string)
    assert len(result) <= MAX_VALUE_LENGTH + 3  # +3 for "..."
    assert result.endswith("...")


def test_sanitize_string_removes_control_chars() -> None:
    """Test sanitize_string removes control characters."""
    result = _sanitize_string("text\x00\x01\x02text")
    assert "\x00" not in result
    assert "\x01" not in result
    assert "\x02" not in result


def test_sanitize_string_with_custom_length() -> None:
    """Test sanitize_string with custom max_length."""
    result = _sanitize_string("a" * 100, max_length=50)
    assert len(result) <= 53  # 50 + "..."


def test_sanitize_string_non_string_input() -> None:
    """Test sanitize_string converts non-string to string."""
    result = _sanitize_string(12345)  # type: ignore[arg-type]
    assert result == "12345"


# ============================================================================
# Tests for _is_pii_column
# ============================================================================


def test_is_pii_column_student_id() -> None:
    """Test PII detection for student_id."""
    assert _is_pii_column("student_id") is True
    assert _is_pii_column("Student_ID") is True
    assert _is_pii_column("STUDENT_ID") is True


def test_is_pii_column_email() -> None:
    """Test PII detection for email."""
    assert _is_pii_column("email") is True
    assert _is_pii_column("email_address") is True
    assert _is_pii_column("user_email") is True


def test_is_pii_column_name() -> None:
    """Test PII detection for name fields."""
    assert _is_pii_column("first_name") is True
    assert _is_pii_column("last_name") is True
    assert _is_pii_column("full_name") is True
    # Note: "name" alone is not flagged to avoid false positives (e.g., "course_name")
    # Only specific variants like "first_name", "last_name" are flagged


def test_is_pii_column_ssn() -> None:
    """Test PII detection for SSN."""
    assert _is_pii_column("ssn") is True
    assert _is_pii_column("social_security") is True
    assert _is_pii_column("social_security_number") is True


def test_is_pii_column_non_pii() -> None:
    """Test PII detection returns False for non-PII columns."""
    assert _is_pii_column("grade") is False
    assert _is_pii_column("course_name") is False
    assert _is_pii_column("credits") is False
    assert _is_pii_column("term") is False


def test_is_pii_column_high_risk_indicators() -> None:
    """Test high-risk PII indicators are detected (substring matching)."""
    for indicator in PII_HIGH_RISK_INDICATORS:
        assert _is_pii_column(indicator) is True
        assert _is_pii_column(f"prefix_{indicator}") is True
        assert _is_pii_column(f"{indicator}_suffix") is True


def test_is_pii_column_medium_risk_token_matching() -> None:
    """Test medium-risk PII indicators use token matching (reduces false positives)."""
    # These should match (token match)
    assert _is_pii_column("first_name") is True
    assert _is_pii_column("last_name") is True
    assert _is_pii_column("full_name") is True
    assert _is_pii_column("student_id") is True
    assert _is_pii_column("home_address") is True

    # These should NOT match (false positive prevention)
    assert _is_pii_column("course_name") is False
    assert _is_pii_column("district_name") is False
    assert _is_pii_column("school_name") is False
    assert _is_pii_column("column_name") is False
    assert _is_pii_column("file_name") is False


# ============================================================================
# Tests for _mask_pii_value
# ============================================================================


def test_mask_pii_value_long() -> None:
    """Test masking long PII values."""
    result = _mask_pii_value("ABCDEFGHXY")
    assert result.startswith("AB")
    assert result.endswith("XY")
    assert "*" in result
    assert "ABCDEFGHXY" not in result


def test_mask_pii_value_short() -> None:
    """Test masking short PII values."""
    result = _mask_pii_value("AB")
    assert result == "****"
    assert "AB" not in result


def test_mask_pii_value_very_short() -> None:
    """Test masking very short PII values."""
    result = _mask_pii_value("A")
    assert result == "****"


def test_mask_pii_value_none() -> None:
    """Test masking None value."""
    result = _mask_pii_value(None)
    assert result == "N/A"


def test_mask_pii_value_email() -> None:
    """Test masking email address."""
    result = _mask_pii_value("john.doe@example.com")
    assert result.startswith("jo")
    assert result.endswith("om")
    assert "@example.com" not in result
    assert "john.doe" not in result


def test_mask_pii_value_truncates_very_long() -> None:
    """Test masking truncates very long values."""
    very_long = "A" * 1000
    result = _mask_pii_value(very_long)
    # Should be truncated before masking
    assert len(result) < 1000


def test_mask_pii_value_non_string() -> None:
    """Test masking non-string values."""
    result = _mask_pii_value(12345)
    assert "*" in result or result == "N/A"


# ============================================================================
# Tests for _normalize_failure_cases
# ============================================================================


def test_normalize_failure_cases_list() -> None:
    """Test normalizing list of dicts."""
    cases = [{"column": "test", "index": 0}]
    result = _normalize_failure_cases(cases)
    assert result == cases


def test_normalize_failure_cases_empty_list() -> None:
    """Test normalizing empty list."""
    result = _normalize_failure_cases([])
    assert result == []


def test_normalize_failure_cases_none() -> None:
    """Test normalizing None."""
    result = _normalize_failure_cases(None)
    assert result == []


def test_normalize_failure_cases_filters_non_dicts() -> None:
    """Test normalizing filters out non-dict items."""
    cases = [{"column": "test"}, "not_a_dict", 123, {"column": "test2"}]
    result = _normalize_failure_cases(cases)
    assert len(result) == 2
    assert all(isinstance(item, dict) for item in result)


def test_normalize_failure_cases_converts_iterable() -> None:
    """Test normalizing converts iterable to list."""
    cases = ({"column": "test"}, {"column": "test2"})
    result = _normalize_failure_cases(cases)
    assert isinstance(result, list)
    assert len(result) == 2


def test_normalize_failure_cases_invalid_type() -> None:
    """Test normalizing handles invalid types."""
    result = _normalize_failure_cases("not_iterable")
    assert result == []


@pytest.mark.skipif(not HAS_PANDAS, reason="pandas not available")
def test_normalize_failure_cases_dataframe() -> None:
    """Test normalizing pandas DataFrame (critical fix for Pandera integration)."""
    df = pd.DataFrame(
        [
            {"column": "col1", "index": 0, "check": "test", "failure_case": "val1"},
            {"column": "col2", "index": 1, "check": "test", "failure_case": "val2"},
        ]
    )
    result = _normalize_failure_cases(df)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(item, dict) for item in result)
    assert result[0]["column"] == "col1"
    assert result[1]["column"] == "col2"


@pytest.mark.skipif(not HAS_PANDAS, reason="pandas not available")
def test_normalize_failure_cases_dataframe_empty() -> None:
    """Test normalizing empty DataFrame."""
    df = pd.DataFrame()
    result = _normalize_failure_cases(df)
    assert result == []


# ============================================================================
# Tests for _group_failure_cases_by_column
# ============================================================================


def test_group_failure_cases_by_column() -> None:
    """Test grouping failure cases by column."""
    cases = [
        {"column": "col1", "index": 0, "check": "str_length", "failure_case": "val1"},
        {"column": "col1", "index": 1, "check": "str_length", "failure_case": "val2"},
        {"column": "col2", "index": 0, "check": "isin", "failure_case": "val3"},
    ]
    result = _group_failure_cases_by_column(cases)
    assert "col1" in result
    assert "col2" in result
    assert len(result["col1"]) == 2
    assert len(result["col2"]) == 1
    assert result["col1"][0]["row"] == 1  # 0-indexed to 1-indexed
    assert result["col1"][1]["row"] == 2


def test_group_failure_cases_by_column_negative_index() -> None:
    """Test grouping handles negative index."""
    cases = [
        {"column": "col1", "index": -1, "check": "test", "failure_case": "val"},
    ]
    result = _group_failure_cases_by_column(cases)
    assert result["col1"][0]["row"] is None


def test_group_failure_cases_by_column_missing_fields() -> None:
    """Test grouping handles missing fields."""
    cases: List[Dict[str, Any]] = [
        {"column": "col1"},  # Missing other fields
        {"column": "col2", "index": 0},
    ]
    result = _group_failure_cases_by_column(cases)
    assert "col1" in result
    assert "col2" in result


def test_group_failure_cases_by_column_schema_level() -> None:
    """Test grouping schema-level errors (no column)."""
    cases: List[Dict[str, Any]] = [
        {"index": 0, "check": "test", "failure_case": "val1"},  # No column
        {"column": None, "index": 1, "check": "test", "failure_case": "val2"},
        {"column": "", "index": 2, "check": "test", "failure_case": "val3"},
    ]
    result = _group_failure_cases_by_column(cases)
    assert "_schema_level" in result
    assert len(result["_schema_level"]) == 3


def test_group_failure_cases_by_column_nan_index() -> None:
    """Test grouping handles NaN row indices."""
    cases = [
        {
            "column": "col1",
            "index": float("nan"),
            "check": "test",
            "failure_case": "val",
        },
    ]
    result = _group_failure_cases_by_column(cases)
    assert "col1" in result
    assert result["col1"][0]["row"] is None


@pytest.mark.skipif(not HAS_PANDAS, reason="numpy not available")
def test_group_failure_cases_by_column_numpy_index() -> None:
    """Test grouping handles numpy integer types."""
    cases = [
        {
            "column": "col1",
            "index": np.int64(0),
            "check": "test",
            "failure_case": "val",
        },
        {
            "column": "col2",
            "index": np.int32(1),
            "check": "test",
            "failure_case": "val",
        },
    ]
    result = _group_failure_cases_by_column(cases)
    assert result["col1"][0]["row"] == 1  # 0-indexed to 1-indexed
    assert result["col2"][0]["row"] == 2


def test_group_failure_cases_by_column_string_index() -> None:
    """Test grouping handles string indices (non-int row labels)."""
    cases = [
        {"column": "col1", "index": "row_1", "check": "test", "failure_case": "val"},
    ]
    result = _group_failure_cases_by_column(cases)
    assert "col1" in result
    # Should return sanitized string, not None
    assert result["col1"][0]["row"] is not None
    assert "row_1" in str(result["col1"][0]["row"])


def test_normalize_row_index() -> None:
    """Test row index normalization."""
    from .validation_error_formatter import _normalize_row_index

    # Normal int
    assert _normalize_row_index(0) == 1  # 0-indexed to 1-indexed
    assert _normalize_row_index(5) == 6

    # Negative
    assert _normalize_row_index(-1) is None

    # NaN
    assert _normalize_row_index(float("nan")) is None

    # None
    assert _normalize_row_index(None) is None

    # String index
    result = _normalize_row_index("row_1")
    assert result is not None
    assert isinstance(result, str)

    # Float
    assert _normalize_row_index(0.0) == 1
    # Non-integer float returns sanitized string (not misleading conversion)
    result_float = _normalize_row_index(5.7)
    assert isinstance(result_float, str)
    assert "5.7" in result_float


@pytest.mark.skipif(not HAS_PANDAS, reason="numpy not available")
def test_normalize_row_index_numpy() -> None:
    """Test row index normalization with numpy types."""
    from .validation_error_formatter import _normalize_row_index

    assert _normalize_row_index(np.int64(0)) == 1
    assert _normalize_row_index(np.int32(5)) == 6
    assert _normalize_row_index(np.nan) is None


# ============================================================================
# Tests for _format_missing_required
# ============================================================================


def test_format_missing_required(sample_error: HardValidationError) -> None:
    """Test formatting missing required columns."""
    result = _format_missing_required(sample_error)
    assert result is not None
    assert "Missing required columns" in result
    assert "Student ID" in result
    assert "Grade" in result
    assert "A unique identifier for each student" in result


def test_format_missing_required_empty() -> None:
    """Test formatting with no missing columns."""
    error = HardValidationError(missing_required=[])
    result = _format_missing_required(error)
    assert result is None


def test_format_missing_required_no_mappings() -> None:
    """Test formatting with missing mappings."""
    error = HardValidationError(
        missing_required=["student_id"],
        canon_to_raw=None,  # type: ignore
        merged_specs=None,  # type: ignore
    )
    result = _format_missing_required(error)
    assert result is not None
    assert "student_id" in result


def test_format_missing_required_no_description() -> None:
    """Test formatting without column descriptions."""
    error = HardValidationError(
        missing_required=["student_id"],
        canon_to_raw={"student_id": "Student ID"},
        merged_specs={"student_id": {}},  # No description
    )
    result = _format_missing_required(error)
    assert result is not None
    assert "Student ID" in result
    assert "(" not in result or "description" not in result.lower()


def test_format_missing_required_non_bijective_mapping() -> None:
    """Test formatting with non-bijective mapping (multiple raw → same canonical)."""
    # Only raw_to_canon provided, should derive canon_to_raw (first occurrence wins)
    error = HardValidationError(
        missing_required=["student_id"],
        raw_to_canon={
            "Student ID": "student_id",
            "StudentID": "student_id",
        },  # Two raw → one canon
        canon_to_raw=None,  # Not provided, should derive
        merged_specs={"student_id": {}},
    )
    result = _format_missing_required(error)
    assert result is not None
    # Should use first raw name seen
    assert "Student ID" in result or "student_id" in result


def test_get_canon_to_raw_mapping() -> None:
    """Test canon_to_raw mapping helper."""
    # Prefer canon_to_raw if available
    error1 = HardValidationError(
        missing_required=[],
        canon_to_raw={"student_id": "Student ID"},
        raw_to_canon={"Student ID": "student_id", "StudentID": "student_id"},
    )
    mapping1 = _get_canon_to_raw_mapping(error1)
    assert mapping1["student_id"] == "Student ID"

    # Derive from raw_to_canon if canon_to_raw not available
    error2 = HardValidationError(
        missing_required=[],
        canon_to_raw=None,  # type: ignore
        raw_to_canon={"Student ID": "student_id", "StudentID": "student_id"},
    )
    mapping2 = _get_canon_to_raw_mapping(error2)
    # First occurrence should win
    assert mapping2["student_id"] == "Student ID"


# ============================================================================
# Tests for _format_extra_columns
# ============================================================================


def test_format_extra_columns() -> None:
    """Test formatting extra columns."""
    error = HardValidationError(extra_columns=["unknown_col1", "unknown_col2"])
    result = _format_extra_columns(error)
    assert result is not None
    assert "Unexpected columns found" in result
    assert "unknown_col1" in result
    assert "unknown_col2" in result


def test_format_extra_columns_empty() -> None:
    """Test formatting with no extra columns."""
    error = HardValidationError(extra_columns=[])
    result = _format_extra_columns(error)
    assert result is None


def test_format_extra_columns_sanitizes() -> None:
    """Test formatting sanitizes column names."""
    error = HardValidationError(extra_columns=["col\nwith\nnewlines"])
    result = _format_extra_columns(error)
    assert result is not None
    assert "\n" not in result


# ============================================================================
# Tests for _format_check_error
# ============================================================================


def test_format_check_error_str_length() -> None:
    """Test formatting str_length check error."""
    spec = {"checks": [{"type": "str_length", "kwargs": {"min_value": 3}}]}
    result = _format_check_error("str_length", spec, "AB")
    assert "at least 3 characters" in result


def test_format_check_error_str_length_range() -> None:
    """Test formatting str_length with min and max."""
    spec = {
        "checks": [{"type": "str_length", "kwargs": {"min_value": 3, "max_value": 10}}]
    }
    result = _format_check_error("str_length", spec, "AB")
    assert "between 3 and 10" in result


def test_format_check_error_isin() -> None:
    """Test formatting isin check error."""
    spec = {"checks": [{"type": "isin", "args": [["A", "B", "C"]]}]}
    result = _format_check_error("isin", spec, "X")
    assert "one of" in result.lower()
    assert "A" in result or "B" in result or "C" in result


def test_format_check_error_isin_many_values() -> None:
    """Test formatting isin with many values."""
    spec = {"checks": [{"type": "isin", "args": [["A", "B", "C", "D", "E", "F", "G"]]}]}
    result = _format_check_error("isin", spec, "X")
    assert "one of the allowed values" in result.lower()


def test_format_check_error_matches() -> None:
    """Test formatting matches check error."""
    spec = {"checks": [{"type": "matches", "args": [r"^\d{4}-\d{2}$"]}]}
    result = _format_check_error("matches", spec, "invalid")
    assert "YYYY-YY" in result or "format" in result.lower()


def test_format_check_error_ge() -> None:
    """Test formatting ge check error."""
    spec = {"checks": [{"type": "ge", "kwargs": {"ge": 0}}]}
    result = _format_check_error("ge", spec, -1)
    assert "greater than or equal to 0" in result


def test_format_check_error_le() -> None:
    """Test formatting le check error."""
    spec = {"checks": [{"type": "le", "kwargs": {"le": 100}}]}
    result = _format_check_error("le", spec, 101)
    assert "less than or equal to 100" in result


def test_format_check_error_not_nullable() -> None:
    """Test formatting not_nullable check error."""
    spec: Dict[str, Any] = {"checks": []}
    result = _format_check_error("not_nullable", spec, None)
    assert "cannot be empty" in result.lower()


def test_format_check_error_unknown() -> None:
    """Test formatting unknown check type."""
    spec: Dict[str, Any] = {"checks": []}
    result = _format_check_error("unknown_check", spec, "value")
    assert "unknown_check" in result.lower()


# ============================================================================
# Tests for _format_column_validation_errors
# ============================================================================


def test_format_column_validation_errors(
    error_with_failure_cases: HardValidationError,
) -> None:
    """Test formatting column validation errors."""
    errors = [
        {"row": 1, "check": "str_length", "value": "AB"},
        {"row": 2, "check": "isin", "value": "X"},
    ]
    result = _format_column_validation_errors("grade", errors, error_with_failure_cases)
    assert len(result) > 0
    assert "Grade" in result[0]
    assert "Row 1" in result[0] or "Row 2" in result[0]


def test_format_column_validation_errors_pii_masking(
    error_with_pii: HardValidationError,
) -> None:
    """Test PII masking in column validation errors."""
    errors = [
        {"row": 1, "check": "str_length", "value": "STU-12345-ABCDEF"},
    ]
    result = _format_column_validation_errors("student_id", errors, error_with_pii)
    assert len(result) > 0
    assert "STU-12345-ABCDEF" not in result[0]
    assert "masked for privacy" in result[0]
    assert "*" in result[0]


def test_format_column_validation_errors_limit_examples() -> None:
    """Test limiting error examples."""
    error = HardValidationError(
        missing_required=[],
        canon_to_raw={"col": "Column"},
        merged_specs={"col": {}},
    )
    # Create 15 errors
    errors = [{"row": i, "check": "test", "value": f"val{i}"} for i in range(15)]
    result = _format_column_validation_errors("col", errors, error)
    # Should mention additional errors
    assert any("additional errors" in msg.lower() for msg in result)


def test_format_column_validation_errors_no_mappings() -> None:
    """Test formatting with missing mappings."""
    error = HardValidationError(
        missing_required=[],
        canon_to_raw=None,  # type: ignore
        merged_specs=None,  # type: ignore
    )
    errors = [{"row": 1, "check": "test", "value": "val"}]
    result = _format_column_validation_errors("col", errors, error)
    assert len(result) > 0


def test_format_column_validation_errors_schema_level() -> None:
    """Test formatting schema-level errors (no column)."""
    error = HardValidationError(
        missing_required=[],
        canon_to_raw={},
        merged_specs={},
    )
    errors = [
        {"row": 1, "check": "dataframe_check", "value": "error1"},
        {"row": 2, "check": "multi_column_check", "value": "error2"},
    ]
    result = _format_column_validation_errors("_schema_level", errors, error)
    assert len(result) > 0
    assert "File-level validation errors" in result[0]
    assert "Column" not in result[0]  # Should not say "Column '...'"


# ============================================================================
# Tests for _format_schema_validation_errors
# ============================================================================


def test_format_schema_validation_errors(
    error_with_failure_cases: HardValidationError,
) -> None:
    """Test formatting schema validation errors."""
    result = _format_schema_validation_errors(error_with_failure_cases)
    assert len(result) > 0
    assert any("Student ID" in msg or "Grade" in msg for msg in result)


def test_format_schema_validation_errors_empty() -> None:
    """Test formatting with no failure cases."""
    error = HardValidationError(failure_cases=[])
    result = _format_schema_validation_errors(error)
    assert result == []


def test_format_schema_validation_errors_invalid_cases() -> None:
    """Test formatting with invalid failure cases."""
    error = HardValidationError(failure_cases="not_a_list")
    result = _format_schema_validation_errors(error)
    assert isinstance(result, list)


def test_format_schema_validation_errors_deterministic_ordering() -> None:
    """Test that schema validation errors are sorted deterministically."""
    error = HardValidationError(
        failure_cases=[
            {"column": "zebra", "index": 0, "check": "test", "failure_case": "val"},
            {"column": "alpha", "index": 1, "check": "test", "failure_case": "val"},
            {"index": 2, "check": "test", "failure_case": "val"},  # Schema-level
            {"column": "beta", "index": 3, "check": "test", "failure_case": "val"},
        ],
        canon_to_raw={"alpha": "Alpha", "beta": "Beta", "zebra": "Zebra"},
        merged_specs={"alpha": {}, "beta": {}, "zebra": {}},
    )
    result1 = _format_schema_validation_errors(error)
    result2 = _format_schema_validation_errors(error)

    # Results should be identical (deterministic)
    assert result1 == result2

    # Schema-level should come first, then alphabetical
    result_text = "\n".join(result1)
    schema_level_pos = result_text.find("File-level")
    alpha_pos = result_text.find("Alpha")
    beta_pos = result_text.find("Beta")
    zebra_pos = result_text.find("Zebra")

    # Schema-level should be first
    if schema_level_pos >= 0:
        assert schema_level_pos < alpha_pos or alpha_pos < 0
    # Then alphabetical
    if alpha_pos >= 0 and beta_pos >= 0:
        assert alpha_pos < beta_pos
    if beta_pos >= 0 and zebra_pos >= 0:
        assert beta_pos < zebra_pos


# ============================================================================
# Tests for format_validation_error (Main Function)
# ============================================================================


def test_format_validation_error_missing_required(
    sample_error: HardValidationError,
) -> None:
    """Test formatting error with missing required columns."""
    result = format_validation_error(sample_error)
    assert "Missing required columns" in result
    assert "Student ID" in result


def test_format_validation_error_extra_columns() -> None:
    """Test formatting error with extra columns."""
    error = HardValidationError(extra_columns=["unknown1", "unknown2"])
    result = format_validation_error(error)
    assert "Unexpected columns found" in result
    assert "unknown1" in result


def test_format_validation_error_failure_cases(
    error_with_failure_cases: HardValidationError,
) -> None:
    """Test formatting error with failure cases."""
    result = format_validation_error(error_with_failure_cases)
    assert "validation errors" in result.lower() or "Row" in result


def test_format_validation_error_pii_masking(
    error_with_pii: HardValidationError,
) -> None:
    """Test PII masking in formatted error."""
    result = format_validation_error(error_with_pii)
    assert "STU-12345-ABCDEF" not in result
    assert "john.doe@example.com" not in result
    assert "masked for privacy" in result


def test_format_validation_error_decode_error() -> None:
    """Test formatting decode error."""
    error = HardValidationError(
        schema_errors="decode_error",
        failure_cases=["UnicodeDecodeError: invalid encoding"],
    )
    result = format_validation_error(error)
    assert "File encoding error" in result
    assert "UTF-8" in result


def test_format_validation_error_generic_schema_error() -> None:
    """Test formatting generic schema error."""
    error = HardValidationError(schema_errors="Invalid schema format")
    result = format_validation_error(error)
    assert "Schema validation error" in result


def test_format_validation_error_none() -> None:
    """Test formatting with None error raises ValueError."""
    with pytest.raises(ValueError, match="cannot be None"):
        format_validation_error(None)  # type: ignore


def test_format_validation_error_invalid_object() -> None:
    """Test formatting with invalid error object."""
    # Mock without missing_required attribute and without schema_errors
    invalid_error = Mock(spec=[])  # Empty spec means no attributes
    result = format_validation_error(invalid_error)  # type: ignore
    assert "Validation error occurred" in result


def test_format_validation_error_empty() -> None:
    """Test formatting error with all empty attributes."""
    error = HardValidationError()
    result = format_validation_error(error)
    # Should return fallback message
    assert len(result) > 0


def test_format_validation_error_message_size_limit() -> None:
    """Test message size limit enforcement."""
    # Create error with many failure cases
    failure_cases = [
        {
            "column": f"col_{i}",
            "index": i,
            "check": "test",
            "failure_case": "x" * 100,  # Long values
        }
        for i in range(100)
    ]
    error = HardValidationError(
        failure_cases=failure_cases,
        canon_to_raw={f"col_{i}": f"Column {i}" for i in range(100)},
        merged_specs={f"col_{i}": {} for i in range(100)},
    )
    result = format_validation_error(error)
    # Message should be within limits (may be truncated if it exceeds)
    assert len(result) <= MAX_MESSAGE_LENGTH + 100  # Allow some buffer
    # If message is very long, it should either be truncated or contain a notice
    # (The current implementation truncates at the section level, so very long messages
    # may not show all errors but won't necessarily have a truncation notice unless
    # the final result exceeds MAX_MESSAGE_LENGTH)
    if len(result) >= MAX_MESSAGE_LENGTH:
        assert "truncated" in result.lower() or "size limits" in result.lower()


def test_format_validation_error_all_types() -> None:
    """Test formatting error with all error types."""
    error = HardValidationError(
        missing_required=["col1"],
        extra_columns=["col2"],
        schema_errors="test_error",
        failure_cases=[
            {"column": "col3", "index": 0, "check": "test", "failure_case": "val"}
        ],
        canon_to_raw={"col1": "Column 1", "col3": "Column 3"},
        merged_specs={"col1": {}, "col3": {}},
    )
    result = format_validation_error(error)
    assert "Missing required columns" in result
    assert "Unexpected columns" in result
    assert "Schema validation error" in result
    assert "validation errors" in result.lower() or "Row" in result


def test_format_validation_error_handles_exceptions() -> None:
    """Test that exceptions in formatting are handled gracefully."""
    # Create error that might cause issues
    error = HardValidationError(
        missing_required=["col"],
        canon_to_raw={"col": object()},  # type: ignore[dict-item]  # Invalid type that might cause issues
        merged_specs={"col": object()},  # type: ignore[dict-item]  # Invalid type
    )
    # Should not raise, should return something
    result = format_validation_error(error)
    assert isinstance(result, str)
    assert len(result) > 0


def test_format_validation_error_very_long_values() -> None:
    """Test handling of very long values."""
    error = HardValidationError(
        failure_cases=[
            {
                "column": "col",
                "index": 0,
                "check": "test",
                "failure_case": "x" * 1000,  # Very long value
            }
        ],
        canon_to_raw={"col": "Column"},
        merged_specs={"col": {}},
    )
    result = format_validation_error(error)
    # Should be truncated
    assert len(result) < MAX_MESSAGE_LENGTH


def test_format_validation_error_special_characters() -> None:
    """Test handling of special characters in values."""
    error = HardValidationError(
        failure_cases=[
            {
                "column": "col",
                "index": 0,
                "check": "test",
                "failure_case": "value\nwith\nnewlines",
            }
        ],
        canon_to_raw={"col": "Column"},
        merged_specs={"col": {}},
    )
    result = format_validation_error(error)
    # Should sanitize newlines
    assert "\n" not in result or result.count("\n") < 3


def test_format_validation_error_pii_false_positive_prevention() -> None:
    """Test that PII detection doesn't flag false positives like 'course_name'."""
    error = HardValidationError(
        failure_cases=[
            {
                "column": "course_name",
                "index": 0,
                "check": "test",
                "failure_case": "Math 101",
            },
            {
                "column": "district_name",
                "index": 1,
                "check": "test",
                "failure_case": "District A",
            },
        ],
        canon_to_raw={"course_name": "Course Name", "district_name": "District Name"},
        merged_specs={"course_name": {}, "district_name": {}},
    )
    result = format_validation_error(error)
    # Values should NOT be masked (not PII)
    assert "Math 101" in result
    assert "District A" in result
    assert "masked for privacy" not in result


def test_format_validation_error_pii_true_positive() -> None:
    """Test that PII detection correctly flags true positives."""
    error = HardValidationError(
        failure_cases=[
            {
                "column": "first_name",
                "index": 0,
                "check": "test",
                "failure_case": "John",
            },
            {
                "column": "email_address",
                "index": 1,
                "check": "test",
                "failure_case": "john@example.com",
            },
        ],
        canon_to_raw={"first_name": "First Name", "email_address": "Email Address"},
        merged_specs={"first_name": {}, "email_address": {}},
    )
    result = format_validation_error(error)
    # Values should be masked (PII)
    assert "John" not in result
    assert "john@example.com" not in result
    assert "masked for privacy" in result


@pytest.mark.skipif(not HAS_PANDAS, reason="pandas not available")
def test_format_validation_error_dataframe_failure_cases() -> None:
    """Test formatting with DataFrame failure_cases (critical fix)."""
    df = pd.DataFrame(
        [
            {"column": "grade", "index": 0, "check": "isin", "failure_case": "X"},
            {"column": "grade", "index": 1, "check": "isin", "failure_case": "Y"},
        ]
    )
    error = HardValidationError(
        failure_cases=df,  # DataFrame, not list
        canon_to_raw={"grade": "Grade"},
        merged_specs={
            "grade": {"checks": [{"type": "isin", "args": [["A", "B", "C"]]}]}
        },
    )
    result = format_validation_error(error)
    # Should format errors (not drop them)
    assert "Grade" in result
    assert "validation errors" in result.lower() or "Row" in result


# ============================================================================
# Integration Tests
# ============================================================================


def _create_complete_error_failure_cases() -> List[dict]:
    """Create failure cases for complete error fixture."""
    return [
        {
            "column": "student_id",
            "index": 0,
            "check": "str_length",
            "failure_case": "AB",  # Too short
        },
        {
            "column": "email",
            "index": 1,
            "check": "matches",
            "failure_case": "invalid-email",  # PII
        },
        {
            "column": "grade",
            "index": 2,
            "check": "isin",
            "failure_case": "X",
        },
    ]


def _create_complete_error_mappings() -> tuple[Dict[str, str], Dict[str, str]]:
    """Create column mappings for complete error fixture."""
    raw_to_canon = {
        "Student ID": "student_id",
        "Course Name": "course_name",
        "Email": "email",
        "Grade": "grade",
    }
    canon_to_raw = {
        "student_id": "Student ID",
        "course_name": "Course Name",
        "email": "Email",
        "grade": "Grade",
    }
    return raw_to_canon, canon_to_raw


def _create_complete_error_specs() -> Dict[str, dict]:
    """Create merged specs for complete error fixture."""
    return {
        "student_id": {
            "description": "Unique student identifier",
            "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}],
        },
        "course_name": {"description": "Name of the course"},
        "email": {
            "description": "Student email",
            "checks": [{"type": "matches", "args": [r"^[^@]+@[^@]+\.[^@]+$"]}],
        },
        "grade": {
            "description": "Course grade",
            "checks": [{"type": "isin", "args": [["A", "B", "C", "D", "F"]]}],
        },
    }


@pytest.fixture
def complete_error() -> HardValidationError:
    """Create a complete error with all error types for integration testing."""
    raw_to_canon, canon_to_raw = _create_complete_error_mappings()
    return HardValidationError(
        missing_required=["student_id", "course_name"],
        extra_columns=["unknown_field"],
        failure_cases=_create_complete_error_failure_cases(),
        raw_to_canon=raw_to_canon,
        canon_to_raw=canon_to_raw,
        merged_specs=_create_complete_error_specs(),
    )


def test_integration_all_error_types_present(
    complete_error: HardValidationError,
) -> None:
    """Test that all error types are present in formatted output."""
    result = format_validation_error(complete_error)
    assert "Missing required columns" in result
    assert "Unexpected columns found" in result
    assert "validation errors" in result.lower()


def test_integration_pii_masking_and_non_pii_display(
    complete_error: HardValidationError,
) -> None:
    """Test PII masking and non-PII value display in integration."""
    result = format_validation_error(complete_error)
    # Check PII is masked
    assert "invalid-email" not in result
    assert "masked for privacy" in result
    # Check non-PII values are shown
    assert "X" in result  # Grade is not PII


def test_integration_user_friendly_column_names(
    complete_error: HardValidationError,
) -> None:
    """Test that user-friendly column names are used."""
    result = format_validation_error(complete_error)
    assert "Student ID" in result
    assert "Course Name" in result
    assert "Email" in result


def test_integration_row_numbering_and_message_size(
    complete_error: HardValidationError,
) -> None:
    """Test row numbering and message size limits."""
    result = format_validation_error(complete_error)
    # Check row numbers are 1-indexed
    assert "Row 1" in result or "Row 2" in result or "Row 3" in result
    # Check message is reasonable size
    assert len(result) < MAX_MESSAGE_LENGTH


# ============================================================================
# Tests for Recently Added Fixes
# ============================================================================


def test_format_extra_columns_reverse_lookup_single() -> None:
    """Test extra columns reverse lookup with single raw name."""
    error = HardValidationError(
        extra_columns=["student_id"],
        raw_to_canon={"Student ID": "student_id"},  # Maps to different canonical
    )
    result = _format_extra_columns(error)
    assert result is not None
    # Should find raw name via reverse lookup
    assert "Student ID" in result


def test_format_extra_columns_reverse_lookup_multiple() -> None:
    """Test extra columns reverse lookup with multiple raw names (ambiguity handling)."""
    error = HardValidationError(
        extra_columns=["student_id"],
        raw_to_canon={
            "Student ID": "student_id",  # First occurrence
            "StudentID": "student_id",  # Second occurrence (same normalized)
            "STUDENT-ID": "student_id",  # Third occurrence
        },
    )
    result = _format_extra_columns(error)
    assert result is not None
    # Should use first encountered raw name (deterministic)
    assert "Student ID" in result


def test_format_extra_columns_reverse_lookup_exact_match() -> None:
    """Test extra columns reverse lookup prefers exact match."""
    error = HardValidationError(
        extra_columns=["student_id"],
        raw_to_canon={
            "Student ID": "student_id",  # First occurrence
            "student_id": "student_id",  # Exact match (should be preferred)
        },
    )
    result = _format_extra_columns(error)
    assert result is not None
    # Should prefer exact match
    assert "student_id" in result


def test_format_extra_columns_reverse_lookup_two_matches() -> None:
    """Test extra columns reverse lookup with exactly 2 matches."""
    error = HardValidationError(
        extra_columns=["student_id"],
        raw_to_canon={
            "Student ID": "student_id",
            "StudentID": "student_id",
        },
    )
    result = _format_extra_columns(error)
    assert result is not None
    # Should show both with "or"
    assert "Student ID" in result
    assert "StudentID" in result
    assert " or " in result


def test_format_extra_columns_reverse_lookup_three_plus_matches() -> None:
    """Test extra columns reverse lookup with 3+ matches."""
    error = HardValidationError(
        extra_columns=["student_id"],
        raw_to_canon={
            "Student ID": "student_id",
            "StudentID": "student_id",
            "STUDENT-ID": "student_id",
            "student_id_col": "student_id",
        },
    )
    result = _format_extra_columns(error)
    assert result is not None
    # Should show first with count
    assert "Student ID" in result
    assert "and" in result.lower()
    assert "similar" in result.lower()


def test_normalize_failure_cases_dataframe_like_no_iterrows() -> None:
    """Test normalize_failure_cases with DataFrame-like object without iterrows (behavior-based)."""

    # Mock object with to_dict but no iterrows (behavior-based detection)
    class MockDataFrame:
        def to_dict(self, orient: Optional[str] = None) -> Any:
            if orient == "records":
                return [{"column": "col1", "index": 0}]
            return {"col1": {0: "val"}}

    mock_df = MockDataFrame()
    result = _normalize_failure_cases(mock_df)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["column"] == "col1"


def test_normalize_failure_cases_dataframe_fallback_to_dict() -> None:
    """Test normalize_failure_cases fallback when to_dict(orient='records') fails."""

    # Mock object where orient='records' fails but to_dict() works
    class MockDataFrame:
        def to_dict(self, orient: Optional[str] = None) -> Any:
            if orient == "records":
                raise ValueError("orient='records' not supported")
            # Return dict of dicts format
            return {"col1": {0: "val1"}, "col2": {0: "val2"}}

    mock_df = MockDataFrame()
    result = _normalize_failure_cases(mock_df)
    # Should handle fallback gracefully
    assert isinstance(result, list)


def test_normalize_row_index_non_integer_float() -> None:
    """Test normalize_row_index with non-integer float (should return sanitized string)."""
    from .validation_error_formatter import _normalize_row_index

    result = _normalize_row_index(5.7)
    # Should return sanitized string, not misleading conversion
    assert isinstance(result, str)
    assert "5.7" in result or "5" in result


def test_normalize_row_index_whole_number_float() -> None:
    """Test normalize_row_index with whole number float (should convert correctly)."""
    from .validation_error_formatter import _normalize_row_index

    result = _normalize_row_index(5.0)
    # Should convert to int and add 1 (0-indexed to 1-indexed)
    assert result == 6

    result = _normalize_row_index(0.0)
    assert result == 1


def test_format_isin_error_numeric_tie_breaking() -> None:
    """Test isin error formatting with numeric tie-breaking (01, 1, 1.0)."""
    from .validation_error_formatter import _format_isin_error

    check_spec = {"type": "isin", "args": [["01", "1", "1.0", "2", "10"]]}
    result1 = _format_isin_error(check_spec)
    result2 = _format_isin_error(check_spec)

    # Should be deterministic (same result each time)
    assert result1 == result2

    # Should show values in sorted order
    assert "01" in result1
    assert "1" in result1
    assert "1.0" in result1
    # Order should be deterministic (numeric sort with string tie-breaker)
    idx_01 = result1.find("01")
    idx_1 = result1.find("1")
    idx_10 = result1.find("10")
    idx_2 = result1.find("2")
    # Should be in order: 01, 1, 1.0, 2, 10 (or similar deterministic order)
    assert idx_01 < idx_10
    assert idx_1 < idx_10
    assert idx_2 < idx_10


def test_format_isin_error_nan_inf_handling() -> None:
    """Test isin error formatting with NaN/inf values (should go to non-numeric bucket)."""
    from .validation_error_formatter import _format_isin_error

    check_spec = {"type": "isin", "args": [["2", "NaN", "10", "inf", "1"]]}
    result = _format_isin_error(check_spec)

    # Should handle NaN/inf gracefully
    assert "NaN" in result or "inf" in result
    # Numeric values should be sorted
    assert "1" in result
    assert "2" in result
    assert "10" in result


def test_format_isin_error_set_input() -> None:
    """Test isin error formatting with set input (unstable ordering → stable output)."""
    from .validation_error_formatter import _format_isin_error

    # Set has unstable ordering
    check_spec = {"type": "isin", "args": [{"A", "B", "C", "D"}]}
    result1 = _format_isin_error(check_spec)
    result2 = _format_isin_error(check_spec)

    # Should be deterministic (same result each time)
    assert result1 == result2


def test_format_isin_error_mixed_numeric_non_numeric() -> None:
    """Test isin error formatting with mixed numeric and non-numeric values."""
    from .validation_error_formatter import _format_isin_error

    check_spec = {"type": "isin", "args": [["2", "A", "10", "1", "B"]]}
    result = _format_isin_error(check_spec)

    # Numeric should come first, sorted: 1, 2, 10
    # Then non-numeric, sorted: A, B
    idx_1 = result.find("1")
    idx_2 = result.find("2")
    idx_10 = result.find("10")
    idx_a = result.find("A")
    idx_b = result.find("B")

    # Numeric before non-numeric
    assert idx_1 < idx_a
    assert idx_2 < idx_a
    assert idx_10 < idx_a
    # Numeric sorted
    assert idx_1 < idx_2 < idx_10
    # Non-numeric sorted
    assert idx_a < idx_b


def test_format_missing_required_empty_display() -> None:
    """Test format_missing_required with all invalid entries (empty display)."""
    error = HardValidationError(
        missing_required=[None, "", 123, object()],  # type: ignore[list-item]  # All invalid
        canon_to_raw={},
        merged_specs={},
    )
    result = _format_missing_required(error)
    assert result is not None
    # Should return generic message, not empty list
    assert "Missing required columns detected" in result
    assert (
        "These columns must be present" in result or "check your file" in result.lower()
    )
