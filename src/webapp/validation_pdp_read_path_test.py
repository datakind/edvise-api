"""Tests for PDP validation path that uses edvise read_raw_pdp_* (single source of truth)."""

import io
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from edvise.dataio.pdp_cohort_converters import converter_func_cohort

from src.webapp.validation import (
    HardValidationError,
    _path_for_edvise_read,
    _read_pdp_course_edvise,
    _validate_pdp_with_edvise_read,
    validate_file_reader,
)


# --------------------------------------------------------------------------- #
# PDP path routing (validate_file_reader calls _validate_pdp_with_edvise_read)
# --------------------------------------------------------------------------- #


def test_validate_file_reader_pdp_student_calls_edvise_read_path(
    tmp_path: Path,
) -> None:
    """When institution_id is pdp and allowed_schema is [STUDENT], PDP edvise-read path is used."""
    csv_path = tmp_path / "cohort.csv"
    pd.DataFrame({"x": [1]}).to_csv(csv_path, index=False)

    with (
        patch(
            "src.webapp.validation._compute_model_list_and_merged_specs",
            return_value=(
                ["STUDENT"],
                {"student_id": {"dtype": "string", "required": True}},
            ),
        ),
        patch(
            "src.webapp.validation.pdp_edvise.get_edvise_schema_for_upload",
            return_value=object(),  # non-None so PDP path is taken
        ),
        patch(
            "src.webapp.validation._validate_pdp_with_edvise_read",
            return_value={
                "validation_status": "passed",
                "schemas": ["STUDENT"],
                "missing_optional": [],
                "unknown_extra_columns": [],
                "normalized_df": pd.DataFrame({"student_id": ["s1"]}),
            },
        ) as mock_pdp,
    ):
        result = validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            base_schema={"base": {"data_models": {}}},
            inst_schema={"institutions": {"pdp": {"data_models": {}}}},
            institution_id="pdp",
        )
        assert result["validation_status"] == "passed"
        assert result["schemas"] == ["STUDENT"]
        assert result["normalized_df"] is not None
        assert list(result["normalized_df"]["student_id"]) == ["s1"]
        mock_pdp.assert_called_once()
        # _validate_pdp_with_edvise_read(filename, enc, model_list, institution_id) – positional
        call_args = mock_pdp.call_args[0]
        assert call_args[2] == ["STUDENT"]
        assert call_args[3] == "pdp"


def test_validate_file_reader_pdp_course_calls_edvise_read_path(tmp_path: Path) -> None:
    """When institution_id is pdp and allowed_schema is [COURSE], PDP edvise-read path is used."""
    csv_path = tmp_path / "course.csv"
    pd.DataFrame({"y": [1]}).to_csv(csv_path, index=False)

    with (
        patch(
            "src.webapp.validation._compute_model_list_and_merged_specs",
            return_value=(
                ["COURSE"],
                {"course_id": {"dtype": "string", "required": True}},
            ),
        ),
        patch(
            "src.webapp.validation.pdp_edvise.get_edvise_schema_for_upload",
            return_value=object(),
        ),
        patch(
            "src.webapp.validation._validate_pdp_with_edvise_read",
            return_value={
                "validation_status": "passed",
                "schemas": ["COURSE"],
                "missing_optional": [],
                "unknown_extra_columns": [],
                "normalized_df": pd.DataFrame({"course_id": ["c1"]}),
            },
        ) as mock_pdp,
    ):
        result = validate_file_reader(
            str(csv_path),
            ["COURSE"],
            base_schema={"base": {"data_models": {}}},
            inst_schema={"institutions": {"pdp": {"data_models": {}}}},
            institution_id="pdp",
        )
        assert result["validation_status"] == "passed"
        assert result["schemas"] == ["COURSE"]
        mock_pdp.assert_called_once()
        assert mock_pdp.call_args[0][2] == ["COURSE"]


# --------------------------------------------------------------------------- #
# _path_for_edvise_read
# --------------------------------------------------------------------------- #


def test_path_for_edvise_read_with_path_yields_same_path(tmp_path: Path) -> None:
    """When filename is a path, context manager yields that path (no temp file)."""
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2")
    with _path_for_edvise_read(str(path), "utf-8") as resolved:
        assert resolved == str(path)
        assert Path(resolved).exists()


def test_path_for_edvise_read_with_file_like_yields_temp_path_and_cleans_up() -> None:
    """When filename is file-like, yields path to temp file; temp file is removed on exit."""
    content = "col1,col2\n1,2"
    stream = io.StringIO(content)
    with _path_for_edvise_read(stream, "utf-8") as resolved:
        assert Path(resolved).exists()
        assert Path(resolved).read_text() == content
        temp_path = Path(resolved)
    assert not temp_path.exists()


