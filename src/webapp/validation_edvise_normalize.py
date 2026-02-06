"""Edvise → PDP normalization for validation.

When institution_id is "edvise", the upload DataFrame uses Edvise canonical
column names and value formats. This module renames columns to PDP (repo) names,
normalizes values to repo categories, and adds required repo columns that
Edvise does not provide, so the same RawPDPCohortDataSchema / RawPDPCourseDataSchema
can be run on the normalized DataFrame.

All logic lives in edvise-api; the edvise package is read-only.
Mapping reference: .cursor/docs/EDVISE_TO_PDP_NORMALIZATION.md
"""

from __future__ import annotations

import logging
from typing import Any, Collection, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# Minimum length for academic year string "YYYY-YY".
ACADEMIC_YEAR_STR_MIN_LENGTH = 7
# Width for section_id placeholder so values sort and stay distinct.
SECTION_ID_PLACEHOLDER_WIDTH = 6

# Edvise extension canonical name -> PDP (repo) column name.
# Keys match merged_specs when institution is "edvise" (e.g. cohort_year, not cohort).
EDVISE_TO_PDP_COHORT_RENAME: Dict[str, str] = {
    "student_id": "student_id",
    "cohort_year": "cohort",
    "cohort": "cohort",
    "cohort_term": "cohort_term",
    "student_age": "student_age",
    "enrollment_type": "enrollment_type",
    "race": "race",
    "ethnicity": "ethnicity",
    "gender": "gender",
    "first_gen": "first_gen",
    "pell_status_first_year": "pell_status_first_year",
    "credential_type_sought_year_1": "credential_type_sought_year_1",
    "program_of_study_term_1": "program_of_study_term_1",
    "incarcerated_status": "incarcerated_status",
    "military_status": "military_status",
    "employment_status": "employment_status",
    "disability_status": "disability_status",
    "bachelors_time_to_degree": "years_to_bachelors_at_cohort_inst",
    "associates_time_to_degree": "years_to_associates_or_certificate_at_cohort_inst",
    "certificate_time_to_degree": "years_to_associates_or_certificate_at_cohort_inst",
}

EDVISE_TO_PDP_COURSE_RENAME: Dict[str, str] = {
    "student_id": "student_id",
    "academic_year": "academic_year",
    "academic_term": "academic_term",
    "course_prefix": "course_prefix",
    "course_number": "course_number",
    "course_name": "course_name",
    "course_type": "course_type",
    "course_begin_date": "course_begin_date",
    "course_end_date": "course_end_date",
    "grade": "grade",
    "course_credits_attempted": "number_of_credits_attempted",
    "course_credits_earned": "number_of_credits_earned",
    "delivery_method": "delivery_method",
    "core_course": "core_course",
    "course_instructor_employment_status": "course_instructor_employment_status",
    "gateway_or_development_flag": "math_or_english_gateway",
    "term_major": "term_program_of_study",
}

# Term value normalization: common variants -> repo categories.
_TERM_MAP: Dict[str, str] = {
    "fall": "FALL",
    "fa": "FALL",
    "winter": "WINTER",
    "wi": "WINTER",
    "spring": "SPRING",
    "sp": "SPRING",
    "summer": "SUMMER",
    "su": "SUMMER",
    "sm": "SUMMER",
}

# Enrollment type: Edvise descriptions -> repo categories.
_ENROLLMENT_MAP: Dict[str, str] = {
    "first-time": "FIRST-TIME",
    "first time": "FIRST-TIME",
    "freshman": "FIRST-TIME",
    "transfer": "TRANSFER-IN",
    "transfer-in": "TRANSFER-IN",
    "re-admit": "RE-ADMIT",
    "readmit": "RE-ADMIT",
}


def _normalize_term_values(series: pd.Series) -> pd.Series:
    """Normalize term-like values to FALL/WINTER/SPRING/SUMMER."""
    if series is None or series.empty:
        return series
    out = series.astype(str).str.strip().str.lower()
    # Strip optional year prefix/suffix (e.g. "2024 fall", "fa 2024").
    out = out.str.replace(r"\d{4}\s*", "", regex=True).str.strip()
    out = out.str.replace(r"\s*\d{4}$", "", regex=True).str.strip()
    return out.map(_TERM_MAP).fillna(series)


