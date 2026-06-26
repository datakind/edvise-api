from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.webapp.validation import HardValidationError, validate_file_reader


@pytest.fixture
def tmp_csv_file(tmp_path: Path) -> str:
    df = pd.DataFrame({"foo_col": [1, 2], "bar_col": ["a", "b"]})
    file_path = tmp_path / "test.csv"
    df.to_csv(file_path, index=False)
    return str(file_path)


def test_validate_file_reader_legacy_accepts_any_format(tmp_path: Path) -> None:
    """Legacy institutions accept any CSV format after encoding/read checks."""
    csv_path = tmp_path / "any_columns.csv"
    csv_path.write_text("a,b,c\n1,2,hello\n3,4,world", encoding="utf-8")

    result = validate_file_reader(
        str(csv_path),
        ["STUDENT"],
        institution_id="legacy",
    )

    assert result["validation_status"] == "passed"
    assert result["schemas"] == ["STUDENT"]
    df = result["normalized_df"]
    assert df is not None
    assert list(df.columns) == ["a", "b", "c"]
    assert len(df) == 2


def test_validate_file_reader_legacy_defaults_empty_models_to_unknown(
    tmp_path: Path,
) -> None:
    """Legacy validation preserves UNKNOWN when no schema models are provided."""
    csv_path = tmp_path / "unknown.csv"
    csv_path.write_text("a,b\n1,2", encoding="utf-8")

    result = validate_file_reader(
        str(csv_path),
        [],
        institution_id="legacy",
    )

    assert result["validation_status"] == "passed"
    assert result["schemas"] == ["UNKNOWN"]
    assert result["normalized_df"] is not None


def test_validate_file_reader_legacy_accepts_student_id_column(tmp_path: Path) -> None:
    """Legacy institutions allow student_id as a de-identified identifier column."""
    csv_path = tmp_path / "with_student_id.csv"
    csv_path.write_text(
        "student_id,grade,term\nabc123,A,Fall\nxyz789,B,Spring", encoding="utf-8"
    )

    result = validate_file_reader(
        str(csv_path),
        ["STUDENT"],
        institution_id="legacy",
    )

    assert result["validation_status"] == "passed"
    assert list(result["normalized_df"].columns) == ["student_id", "grade", "term"]


def test_validate_file_reader_legacy_accepts_course_metadata_name_columns(
    tmp_path: Path,
) -> None:
    """Legacy course files allow *_name columns for course metadata (not people)."""
    csv_path = tmp_path / "course_component.csv"
    csv_path.write_text(
        "datakind_id,class_course_name,primary_class_section_name,component_name,course_name,grade\n"
        "dk-1,Intro Math,Section 001,Lecture,Intro Math,A\n",
        encoding="utf-8",
    )

    result = validate_file_reader(
        str(csv_path),
        ["COURSE"],
        institution_id="legacy",
    )

    assert result["validation_status"] == "passed"
    assert list(result["normalized_df"].columns) == [
        "datakind_id",
        "class_course_name",
        "primary_class_section_name",
        "component_name",
        "course_name",
        "grade",
    ]


def test_validate_file_reader_legacy_header_only_csv_passes(tmp_path: Path) -> None:
    """Legacy institutions accept header-only CSVs and still return the DataFrame."""
    csv_path = tmp_path / "header_only.csv"
    csv_path.write_text("col_a,col_b,col_c\n", encoding="utf-8")

    result = validate_file_reader(
        str(csv_path),
        ["STUDENT"],
        institution_id="legacy",
    )

    assert result["validation_status"] == "passed"
    df = result["normalized_df"]
    assert list(df.columns) == ["col_a", "col_b", "col_c"]
    assert len(df) == 0


def test_validate_file_reader_legacy_rejects_header_only_pii_columns(
    tmp_path: Path,
) -> None:
    """Legacy validation rejects PII-looking headers even when there are no rows."""
    csv_path = tmp_path / "header_only_pii.csv"
    csv_path.write_text("email,ssn\n", encoding="utf-8")

    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            institution_id="legacy",
        )

    err = exc_info.value
    assert "PII" in (err.schema_errors or "")
    assert "email" in (err.failure_cases or [])
    assert "ssn" in (err.failure_cases or [])


