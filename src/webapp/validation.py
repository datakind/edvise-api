"""File validation functions for various schemas.
Record-by-record validation happens in the pipelines; this module performs
general file validation with performance-focused improvements.

Key speed-ups (without losing accuracy):
- Header-only pass to discover/resolve columns before full load
- Selective, typed CSV read via `usecols` and dtype mapping
- Exact-name Pandera schemas (avoid regex column matching)
- Fuzzy matching only for unresolved headers; use rapidfuzz if available
- Precompiled regexes and set-based membership checks inside Pandera checks
"""

from __future__ import annotations

import io
import os
import json
import re
import logging
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from typing import (
    Any,
    BinaryIO,
    Callable,
    Dict,
    Generator,
    List,
    Optional,
    Tuple,
    Union,
    cast,
)

import pandas as pd
from pandera import Column, Check, DataFrameSchema
from pandera.errors import SchemaError, SchemaErrors

from edvise.dataio.read import read_raw_pdp_cohort_data, read_raw_pdp_course_data
from edvise.dataio.pdp_cohort_converters import converter_func_cohort
from edvise.utils.data_cleaning import handling_duplicates

from . import validation_pdp_edvise as pdp_edvise

# Type for PDP converter functions (DataFrame -> DataFrame); used for cohort/course.
PDPConverterFunc = Optional[Callable[[pd.DataFrame], pd.DataFrame]]


def _default_pdp_course_duplicate_converter(df: pd.DataFrame) -> pd.DataFrame:
    """
    PDP course duplicate cleanup for read_raw_pdp_course_data.

    Passes the schema selector as the second *positional* argument so this works
    with current edvise (``schema_type``) and older builds that used the same slot
    for ``school_type``. Do not pass bare ``handling_duplicates`` as a converter:
    read_raw_pdp_course_data calls ``converter_func(df)`` with a single argument.
    """
    return handling_duplicates(df, "pdp")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def validate_file_reader(
    filename: Union[str, os.PathLike[str], BinaryIO, io.TextIOWrapper, io.StringIO],
    allowed_schema: list[str],
    base_schema: dict,
    inst_schema: Optional[Dict[Any, Any]] = None,
    institution_id: str = "pdp",
    institution_identifier: Optional[str] = None,
    pdp_cohort_converter_func: PDPConverterFunc = None,
    pdp_course_converter_func: PDPConverterFunc = None,
) -> dict[str, Any]:
    """Validates a dataset given a filename and schema selection.

    Args:
        filename: Path or file-like object for the CSV.
        allowed_schema: List of model names to validate against.
        base_schema: Base schema dict (e.g. base.data_models).
        inst_schema: Optional extension schema with institutions.* blocks.
        institution_id: Key into inst_schema["institutions"]: "edvise", "pdp", or
            institution UUID for custom. Default "pdp" for backward compatibility.
        institution_identifier: Optional institution identifier (e.g. UUID) for display/context.
        pdp_cohort_converter_func: Optional custom PDP cohort converter (school-specific).
        pdp_course_converter_func: Optional custom PDP course converter (school-specific).

    Returns:
        Dict with validation_status, schemas, missing_optional, unknown_extra_columns.
        On success also contains "normalized_df" (DataFrame, or None if nothing was validated).

    Raises:
        HardValidationError: When required columns are missing or schema validation fails.
        UnicodeError: When file encoding is not UTF-8/UTF-16/UTF-32.
    """
    return validate_dataset(
        filename,
        base_schema,
        inst_schema,
        allowed_schema,
        institution_id,
        institution_identifier,
        pdp_cohort_converter_func=pdp_cohort_converter_func,
        pdp_course_converter_func=pdp_course_converter_func,
    )


