"""File validation functions for upload workflows.

PDP uploads validate through Pandera schemas imported from the ``edvise``
package (optional school converters may be passed in). Edvise Schema (ES)
uploads fetch school ``training_inputs/dataio.py`` converters and
``training_inputs/config.toml`` grade maps from the institution bronze volume
(when present), then validate via ``read_raw_es_*`` + ES Pandera schemas —
parity with ES Databricks data-audit jobs. Legacy uploads use any-format CSV
read plus a PII column-name guard.
The old API-local JSON schema validation path has been removed.
"""

from __future__ import annotations

import io
import importlib.util
import os
import re
import logging
import sys
import tempfile
import uuid
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
from pandera.errors import SchemaError, SchemaErrors

from edvise.data_audit.raw_course_grade_map import (
    apply_raw_course_grade_map,
    resolve_es_grade_map,
)
from edvise.dataio.read import (
    read_raw_es_cohort_data,
    read_raw_es_course_data,
    read_raw_pdp_cohort_data,
    read_raw_pdp_course_data,
)
from edvise.utils.data_cleaning import handling_duplicates

from . import validation_pdp_edvise as pdp_edvise

# Type for PDP/ES converter functions (DataFrame -> DataFrame); used for cohort/course.
PDPConverterFunc = Optional[Callable[[pd.DataFrame], pd.DataFrame]]


def _default_pdp_course_duplicate_converter(df: pd.DataFrame) -> pd.DataFrame:
    """PDP course duplicate cleanup for ``read_raw_pdp_course_data``."""
    return handling_duplicates(df)


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
        institution_identifier: For ES, institution name for bronze ``dataio`` lookup.
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

# Datetime formats for ES cohort/course (same order as es_data_audit)
ES_DTTM_FORMATS = ("ISO8601", "%Y%m%d.0")


def _reject_pii_columns(columns: Any) -> None:
    """Raise HardValidationError if any column name looks like PII."""
    # Lazy import to avoid circular dependency: validation_error_formatter imports from this module.
    from .validation_error_formatter import _is_pii_column

    pii_columns = [str(c) for c in columns if _is_pii_column(str(c))]
    if pii_columns:
        logger.warning("Upload rejected: PII columns detected: %s", pii_columns)
        raise HardValidationError(
            schema_errors=(
                "Upload: file contains columns that may contain personally "
                "identifiable information (PII). Please remove or de-identify "
                "these columns before uploading."
            ),
            failure_cases=pii_columns,
        )


def _read_stream_to_bytes(stream: Any) -> bytes:
    """Read a Databricks files download stream (or bytes-like) into bytes."""
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream)
    read = getattr(stream, "read", None)
    if not callable(read):
        raise ValueError("Download stream has no read() method.")
    data = read()
    if isinstance(data, str):
        return data.encode("utf-8")
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Download stream read() did not return bytes.")
    return bytes(data)