def test_validate_file_reader_legacy_rejects_pii_columns(tmp_path: Path) -> None:
    """Legacy validation rejects files with PII-looking column names."""
    csv_path = tmp_path / "with_pii.csv"
    csv_path.write_text(
        "id,email,score\n1,user@example.com,85\n2,other@test.org,90", encoding="utf-8"
    )

    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            institution_id="legacy",
        )

    err = exc_info.value
    assert "PII" in (err.schema_errors or "")
    assert "email" in (err.failure_cases or [])


def test_validate_file_reader_legacy_rejects_multiple_pii_columns(
    tmp_path: Path,
) -> None:
    """Legacy validation reports all detected PII-looking column names."""
    csv_path = tmp_path / "multi_pii.csv"
    csv_path.write_text(
        "first_name,last_name,grade\nAlice,Smith,A\nBob,Jones,B",
        encoding="utf-8",
    )

    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            institution_id="legacy",
        )

    err = exc_info.value
    assert "PII" in (err.schema_errors or "")
    failure_cases = err.failure_cases or []
    assert "first_name" in failure_cases
    assert "last_name" in failure_cases


def test_validate_file_reader_pdp_student_routes_to_repo_validation(
    tmp_csv_file: str,
) -> None:
    """PDP STUDENT uploads use repo-backed validation, not JSON schema docs."""
    expected_df = pd.DataFrame({"student_id": ["s1"]})

    with patch(
        "src.webapp.validation._validate_pdp_with_edvise_read",
        return_value={
            "validation_status": "passed",
            "schemas": ["STUDENT"],
            "missing_optional": [],
            "unknown_extra_columns": [],
            "normalized_df": expected_df,
        },
    ) as mock_validate:
        result = validate_file_reader(
            tmp_csv_file,
            ["STUDENT"],
            institution_id="pdp",
        )

    assert result["validation_status"] == "passed"
    assert result["normalized_df"] is expected_df
    mock_validate.assert_called_once()
    assert mock_validate.call_args.args[2] == ["STUDENT"]
    assert mock_validate.call_args.args[3] == "pdp"


def test_validate_file_reader_edvise_course_routes_to_repo_validation(
    tmp_csv_file: str,
) -> None:
    """Edvise COURSE uploads use repo-backed validation, not JSON schema docs."""
    expected_df = pd.DataFrame({"course_id": ["c1"]})

    with patch(
        "src.webapp.validation._validate_edvise_with_repo_schema",
        return_value={
            "validation_status": "passed",
            "schemas": ["COURSE"],
            "missing_optional": [],
            "unknown_extra_columns": [],
            "normalized_df": expected_df,
        },
    ) as mock_validate:
        result = validate_file_reader(
            tmp_csv_file,
            ["COURSE"],
            institution_id="edvise",
        )

    assert result["validation_status"] == "passed"
    assert result["normalized_df"] is expected_df
    mock_validate.assert_called_once()
    assert mock_validate.call_args.args[2] == ["COURSE"]
    assert mock_validate.call_args.args[3] == "edvise"


def test_validate_file_reader_pdp_rejects_unsupported_model_set(
    tmp_csv_file: str,
) -> None:
    """PDP uploads no longer fall back to API-local JSON validation."""
    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            tmp_csv_file,
            ["SEMESTER"],
            institution_id="pdp",
        )

    assert "edvise repo" in str(exc_info.value)
    assert "SEMESTER" in str(exc_info.value)


def test_validate_file_reader_edvise_rejects_multi_model_upload(
    tmp_csv_file: str,
) -> None:
    """Edvise multi-model uploads are rejected instead of using old JSON validation."""
    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            tmp_csv_file,
            ["STUDENT", "COURSE"],
            institution_id="edvise",
        )

    assert "edvise repo" in str(exc_info.value)
    assert "STUDENT, COURSE" in str(exc_info.value)