def _normalize_enrollment_type(series: pd.Series) -> pd.Series:
    """Normalize enrollment_type to FIRST-TIME / RE-ADMIT / TRANSFER-IN."""
    if series is None or series.empty:
        return series
    out = series.astype(str).str.strip().str.lower()
    result = pd.Series(index=series.index, dtype=object)
    for val, norm in _ENROLLMENT_MAP.items():
        result = result.where(~out.str.contains(val, regex=False), norm)
    return result.fillna(series)


def _apply_cohort_renames_and_values(df: pd.DataFrame) -> pd.DataFrame:
    """Rename cohort columns and normalize term/enrollment values."""
    rename = {k: v for k, v in EDVISE_TO_PDP_COHORT_RENAME.items() if k in df.columns}
    # If both associates and certificate time-to-degree exist, prefer associates for the single PDP column.
    prefer_associates = (
        "associates_time_to_degree" in df.columns
        and "certificate_time_to_degree" in df.columns
    )
    if prefer_associates:
        rename.pop("certificate_time_to_degree", None)
    out = df.rename(columns=rename)
    if prefer_associates and "certificate_time_to_degree" in out.columns:
        out = out.drop(columns=["certificate_time_to_degree"])
    if "cohort_term" in out.columns:
        out["cohort_term"] = _normalize_term_values(out["cohort_term"])
    if "enrollment_type" in out.columns:
        out["enrollment_type"] = _normalize_enrollment_type(out["enrollment_type"])
    return out


def _apply_course_renames_and_values(df: pd.DataFrame) -> pd.DataFrame:
    """Rename course columns and normalize term values."""
    rename = {k: v for k, v in EDVISE_TO_PDP_COURSE_RENAME.items() if k in df.columns}
    out = df.rename(columns=rename)
    for col in ("academic_term", "cohort_term"):
        if col in out.columns:
            out[col] = _normalize_term_values(out[col])
    return out


def _add_required_repo_columns_cohort(
    df: pd.DataFrame,
    institution_identifier: Optional[str],
) -> pd.DataFrame:
    """Add required PDP cohort columns not provided by Edvise."""
    out = df.copy()
    if "institution_id" not in out.columns:
        out["institution_id"] = institution_identifier if institution_identifier else ""
    if "retention" not in out.columns:
        out["retention"] = True
    if "persistence" not in out.columns:
        out["persistence"] = True
    if "years_of_last_enrollment_at_cohort_institution" not in out.columns:
        out["years_of_last_enrollment_at_cohort_institution"] = 0
    if "years_of_last_enrollment_at_other_institution" not in out.columns:
        out["years_of_last_enrollment_at_other_institution"] = 0
    return out


def _first_valid_academic_year_series(series: pd.Series) -> str:
    """Return first non-null value as YYYY-YY string; handle datetime and NaN."""
    if series is None or series.empty:
        return ""
    valid = series.dropna()
    if valid.empty:
        return ""
    val = valid.iloc[0]
    if pd.isna(val):
        return ""
    if hasattr(val, "year"):
        return f"{val.year}-{str((val.year + 1) % 100).zfill(2)}"
    s = str(val).strip()
    if len(s) >= ACADEMIC_YEAR_STR_MIN_LENGTH and s[4] in "-/":
        return s[:ACADEMIC_YEAR_STR_MIN_LENGTH].replace("/", "-")
    return s[:ACADEMIC_YEAR_STR_MIN_LENGTH] if len(s) >= ACADEMIC_YEAR_STR_MIN_LENGTH else s


def _add_required_repo_columns_course(
    df: pd.DataFrame,
    institution_identifier: Optional[str],
    cohort_fill: str,
) -> pd.DataFrame:
    """Add required PDP course columns not provided by Edvise."""
    out = df.copy()
    if "institution_id" not in out.columns:
        out["institution_id"] = institution_identifier if institution_identifier else ""
    if "cohort" not in out.columns:
        out["cohort"] = cohort_fill
    if "cohort_term" not in out.columns and "academic_term" in out.columns:
        out["cohort_term"] = out["academic_term"]
    # Unique placeholder per row so (student_id, academic_year, academic_term, course_prefix, course_number, section_id) stays unique.
    if "section_id" not in out.columns:
        out["section_id"] = out.index.astype(str).str.zfill(SECTION_ID_PLACEHOLDER_WIDTH)
    return out


