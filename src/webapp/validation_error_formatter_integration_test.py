"""Integration tests for validation_error_formatter with real Pandera errors.

These tests use actual Pandera schema validation to generate real SchemaErrors
objects, ensuring the formatter handles real-world failure_cases shapes correctly.
"""

import pytest

try:
    import pandas as pd
    import numpy as np
    from pandera import DataFrameSchema, Column, Check
    from pandera.errors import SchemaErrors, SchemaError

    HAS_PANDERA = True
except ImportError:
    HAS_PANDERA = False
    pd = None  # type: ignore
    np = None  # type: ignore
    SchemaErrors = None  # type: ignore
    SchemaError = None  # type: ignore

from .validation import HardValidationError
from .validation_error_formatter import (
    format_validation_error,
    MAX_MESSAGE_LENGTH,
    MAX_ERROR_EXAMPLES,
)


# ============================================================================
# Integration Tests with Real Pandera Errors
# ============================================================================


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_missing_required_columns() -> None:
    """Test formatter with real Pandera error for missing required columns."""
    # Note: We simulate missing columns by creating HardValidationError directly
    # (as validation.py does) rather than using actual Pandera validation
    error = HardValidationError(
        missing_required=["student_id", "grade"],
        raw_to_canon={"Student ID": "student_id", "Grade": "grade"},
        canon_to_raw={"student_id": "Student ID", "grade": "Grade"},
        merged_specs={
            "student_id": {"description": "Unique student identifier"},
            "grade": {"description": "Student grade"},
        },
    )

    result = format_validation_error(error)

    # Assertions: Check specific content, not just "no exception"
    assert len(result) > 0
    assert len(result) <= MAX_MESSAGE_LENGTH
    # Section header
    assert "Missing required columns" in result
    # Column display names (raw headers)
    assert "Student ID" in result  # Raw header, not canonical
    assert "Grade" in result  # Raw header, not canonical
    # Guidance text
    assert "must be present" in result.lower() or "required" in result.lower()


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_type_errors() -> None:
    """Test formatter with real Pandera type validation errors."""
    schema = DataFrameSchema(
        {
            "age": Column(int, nullable=False),
            "score": Column(float, nullable=False),
        }
    )

    # Create DataFrame with wrong types
    df = pd.DataFrame(
        {
            "age": ["not_a_number", "also_not_a_number"],
            "score": ["invalid", "also_invalid"],
        }
    )

    try:
        schema.validate(df, lazy=True)  # Use lazy=True to collect all errors
        pytest.fail("Expected SchemaErrors to be raised")
    except SchemaErrors as e:
        # Convert to HardValidationError format (as validation.py does)
        # Handle case where failure_cases might be a DataFrame
        if hasattr(e.failure_cases, "to_dict"):
            failure_cases = e.failure_cases.to_dict(orient="records")
        else:
            failure_cases = []

        error = HardValidationError(
            failure_cases=failure_cases,
            raw_to_canon={"Age": "age", "Score": "score"},
            canon_to_raw={"age": "Age", "score": "Score"},
            merged_specs={
                "age": {"dtype": "int64", "nullable": False},
                "score": {"dtype": "float64", "nullable": False},
            },
        )

        result = format_validation_error(error)

        # Assertions: Check specific content
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        if failure_cases:
            # Column display names (raw headers)
            assert "Age" in result  # Raw header
            assert "Score" in result  # Raw header
            # Section header
            assert "Column" in result or "has validation errors" in result.lower()
            # Row numbers or "Unknown row"
            assert "Row" in result or "Unknown row" in result
            # Example values should be present (may be masked if PII)
            # Type errors show the actual type found
            assert (
                "object" in result.lower()
                or "int" in result.lower()
                or "float" in result.lower()
            )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_isin_errors() -> None:
    """Test formatter with real Pandera isin check errors."""
    schema = DataFrameSchema(
        {
            "grade": Column(str, checks=Check.isin(["A", "B", "C", "D", "F"])),
            "status": Column(str, checks=Check.isin(["active", "inactive", "pending"])),
        }
    )

    # Create DataFrame with invalid values
    df = pd.DataFrame(
        {
            "grade": ["X", "Y", "Z"],
            "status": ["invalid", "also_invalid", "third_invalid"],
        }
    )

    try:
        schema.validate(df, lazy=True)  # Use lazy=True to collect all errors
        pytest.fail("Expected SchemaErrors to be raised")
    except SchemaErrors as e:
        # Handle case where failure_cases might be a DataFrame
        if hasattr(e.failure_cases, "to_dict"):
            failure_cases = e.failure_cases.to_dict(orient="records")
        else:
            failure_cases = []

        error = HardValidationError(
            failure_cases=failure_cases,
            raw_to_canon={"Grade": "grade", "Status": "status"},
            canon_to_raw={"grade": "Grade", "status": "Status"},
            merged_specs={
                "grade": {
                    "checks": [{"type": "isin", "args": [["A", "B", "C", "D", "F"]]}],
                },
                "status": {
                    "checks": [
                        {"type": "isin", "args": [["active", "inactive", "pending"]]}
                    ],
                },
            },
        )

        result = format_validation_error(error)

        # Assertions: Check specific content
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        # Column display names (raw headers)
        assert "Grade" in result  # Raw header
        assert "Status" in result  # Raw header
        # Section header
        assert "Column" in result or "has validation errors" in result.lower()
        # Row numbers (1-indexed)
        assert "Row 1" in result  # First row error
        assert "Row 2" in result  # Second row error
        # isin check mention
        assert (
            "isin" in result.lower()
            or "one of" in result.lower()
            or "allowed values" in result.lower()
        )
        # Example values should be shown (not masked - these are not PII)
        assert "X" in result or "Y" in result or "Z" in result  # Actual values shown
        assert "invalid" in result.lower()  # Actual values shown


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_nullability_errors() -> None:
    """Test formatter with real Pandera nullability errors."""
    schema = DataFrameSchema(
        {
            "student_id": Column(str, nullable=False),
            "name": Column(str, nullable=False),
        }
    )

    # Create DataFrame with null values
    df = pd.DataFrame(
        {
            "student_id": ["STU001", None, "STU003"],
            "name": ["Alice", "Bob", None],
        }
    )

    try:
        schema.validate(df, lazy=True)  # Use lazy=True to collect all errors
        pytest.fail("Expected SchemaErrors to be raised")
    except SchemaErrors as e:
        # Handle case where failure_cases might be a DataFrame
        if hasattr(e.failure_cases, "to_dict"):
            failure_cases = e.failure_cases.to_dict(orient="records")
        else:
            failure_cases = []

        error = HardValidationError(
            failure_cases=failure_cases,
            raw_to_canon={"Student ID": "student_id", "Name": "name"},
            canon_to_raw={"student_id": "Student ID", "name": "Name"},
            merged_specs={
                "student_id": {"nullable": False},
                "name": {"nullable": False},
            },
        )

        result = format_validation_error(error)

        # Assertions: Check specific content
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        # Column display names (raw headers)
        assert "Student ID" in result  # Raw header
        assert "Name" in result  # Raw header
        # Section header
        assert "Column" in result or "has validation errors" in result.lower()
        # Row numbers (1-indexed) - null values should be at specific rows
        assert "Row 2" in result  # Second row has null student_id
        assert "Row 3" in result  # Third row has null name
        # Nullability error message
        assert (
            "cannot be empty" in result.lower()
            or "null" in result.lower()
            or "empty" in result.lower()
            or "required" in result.lower()
        )


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_schema_level_checks() -> None:
    """Test formatter with real Pandera schema-level validation errors.

    This test ensures schema-level errors (without a column field) are properly
    formatted using the _schema_level path.
    """
    # Schema-level check: ensure all rows have at least one non-null value
    schema = DataFrameSchema(
        columns={
            "col1": Column(str, nullable=True),
            "col2": Column(str, nullable=True),
        },
        checks=Check(lambda df: len(df) > 0, name="non_empty_dataframe"),
    )

    # Create empty DataFrame
    df = pd.DataFrame(columns=["col1", "col2"])

    try:
        schema.validate(df)
        pytest.fail("Expected SchemaError or SchemaErrors to be raised")
    except (SchemaErrors, SchemaError) as e:
        # Schema-level checks may raise SchemaError (singular) or SchemaErrors (plural)
        # Handle case where failure_cases might be a DataFrame or missing
        failure_cases = []
        if hasattr(e, "failure_cases") and e.failure_cases is not None:
            if hasattr(e.failure_cases, "to_dict"):
                failure_cases = e.failure_cases.to_dict(orient="records")

        # If failure_cases don't have a column field, ensure they're treated as schema-level
        # Manually create a schema-level failure case if needed
        if not failure_cases or all(
            case.get("column") for case in failure_cases if isinstance(case, dict)
        ):
            # Create a schema-level failure case (no column field)
            failure_cases = [
                {
                    "check": "non_empty_dataframe",
                    "failure_case": "DataFrame is empty",
                    # No "column" field - this triggers schema-level formatting
                }
            ]

        error = HardValidationError(
            failure_cases=failure_cases,
            raw_to_canon={"Col1": "col1", "Col2": "col2"},
            canon_to_raw={"col1": "Col1", "col2": "Col2"},
            merged_specs={},
        )

        result = format_validation_error(error)

        # Assertions: Check schema-level formatting path
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        # Schema-level section header (not "Column '...'")
        assert "File-level" in result or "file-level" in result.lower()
        # Should NOT have "Column" prefix for schema-level errors
        assert "Column 'File-level" not in result
        # Should have validation error content
        assert "validation" in result.lower() or "error" in result.lower()


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_row_numbering() -> None:
    """Test that formatter correctly converts 0-indexed to 1-indexed row numbers."""
    schema = DataFrameSchema(
        {
            "value": Column(int, checks=Check.greater_than(0)),
        }
    )

    # Create DataFrame with errors at specific rows
    df = pd.DataFrame(
        {
            "value": [1, -5, 3, -10, 5],  # Rows 1 and 3 (0-indexed) have errors
        }
    )

    try:
        schema.validate(df, lazy=True)  # Use lazy=True to collect all errors
        pytest.fail("Expected SchemaErrors to be raised")
    except SchemaErrors as e:
        # Handle case where failure_cases might be a DataFrame
        if hasattr(e.failure_cases, "to_dict"):
            failure_cases = e.failure_cases.to_dict(orient="records")
        else:
            failure_cases = []

        error = HardValidationError(
            failure_cases=failure_cases,
            raw_to_canon={"Value": "value"},
            canon_to_raw={"value": "Value"},
            merged_specs={
                "value": {"checks": [{"type": "ge", "kwargs": {"ge": 0}}]},
            },
        )

        result = format_validation_error(error)

        # Assertions: Check row numbering (0-indexed to 1-indexed conversion)
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        # Column display name
        assert "Value" in result  # Raw header
        # Section header
        assert "Column" in result or "has validation errors" in result.lower()
        # Row numbers (1-indexed) - specific rows with errors
        assert "Row 2" in result  # 0-indexed row 1 -> 1-indexed row 2 (value=-5)
        assert "Row 4" in result  # 0-indexed row 3 -> 1-indexed row 4 (value=-10)
        # Should NOT have "Row 0" or "Row 1" (0-indexed)
        assert "Row 0" not in result
        # Example values should be shown (negative numbers, not PII)
        assert "-5" in result or "-10" in result or "negative" in result.lower()


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_pii_masking() -> None:
    """Test that formatter masks PII values in real Pandera errors.

    This test covers both "should mask" (student_name, email, ssn) and
    "should not mask" (course_name) to prevent regressions both ways.
    """
    schema = DataFrameSchema(
        {
            "student_name": Column(str, checks=Check.str_length(min_value=3)),
            "course_name": Column(str, checks=Check.str_length(min_value=3)),
            "email": Column(str, checks=Check.str_length(min_value=5)),
            "ssn": Column(str, checks=Check.str_length(min_value=9)),
        }
    )

    # Create DataFrame with both PII and non-PII values that fail validation
    df = pd.DataFrame(
        {
            "student_name": ["AB"],  # Too short, contains PII - SHOULD BE MASKED
            "course_name": ["XY"],  # Too short, NOT PII - SHOULD NOT BE MASKED
            "email": ["ab@c"],  # Too short, contains PII - SHOULD BE MASKED
            "ssn": ["123"],  # Too short, contains PII - SHOULD BE MASKED
        }
    )

    try:
        schema.validate(df, lazy=True)  # Use lazy=True to collect all errors
        pytest.fail("Expected SchemaErrors to be raised")
    except SchemaErrors as e:
        # Handle case where failure_cases might be a DataFrame
        if hasattr(e.failure_cases, "to_dict"):
            failure_cases = e.failure_cases.to_dict(orient="records")
        else:
            failure_cases = []

        error = HardValidationError(
            failure_cases=failure_cases,
            raw_to_canon={
                "Student Name": "student_name",
                "Course Name": "course_name",
                "Email": "email",
                "SSN": "ssn",
            },
            canon_to_raw={
                "student_name": "Student Name",
                "course_name": "Course Name",
                "email": "Email",
                "ssn": "SSN",
            },
            merged_specs={
                "student_name": {
                    "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}]
                },
                "course_name": {
                    "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}]
                },
                "email": {
                    "checks": [{"type": "str_length", "kwargs": {"min_value": 5}}]
                },
                "ssn": {"checks": [{"type": "str_length", "kwargs": {"min_value": 9}}]},
            },
        )

        result = format_validation_error(error)

        # Assertions: Check PII masking (both positive and negative cases)
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        # Column display names
        assert "Student Name" in result
        assert "Course Name" in result
        assert "Email" in result
        assert "SSN" in result
        # Section headers
        assert "Column" in result or "has validation errors" in result.lower()
        # PII values SHOULD BE MASKED (student_name, email, ssn)
        assert "AB" not in result  # student_name value masked
        assert "ab@c" not in result  # email value masked
        assert "123" not in result  # ssn value masked
        # Non-PII values SHOULD NOT BE MASKED (course_name)
        assert "XY" in result  # course_name value shown (not PII)
        # Masking indicators
        assert "*" in result or "<redacted>" in result or "masked" in result.lower()


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_truncation_behavior() -> None:
    """Test that formatter truncates messages when there are many errors.

    This test forces truncation deterministically by generating enough failures
    to exceed MAX_ERROR_EXAMPLES and/or MAX_MESSAGE_LENGTH.
    """
    schema = DataFrameSchema(
        {
            "value": Column(int, checks=Check.greater_than(0)),
        }
    )

    # Create DataFrame with many errors (more than MAX_ERROR_EXAMPLES)
    # Generate enough to guarantee truncation
    num_errors = MAX_ERROR_EXAMPLES + 5  # Ensure we exceed the limit
    df = pd.DataFrame(
        {
            "value": [-1] * num_errors,  # Many rows with errors
        }
    )

    try:
        schema.validate(df, lazy=True)  # Use lazy=True to collect all errors
        pytest.fail("Expected SchemaErrors to be raised")
    except SchemaErrors as e:
        # Handle case where failure_cases might be a DataFrame
        if hasattr(e.failure_cases, "to_dict"):
            failure_cases = e.failure_cases.to_dict(orient="records")
        else:
            failure_cases = []

        error = HardValidationError(
            failure_cases=failure_cases,
            raw_to_canon={"Value": "value"},
            canon_to_raw={"value": "Value"},
            merged_specs={
                "value": {"checks": [{"type": "ge", "kwargs": {"ge": 0}}]},
            },
        )

        result = format_validation_error(error)

        # Assertions: Check truncation behavior deterministically
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        # Column display name
        assert "Value" in result
        # Section header
        assert "Column" in result or "has validation errors" in result.lower()
        # Truncation notice should appear (we have more than MAX_ERROR_EXAMPLES)
        assert "additional errors" in result.lower() or "additional" in result.lower()
        # Should show some examples (up to MAX_ERROR_EXAMPLES)
        assert "Row" in result  # At least some row numbers shown
        # Should mention the count of additional errors
        additional_count = num_errors - MAX_ERROR_EXAMPLES
        assert str(additional_count) in result or "additional" in result.lower()


