"""Unit tests for PDP/Edvise schema validation (same validation as edvise repo)."""

import pandas as pd
import pytest
from unittest.mock import MagicMock

from src.webapp.validation_pdp_edvise import (
    PDP_EDVISE_NAMESPACES,
    _extract_missing_required_from_pandera_error,
    _normalize_failure_cases_for_formatter,
    get_edvise_schema_for_models,
    get_edvise_schema_for_upload,
    is_edvise_schema_available,
    should_use_edvise_schema,
)


def test_should_use_edvise_schema_returns_false_for_empty_institution_id() -> None:
    """Empty or invalid institution_id should not use edvise schema."""
    assert should_use_edvise_schema("", ["STUDENT"]) is False
    assert should_use_edvise_schema("  ", ["COURSE"]) is False


def test_should_use_edvise_schema_returns_false_for_custom_namespace() -> None:
    """Custom institution UUID should not use edvise schema."""
    assert (
        should_use_edvise_schema("a1b2c3d4-e5f6-7890-abcd-ef1234567890", ["STUDENT"])
        is False
    )


def test_should_use_edvise_schema_returns_false_for_multi_model() -> None:
    """Multiple models (STUDENT and COURSE) should not use edvise schema."""
    assert should_use_edvise_schema("pdp", ["STUDENT", "COURSE"]) is False
    assert should_use_edvise_schema("edvise", ["COURSE", "STUDENT"]) is False


def test_should_use_edvise_schema_returns_false_for_other_models() -> None:
    """SEMESTER or other model alone should not use edvise schema."""
    assert should_use_edvise_schema("pdp", ["SEMESTER"]) is False
    assert should_use_edvise_schema("pdp", []) is False


def test_should_use_edvise_schema_behavior_for_pdp_single_model() -> None:
    """For pdp with single STUDENT or COURSE, result depends on edvise availability."""
    # When edvise is available, should return True; when not, False.
    result_student = should_use_edvise_schema("pdp", ["STUDENT"])
    result_course = should_use_edvise_schema("pdp", ["COURSE"])
    assert result_student == result_course  # Both depend on _EDVISE_AVAILABLE
    assert result_student is (is_edvise_schema_available())


def test_should_use_edvise_schema_edvise_namespace_uses_json_validation() -> None:
    """edvise namespace does not use repo schema; uses JSON-based validation (different columns)."""
    assert should_use_edvise_schema("edvise", ["STUDENT"]) is False
    assert should_use_edvise_schema("edvise", ["COURSE"]) is False


def test_should_use_edvise_schema_normalizes_model_names_to_uppercase() -> None:
    """Lowercase or mixed-case model names are normalized so single STUDENT/COURSE still match."""
    if not is_edvise_schema_available():
        pytest.skip("edvise package not installed")
    assert should_use_edvise_schema("pdp", ["student"]) is True
    assert should_use_edvise_schema("pdp", ["course"]) is True
    assert should_use_edvise_schema("pdp", ["Student"]) is True


def test_get_edvise_schema_for_models_returns_none_for_multi_model() -> None:
    """Multiple models should return None."""
    assert get_edvise_schema_for_models(["STUDENT", "COURSE"]) is None


def test_get_edvise_schema_for_models_returns_none_for_empty() -> None:
    """Empty model list should return None."""
    assert get_edvise_schema_for_models([]) is None


def test_get_edvise_schema_for_models_returns_none_for_other_model() -> None:
    """SEMESTER alone should return None."""
    assert get_edvise_schema_for_models(["SEMESTER"]) is None


def test_get_edvise_schema_for_models_returns_class_when_available() -> None:
    """When edvise is available, STUDENT returns cohort schema, COURSE returns course schema."""
    if not is_edvise_schema_available():
        pytest.skip("edvise package not installed")
    cohort_schema = get_edvise_schema_for_models(["STUDENT"])
    course_schema = get_edvise_schema_for_models(["COURSE"])
    assert cohort_schema is not None
    assert course_schema is not None
    assert cohort_schema.__name__ == "RawPDPCohortDataSchema"
    assert course_schema.__name__ == "RawPDPCourseDataSchema"


def test_get_edvise_schema_for_models_normalizes_lowercase_model_names() -> None:
    """Lowercase model names are normalized so get_edvise_schema_for_models still returns schema."""
    if not is_edvise_schema_available():
        pytest.skip("edvise package not installed")
    cohort = get_edvise_schema_for_models(["student"])
    course = get_edvise_schema_for_models(["course"])
    assert cohort is not None and cohort.__name__ == "RawPDPCohortDataSchema"
    assert course is not None and course.__name__ == "RawPDPCourseDataSchema"


