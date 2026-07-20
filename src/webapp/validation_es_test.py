"""Tests for ES upload validation: bronze dataio converters + read_raw_es_* + Pandera."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import pandas as pd
import pytest
from pandera.errors import SchemaError

from src.webapp.validation import (
    HardValidationError,
    _import_dataio_module_isolated,
    load_es_converters_from_bronze,
    validate_file_reader,
)


def _dataio_source(cohort_marker: str, course_marker: str) -> bytes:
    return f'''
import pandas as pd

SCHOOL_MARKER = "{cohort_marker}"

def converter_func_cohort(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "student_guid" in out.columns and "learner_id" not in out.columns:
        out = out.rename(columns={{"student_guid": "learner_id"}})
    out.attrs["school_marker"] = "{cohort_marker}"
    return out

def converter_func_course(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.attrs["school_marker"] = "{course_marker}"
    return out
'''.encode("utf-8")


def test_load_es_converters_applies_cohort_rename(tmp_path: Path) -> None:
    """Converter from bronze dataio.py is loaded and renames columns like the ES job."""
    stream = io.BytesIO(_dataio_source("school_a", "school_a_course"))

    with patch(
        "src.webapp.databricks.DatabricksControl.download_bronze_training_inputs_file",
        return_value=stream,
    ):
        cohort_fn, course_fn = load_es_converters_from_bronze("school_a")

    assert callable(cohort_fn)
    assert callable(course_fn)
    df = pd.DataFrame({"student_guid": ["s1"], "entry_year": ["2024"]})
    converted = cohort_fn(df)
    assert "learner_id" in converted.columns
    assert "student_guid" not in converted.columns
    assert converted.attrs["school_marker"] == "school_a"


def test_load_es_converters_missing_dataio_soft_fallback() -> None:
    """Missing bronze dataio.py soft-falls back to no converters (job parity)."""
    with patch(
        "src.webapp.databricks.DatabricksControl.download_bronze_training_inputs_file",
        side_effect=ValueError(
            "Failed to download bronze training_inputs file: missing"
        ),
    ):
        cohort_fn, course_fn = load_es_converters_from_bronze("school_missing")

    assert cohort_fn is None
    assert course_fn is None


def test_es_converter_import_isolation_across_institutions() -> None:
    """Two institutions with different dataio.py never share converters or bare dataio."""

    def download_side_effect(inst_name: str, relative_path: str = "dataio.py") -> Any:
        assert relative_path == "dataio.py"
        if inst_name == "inst_alpha":
            return io.BytesIO(_dataio_source("inst_alpha", "inst_alpha_c"))
        if inst_name == "inst_beta":
            return io.BytesIO(_dataio_source("inst_beta", "inst_beta_c"))
        raise AssertionError(f"unexpected institution {inst_name}")

    with patch(
        "src.webapp.databricks.DatabricksControl.download_bronze_training_inputs_file",
        side_effect=download_side_effect,
    ):
        cohort_a, _ = load_es_converters_from_bronze("inst_alpha")
        cohort_b, _ = load_es_converters_from_bronze("inst_beta")

    assert "dataio" not in sys.modules
    assert callable(cohort_a) and callable(cohort_b)
    assert cohort_a is not cohort_b

    df = pd.DataFrame({"student_guid": ["x"]})
    assert cohort_a(df).attrs["school_marker"] == "inst_alpha"
    assert cohort_b(df).attrs["school_marker"] == "inst_beta"

    bronze_modules = [
        name for name in sys.modules if name.startswith("es_bronze_dataio_")
    ]
    assert len(bronze_modules) >= 2


def test_import_dataio_module_isolated_unique_names(tmp_path: Path) -> None:
    """spec_from_file_location uses unique module names per institution/request."""
    path = tmp_path / "dataio.py"
    path.write_text("MARKER = 'ok'\n", encoding="utf-8")
    mod1 = _import_dataio_module_isolated("school_one", str(path))
    mod2 = _import_dataio_module_isolated("school_two", str(path))
    assert mod1 is not mod2
    assert mod1.__name__ != mod2.__name__
    assert mod1.__name__.startswith("es_bronze_dataio_school_one_")
    assert mod2.__name__.startswith("es_bronze_dataio_school_two_")
    assert "dataio" not in sys.modules


def test_es_with_converter_passed_to_read_raw_es(tmp_path: Path) -> None:
    """ES validate_file_reader passes the loaded cohort converter into read_raw_es_*."""
    csv_path = tmp_path / "student.csv"
    csv_path.write_text("student_guid,entry_year\ns1,2024\n", encoding="utf-8")

    def cohort_converter(df: pd.DataFrame) -> pd.DataFrame:
        return df.rename(columns={"student_guid": "learner_id"})

    captured: dict[str, Any] = {}

    def fake_read(**kwargs: Any) -> pd.DataFrame:
        captured["converter_func"] = kwargs.get("converter_func")
        return pd.DataFrame({"learner_id": ["s1"]})

    with (
        patch(
            "src.webapp.validation.load_es_converters_from_bronze",
            return_value=(cohort_converter, None),
        ),
        patch(
            "src.webapp.validation.read_raw_es_cohort_data",
            side_effect=fake_read,
        ),
    ):
        result = validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            institution_id="edvise",
            institution_identifier="edvise_school",
        )

    assert result["validation_status"] == "passed"
    assert captured["converter_func"] is cohort_converter


def test_es_missing_dataio_validates_without_converter(tmp_path: Path) -> None:
    """Missing dataio still validates via read_raw_es_* with converter_func=None."""
    csv_path = tmp_path / "student.csv"
    csv_path.write_text("learner_id,entry_year\ns1,2024\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_read(**kwargs: Any) -> pd.DataFrame:
        captured["converter_func"] = kwargs.get("converter_func")
        return pd.DataFrame({"learner_id": ["s1"]})

    with (
        patch(
            "src.webapp.validation.load_es_converters_from_bronze",
            return_value=(None, None),
        ) as mock_load,
        patch(
            "src.webapp.validation.read_raw_es_cohort_data",
            side_effect=fake_read,
        ),
    ):
        result = validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            institution_id="edvise",
            institution_identifier="edvise_school",
        )

    mock_load.assert_called_once_with("edvise_school")
    assert result["validation_status"] == "passed"
    assert captured["converter_func"] is None


def test_es_pandera_failure_returns_hard_validation_error(tmp_path: Path) -> None:
    """Pandera SchemaErrors from read_raw_es_* surface as HardValidationError."""
    csv_path = tmp_path / "student.csv"
    csv_path.write_text("learner_id\ns1\n", encoding="utf-8")

    # SchemaError requires schema/data kwargs; build a bare instance for the raise path.
    pandera_err = SchemaError.__new__(SchemaError)
    Exception.__init__(pandera_err, "missing required columns")

    with (
        patch(
            "src.webapp.validation.load_es_converters_from_bronze",
            return_value=(None, None),
        ),
        patch(
            "src.webapp.validation.read_raw_es_cohort_data",
            side_effect=pandera_err,
        ),
        patch(
            "src.webapp.validation.pdp_edvise._convert_schema_errors_to_hard_validation_error",
            return_value=HardValidationError(
                schema_errors="ES pandera failed",
                failure_cases=[
                    {"column": "entry_year", "check": "column_in_dataframe"}
                ],
            ),
        ),
    ):
        with pytest.raises(HardValidationError) as exc_info:
            validate_file_reader(
                str(csv_path),
                ["STUDENT"],
                institution_id="edvise",
                institution_identifier="edvise_school",
            )

    assert "ES pandera failed" in str(exc_info.value.schema_errors)


def test_es_converter_runtime_error_fails_closed(tmp_path: Path) -> None:
    """Converter exceptions during validation become HardValidationError (fail closed)."""
    csv_path = tmp_path / "student.csv"
    csv_path.write_text("learner_id\ns1\n", encoding="utf-8")

    def bad_converter(df: pd.DataFrame) -> pd.DataFrame:
        raise RuntimeError("converter blew up")

    def fake_read(**kwargs: Any) -> pd.DataFrame:
        converter: Callable[[pd.DataFrame], pd.DataFrame] | None = kwargs.get(
            "converter_func"
        )
        assert converter is not None
        return converter(pd.DataFrame({"learner_id": ["s1"]}))

    with (
        patch(
            "src.webapp.validation.load_es_converters_from_bronze",
            return_value=(bad_converter, None),
        ),
        patch(
            "src.webapp.validation.read_raw_es_cohort_data",
            side_effect=fake_read,
        ),
    ):
        with pytest.raises(HardValidationError) as exc_info:
            validate_file_reader(
                str(csv_path),
                ["STUDENT"],
                institution_id="edvise",
                institution_identifier="edvise_school",
            )

    assert "converter blew up" in str(exc_info.value)


def test_pdp_routing_unchanged_does_not_load_es_converters(tmp_path: Path) -> None:
    """PDP uploads do not fetch bronze ES dataio converters."""
    csv_path = tmp_path / "cohort.csv"
    pd.DataFrame({"x": [1]}).to_csv(csv_path, index=False)

    with (
        patch(
            "src.webapp.validation.load_es_converters_from_bronze",
        ) as mock_load,
        patch(
            "src.webapp.validation._validate_pdp_with_edvise_read",
            return_value={
                "validation_status": "passed",
                "schemas": ["STUDENT"],
                "missing_optional": [],
                "unknown_extra_columns": [],
                "normalized_df": pd.DataFrame({"student_id": ["s1"]}),
            },
        ),
    ):
        result = validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            institution_id="pdp",
        )

    assert result["validation_status"] == "passed"
    mock_load.assert_not_called()