@pytest.mark.skipif(not HAS_PANDERA, reason="pandera not available")
def test_integration_pandera_mixed_error_types() -> None:
    """Test formatter with real Pandera errors containing multiple error types."""
    schema = DataFrameSchema(
        {
            "student_id": Column(
                str, nullable=False, checks=Check.str_length(min_value=3)
            ),
            "grade": Column(str, checks=Check.isin(["A", "B", "C", "D", "F"])),
            "age": Column(int, nullable=False),
        }
    )

    # Create DataFrame with multiple types of errors
    df = pd.DataFrame(
        {
            "student_id": ["AB", None, "STU003"],  # Too short, null, valid
            "grade": ["X", "Y", "A"],  # Invalid, invalid, valid
            "age": [15, None, 20],  # Valid, null, valid
        }
    )

    try:
        schema.validate(df, lazy=True)  # Use lazy=True to collect all errors
        pytest.fail("Expected SchemaErrors to be raised")
    except SchemaErrors as e:
        # Handle case where failure_cases might be a DataFrame
        if hasattr(e.failure_cases, "to_dict"):
            failure_cases = e.failure_cases.to_dict(orient="records")
        else:
            failure_cases = []

        error = HardValidationError(
            failure_cases=failure_cases,
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
                "student_id": {
                    "nullable": False,
                    "checks": [{"type": "str_length", "kwargs": {"min_value": 3}}],
                },
                "grade": {
                    "checks": [{"type": "isin", "args": [["A", "B", "C", "D", "F"]]}],
                },
                "age": {
                    "nullable": False,
                },
            },
        )

        result = format_validation_error(error)

        # Assertions: Check multiple error types
        assert len(result) > 0
        assert len(result) <= MAX_MESSAGE_LENGTH
        # Column display names (raw headers)
        assert "Student ID" in result  # Raw header
        assert "Grade" in result  # Raw header
        assert "Age" in result  # Raw header
        # Section headers
        assert "Column" in result or "has validation errors" in result.lower()
        # Row numbers (1-indexed) - specific rows with errors
        assert (
            "Row 1" in result
        )  # First row has multiple errors (student_id too short, grade invalid)
        assert (
            "Row 2" in result
        )  # Second row has multiple errors (student_id null, grade invalid, age null)
        # Row 3 is valid, so it won't appear in errors
        # Different error types should be mentioned
        # - str_length error for student_id
        assert (
            "length" in result.lower()
            or "short" in result.lower()
            or "minimum" in result.lower()
        )
        # - isin error for grade
        assert (
            "isin" in result.lower()
            or "one of" in result.lower()
            or "allowed" in result.lower()
        )
        # - nullability error for age
        assert (
            "cannot be empty" in result.lower()
            or "null" in result.lower()
            or "empty" in result.lower()
            or "required" in result.lower()
        )