class HardValidationError(Exception):
    def __init__(
        self,
        missing_required: Optional[List[str]] = None,
        extra_columns: Optional[List[str]] = None,
        schema_errors: Any = None,
        failure_cases: Any = None,
        raw_to_canon: Optional[Dict[str, str]] = None,
        canon_to_raw: Optional[Dict[str, str]] = None,
        merged_specs: Optional[Dict[str, dict]] = None,
    ):
        self.missing_required = missing_required or []
        self.extra_columns = extra_columns or []
        self.schema_errors = schema_errors
        self.failure_cases = failure_cases
        self.raw_to_canon = raw_to_canon or {}
        self.canon_to_raw = canon_to_raw or {}
        self.merged_specs = merged_specs or {}
        parts = []
        if self.missing_required:
            parts.append(f"Missing required columns: {self.missing_required}")
        if self.extra_columns:
            parts.append(f"Unexpected columns: {self.extra_columns}")
        if self.schema_errors is not None:
            parts.append(f"Schema errors: {self.schema_errors}")
        super().__init__("; ".join(parts))


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=4096)
def normalize_col(name: str) -> str:
    """Normalize a column name: trim, lowercase, non-alnum->'_', collapse '_'s."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def load_json(path: str) -> Any:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        raise FileNotFoundError(f"Failed to load JSON schema at {path}: {e}") from e


def merge_model_columns(
    base_schema: dict,
    extension_schema: Any,
    institution: str,
    model: str,
) -> Dict[str, dict]:
    """
    Merge base model columns with institution-specific extension, if present.
    """
    base_models = base_schema.get("base", {}).get("data_models", {})
    if model not in base_models:
        logger.error("Model '%s' not found in base schema", model)
        raise KeyError(f"Model '{model}' not in base schema")
    merged = dict(base_models[model].get("columns", {}))
    if extension_schema:
        inst_block = extension_schema.get("institutions", {}).get(institution, {})
        ext_models = inst_block.get("data_models", {})
        if model in ext_models:
            merged.update(ext_models[model].get("columns", {}))
    return merged


def get_extension_model_columns_only(
    extension_schema: Any,
    institution: str,
    model: str,
) -> Dict[str, dict]:
    """
    Return only the extension columns for the given institution and model (no base).
    Used for PDP and Edvise so we do not pull in base columns that don't match the repo schema.
    """
    if not extension_schema:
        return {}
    inst_block = extension_schema.get("institutions", {}).get(institution, {})
    ext_models = inst_block.get("data_models", {})
    if model not in ext_models:
        return {}
    return dict(ext_models[model].get("columns", {}))


# --------------------------------------------------------------------------- #
# Encoding sniffing (mypy-friendly)
# --------------------------------------------------------------------------- #

Src = Union[str, os.PathLike[str], BinaryIO, io.TextIOWrapper, io.StringIO]


def _read_sample(buf: BinaryIO, n: int) -> bytes:
    pos = buf.tell() if buf.seekable() else None
    chunk = buf.read(n)
    if pos is not None:
        buf.seek(pos)
    return chunk


def sniff_encoding(src: Src, sample_bytes: int = 1_048_576) -> str:
    """
    Best-guess encoding via BOM detection + utf-8 trial.
    Works with a filesystem path, a binary stream, or a TextIOWrapper.
    Restores stream position if seekable. Raises if latin-1 would be used (by default).
    """
    # --- read a small binary sample ---
    if isinstance(src, (str, os.PathLike)):
        with open(src, "rb") as f:
            chunk: bytes = f.read(sample_bytes)
    elif isinstance(src, io.TextIOWrapper):
        # Text wrapper => use underlying binary buffer, cast to BinaryIO for mypy
        chunk = _read_sample(cast(BinaryIO, src.buffer), sample_bytes)
    else:
        # Already a binary stream
        chunk = _read_sample(cast(BinaryIO, src), sample_bytes)

    # --- BOMs first ---
    if chunk.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if chunk.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32le"
    if chunk.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32be"
    if chunk.startswith(b"\xff\xfe"):
        return "utf-16le"
    if chunk.startswith(b"\xfe\xff"):
        return "utf-16be"

    # --- utf-8 strict on sample ---
    try:
        chunk.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        raise UnicodeError(
            "file is not UTF-8/UTF-16/UTF-32; please re-export as UTF-8."
        )


def _reset_to_start_if_possible(src: Src) -> None:
    """Best-effort reset to the beginning for file-like objects."""
    try:
        if hasattr(src, "seek") and callable(getattr(src, "seek")):
            src.seek(0)  # type: ignore[attr-defined]
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Fast header pass & mapping
# --------------------------------------------------------------------------- #


def _spec_alias_lookup(
    merged_specs: Dict[str, dict],
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """
    Build:
      - alias2canon: normalized alias -> canonical
      - canon_to_aliases_norm: canonical -> list of normalized aliases (incl. canonical)
    """
    alias2canon: Dict[str, str] = {}
    canon_to_aliases_norm: Dict[str, List[str]] = {}
    for canon, spec in merged_specs.items():
        aliases = [canon] + spec.get("aliases", [])
        normed = [normalize_col(a) for a in aliases]
        canon_to_aliases_norm[canon] = normed
        for a in normed:
            alias2canon[a] = canon
    return alias2canon, canon_to_aliases_norm


def _fuzzy_map_unresolved(
    unresolved: List[Tuple[str, str]],  # [(raw_header, normalized_header)]
    choices: List[str],  # normalized aliases
    alias2canon: Dict[str, str],
    threshold: int = 90,
) -> Dict[str, str]:  # raw_header -> canonical
    """
    Fuzzy-match only the unresolved headers, using RapidFuzz if available, otherwise thefuzz.
    """
    mapping: Dict[str, str] = {}
    try:
        from rapidfuzz import process, fuzz as rf_fuzz  # type: ignore

        for raw, norm in unresolved:
            hit = process.extractOne(
                norm, choices, scorer=rf_fuzz.ratio, score_cutoff=threshold
            )
            if hit:
                best_alias, score, _ = hit
                mapping[raw] = alias2canon[best_alias]  # type: ignore[index]
    except Exception:
        # fallback to thefuzz if rapidfuzz is unavailable
        try:
            from thefuzz import fuzz as tf_fuzz  # type: ignore
        except Exception:
            # If neither library is available, do not fuzz-map anything.
            return mapping
        for raw, norm in unresolved:
            best_score = 0
            best_alias = None
            for alias in choices:
                s = tf_fuzz.ratio(norm, alias)
                if s > best_score:
                    best_score, best_alias = s, alias
            if best_alias and best_score >= threshold:
                mapping[raw] = alias2canon[best_alias]
    return mapping


def _header_missing_and_extra(
    merged_specs: Dict[str, dict],
    raw_to_canon: Dict[str, str],
    unresolved: List[Tuple[str, str]],
    known_aliases: set,
) -> Tuple[List[str], List[str], List[str]]:
    """Compute missing_required, missing_optional, unknown_extra from header mapping."""
    incoming_canons = set(raw_to_canon.values())
    missing_required = [
        c
        for c, spec in merged_specs.items()
        if spec.get("required", False) and c not in incoming_canons
    ]
    missing_optional = [
        c
        for c, spec in merged_specs.items()
        if not spec.get("required", False) and c not in incoming_canons
    ]
    unknown_extra = sorted(
        {norm for (_, norm) in unresolved if norm not in known_aliases}
    )
    return missing_required, missing_optional, unknown_extra


def _header_pass(
    filename: Src,
    encoding: str,
    merged_specs: Dict[str, dict],
    fuzzy_threshold: int = 90,
) -> Tuple[List[str], Dict[str, str], List[str], List[str], List[str]]:
    """
    Read only the header. Return:
      - raw_cols: list of column names as in file
      - raw_to_canon: mapping raw header -> canonical (after exact+fuzzy)
      - missing_required: list of canonical columns missing
      - missing_optional: list of optional canonical columns missing
      - unknown_extra: normalized headers that don't map to any alias
    """
    header_df = pd.read_csv(filename, encoding=encoding, nrows=0)
    raw_cols = list(header_df.columns)

    alias2canon, canon_to_aliases_norm = _spec_alias_lookup(merged_specs)
    known_aliases = set(alias2canon.keys())

    raw_to_canon: Dict[str, str] = {}
    unresolved: List[Tuple[str, str]] = []
    for raw in raw_cols:
        norm = normalize_col(raw)
        if norm in alias2canon:
            raw_to_canon[raw] = alias2canon[norm]
        else:
            unresolved.append((raw, norm))

    if unresolved:
        choices = list(known_aliases)
        fuzzy_map = _fuzzy_map_unresolved(
            unresolved, choices, alias2canon, threshold=fuzzy_threshold
        )
        raw_to_canon.update(fuzzy_map)

    missing_required, missing_optional, unknown_extra = _header_missing_and_extra(
        merged_specs, raw_to_canon, unresolved, known_aliases
    )
    return raw_cols, raw_to_canon, missing_required, missing_optional, unknown_extra


def _pandas_dtype_and_parse_dates(
    merged_specs: Dict[str, dict],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Conservative mapping from spec dtype -> pandas read_csv dtype/parse_dates.
    Keeps behavior stable while avoiding heavy inference.
    """
    dtype_map: Dict[str, Any] = {}
    parse_dates: List[str] = []

    for canon, spec in merged_specs.items():
        dt = str(spec.get("dtype"))
        if dt in {"string", "str", "object"}:
            dtype_map[canon] = "string"
        elif dt in {"int", "int64", "Int64"}:
            dtype_map[canon] = "Int64"  # nullable integers are safer for dirty data
        elif dt in {"float", "float64"}:
            dtype_map[canon] = "float64"
        elif "datetime" in dt or "date" in dt:
            parse_dates.append(canon)
        elif dt in {"bool", "boolean"}:
            dtype_map[canon] = "boolean"
        elif dt == "category":
            dtype_map[canon] = "category"
        else:
            # leave to pandas inference
            pass

    return dtype_map, parse_dates