def _schema_column_names(schema_class: Optional[type]) -> List[str]:
    """Introspect schema class for expected column names; return [] on failure."""
    if schema_class is None:
        return []
    try:
        return list(schema_class.to_schema().columns.keys())
    except (AttributeError, TypeError, ValueError, KeyError) as e:
        # Schema introspection can fail if Pandera API or schema structure differs.
        logger.debug("Could not get schema columns from %s: %s", schema_class, e)
        return []


def _add_missing_schema_columns(
    df: pd.DataFrame, required_columns: List[str]
) -> pd.DataFrame:
    """Add any required schema columns not present, with pd.NA so schema can run."""
    if not required_columns:
        return df
    missing = [c for c in required_columns if c not in df.columns]
    if not missing:
        return df
    out = df.copy()
    for col in missing:
        out[col] = pd.NA
    return out


def _build_pdp_to_edvise_display(
    rename_map: Dict[str, str],
    df_columns: Collection[str],
    extra: Dict[str, str],
) -> Dict[str, str]:
    """Build PDP column -> Edvise name map for error messages."""
    display = {}
    for edvise_name, pdp_name in rename_map.items():
        if edvise_name in df_columns:
            display[pdp_name] = edvise_name
    for pdp_name, edvise_name in extra.items():
        display.setdefault(pdp_name, edvise_name)
    return display


def normalize_edvise_dataframe_to_pdp(
    df: pd.DataFrame,
    model_list: List[str],
    institution_identifier: Optional[str] = None,
    schema_class: Optional[type] = None,
) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """
    Normalize an Edvise canonical DataFrame to PDP shape for repo validation.

    Applies renames, value normalization, and adds required repo columns.
    Call only when institution_id == "edvise" and single-model STUDENT or COURSE.

    Args:
        df: DataFrame with Edvise extension canonical column names (after header pass + read).
        model_list: Single-model list, e.g. ["STUDENT"] or ["COURSE"].
        institution_identifier: Institution ID (e.g. UUID) to fill institution_id column.
        schema_class: Optional repo schema class (RawPDPCohortDataSchema or RawPDPCourseDataSchema).
            When provided, missing columns are added with pd.NA so validation can run.

    Returns:
        (normalized_df, pdp_to_edvise_display): Normalized DataFrame and mapping from
        PDP column name -> Edvise canonical name for error messages. Returns (df, {})
        unchanged when model_list is empty or not single STUDENT/COURSE.
    """
    if not model_list:
        return df, {}
    model_set = {str(m).strip().upper() for m in model_list if m}
    required_columns = _schema_column_names(schema_class)

    if model_set == {"STUDENT"}:
        out = _apply_cohort_renames_and_values(df)
        out = _add_required_repo_columns_cohort(out, institution_identifier)
        out = _add_missing_schema_columns(out, required_columns)
        display = _build_pdp_to_edvise_display(
            EDVISE_TO_PDP_COHORT_RENAME,
            df.columns,
            {"institution_id": "institution_id", "retention": "retention", "persistence": "persistence"},
        )
        return out, display

    if model_set == {"COURSE"}:
        out = _apply_course_renames_and_values(df)
        cohort_fill = ""
        if "academic_year" in out.columns and not out["academic_year"].empty:
            cohort_fill = _first_valid_academic_year_series(out["academic_year"])
        out = _add_required_repo_columns_course(
            out, institution_identifier, cohort_fill
        )
        out = _add_missing_schema_columns(out, required_columns)
        display = _build_pdp_to_edvise_display(
            EDVISE_TO_PDP_COURSE_RENAME,
            df.columns,
            {"institution_id": "institution_id", "cohort": "academic_year", "section_id": "section_id"},
        )
        return out, display

    return df, {}