def test_normalize_failure_cases_for_formatter_returns_empty_for_none() -> None:
    """None input should return empty list."""
    assert _normalize_failure_cases_for_formatter(None) == []


def test_normalize_failure_cases_for_formatter_keeps_expected_keys() -> None:
    """Output records should have column, index, check, failure_case."""
    mock_df = MagicMock()
    mock_df.to_dict.return_value = [
        {"column": "cohort_term", "index": 0, "check": "isin", "failure_case": "Fall"},
        {"column": "gpa", "index": 2, "check": "ge", "failure_case": 5.0},
    ]
    result = _normalize_failure_cases_for_formatter(mock_df)
    assert len(result) == 2
    for record in result:
        assert "column" in record
        assert "index" in record
        assert "check" in record
        assert "failure_case" in record
    assert result[0]["column"] == "cohort_term"
    assert result[0]["failure_case"] == "Fall"
    assert result[1]["index"] == 2


def test_normalize_failure_cases_for_formatter_handles_failure_cases_key() -> None:
    """Some Pandera versions may use failure_cases (plural); we normalize to failure_case."""
    mock_df = MagicMock()
    mock_df.to_dict.return_value = [
        {"column": "x", "index": 0, "check": "gt", "failure_cases": 10},
    ]
    result = _normalize_failure_cases_for_formatter(mock_df)
    assert len(result) == 1
    assert result[0]["failure_case"] == 10


def test_extract_missing_required_returns_empty_for_none_failure_cases() -> None:
    """When failure_cases is None, return empty list."""
    err = MagicMock()
    err.failure_cases = None
    assert _extract_missing_required_from_pandera_error(err) == []


def test_extract_missing_required_does_not_treat_value_checks_as_missing() -> None:
    """Value-check failures (e.g. isin) must not be reported as missing_required."""
    err = MagicMock()
    err.failure_cases = pd.DataFrame(
        [
            {
                "column": "cohort_term",
                "check": "isin",
                "index": 0,
                "failure_case": "Fall",
            },
        ]
    )
    assert _extract_missing_required_from_pandera_error(err) == []


def test_extract_missing_required_includes_only_missing_column_checks() -> None:
    """Only rows with check indicating missing column are returned."""
    err = MagicMock()
    err.failure_cases = pd.DataFrame(
        [
            {"column": "cohort_term", "check": "isin", "index": 0},
            {"column": "other_col", "check": "column_in_dataframe", "index": -1},
        ]
    )
    result = _extract_missing_required_from_pandera_error(err)
    assert result == ["other_col"]


def test_get_edvise_schema_for_upload_single_entry_point() -> None:
    """get_edvise_schema_for_upload is the single check: None = use JSON path, else run repo schema (PDP only)."""
    assert get_edvise_schema_for_upload("", ["STUDENT"]) is None
    assert get_edvise_schema_for_upload("pdp", ["STUDENT", "COURSE"]) is None
    assert get_edvise_schema_for_upload("edvise", ["COURSE"]) is None
    assert get_edvise_schema_for_upload("edvise", ["STUDENT"]) is None
    if is_edvise_schema_available():
        assert get_edvise_schema_for_upload("pdp", ["STUDENT"]) is not None
        assert get_edvise_schema_for_upload("pdp", ["COURSE"]) is not None
    assert get_edvise_schema_for_upload("other-uuid", ["STUDENT"]) is None


def test_get_edvise_schema_for_upload_rejects_non_list_model_list() -> None:
    """When model_list is not a list (e.g. wrong type), return None to fall back to JSON validation."""
    assert (
        get_edvise_schema_for_upload("pdp", None) is None
    )  # None is allowed, treated as []
    assert get_edvise_schema_for_upload("pdp", "STUDENT") is None  # str is not a list
    assert get_edvise_schema_for_upload("pdp", {"STUDENT"}) is None  # set is not a list


def test_pdp_edvise_namespaces_pdp_only_uses_repo_schema() -> None:
    """Only PDP uses edvise repo schema; edvise extension uses JSON validation."""
    assert "pdp" in PDP_EDVISE_NAMESPACES
    assert "edvise" not in PDP_EDVISE_NAMESPACES
    assert len(PDP_EDVISE_NAMESPACES) == 1