def _build_exact_schema(
    specs: Dict[str, dict], only_canons: List[str]
) -> DataFrameSchema:
    """
    Build a Pandera schema with exact column names (no regex).
    This avoids regex matching overhead during validation.
    """
    cols: Dict[str, Column] = {}
    for canon in only_canons:
        spec = specs[canon]
        checks = []
        for chk in spec.get("checks", []):
            args = list(chk.get("args", []))
            # precompile regex patterns once
            if (
                chk["type"] in {"str_matches", "matches"}
                and args
                and isinstance(args[0], str)
            ):
                args[0] = re.compile(args[0])
            # set-based membership for faster 'isin'
            if chk["type"] in {"isin", "is_in"} and args and isinstance(args[0], list):
                args[0] = set(args[0])

            factory = getattr(Check, chk["type"])
            checks.append(factory(*args, **chk.get("kwargs", {})))

        cols[canon] = Column(
            name=canon,
            regex=False,
            dtype=spec["dtype"],
            nullable=spec["nullable"],
            required=True,  # present-by-construction
            checks=checks or None,
            coerce=spec.get("coerce", False),
        )
    return DataFrameSchema(cols, strict=False)


# --------------------------------------------------------------------------- #
# Main validation helpers
# --------------------------------------------------------------------------- #