def test_path_for_edvise_read_file_like_read_failure_raises_hard_validation_error() -> (
    None
):
    """When file-like read() raises, HardValidationError is raised with context."""

    # Use a real file-like that is not str/PathLike so we hit the read() path
    class BrokenReader(io.BytesIO):
        def read(self, *args: object, **kwargs: object) -> bytes:
            raise OSError("read failed")

    broken = BrokenReader(b"x")
    with pytest.raises(HardValidationError, match="Could not read file") as exc_info:
        with _path_for_edvise_read(broken, "utf-8") as _:
            pass
    assert exc_info.value.schema_errors is not None
    assert "read failed" in str(exc_info.value.failure_cases) or "read failed" in str(
        exc_info.value.schema_errors
    )


# --------------------------------------------------------------------------- #
# _validate_pdp_with_edvise_read
# --------------------------------------------------------------------------- #


def test_validate_pdp_with_edvise_read_student_success_returns_normalized_df(
    tmp_path: Path,
) -> None:
    """When STUDENT and read_raw_pdp_cohort_data returns df, result contains normalized_df."""
    csv_path = tmp_path / "cohort.csv"
    csv_path.write_text("student_id,cohort\ns1,2016")
    expected_df = pd.DataFrame({"student_id": ["s1"], "cohort": ["2016"]})

    with patch(
        "src.webapp.validation.read_raw_pdp_cohort_data",
        return_value=expected_df,
    ):
        result = _validate_pdp_with_edvise_read(
            str(csv_path),
            enc="utf-8",
            model_list=["STUDENT"],
            institution_id="pdp",
        )
    assert result["validation_status"] == "passed"
    assert result["schemas"] == ["STUDENT"]
    assert result["normalized_df"] is not None
    pd.testing.assert_frame_equal(result["normalized_df"], expected_df)


def test_validate_pdp_with_edvise_read_schema_errors_converted_to_hard_validation_error(
    tmp_path: Path,
) -> None:
    """When edvise schema validation raises SchemaErrors, HardValidationError is raised."""
    from edvise.data_audit.schemas.raw_cohort import RawPDPCohortDataSchema

    csv_path = tmp_path / "cohort.csv"
    csv_path.write_text("a,b\n1,2")

    # Obtain a real SchemaErrors by validating a dataframe that fails schema
    bad_df = pd.DataFrame(
        {"institution_id": [1], "cohort": ["x"], "student_guid": ["y"]}
    )
    schema_err_to_raise: SchemaErrors | None = None
    try:
        RawPDPCohortDataSchema.validate(bad_df, lazy=True)  # type: ignore[attr-defined]
    except SchemaErrors as real_err:
        schema_err_to_raise = real_err
    else:
        pytest.skip(
            "RawPDPCohortDataSchema did not raise SchemaErrors for minimal bad df"
        )
    assert schema_err_to_raise is not None

    with patch(
        "src.webapp.validation.read_raw_pdp_cohort_data",
        side_effect=schema_err_to_raise,
    ):
        with pytest.raises(HardValidationError) as exc_info:
            _validate_pdp_with_edvise_read(
                str(csv_path),
                enc="utf-8",
                model_list=["STUDENT"],
                institution_id="pdp",
            )
    err = exc_info.value
    assert err.schema_errors is not None or err.failure_cases is not None


def test_validate_pdp_with_edvise_read_invalid_model_set_raises_hard_validation_error(
    tmp_path: Path,
) -> None:
    """When model_set is not STUDENT or COURSE, HardValidationError is raised."""
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("x\n1")

    with patch(
        "src.webapp.validation.read_raw_pdp_cohort_data", return_value=pd.DataFrame()
    ):
        with pytest.raises(
            HardValidationError, match="PDP single-model expected"
        ) as exc_info:
            _validate_pdp_with_edvise_read(
                str(csv_path),
                enc="utf-8",
                model_list=["UNKNOWN"],
                institution_id="pdp",
            )
    assert "models=" in str(exc_info.value.schema_errors)


def test_validate_pdp_with_edvise_read_accepts_file_like() -> None:
    """File-like input is read and passed to edvise read (temp file created and removed)."""
    content = "student_id,cohort\ns1,2016"
    stream = io.StringIO(content)
    expected_df = pd.DataFrame({"student_id": ["s1"], "cohort": ["2016"]})

    with patch(
        "src.webapp.validation.read_raw_pdp_cohort_data",
        return_value=expected_df,
    ) as mock_read:
        result = _validate_pdp_with_edvise_read(
            stream,
            enc="utf-8",
            model_list=["STUDENT"],
            institution_id="pdp",
        )
    assert result["validation_status"] == "passed"
    assert result["normalized_df"] is not None
    pd.testing.assert_frame_equal(result["normalized_df"], expected_df)
    mock_read.assert_called_once()
    # Edvise read was given a path (temp file when file-like); keyword is file_path
    assert "file_path" in mock_read.call_args[1]
    assert isinstance(mock_read.call_args[1]["file_path"], str)
    # Cohort validation uses converter_func_cohort by default (filters DE/DS/SE)
    assert mock_read.call_args[1]["converter_func"] is converter_func_cohort