def _import_dataio_module_isolated(inst_name: str, file_path: str) -> Any:
    """
    Import a ``dataio.py`` file under a unique module name (no shared ``dataio`` cache).

    Does not mutate ``sys.path``. Registers the module under a unique name so
    converters from different institutions never collide in ``sys.modules``.
    """
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", inst_name) or "unknown"
    module_name = f"es_bronze_dataio_{safe}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    # Unique name only — never reuse bare "dataio".
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_es_converters_from_bronze(
    inst_name: str,
) -> Tuple[PDPConverterFunc, PDPConverterFunc]:
    """
    Download ``training_inputs/dataio.py`` from the institution bronze volume and
    load ``converter_func_cohort`` / ``converter_func_course`` when present.

    Soft-falls back to ``(None, None)`` on missing file, download failure, import
    failure, or missing attributes — parity with ES Databricks data-audit jobs.
    Fresh fetch per call; no process-wide shared ``dataio`` module.

    Converter *runtime* errors during validation are not soft-fallbacked; they
    surface as ``HardValidationError`` (fail closed for upload UX).

    Args:
        inst_name: Institution name used to resolve the bronze volume path.

    Returns:
        ``(cohort_converter | None, course_converter | None)``.
    """
    # Lazy import: DatabricksControl pulls in a large dependency surface.
    from .databricks import DatabricksControl

    tmp_path: Optional[str] = None
    try:
        stream = DatabricksControl().download_bronze_training_inputs_file(
            inst_name, relative_path="dataio.py"
        )
        raw = _read_stream_to_bytes(stream)
        fd, tmp_path = tempfile.mkstemp(suffix="_dataio.py", prefix="es_bronze_")
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)

        module = _import_dataio_module_isolated(inst_name, tmp_path)
    except Exception as e:
        logger.warning(
            "ES bronze dataio unavailable for institution=%s; validating without "
            "converters: %s",
            inst_name,
            e,
        )
        return None, None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    cohort_converter: PDPConverterFunc = None
    course_converter: PDPConverterFunc = None
    try:
        cohort_converter = module.converter_func_cohort
        logger.info("Loaded custom ES cohort converter for institution=%s", inst_name)
    except Exception as e:
        logger.info(
            "Running ES validation with default cohort converter for institution=%s",
            inst_name,
        )
        logger.warning("Failed to load custom ES cohort converter: %s", e)
    try:
        course_converter = module.converter_func_course
        logger.info("Loaded custom ES course converter for institution=%s", inst_name)
    except Exception as e:
        logger.info(
            "Running ES validation with default course converter for institution=%s",
            inst_name,
        )
        logger.warning("Failed to load custom ES course converter: %s", e)

    if cohort_converter is not None and not callable(cohort_converter):
        logger.warning(
            "converter_func_cohort for institution=%s is not callable; ignoring",
            inst_name,
        )
        cohort_converter = None
    if course_converter is not None and not callable(course_converter):
        logger.warning(
            "converter_func_course for institution=%s is not callable; ignoring",
            inst_name,
        )
        course_converter = None

    return cohort_converter, course_converter


def load_es_institution_grade_map_from_bronze(
    inst_name: str,
) -> Optional[Dict[str, str]]:
    """
    Download ``training_inputs/config.toml`` and return institution ``grade_map``.

    Soft-falls back to ``None`` (caller should still apply platform defaults via
    ``resolve_es_grade_map``) when config is missing or unreadable — parity with
    jobs that always merge defaults even without school overrides.
    """
    from edvise.configs.es import ESProjectConfig
    from edvise.dataio.read import read_config

    from .databricks import DatabricksControl

    tmp_path: Optional[str] = None
    try:
        stream = DatabricksControl().download_bronze_training_inputs_file(
            inst_name, relative_path="config.toml"
        )
        raw = _read_stream_to_bytes(stream)
        fd, tmp_path = tempfile.mkstemp(suffix="_config.toml", prefix="es_bronze_")
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)

        cfg = read_config(file_path=tmp_path, schema=ESProjectConfig)
        pre = getattr(cfg, "preprocessing", None)
        features = getattr(pre, "features", None) if pre is not None else None
        grade_map = (
            getattr(features, "grade_map", None) if features is not None else None
        )
        if grade_map:
            logger.info(
                "Loaded ES institution grade_map from bronze config for institution=%s "
                "(%s entries)",
                inst_name,
                len(grade_map),
            )
        else:
            logger.info(
                "ES bronze config.toml for institution=%s has no grade_map; "
                "using platform defaults only",
                inst_name,
            )
        return cast(Optional[Dict[str, str]], grade_map)
    except Exception as e:
        logger.warning(
            "ES bronze config.toml unavailable for institution=%s; using platform "
            "grade_map defaults only: %s",
            inst_name,
            e,
        )
        return None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _chain_es_course_converters(
    course_converter_func: PDPConverterFunc,
    grade_map: Dict[str, str],
) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """
    Match ES data-audit order: config grade_map first, then school dataio converter.

    Always returns a callable because ``resolve_es_grade_map`` yields at least
    platform defaults.
    """

    def _course_converter_chain(df: pd.DataFrame) -> pd.DataFrame:
        df = apply_raw_course_grade_map(df, grade_map)
        if course_converter_func is not None:
            df = course_converter_func(df)
        return df

    return _course_converter_chain