def _header_pass_and_build_canon_mappings(
    filename: Src,
    enc: str,
    merged_specs: Dict[str, dict],
) -> Tuple[Dict[str, str], Dict[str, str], List[str], List[str], List[str], List[str]]:
    """Run header pass; if missing required columns raise; else return mappings and present_canons."""
    _, raw_to_canon, missing_required, missing_optional, unknown_extra = _header_pass(
        filename, enc, merged_specs, fuzzy_threshold=90
    )
    if missing_required:
        logger.error("Missing required columns: %s", missing_required)
        canon_to_raw_for_missing: Dict[str, str] = {}
        for canon in missing_required:
            for raw, mapped_canon in raw_to_canon.items():
                if mapped_canon == canon:
                    canon_to_raw_for_missing[canon] = raw
                    break
            if canon not in canon_to_raw_for_missing:
                canon_to_raw_for_missing[canon] = canon
        raise HardValidationError(
            missing_required=missing_required,
            raw_to_canon=raw_to_canon,
            canon_to_raw=canon_to_raw_for_missing,
            merged_specs=merged_specs,
        )
    _reset_to_start_if_possible(filename)
    canon_to_raw: Dict[str, str] = {}
    for raw, canon in raw_to_canon.items():
        if canon not in canon_to_raw or normalize_col(raw) == canon:
            canon_to_raw[canon] = raw
    present_canons = sorted(canon_to_raw.keys())
    return (
        raw_to_canon,
        canon_to_raw,
        missing_required,
        missing_optional,
        unknown_extra,
        present_canons,
    )


def _get_csv_read_kwargs(
    filename: Src,
    enc: str,
    canon_to_raw: Dict[str, str],
    merged_specs: Dict[str, dict],
) -> Tuple[Dict[str, Any], str, List[str]]:
    """Build read_csv kwargs and return (read_kwargs, engine, parse_dates_canons)."""
    canon_dtype_map, parse_dates_canons = _pandas_dtype_and_parse_dates(merged_specs)
    raw_dtype_map = {
        canon_to_raw[c]: dt for c, dt in canon_dtype_map.items() if c in canon_to_raw
    }
    parse_dates_raw = [canon_to_raw[c] for c in parse_dates_canons if c in canon_to_raw]
    engine = "c"
    try:
        import pyarrow  # noqa: F401

        engine = "pyarrow"
    except ImportError:
        pass
    read_kwargs: Dict[str, Any] = dict(
        encoding=enc,
        usecols=list(canon_to_raw.values()),
        dtype=raw_dtype_map or None,
        engine=engine,
    )
    if engine == "c" and isinstance(filename, (str, os.PathLike)):
        read_kwargs["memory_map"] = True
        if parse_dates_raw:
            read_kwargs["parse_dates"] = parse_dates_raw
    return read_kwargs, engine, parse_dates_canons