def test_validate_pdp_with_edvise_read_student_uses_custom_cohort_converter_when_provided(
    tmp_path: Path,
) -> None:
    """When pdp_cohort_converter_func is provided, it is passed to read_raw_pdp_cohort_data."""
    csv_path = tmp_path / "cohort.csv"
    csv_path.write_text("student_id,cohort\ns1,2016")
    expected_df = pd.DataFrame({"student_id": ["s1"], "cohort": ["2016"]})
    custom_converter = lambda df: df  # noqa: E731

    with patch(
        "src.webapp.validation.read_raw_pdp_cohort_data",
        return_value=expected_df,
    ) as mock_read:
        _validate_pdp_with_edvise_read(
            str(csv_path),
            enc="utf-8",
            model_list=["STUDENT"],
            institution_id="pdp",
            pdp_cohort_converter_func=custom_converter,
        )
    mock_read.assert_called_once()
    assert mock_read.call_args[1]["converter_func"] is custom_converter


def test_validate_pdp_with_edvise_read_non_callable_cohort_converter_raises_hard_validation_error(
    tmp_path: Path,
) -> None:
    """When pdp_cohort_converter_func is not callable, HardValidationError is raised (API returns 400)."""
    csv_path = tmp_path / "cohort.csv"
    csv_path.write_text("student_id,cohort\ns1,2016")

    with pytest.raises(HardValidationError, match="callable"):
        _validate_pdp_with_edvise_read(
            str(csv_path),
            enc="utf-8",
            model_list=["STUDENT"],
            institution_id="pdp",
            pdp_cohort_converter_func=cast(Any, "not a function"),
        )


def test_validate_pdp_with_edvise_read_non_callable_course_converter_raises_hard_validation_error(
    tmp_path: Path,
) -> None:
    """When pdp_course_converter_func is not callable, HardValidationError is raised (API returns 400)."""
    csv_path = tmp_path / "course.csv"
    csv_path.write_text("student_id,academic_year\ns1,2020")

    with pytest.raises(HardValidationError, match="callable"):
        _validate_pdp_with_edvise_read(
            str(csv_path),
            enc="utf-8",
            model_list=["COURSE"],
            institution_id="pdp",
            pdp_course_converter_func=cast(Any, 123),
        )


# --------------------------------------------------------------------------- #
# _read_pdp_course_edvise
# --------------------------------------------------------------------------- #


def test_read_pdp_course_edvise_success_returns_dataframe() -> None:
    """When read_raw_pdp_course_data returns a df, _read_pdp_course_edvise returns it."""
    expected = pd.DataFrame({"course_id": ["c1"], "credits": [3]})
    with patch(
        "src.webapp.validation.read_raw_pdp_course_data",
        return_value=expected,
    ):
        result = _read_pdp_course_edvise("/nonexistent/path.csv")
    pd.testing.assert_frame_equal(result, expected)


def test_read_pdp_course_edvise_all_attempts_fail_raises_hard_validation_error() -> (
    None
):
    """When all converter/format attempts raise ValueError, HardValidationError is raised."""
    with patch(
        "src.webapp.validation.read_raw_pdp_course_data",
        side_effect=ValueError("bad datetime"),
    ):
        with pytest.raises(HardValidationError, match="datetime format") as exc_info:
            _read_pdp_course_edvise("/nonexistent/path.csv")
    assert (
        "datetime" in str(exc_info.value.schema_errors).lower()
        or "format" in str(exc_info.value.schema_errors).lower()
    )


def test_read_pdp_course_edvise_falls_back_after_custom_converter_fails() -> None:
    """When custom converter fails all datetime formats, default PDP converter is used."""
    expected = pd.DataFrame({"course_id": ["c1"]})
    with patch(
        "src.webapp.validation.read_raw_pdp_course_data",
        side_effect=[
            ValueError("bad datetime"),
            ValueError("bad datetime"),
            ValueError("bad datetime"),
            expected,
        ],
    ) as mock_read:
        result = _read_pdp_course_edvise(
            "/path.csv",
            course_converter_func=lambda df: df,  # noqa: ARG005
        )
    pd.testing.assert_frame_equal(result, expected)
    assert mock_read.call_count == 4


def test_read_pdp_course_edvise_custom_converter_tried_first() -> None:
    """When course_converter_func is provided, it is tried before default converters."""
    expected = pd.DataFrame({"course_id": ["c1"]})
    custom_converter = lambda df: df  # noqa: E731
    with patch(
        "src.webapp.validation.read_raw_pdp_course_data",
        return_value=expected,
    ) as mock_read:
        result = _read_pdp_course_edvise(
            "/path.csv", course_converter_func=custom_converter
        )
    pd.testing.assert_frame_equal(result, expected)
    # Custom converter should have been used (first call succeeds)
    assert mock_read.call_count == 1
    assert mock_read.call_args[1]["converter_func"] is custom_converter