def resolve_es_course_converter_for_upload(
    inst_name: Optional[str],
    course_converter_func: PDPConverterFunc,
) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """
    Build the course converter used at ES upload time (job parity).

    Loads institution ``grade_map`` from bronze ``config.toml`` when ``inst_name``
    is set (soft-fallback to platform defaults), then chains grade map + optional
    ``dataio.converter_func_course``.
    """
    institution_map: Optional[Dict[str, str]] = None
    if inst_name:
        institution_map = load_es_institution_grade_map_from_bronze(inst_name)
    else:
        logger.warning(
            "ES course validation without institution_identifier; using platform "
            "grade_map defaults only"
        )
    grade_map = resolve_es_grade_map(institution_map)
    return _chain_es_course_converters(course_converter_func, grade_map)


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
    Read and validate a PDP course CSV via :func:`read_raw_pdp_course_data`.

    Uses the COURSE schema and ``course_converter_func`` when provided, otherwise
    :func:`_default_pdp_course_duplicate_converter`.

    Args:
        path: Path to course CSV.
        course_converter_func: Optional school-specific converter.

    Returns:
        Validated DataFrame.

    Raises:
        HardValidationError: If the file cannot be read or validated.
    """
    converter = (
        course_converter_func
        if course_converter_func is not None
        else _default_pdp_course_duplicate_converter
    )
    try:
        return read_raw_pdp_course_data(
            file_path=path,
            schema=pdp_edvise.get_edvise_schema_for_models(["COURSE"]),
            converter_func=converter,
            spark_session=None,
        )
    except ValueError as e:
        logger.error("PDP course validation failed: path=%s, error=%s", path, e)
        raise HardValidationError(
            schema_errors=str(e),
            failure_cases=[str(e)],
        ) from e


def _validate_edvise_with_repo_schema(
    filename: Src,
    enc: str,
    model_list: List[str],
    institution_id: str,
    cohort_converter_func: PDPConverterFunc = None,
    course_converter_func: PDPConverterFunc = None,
) -> Dict[str, Any]:
    """
    Validate Edvise Schema uploads via ``read_raw_es_*`` + raw Edvise Pandera schemas.

    Matches ES Databricks data-audit: optional school converters, then schema
    validation with the same datetime-format retry order as ``es_data_audit``.
    Converter runtime errors fail closed as ``HardValidationError``.
    """
    schema_class = pdp_edvise.get_edvise_schema_for_upload(institution_id, model_list)
    if schema_class is None:
        raise HardValidationError(
            schema_errors=f"Edvise repo schema expected; got models={model_list}",
            failure_cases=[],
        )
    model_set = {str(m).strip().upper() for m in model_list if m}

    with _path_for_edvise_read(filename, enc) as path:
        read_enc = "utf-8" if not isinstance(filename, (str, os.PathLike)) else enc
        try:
            header_df = pd.read_csv(path, encoding=read_enc, nrows=0)
        except (
            pd.errors.ParserError,
            pd.errors.EmptyDataError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            logger.exception("Edvise CSV header read failed: %s", e)
            raise HardValidationError(
                schema_errors="Edvise upload: could not read CSV.",
                failure_cases=[str(e)],
            ) from e
        _reject_pii_columns(header_df.columns)

        try:
            df = _read_es_validated_dataframe(
                path,
                model_set,
                schema_class,
                cohort_converter_func,
                course_converter_func,
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
                "ES validation failed: model_set=%s, error=%s", model_set, e
            )
            raise HardValidationError(
                schema_errors=f"ES validation failed (model_set={model_set!r}): {e}",
                failure_cases=[str(e)],
            ) from e

    return {}  # Unreachable: every path above returns or raises


def _read_es_validated_dataframe(
    path: str,
    model_set: set[str],
    schema_class: type,
    cohort_converter_func: PDPConverterFunc,
    course_converter_func: PDPConverterFunc,
) -> pd.DataFrame:
    """Read and validate ES cohort or course data; return validated DataFrame or raise."""
    if model_set == {"STUDENT"}:
        last_error: Optional[Exception] = None
        for fmt in ES_DTTM_FORMATS:
            try:
                return read_raw_es_cohort_data(
                    file_path=path,
                    schema=schema_class,
                    dttm_format=fmt,
                    converter_func=cohort_converter_func,
                    spark_session=None,
                )
            except ValueError as e:
                last_error = e
                continue
        raise HardValidationError(
            schema_errors=(
                "Failed to parse ES cohort data with all known datetime formats."
            ),
            failure_cases=[str(last_error)] if last_error else [],
        )
    if model_set == {"COURSE"}:
        last_error = None
        for fmt in ES_DTTM_FORMATS:
            try:
                return read_raw_es_course_data(
                    file_path=path,
                    schema=schema_class,
                    dttm_format=fmt,
                    converter_func=course_converter_func,
                    spark_session=None,
                )
            except ValueError as e:
                last_error = e
                continue
        raise HardValidationError(
            schema_errors=(
                "Failed to parse ES course data with all known datetime formats."
            ),
            failure_cases=[str(last_error)] if last_error else [],
        )
    raise HardValidationError(
        schema_errors=f"ES single-model expected; got models={list(model_set)}",
        failure_cases=[],
    )


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


def _validate_any_format_csv(
    filename: Src,
    enc: str,
    models: Union[str, List[str], None],
) -> Dict[str, Any]:
    """
    Accept any CSV format (encoding/parse + PII column-name guard; no Pandera).

    Used for Legacy uploads. Reads the file as CSV with no column or type checks;
    returns the DataFrame as-is as normalized_df so it can be written to validated/.

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
            logger.exception("Any-format CSV read failed: %s", e)
            raise HardValidationError(
                schema_errors="Upload: could not read CSV.",
                failure_cases=[str(e)],
            ) from e
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame()

    # PII check: reject uploads with columns indicating PII (before raw/validated).
    # Run whenever there are columns (including header-only CSVs: df.empty is True for 0 rows).
    if len(df.columns) > 0:
        _reject_pii_columns(df.columns)

    return {
        "validation_status": "passed",
        "schemas": model_list,
        "missing_optional": [],
        "unknown_extra_columns": [],
        "normalized_df": df,
    }