def _read_dataframe_with_specs(
    filename: Src,
    enc: str,
    canon_to_raw: Dict[str, str],
    merged_specs: Dict[str, dict],
) -> pd.DataFrame:
    """Read CSV with spec-based dtypes/parse_dates; return DataFrame with canonical column names."""
    _reset_to_start_if_possible(filename)
    read_kwargs, engine, parse_dates_canons = _get_csv_read_kwargs(
        filename, enc, canon_to_raw, merged_specs
    )
    try:
        df = pd.read_csv(
            filename, **{k: v for k, v in read_kwargs.items() if v is not None}
        )
    except Exception as read_ex:
        logger.exception("CSV read failed: %s", read_ex)
        raise HardValidationError(
            schema_errors="The file could not be read. Please check that it is a valid CSV file.",
            raw_to_canon={raw: canon for canon, raw in canon_to_raw.items()},
            canon_to_raw=canon_to_raw,
            merged_specs=merged_specs,
        ) from read_ex
    if engine == "pyarrow" and parse_dates_canons:
        for canon in parse_dates_canons:
            raw = str(canon_to_raw.get(canon))
            if raw and raw in df.columns:
                df[raw] = pd.to_datetime(df[raw], errors="coerce")
    df.rename(
        columns={
            raw: canon for canon, raw in canon_to_raw.items() if raw in df.columns
        },
        inplace=True,
    )
    return df


def _try_pdp_repo_validation_and_return(
    df: pd.DataFrame,
    model_list: List[str],
    canon_to_raw: Dict[str, str],
    raw_to_canon: Dict[str, str],
    missing_optional: List[str],
    unknown_extra: List[str],
    merged_specs: Dict[str, dict],
    institution_id: str,
) -> Optional[Dict[str, Any]]:
    """If PDP single-model, run repo schema and return result dict; otherwise return None."""
    schema_class = pdp_edvise.get_edvise_schema_for_upload(institution_id, model_list)
    if schema_class is None:
        return None
    validation_df, display_canon_to_raw = (
        pdp_edvise.rename_pdp_dataframe_to_repo_schema(df, canon_to_raw, model_list)
    )
    pdp_edvise.validate_dataframe_with_edvise_schema(
        validation_df,
        schema_class,
        raw_to_canon,
        display_canon_to_raw,
        merged_specs,
    )
    if missing_optional or unknown_extra:
        return {
            "validation_status": "passed_with_soft_errors",
            "schemas": model_list,
            "missing_optional": missing_optional,
            "optional_validation_failures": [],
            "failure_cases": [],
            "unknown_extra_columns": unknown_extra,
            "normalized_df": validation_df,
        }
    return {
        "validation_status": "passed",
        "schemas": model_list,
        "missing_optional": [],
        "unknown_extra_columns": [],
        "normalized_df": validation_df,
    }


def _validate_optional_columns_json(
    df: pd.DataFrame,
    merged_specs: Dict[str, dict],
    present_canons: List[str],
) -> Tuple[List[str], List[dict]]:
    """Validate optional columns with JSON schema; return (opt_failures, failure_cases_records)."""
    optional_canons = [
        c for c in present_canons if not merged_specs[c].get("required", False)
    ]
    opt_failures: List[str] = []
    failure_cases_records: List[dict] = []
    if optional_canons:
        opt_schema = _build_exact_schema(merged_specs, optional_canons)
        try:
            opt_schema.validate(df[optional_canons], lazy=True)
        except SchemaErrors as err:
            opt_failures = sorted(set(err.failure_cases["column"]))
            failure_cases_records = err.failure_cases.to_dict(orient="records")
    return opt_failures, failure_cases_records


