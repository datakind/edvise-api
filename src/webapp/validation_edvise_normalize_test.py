"""Unit tests for Edvise → PDP normalization (validation_edvise_normalize)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.webapp.validation_edvise_normalize import (
    SECTION_ID_PLACEHOLDER_WIDTH,
    normalize_edvise_dataframe_to_pdp,
)


def test_normalize_edvise_dataframe_to_pdp_returns_unchanged_for_empty_model_list() -> None:
    """Empty model_list should return DataFrame and display map unchanged."""
    df = pd.DataFrame({"student_id": ["a"], "cohort_year": ["2023-24"]})
    out_df, display = normalize_edvise_dataframe_to_pdp(df, [])
    pd.testing.assert_frame_equal(out_df, df)
    assert display == {}


def test_normalize_edvise_dataframe_to_pdp_returns_unchanged_for_non_student_course() -> None:
    """Model list that is not single STUDENT or COURSE should return unchanged."""
    df = pd.DataFrame({"student_id": ["a"]})
    out_df, display = normalize_edvise_dataframe_to_pdp(df, ["SEMESTER"])
    pd.testing.assert_frame_equal(out_df, df)
    assert display == {}


def test_normalize_edvise_dataframe_to_pdp_student_renames_cohort_columns() -> None:
    """STUDENT path renames cohort_year to cohort and adds required repo columns."""
    df = pd.DataFrame({
        "student_id": ["s1", "s2"],
        "cohort_year": ["2023-24", "2024-25"],
        "cohort_term": ["Fall", "Spring"],
        "enrollment_type": ["First-time", "Transfer"],
    })
    out_df, display = normalize_edvise_dataframe_to_pdp(
        df, ["STUDENT"], institution_identifier="inst-uuid-123"
    )
    assert "cohort" in out_df.columns
    assert "cohort_year" not in out_df.columns or "cohort" in out_df.columns
    assert list(out_df["cohort"]) == ["2023-24", "2024-25"]
    assert "institution_id" in out_df.columns
    assert list(out_df["institution_id"]) == ["inst-uuid-123", "inst-uuid-123"]
    assert "retention" in out_df.columns
    assert "persistence" in out_df.columns
    assert display.get("cohort") == "cohort_year"
    assert display.get("institution_id") == "institution_id"


def test_normalize_edvise_dataframe_to_pdp_student_normalizes_term_values() -> None:
    """STUDENT path normalizes cohort_term to FALL/WINTER/SPRING/SUMMER."""
    df = pd.DataFrame({
        "student_id": ["s1"],
        "cohort_term": ["  fa  "],
        "enrollment_type": ["First-time"],
    })
    out_df, _ = normalize_edvise_dataframe_to_pdp(df, ["STUDENT"])
    assert list(out_df["cohort_term"]) == ["FALL"]


def test_normalize_edvise_dataframe_to_pdp_student_normalizes_enrollment_type() -> None:
    """STUDENT path normalizes enrollment_type to FIRST-TIME / TRANSFER-IN / RE-ADMIT."""
    df = pd.DataFrame({
        "student_id": ["s1", "s2", "s3"],
        "enrollment_type": ["First-time student", "Transfer", "Re-admit"],
    })
    out_df, _ = normalize_edvise_dataframe_to_pdp(df, ["STUDENT"])
    assert list(out_df["enrollment_type"]) == ["FIRST-TIME", "TRANSFER-IN", "RE-ADMIT"]


def test_normalize_edvise_dataframe_to_pdp_student_prefers_associates_over_certificate() -> None:
    """When both associates and certificate time-to-degree exist, prefer associates and drop certificate column."""
    df = pd.DataFrame({
        "student_id": ["s1"],
        "associates_time_to_degree": [2],
        "certificate_time_to_degree": [1],
    })
    out_df, _ = normalize_edvise_dataframe_to_pdp(df, ["STUDENT"])
    assert "years_to_associates_or_certificate_at_cohort_inst" in out_df.columns
    assert list(out_df["years_to_associates_or_certificate_at_cohort_inst"]) == [2]
    assert "certificate_time_to_degree" not in out_df.columns


def test_normalize_edvise_dataframe_to_pdp_student_uses_empty_string_when_no_institution_identifier() -> None:
    """When institution_identifier is None, institution_id column should be empty string."""
    df = pd.DataFrame({"student_id": ["s1"], "enrollment_type": ["First-time"]})
    out_df, _ = normalize_edvise_dataframe_to_pdp(df, ["STUDENT"], institution_identifier=None)
    assert list(out_df["institution_id"]) == [""]


def test_normalize_edvise_dataframe_to_pdp_course_renames_columns() -> None:
    """COURSE path renames course_credits_* to number_of_credits_* and term_major to term_program_of_study."""
    df = pd.DataFrame({
        "student_id": ["s1"],
        "academic_year": ["2024-25"],
        "academic_term": ["Fall"],
        "course_credits_attempted": [3.0],
        "course_credits_earned": [3.0],
        "term_major": ["Biology"],
    })
    out_df, display = normalize_edvise_dataframe_to_pdp(
        df, ["COURSE"], institution_identifier="inst-1"
    )
    assert "number_of_credits_attempted" in out_df.columns
    assert "number_of_credits_earned" in out_df.columns
    assert "term_program_of_study" in out_df.columns
    assert list(out_df["term_program_of_study"]) == ["Biology"]
    assert display.get("term_program_of_study") == "term_major"


def test_normalize_edvise_dataframe_to_pdp_course_adds_institution_id_and_section_id() -> None:
    """COURSE path adds institution_id and unique section_id placeholders."""
    df = pd.DataFrame({
        "student_id": ["s1", "s2"],
        "academic_year": ["2024-25", "2024-25"],
    })
    out_df, _ = normalize_edvise_dataframe_to_pdp(df, ["COURSE"], institution_identifier="inst-x")
    assert list(out_df["institution_id"]) == ["inst-x", "inst-x"]
    assert "section_id" in out_df.columns
    section_ids = list(out_df["section_id"])
    assert len(section_ids) == 2
    assert section_ids[0] != section_ids[1]
    assert all(len(s) == SECTION_ID_PLACEHOLDER_WIDTH for s in section_ids)


def test_normalize_edvise_dataframe_to_pdp_course_cohort_fill_from_academic_year() -> None:
    """COURSE path fills cohort from first valid academic_year when cohort is missing."""
    df = pd.DataFrame({
        "student_id": ["s1"],
        "academic_year": ["2024-25"],
    })
    out_df, _ = normalize_edvise_dataframe_to_pdp(df, ["COURSE"])
    assert list(out_df["cohort"]) == ["2024-25"]


def test_normalize_edvise_dataframe_to_pdp_course_cohort_fill_empty_when_no_academic_year() -> None:
    """COURSE path uses empty string for cohort when academic_year column is missing or empty."""
    df = pd.DataFrame({"student_id": ["s1"]})
    out_df, _ = normalize_edvise_dataframe_to_pdp(df, ["COURSE"])
    assert list(out_df["cohort"]) == [""]


def test_normalize_edvise_dataframe_to_pdp_adds_missing_schema_columns_when_schema_class_provided() -> None:
    """When schema_class is provided and has to_schema().columns, missing columns are added with pd.NA."""
    mock_schema = MagicMock()
    mock_schema.to_schema.return_value.columns.keys.return_value = [
        "student_id",
        "institution_id",
        "cohort",
        "extra_required_col",
    ]
    df = pd.DataFrame({"student_id": ["s1"], "cohort_year": ["2023-24"], "enrollment_type": ["First-time"]})
    out_df, _ = normalize_edvise_dataframe_to_pdp(
        df, ["STUDENT"], schema_class=mock_schema
    )
    assert "extra_required_col" in out_df.columns
    assert out_df["extra_required_col"].isna().all()


def test_normalize_edvise_dataframe_to_pdp_handles_schema_class_introspection_failure() -> None:
    """When schema_class.to_schema() raises, missing columns are not added (required_columns is [])."""
    mock_schema = MagicMock()
    mock_schema.to_schema.side_effect = AttributeError("no to_schema")
    df = pd.DataFrame({"student_id": ["s1"], "enrollment_type": ["First-time"]})
    out_df, display = normalize_edvise_dataframe_to_pdp(df, ["STUDENT"], schema_class=mock_schema)
    assert "student_id" in out_df.columns
    assert "institution_id" in out_df.columns
    assert display != {}


def test_normalize_edvise_dataframe_to_pdp_display_map_includes_renamed_and_added_columns() -> None:
    """Display map should map PDP column names to Edvise names for error messages."""
    df = pd.DataFrame({
        "student_id": ["s1"],
        "cohort_year": ["2023-24"],
        "enrollment_type": ["First-time"],
    })
    _, display = normalize_edvise_dataframe_to_pdp(df, ["STUDENT"])
    assert display["cohort"] == "cohort_year"
    assert display["student_id"] == "student_id"
    assert display.get("institution_id") == "institution_id"
    assert display.get("retention") == "retention"
    assert display.get("persistence") == "persistence"


def test_normalize_edvise_dataframe_to_pdp_accepts_lowercase_model_names() -> None:
    """Model list with lowercase 'student' or 'course' should be normalized and handled."""
    df = pd.DataFrame({"student_id": ["s1"], "enrollment_type": ["First-time"]})
    out_df, display = normalize_edvise_dataframe_to_pdp(df, ["student"])
    assert "institution_id" in out_df.columns
    assert display != {}