# Back-compat alias for callers/tests that still use the legacy name.
_validate_legacy_any_format = _validate_any_format_csv


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

    Detects encoding, then routes to Legacy any-format handling, ES
    ``read_raw_es_*`` + Pandera (with optional bronze ``dataio`` converters), or
    PDP repo Pandera validation for supported single-model STUDENT/COURSE uploads.
    Other model sets are rejected explicitly; the API-local JSON schema
    validation fallback has been removed.

    Args:
        filename: CSV path or file-like object.
        models: Model name(s) to validate.
        institution_id: Validation namespace (``"pdp"``, ``"edvise"``, or ``"legacy"``).
            ``"legacy"`` skips Pandera; ``"edvise"`` and ``"pdp"`` use repo schemas.
        institution_identifier: For ES, the institution name used to resolve the
            bronze volume ``training_inputs/dataio.py`` path. Unused for PDP/Legacy.
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

    # Legacy: parse CSV + PII guard only (any format).
    if institution_id == "legacy":
        return _validate_any_format_csv(filename, enc, models)

    model_list = _model_list_from_models(models)

    # ES: bronze dataio + config.toml grade_map (soft-fallback) + read_raw_es_* + Pandera.
    if institution_id == "edvise":
        schema_class = pdp_edvise.get_edvise_schema_for_upload(
            institution_id, model_list
        )
        if schema_class is None:
            supported = "STUDENT and COURSE single-model uploads"
            requested = ", ".join(model_list) if model_list else "none"
            raise HardValidationError(
                schema_errors=(
                    f"{institution_id} upload validation only supports {supported} through "
                    f"the edvise repo. Requested model(s): {requested}."
                ),
                failure_cases=[],
            )
        cohort_converter: PDPConverterFunc = None
        course_converter: PDPConverterFunc = None
        if institution_identifier:
            cohort_converter, course_converter = load_es_converters_from_bronze(
                institution_identifier
            )
        else:
            logger.warning(
                "ES validation without institution_identifier; validating without "
                "bronze dataio converters"
            )
        model_set = {str(m).strip().upper() for m in model_list if m}
        if model_set == {"COURSE"}:
            course_converter = resolve_es_course_converter_for_upload(
                institution_identifier, course_converter
            )
        return _validate_edvise_with_repo_schema(
            filename,
            enc,
            model_list,
            institution_id,
            cohort_converter_func=cohort_converter,
            course_converter_func=course_converter,
        )

    schema_class = pdp_edvise.get_edvise_schema_for_upload(institution_id, model_list)
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