def _validate_with_json_schemas_return(
    df: pd.DataFrame,
    model_list: List[str],
    merged_specs: Dict[str, dict],
    present_canons: List[str],
    canon_to_raw: Dict[str, str],
    raw_to_canon: Dict[str, str],
    missing_optional: List[str],
    unknown_extra: List[str],
) -> Dict[str, Any]:
    """Run JSON-based Pandera validation and return result dict (passed or passed_with_soft_errors)."""
    required_canons = [
        c for c in present_canons if merged_specs[c].get("required", False)
    ]
    if required_canons:
        req_schema = _build_exact_schema(merged_specs, required_canons)
        try:
            req_schema.validate(df[required_canons], lazy=False)
        except SchemaErrors as err:
            logger.error("Required column validation failed.")
            raise HardValidationError(
                schema_errors=err.schema_errors,
                failure_cases=err.failure_cases.to_dict(orient="records"),
                raw_to_canon=raw_to_canon,
                canon_to_raw=canon_to_raw,
                merged_specs=merged_specs,
            )
    opt_failures, failure_cases_records = _validate_optional_columns_json(
        df, merged_specs, present_canons
    )
    logger.info("missing_optional = %s", missing_optional)
    if opt_failures or missing_optional or unknown_extra:
        return {
            "validation_status": "passed_with_soft_errors",
            "schemas": model_list,
            "missing_optional": missing_optional,
            "optional_validation_failures": opt_failures,
            "failure_cases": failure_cases_records,
            "unknown_extra_columns": unknown_extra,
            "normalized_df": df,
        }
    return {
        "validation_status": "passed",
        "schemas": model_list,
        "missing_optional": [],
        "unknown_extra_columns": [],
        "normalized_df": df,
    }


def _run_validation_flow(
    df: pd.DataFrame,
    model_list: List[str],
    merged_specs: Dict[str, dict],
    present_canons: List[str],
    canon_to_raw: Dict[str, str],
    raw_to_canon: Dict[str, str],
    missing_optional: List[str],
    unknown_extra: List[str],
    institution_id: str,
) -> Dict[str, Any]:
    """Run PDP path if applicable; otherwise JSON validation. Returns result dict."""
    pdp_result = _try_pdp_repo_validation_and_return(
        df,
        model_list,
        canon_to_raw,
        raw_to_canon,
        missing_optional,
        unknown_extra,
        merged_specs,
        institution_id,
    )
    if pdp_result is not None:
        return pdp_result
    return _validate_with_json_schemas_return(
        df,
        model_list,
        merged_specs,
        present_canons,
        canon_to_raw,
        raw_to_canon,
        missing_optional,
        unknown_extra,
    )


def _compute_model_list_and_merged_specs(
    base_schema: dict,
    ext_schema: Optional[Dict[Any, Any]],
    institution_id: str,
    models: Union[str, List[str], None],
) -> Tuple[List[str], Dict[str, dict]]:
    """Compute model_list and merged_specs from models and schema."""
    if models is None:
        model_list = []
    elif isinstance(models, str):
        model_list = [models]
    else:
        model_list = list(models)
    merged_specs: Dict[str, dict] = {}
    for m in model_list:
        specs = merge_model_columns(base_schema, ext_schema, institution_id, m.lower())
        merged_specs.update(specs)
    return model_list, merged_specs


# --------------------------------------------------------------------------- #
# PDP: use edvise read + validate (single source of truth)
# --------------------------------------------------------------------------- #

# Datetime formats to try for PDP course (same order as pdp_data_audit)
PDP_COURSE_DTTM_FORMATS = ("ISO8601", "%Y%m%d.0", "%Y%m%d")


def _validate_pdp_converter_callables(
    pdp_cohort_converter_func: PDPConverterFunc,
    pdp_course_converter_func: PDPConverterFunc,
) -> None:
    """Raise HardValidationError if a provided converter is not callable (so API returns 400)."""
    if pdp_cohort_converter_func is not None and not callable(
        pdp_cohort_converter_func
    ):
        raise HardValidationError(
            schema_errors="pdp_cohort_converter_func must be callable (DataFrame -> DataFrame)",
            failure_cases=[],
        )
    if pdp_course_converter_func is not None and not callable(
        pdp_course_converter_func
    ):
        raise HardValidationError(
            schema_errors="pdp_course_converter_func must be callable (DataFrame -> DataFrame)",
            failure_cases=[],
        )


def _convert_pdp_schema_errors_to_hard(
    e: Union[SchemaErrors, SchemaError], model_set: set[str]
) -> None:
    """Log and re-raise Pandera schema errors as HardValidationError (no return)."""
    logger.error(
        "PDP edvise schema validation failed: model_set=%s, error=%s",
        model_set,
        e,
        exc_info=True,
    )
    hard = pdp_edvise._convert_schema_errors_to_hard_validation_error(
        e, raw_to_canon={}, canon_to_raw={}, merged_specs={}
    )
    raise hard from e


