"""File validation functions for upload workflows.

PDP and Edvise uploads validate through Pandera schemas imported from the
``edvise`` package. Legacy uploads keep the API's any-format CSV read plus PII
guard. The old API-local JSON schema validation path has been removed.
"""

from __future__ import annotations

import io
import os
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
    Union,
    cast,
)

import pandas as pd
from pandera.errors import SchemaError, SchemaErrors

from edvise.dataio.read import read_raw_pdp_cohort_data, read_raw_pdp_course_data
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
    institution_id: str = "pdp",
    institution_identifier: Optional[str] = None,
    pdp_cohort_converter_func: PDPConverterFunc = None,
    pdp_course_converter_func: PDPConverterFunc = None,
) -> dict[str, Any]:
    """
    Validate a CSV from a path or file-like handle against schema selection.

    Thin wrapper around :func:`validate_dataset` with the same arguments
    reordered for call sites that pass ``allowed_schema`` first.

    Args:
        filename: Path or file-like object for the CSV.
        allowed_schema: List of model names to validate against.
        institution_id: Validation namespace: "edvise", "pdp", or "legacy".
        institution_identifier: Optional institution identifier (e.g. UUID) for display/context.
        pdp_cohort_converter_func: Optional cohort row transform before Pandera; default
            None. Batch PDP jobs may still apply school-specific cohort converters via ``dataio``.
        pdp_course_converter_func: Optional course converter; default duplicate handling only.

    Returns:
        Dict with validation_status, schemas, missing_optional, unknown_extra_columns,
        and on success normalized_df (DataFrame, or None if nothing was validated).

    Raises:
        HardValidationError: When required columns are missing, schema validation fails,
            or encoding cannot be resolved (decode failures use failure_cases, not UnicodeError).
    """
    return validate_dataset(
        filename,
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


def _model_list_from_models(models: Union[str, List[str], None]) -> List[str]:
    """Normalize model input to a list without consulting schema documents."""
    if models is None:
        return []
    if isinstance(models, str):
        return [models]
    return list(models)


# --------------------------------------------------------------------------- #
# PDP single-model path: edvise read + Pandera validate. Cohort converter defaults
# to None so PDP validated row sets can differ from batch jobs that use dataio
# converters.
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
    cohort_converter: PDPConverterFunc,
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
    Read and validate a PDP course CSV using edvise helpers.

    Tries each value in ``PDP_COURSE_DTTM_FORMATS`` with each converter: optional
    ``course_converter_func`` first, then :func:`_default_pdp_course_duplicate_converter`.

    Batch PDP jobs may also try school-specific converters from ``dataio``; this
    path only runs converters passed in here, so results may differ from those jobs.

    Args:
        path: Path to course CSV.
        course_converter_func: Optional school-specific converter; if None, only the
            default duplicate-handling converter is used.

    Returns:
        Validated DataFrame from ``read_raw_pdp_course_data`` for the first successful
        converter and datetime format.

    Raises:
        HardValidationError: If every converter and format combination fails.
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


def _validate_edvise_with_repo_schema(
    filename: Src,
    enc: str,
    model_list: List[str],
    institution_id: str,
) -> Dict[str, Any]:
    """Validate Edvise Schema uploads with upstream raw Edvise Pandera schemas."""
    schema_class = pdp_edvise.get_edvise_schema_for_upload(institution_id, model_list)
    if schema_class is None:
        raise HardValidationError(
            schema_errors=f"Edvise repo schema expected; got models={model_list}",
            failure_cases=[],
        )

    with _path_for_edvise_read(filename, enc) as path:
        read_enc = "utf-8" if not isinstance(filename, (str, os.PathLike)) else enc
        try:
            df = pd.read_csv(path, encoding=read_enc, dtype="string")
        except (
            pd.errors.ParserError,
            pd.errors.EmptyDataError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            logger.exception("Edvise CSV read failed: %s", e)
            raise HardValidationError(
                schema_errors="Edvise upload: could not read CSV.",
                failure_cases=[str(e)],
            ) from e

    validated_df = pdp_edvise.validate_dataframe_with_edvise_schema(
        df,
        schema_class,
        raw_to_canon={},
        canon_to_raw={},
        merged_specs={},
    )
    return {
        "validation_status": "passed",
        "schemas": model_list,
        "missing_optional": [],
        "unknown_extra_columns": [],
        "normalized_df": validated_df,
    }


def _validate_pdp_with_edvise_read(
    filename: Src,
    enc: str,
    model_list: List[str],
    institution_id: str,
    pdp_cohort_converter_func: PDPConverterFunc = None,
    pdp_course_converter_func: PDPConverterFunc = None,
) -> Dict[str, Any]:
    """
    Validate a single-model PDP cohort or course file via edvise read and Pandera.

    Writes file-like inputs to a temp path, then calls ``read_raw_pdp_cohort_data``
    (STUDENT) or ``_read_pdp_course_edvise`` (COURSE). Cohort rows are only
    transformed when ``pdp_cohort_converter_func`` is set; batch jobs may still
    filter cohort rows via ``dataio``, so API output rows are not guaranteed to
    match pipeline output for the same file.

    Args:
        filename: Path or file-like CSV source.
        enc: Encoding from :func:`sniff_encoding` (used when materializing file-like input).
        model_list: Exactly one model, e.g. ``["STUDENT"]`` or ``["COURSE"]``.
        institution_id: Schema namespace (e.g. ``"pdp"``); reserved for callers and logging.
        pdp_cohort_converter_func: Optional ``DataFrame -> DataFrame`` step before cohort
            schema validation; ``None`` means validate rows as read.
        pdp_course_converter_func: Optional course converter before default duplicate handling.

    Returns:
        Dict with validation_status, schemas, missing_optional, unknown_extra_columns,
        and normalized_df on success.

    Raises:
        HardValidationError: If converters are non-callable, read fails, or Pandera
            validation fails (including converted SchemaErrors).
    """
    _reset_to_start_if_possible(filename)
    model_set = {str(m).strip().upper() for m in model_list if m}

    _validate_pdp_converter_callables(
        pdp_cohort_converter_func, pdp_course_converter_func
    )
    cohort_converter = pdp_cohort_converter_func

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


def _validate_legacy_any_format(
    filename: Src,
    enc: str,
    models: Union[str, List[str], None],
) -> Dict[str, Any]:
    """
    Legacy institutions: accept any CSV format (encoding check only, no schema).

    Reads the file as CSV with no column or type checks; returns the DataFrame
    as-is as normalized_df so it can be written to validated/.

    Args:
        filename: Path or file-like object for the CSV.
        enc: Encoding already sniffed for the file.
        models: Allowed schema names (e.g. ["STUDENT"]); used for response only.

    Returns:
        Dict with validation_status, schemas, missing_optional, unknown_extra_columns,
        and normalized_df (the DataFrame as read, or empty if read failed/empty).

    Raises:
        HardValidationError: If the file cannot be read or parsed as CSV, or if
            column names indicate PII (e.g. email, ssn, first_name); such files
            are rejected before being written to raw/ or validated/.
    """
    if models is None:
        model_list: List[str] = ["UNKNOWN"]
    elif isinstance(models, str):
        model_list = [models]
    else:
        model_list = list(models)
    if not model_list:
        model_list = ["UNKNOWN"]

    with _path_for_edvise_read(filename, enc) as path:
        read_enc = "utf-8" if not isinstance(filename, (str, os.PathLike)) else enc
        try:
            df = pd.read_csv(path, encoding=read_enc)
        except (
            pd.errors.ParserError,
            pd.errors.EmptyDataError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            logger.exception("Legacy CSV read failed: %s", e)
            raise HardValidationError(
                schema_errors="Legacy upload: could not read CSV.",
                failure_cases=[str(e)],
            ) from e
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame()

    # PII check: reject legacy uploads that contain columns indicating PII (before moving to raw/validated).
    # Run whenever there are columns (including header-only CSVs: df.empty is True for 0 rows).
    if len(df.columns) > 0:
        # Lazy import to avoid circular dependency: validation_error_formatter imports from this module.
        from .validation_error_formatter import _is_pii_column

        pii_columns = [str(c) for c in df.columns if _is_pii_column(str(c))]
        if pii_columns:
            logger.warning(
                "Legacy upload rejected: PII columns detected: %s", pii_columns
            )
            raise HardValidationError(
                schema_errors=(
                    "Legacy upload: file contains columns that may contain personally identifiable information (PII). "
                    "Please remove or de-identify these columns before uploading."
                ),
                failure_cases=pii_columns,
            )

    return {
        "validation_status": "passed",
        "schemas": model_list,
        "missing_optional": [],
        "unknown_extra_columns": [],
        "normalized_df": df,
    }


def validate_dataset(
    filename: Src,
    models: Union[str, List[str], None] = None,
    institution_id: str = "pdp",
    institution_identifier: Optional[str] = None,
    pdp_cohort_converter_func: PDPConverterFunc = None,
    pdp_course_converter_func: PDPConverterFunc = None,
) -> Dict[str, Any]:
    """
    Validate a dataset using the active institution upload workflow.

    Detects encoding, then routes to legacy any-format handling or PDP/Edvise
    repo Pandera validation for supported single-model STUDENT/COURSE uploads.
    Other PDP/Edvise model sets are rejected explicitly; the API-local JSON
    schema validation fallback has been removed.

    Args:
        filename: CSV path or file-like object.
        models: Model name(s) to validate.
        institution_id: Validation namespace, or ``"legacy"`` for any-format validation.
        institution_identifier: Optional UUID string for caller context (e.g. Edvise).
        pdp_cohort_converter_func: Optional cohort transform before Pandera; default ``None``.
            Batch PDP jobs may still apply school-specific cohort converters via ``dataio``.
        pdp_course_converter_func: Optional course converter before default duplicate handling.

    Returns:
        Dict with validation_status, schemas, missing_optional, unknown_extra_columns,
        and normalized_df.

    Raises:
        HardValidationError: On decode failure, missing columns, schema errors, or
            other validation failures (including Unicode decode issues from sniff_encoding).
    """
    try:
        enc = sniff_encoding(filename)
    except UnicodeError as ex:
        raise HardValidationError(schema_errors="decode_error", failure_cases=[str(ex)])
    _reset_to_start_if_possible(filename)

    if institution_id == "legacy":
        return _validate_legacy_any_format(filename, enc, models)

    model_list = _model_list_from_models(models)
    schema_class = pdp_edvise.get_edvise_schema_for_upload(institution_id, model_list)
    if schema_class is not None and institution_id == "edvise":
        return _validate_edvise_with_repo_schema(
            filename,
            enc,
            model_list,
            institution_id,
        )
    if schema_class is not None:
        return _validate_pdp_with_edvise_read(
            filename,
            enc,
            model_list,
            institution_id,
            pdp_cohort_converter_func=pdp_cohort_converter_func,
            pdp_course_converter_func=pdp_course_converter_func,
        )

    supported = "STUDENT and COURSE single-model uploads"
    requested = ", ".join(model_list) if model_list else "none"
    raise HardValidationError(
        schema_errors=(
            f"{institution_id} upload validation only supports {supported} through "
            f"the edvise repo. Requested model(s): {requested}."
        ),
        failure_cases=[],
    )
