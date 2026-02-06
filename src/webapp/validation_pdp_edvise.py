"""PDP schema validation using canonical schemas from the edvise package.

This module runs the same validation as the edvise repo (RawPDPCohortDataSchema,
RawPDPCourseDataSchema) for PDP uploads only, so PDP validation rules match pipelines
and audits. The edvise extension/institution uses JSON-based validation only (different
columns and setup). All logic is in edvise-api; the edvise package is consumed read-only.

When the edvise package is not installed, PDP strict schema validation is skipped
and the existing JSON-based validation is used. To enable strict validation for PDP,
add edvise to pyproject.toml (e.g. path or published package).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from pandera.errors import SchemaError, SchemaErrors

logger = logging.getLogger(__name__)


def _get_hard_validation_error_class() -> type:
    """Import HardValidationError lazily to avoid circular import with validation."""
    from .validation import HardValidationError

    return HardValidationError

# Lazy import so missing edvise dependency does not break the app at import time.
_EDVISE_COHORT_SCHEMA: Optional[type] = None
_EDVISE_COURSE_SCHEMA: Optional[type] = None
_EDVISE_AVAILABLE = False

try:
    from edvise.data_audit.schemas.raw_cohort import RawPDPCohortDataSchema
    from edvise.data_audit.schemas.raw_course import RawPDPCourseDataSchema

    _EDVISE_COHORT_SCHEMA = RawPDPCohortDataSchema
    _EDVISE_COURSE_SCHEMA = RawPDPCourseDataSchema
    _EDVISE_AVAILABLE = True
except ImportError as e:
    logger.warning(
        "edvise package not available; PDP strict repo schema validation disabled: %s",
        e,
    )

# Institution namespaces that use edvise repo schemas (RawPDPCohortDataSchema / RawPDPCourseDataSchema).
# Only PDP uses repo validation; edvise extension has different columns and uses JSON validation.
PDP_EDVISE_NAMESPACES = frozenset({"pdp"})

# Single credit columns (from base merge) -> repo expects per-year for cohort; we map to year_1 and fill 2–4 with NA.
# Only applied when is_cohort; course keeps single number_of_credits_attempted / number_of_credits_earned.
PDP_CREDIT_ATTEMPTED_CANON = "number_of_credits_attempted"
PDP_CREDIT_EARNED_CANON = "number_of_credits_earned"
PDP_CREDIT_REPO_YEARS = ["year_1", "year_2", "year_3", "year_4"]


def _ensure_per_year_credit_columns(
    out: pd.DataFrame,
    prefix: str,
    canon_to_raw: Dict[str, str],
    display_canon_to_raw: Dict[str, str],
) -> None:
    """Ensure all 4 per-year columns exist; fill missing with pd.NA. Update display_canon_to_raw."""
    raw_year_1 = canon_to_raw.get(f"{prefix}_year_1", prefix + "_year_1")
    for suf in PDP_CREDIT_REPO_YEARS:
        col = f"{prefix}_{suf}"
        if col not in out.columns:
            out[col] = pd.NA
        if col not in display_canon_to_raw:
            display_canon_to_raw[col] = canon_to_raw.get(col, raw_year_1)


def rename_pdp_dataframe_to_repo_schema(
    df: pd.DataFrame,
    canon_to_raw: Dict[str, str],
    model_list: Optional[List[str]] = None,
) -> tuple[pd.DataFrame, Dict[str, str]]:
    """
    Ensure PDP DataFrame column names and shape match edvise repo schemas.

    Extension + base merge already use repo-shaped canonicals (first_gen, student_age,
    cohort_term, number_of_credits_attempted, delivery_method, etc.), so no renames needed.
    - Cohort only: if program_of_study_year_1 is missing, copy from program_of_study_term_1.
    - Cohort only: ensure 8 per-year credit columns exist; expand single number_of_credits_attempted/earned to year_1 + year_2–4 pd.NA when present.

    Returns:
        (df, display_canon_to_raw): DataFrame (repo-shaped) and repo column name -> raw header for errors.
    """
    out = df.copy()
    models = {str(m).strip().upper() for m in (model_list or []) if m}
    is_cohort = "STUDENT" in models

    display_canon_to_raw = dict(canon_to_raw)

    if is_cohort:
        if "program_of_study_term_1" in out.columns and "program_of_study_year_1" not in out.columns:
            out["program_of_study_year_1"] = out["program_of_study_term_1"].copy()
            display_canon_to_raw["program_of_study_year_1"] = display_canon_to_raw.get(
                "program_of_study_term_1", "program_of_study_year_1"
            )

        raw_attempted = canon_to_raw.get(PDP_CREDIT_ATTEMPTED_CANON, canon_to_raw.get("number_of_credits_attempted_year_1", "number_of_credits_attempted_year_1"))
        raw_earned = canon_to_raw.get(PDP_CREDIT_EARNED_CANON, canon_to_raw.get("number_of_credits_earned_year_1", "number_of_credits_earned_year_1"))
        if PDP_CREDIT_ATTEMPTED_CANON in out.columns:
            out["number_of_credits_attempted_year_1"] = out[PDP_CREDIT_ATTEMPTED_CANON]
            out = out.drop(columns=[PDP_CREDIT_ATTEMPTED_CANON], errors="ignore")
            for suf in PDP_CREDIT_REPO_YEARS[1:]:
                out[f"number_of_credits_attempted_{suf}"] = pd.NA
            display_canon_to_raw["number_of_credits_attempted_year_1"] = raw_attempted
            for suf in PDP_CREDIT_REPO_YEARS[1:]:
                display_canon_to_raw[f"number_of_credits_attempted_{suf}"] = raw_attempted
        _ensure_per_year_credit_columns(out, "number_of_credits_attempted", canon_to_raw, display_canon_to_raw)

        if PDP_CREDIT_EARNED_CANON in out.columns:
            out["number_of_credits_earned_year_1"] = out[PDP_CREDIT_EARNED_CANON]
            out = out.drop(columns=[PDP_CREDIT_EARNED_CANON], errors="ignore")
            for suf in PDP_CREDIT_REPO_YEARS[1:]:
                out[f"number_of_credits_earned_{suf}"] = pd.NA
            display_canon_to_raw["number_of_credits_earned_year_1"] = raw_earned
            for suf in PDP_CREDIT_REPO_YEARS[1:]:
                display_canon_to_raw[f"number_of_credits_earned_{suf}"] = raw_earned
        _ensure_per_year_credit_columns(out, "number_of_credits_earned", canon_to_raw, display_canon_to_raw)

    return out, display_canon_to_raw


def is_edvise_schema_available() -> bool:
    """Return True if the edvise package is installed and schemas can be used."""
    return _EDVISE_AVAILABLE


def get_edvise_schema_for_upload(
    institution_id: str,
    model_list: List[str],
) -> Optional[type]:
    """
    Return the edvise repo schema class for this upload, or None.

    Use this as the single check: when not None, run that schema and skip JSON
    Pandera. Only PDP uses repo validation; edvise institution uses JSON validation.

    Args:
        institution_id: Schema namespace (e.g. "pdp", or institution UUID). Only "pdp" uses repo schema.
        model_list: Inferred model names from filename (e.g. ["STUDENT"], ["COURSE"]).

    Returns:
        RawPDPCohortDataSchema for PDP+STUDENT, RawPDPCourseDataSchema for PDP+COURSE,
        or None (use JSON-based validation).
    """
    if not institution_id or not isinstance(institution_id, str):
        return None
    if institution_id not in PDP_EDVISE_NAMESPACES or not _EDVISE_AVAILABLE:
        return None
    if not _EDVISE_COHORT_SCHEMA or not _EDVISE_COURSE_SCHEMA:
        return None
    if model_list is not None and not isinstance(model_list, list):
        return None
    model_set = {str(m).strip().upper() for m in (model_list or []) if m}
    if model_set == {"STUDENT"}:
        return _EDVISE_COHORT_SCHEMA
    if model_set == {"COURSE"}:
        return _EDVISE_COURSE_SCHEMA
    return None


def should_use_edvise_schema(
    institution_id: str,
    model_list: List[str],
) -> bool:
    """True when upload should use edvise schema (same condition as get_edvise_schema_for_upload)."""
    return get_edvise_schema_for_upload(institution_id, model_list) is not None


def get_edvise_schema_for_models(model_list: List[str]) -> Optional[type]:
    """Return edvise schema for single-model list (pdp namespace). For tests/callers that don't have institution_id."""
    return get_edvise_schema_for_upload("pdp", model_list)