def _read_pdp_validated_dataframe(
    path: str,
    model_set: set[str],
    cohort_converter: Callable[[pd.DataFrame], pd.DataFrame],
    course_converter_func: PDPConverterFunc,
) -> pd.DataFrame:
    """Read and validate PDP cohort or course data; return validated DataFrame or raise."""
    if model_set == {"STUDENT"}:
        return read_raw_pdp_cohort_data(
            file_path=path,
            schema=pdp_edvise.get_edvise_schema_for_models(["STUDENT"]),
            converter_func=cohort_converter,
            spark_session=None,
        )
    if model_set == {"COURSE"}:
        return _read_pdp_course_edvise(
            path, course_converter_func=course_converter_func
        )
    raise HardValidationError(
        schema_errors=f"PDP single-model expected; got models={list(model_set)}",
        failure_cases=[],
    )


@contextmanager
def _path_for_edvise_read(filename: Src, enc: str) -> Generator[str, None, None]:
    """
    Yield a file path that edvise read_raw_pdp_* can use.

    If filename is a path, yield it. If file-like, read content, write to a temp
    file (utf-8), yield that path; the temp file is always removed on exit.

    Args:
        filename: Path or file-like to read from.
        enc: Encoding used to decode file-like content before writing utf-8 temp.

    Yields:
        Path to a CSV file (original or temp).

    Raises:
        HardValidationError: If file-like read fails (with failure_cases=[str(e)]).
    """
    if isinstance(filename, (str, os.PathLike)):
        yield str(filename)
        return
    try:
        raw = filename.read()
    except Exception as e:
        # Intentionally broad: any read failure becomes HardValidationError for API.
        logger.error("Could not read file for validation: %s", e, exc_info=True)
        raise HardValidationError(
            schema_errors="Could not read file for validation.",
            failure_cases=[str(e)],
        ) from e
    if isinstance(raw, bytes):
        raw = raw.decode(enc)
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        os.write(fd, raw.encode("utf-8"))
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _read_pdp_course_edvise(
    path: str,
    course_converter_func: PDPConverterFunc = None,
) -> pd.DataFrame:
    """
    Read and validate PDP course data via edvise (same as pipeline).

    Tries each datetime format with each converter. If a custom
    course_converter_func is provided (e.g. from a school), it is tried first;
    then :func:`_default_pdp_course_duplicate_converter` (``handling_duplicates``
    with PDP settings). Raises HardValidationError if all attempts fail.

    Args:
        path: Path to course CSV.
        course_converter_func: Optional custom converter (e.g. converter_func_course)
            that schools can provide; if None, only default converters are used.

    Returns:
        Validated DataFrame (same as pipeline output).

    Raises:
        HardValidationError: If no (converter, format) pair succeeded.
    """
    default_converters = (_default_pdp_course_duplicate_converter,)
    converters = (
        (course_converter_func,) if course_converter_func is not None else ()
    ) + default_converters
    last_error: Optional[Exception] = None
    for converter in converters:
        for fmt in PDP_COURSE_DTTM_FORMATS:
            try:
                return read_raw_pdp_course_data(
                    file_path=path,
                    schema=pdp_edvise.get_edvise_schema_for_models(["COURSE"]),
                    dttm_format=fmt,
                    converter_func=converter,
                    spark_session=None,
                )
            except ValueError as e:
                last_error = e
            except TypeError as e:
                if "school_type" in str(e) or "schema_type" in str(e):
                    last_error = None
                    break
                raise
    error_message = (
        "Course data did not parse with any known datetime format."
        if last_error is not None
        else "Course validation failed (datetime format or schema)."
    )
    validation_error = HardValidationError(
        schema_errors=error_message,
        failure_cases=[str(last_error)] if last_error else [],
    )
    logger.error(
        "PDP course validation failed: path=%s, last_error=%s",
        path,
        last_error,
    )
    if last_error is not None:
        raise validation_error from last_error
    raise validation_error