def _normalize_failure_cases_for_formatter(failure_cases: Any) -> List[Dict[str, Any]]:
    """
    Convert Pandera failure_cases to a list of dicts with keys the formatter expects.

    Formatter expects each record to have: column, index, check, failure_case.
    Pandera may use different column names (e.g. schema_context, check_number);
    we keep only the keys needed for formatting.
    """
    if failure_cases is None:
        return []
    records: List[Dict[str, Any]] = []
    if hasattr(failure_cases, "to_dict"):
        try:
            raw_records = failure_cases.to_dict(orient="records")
        except (TypeError, ValueError):
            return []
        if not isinstance(raw_records, list):
            return []
        for row in raw_records:
            if not isinstance(row, dict):
                continue
            # Pandera uses 'failure_case' (singular); some versions may differ.
            normalized = {
                "column": row.get("column"),
                "index": row.get("index", -1),
                "check": row.get("check", "validation"),
                "failure_case": row.get("failure_case", row.get("failure_cases", "N/A")),
            }
            records.append(normalized)
        return records
    if isinstance(failure_cases, list):
        for item in failure_cases:
            if isinstance(item, dict):
                normalized = {
                    "column": item.get("column"),
                    "index": item.get("index", -1),
                    "check": item.get("check", "validation"),
                    "failure_case": item.get("failure_case", item.get("failure_cases", "N/A")),
                }
                records.append(normalized)
    return records