def _validate_pdp_with_edvise_read(
    filename: Src,
    enc: str,
    model_list: List[str],
    institution_id: str,
    pdp_cohort_converter_func: PDPConverterFunc = None,
    pdp_course_converter_func: PDPConverterFunc = None,
) -> Dict[str, Any]:
    """
    Validate PDP cohort or course via edvise read + schema (same as pipeline).

    Resolves filename to a path (temp file if file-like), then calls
    read_raw_pdp_cohort_data or read_raw_pdp_course_data. Uses the same
    converter functions as the edvise repo: cohort converter filters dual
    enrollment students (DE/DS/SE); course converter handles duplicates.
    Schools can provide custom converters via the optional func args.

    Args:
        filename: Path or file-like to CSV.
        enc: Encoding (from sniff_encoding) for file-like decode.
        model_list: Single model, e.g. ["STUDENT"] or ["COURSE"].
        institution_id: Institution schema key (e.g. "pdp").
        pdp_cohort_converter_func: Optional custom cohort converter; if None,
            uses converter_func_cohort from edvise (filters DE/DS/SE).
        pdp_course_converter_func: Optional custom course converter (e.g.
            converter_func_course); if None, uses default handling_duplicates.

    Returns:
        Dict with validation_status, schemas, missing_optional,
        unknown_extra_columns, normalized_df.

    Raises:
        HardValidationError: If read/schema fails (or SchemaErrors converted).
    """
    _reset_to_start_if_possible(filename)
    model_set = {str(m).strip().upper() for m in model_list if m}

    _validate_pdp_converter_callables(
        pdp_cohort_converter_func, pdp_course_converter_func
    )
    cohort_converter = pdp_cohort_converter_func or converter_func_cohort

    with _path_for_edvise_read(filename, enc) as path:
        try:
            df = _read_pdp_validated_dataframe(
                path,
                model_set,
                cohort_converter,
                pdp_course_converter_func,
            )
            return {
                "validation_status": "passed",
                "schemas": model_list,
                "missing_optional": [],
                "unknown_extra_columns": [],
                "normalized_df": df,
            }
        except (SchemaErrors, SchemaError) as e:
            _convert_pdp_schema_errors_to_hard(e, model_set)
        except HardValidationError:
            raise
        except Exception as e:
            logger.exception(
                "PDP validation failed: model_set=%s, error=%s", model_set, e
            )
            raise HardValidationError(
                schema_errors=f"PDP validation failed (model_set={model_set!r}): {e}",
                failure_cases=[str(e)],
            ) from e

    return {}  # Unreachable: every path above returns or raises


# --------------------------------------------------------------------------- #
# Main validation
# --------------------------------------------------------------------------- #


def validate_dataset(
    filename: Src,
    base_schema: dict,
    ext_schema: Optional[Dict[Any, Any]] = None,
    models: Union[str, List[str], None] = None,
    institution_id: str = "pdp",
    institution_identifier: Optional[str] = None,
    pdp_cohort_converter_func: PDPConverterFunc = None,
    pdp_course_converter_func: PDPConverterFunc = None,
) -> Dict[str, Any]:
    """
    Validate a dataset against merged base/extension schemas.

    Steps: encoding, merge specs, header pass, typed read, then PDP repo schema
    (if applicable) or JSON-based validation. Returns dict with validation_status,
    schemas, normalized_df (or None if empty merged_specs). Raises HardValidationError
    on failure; UnicodeError if encoding is not UTF-8/UTF-16/UTF-32.

    For PDP uploads, optional pdp_cohort_converter_func and pdp_course_converter_func
    allow schools to supply custom converters (e.g. from config); if None, edvise
    defaults are used (cohort: filter DE/DS/SE; course: handling_duplicates).
    """
    try:
        enc = sniff_encoding(filename)
    except UnicodeError as ex:
        raise HardValidationError(schema_errors="decode_error", failure_cases=[str(ex)])
    _reset_to_start_if_possible(filename)

    model_list, merged_specs = _compute_model_list_and_merged_specs(
        base_schema, ext_schema, institution_id, models
    )
    if not merged_specs:
        return {
            "validation_status": "passed",
            "schemas": model_list,
            "missing_optional": [],
            "unknown_extra_columns": [],
            "normalized_df": None,
        }

    # PDP single-model: use edvise read + validate (same as pipeline)
    if pdp_edvise.get_edvise_schema_for_upload(institution_id, model_list) is not None:
        return _validate_pdp_with_edvise_read(
            filename,
            enc,
            model_list,
            institution_id,
            pdp_cohort_converter_func=pdp_cohort_converter_func,
            pdp_course_converter_func=pdp_course_converter_func,
        )

    (
        raw_to_canon,
        canon_to_raw,
        missing_required,
        missing_optional,
        unknown_extra,
        present_canons,
    ) = _header_pass_and_build_canon_mappings(filename, enc, merged_specs)

    df = _read_dataframe_with_specs(filename, enc, canon_to_raw, merged_specs)

    return _run_validation_flow(
        df,
        model_list,
        merged_specs,
        present_canons,
        canon_to_raw,
        raw_to_canon,
        missing_optional,
        unknown_extra,
        institution_id,
    )