def _extract_missing_required_from_pandera_error(err: Any) -> List[str]:
    """
    Derive missing required column names from a Pandera SchemaErrors exception.

    When the edvise schema requires columns not present in the DataFrame,
    Pandera may report them in failure_cases with a check that indicates
    missing column (e.g. "column_in_dataframe"). Only rows whose check
    suggests a missing-column failure are included; we do not treat
    value-check failures (e.g. wrong category) as missing columns.
    """
    missing: List[str] = []
    if not hasattr(err, "failure_cases") or err.failure_cases is None:
        return missing
    try:
        df = err.failure_cases
        if hasattr(df, "columns") and "column" in df.columns:
            for _, row in df.iterrows():
                col = row.get("column")
                check = str(row.get("check", ""))
                if col and isinstance(col, str) and col not in missing:
                    if "column" in check.lower() or "missing" in check.lower():
                        missing.append(col)
    except (AttributeError, TypeError, ValueError) as e:
        logger.debug("Could not extract missing_required from Pandera error: %s", e)
    return missing


def _convert_schema_errors_to_hard_validation_error(
    err: Any,
    raw_to_canon: Dict[str, str],
    canon_to_raw: Dict[str, str],
    merged_specs: Dict[str, dict],
) -> HardValidationError:
    """
    Convert a Pandera SchemaErrors (or single SchemaError) to HardValidationError.

    Normalizes failure_cases to the shape the validation_error_formatter expects
    and derives missing_required when the failure is due to missing columns.

    Returns:
        HardValidationError with normalized failure_cases, optional missing_required,
        and schema_errors, for the formatter to produce human-readable messages.
    """
    failure_cases = getattr(err, "failure_cases", None)
    normalized_failure_cases = _normalize_failure_cases_for_formatter(failure_cases)
    missing_required = _extract_missing_required_from_pandera_error(err)
    schema_errors = getattr(err, "schema_errors", None)
    if schema_errors is None:
        schema_errors = str(err) if err else None
    logger.error(
        "PDP/Edvise schema validation failed: missing_required=%s, failure_cases_count=%s",
        missing_required,
        len(normalized_failure_cases),
    )
    HardValidationError = _get_hard_validation_error_class()
    return HardValidationError(
        missing_required=missing_required if missing_required else None,
        extra_columns=None,
        schema_errors=schema_errors,
        failure_cases=normalized_failure_cases,
        raw_to_canon=raw_to_canon,
        canon_to_raw=canon_to_raw,
        merged_specs=merged_specs,
    )


def validate_dataframe_with_edvise_schema(
    df: pd.DataFrame,
    schema_class: type,
    raw_to_canon: Dict[str, str],
    canon_to_raw: Dict[str, str],
    merged_specs: Dict[str, dict],
) -> None:
    """
    Validate a DataFrame with the given edvise schema (cohort or course).

    Uses the same schemas as the edvise repo so rules are identical everywhere.
    Raises HardValidationError with normalized failure_cases and optional
    missing_required when validation fails.

    Args:
        df: DataFrame with canonical column names (from header pass + read).
        schema_class: RawPDPCohortDataSchema or RawPDPCourseDataSchema.
        raw_to_canon: Mapping from raw file headers to canonical names.
        canon_to_raw: Mapping from canonical names to raw file headers.
        merged_specs: Merged JSON spec for formatter context.

    Raises:
        HardValidationError: When schema validation fails (missing columns or row-level checks).
    """
    HardValidationError = _get_hard_validation_error_class()
    if df is None or df.empty:
        raise HardValidationError(
            schema_errors="PDP/Edvise schema validation failed: empty or missing DataFrame",
            raw_to_canon=raw_to_canon,
            canon_to_raw=canon_to_raw,
            merged_specs=merged_specs,
        )
    try:
        # Lazy=True so all failures are collected in one SchemaErrors.
        schema_class.validate(df, lazy=True)
    except (SchemaErrors, SchemaError) as e:
        # Pandera raises SchemaErrors for lazy validation; single failure may raise SchemaError.
        hard = _convert_schema_errors_to_hard_validation_error(
            e, raw_to_canon, canon_to_raw, merged_specs
        )
        raise hard from e
