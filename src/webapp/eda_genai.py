"""Resolve GenAI mapped cohort/course files for batch EDA (Step 2).

For GenAI schools, ``GET .../batch/{batch_id}/eda`` must not use raw webapp uploads.
Instead look up ``{catalog}.genai_mapping.pipeline_runs`` for an execute run whose
``input_file_paths`` reference ``gcs_uploads/{batch_id}``, then load that run's
``pipeline_input/cohort.parquet`` and ``course.parquet`` from the silver volume.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

import pandas as pd
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    Disposition,
    ExecuteStatementRequestOnWaitTimeout,
    Format,
    StatementState,
)

from .config import ENV_TO_VOLUME_SCHEMA, databricks_vars, env_vars
from .utilities import databricksify_inst_name

LOGGER = logging.getLogger(__name__)

GenaiEdaStatus = Literal["ready", "pending_mapping", "unavailable"]

_PENDING_MESSAGE = "EDA will be available when GenAI mapping finishes for this batch."
_UNAVAILABLE_MESSAGE = (
    "Could not load GenAI mapped data for EDA. Try again after mapping completes."
)

# Webapp batch ids are UUID hex strings; keep SQL LIKE needles tightly scoped.
_BATCH_ID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")


@dataclass(frozen=True)
class GenaiMappedEdaResult:
    """Outcome of resolving mapped GenAI files for a webapp batch."""

    status: GenaiEdaStatus
    message: Optional[str] = None
    df_cohort: Optional[pd.DataFrame] = None
    df_course: Optional[pd.DataFrame] = None
    execute_run_id: Optional[str] = None


def _sql_string_literal(value: str) -> str:
    """Escape a value for use inside a single-quoted SQL string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _volume_schema() -> Optional[str]:
    env = str(env_vars.get("ENV") or "").strip().upper()
    if env in ENV_TO_VOLUME_SCHEMA:
        return ENV_TO_VOLUME_SCHEMA[env]
    catalog = (databricks_vars.get("CATALOG_NAME") or "").strip()
    return catalog or None


def _pipeline_input_paths(inst_slug: str, execute_run_id: str) -> tuple[str, str]:
    volume_schema = _volume_schema()
    if not volume_schema:
        raise ValueError("No Unity Catalog volume schema configured for this ENV.")
    root = (
        f"/Volumes/{volume_schema}/{inst_slug}_silver/silver_volume/"
        f"genai_mapping/runs/execute/{execute_run_id}/pipeline_input"
    )
    return f"{root}/cohort.parquet", f"{root}/course.parquet"


def _download_volume_bytes(workspace: WorkspaceClient, volume_path: str) -> bytes:
    response = workspace.files.download(volume_path)
    stream = getattr(response, "contents", None)
    if stream is None:
        raise FileNotFoundError(
            f"Databricks download returned no contents: {volume_path}"
        )
    raw = stream.read()
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return bytes(raw)


def _read_volume_parquet(workspace: WorkspaceClient, volume_path: str) -> pd.DataFrame:
    raw = _download_volume_bytes(workspace, volume_path)
    return pd.read_parquet(io.BytesIO(raw))


def _execute_sql(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    statement: str,
) -> list[dict[str, Any]]:
    resp = workspace.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        disposition=Disposition.INLINE,
        format=Format.JSON_ARRAY,
        wait_timeout="30s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )
    if not resp.status or resp.status.state != StatementState.SUCCEEDED:
        raise TimeoutError(
            "Databricks SQL did not succeed for GenAI EDA lookup "
            f"(state={getattr(resp.status, 'state', None)!r})"
        )
    cols: list[str] = [
        str(c.name)
        for c in (resp.manifest.schema.columns or [])  # type: ignore[union-attr]
        if c.name is not None
    ]
    rows = resp.result.data_array or []  # type: ignore[union-attr]
    return [dict(zip(cols, row)) for row in rows]


def lookup_genai_execute_runs_for_batch(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
    institution_id: str,
    batch_id: str,
) -> list[dict[str, Any]]:
    """
    Return execute-mode ``pipeline_runs`` rows tied to ``gcs_uploads/{batch_id}``.

    Newest ``updated_at`` first.
    """
    if not _BATCH_ID_RE.fullmatch(batch_id.strip()):
        raise ValueError(f"Invalid batch_id for GenAI EDA lookup: {batch_id!r}")

    needle = f"gcs_uploads/{batch_id.strip()}"
    table = f"`{catalog}`.`genai_mapping`.`pipeline_runs`"
    sql = f"""
SELECT execute_run_id, status, updated_at, input_file_paths
FROM {table}
WHERE institution_id = {_sql_string_literal(institution_id)}
  AND execute_run_id IS NOT NULL
  AND input_file_paths LIKE {_sql_string_literal("%" + needle + "%")}
ORDER BY updated_at DESC
LIMIT 20
"""
    return _execute_sql(workspace, warehouse_id=warehouse_id, statement=sql)


def resolve_genai_mapped_eda(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
    inst_name: str,
    batch_id: str,
) -> GenaiMappedEdaResult:
    """
    Resolve mapped cohort/course DataFrames for a GenAI webapp batch.

    * ``ready`` — completed execute run found; parquets loaded.
    * ``pending_mapping`` — no completed mapped run for this batch yet.
    * ``unavailable`` — lookup/load failed unexpectedly.
    """
    inst_slug = databricksify_inst_name(inst_name)
    try:
        rows = lookup_genai_execute_runs_for_batch(
            workspace,
            warehouse_id=warehouse_id,
            catalog=catalog,
            institution_id=inst_slug,
            batch_id=batch_id,
        )
    except Exception:
        LOGGER.exception(
            "GenAI EDA pipeline_runs lookup failed (inst=%s batch_id=%s)",
            inst_slug,
            batch_id,
        )
        return GenaiMappedEdaResult(
            status="unavailable",
            message=_UNAVAILABLE_MESSAGE,
        )

    if not rows:
        return GenaiMappedEdaResult(
            status="pending_mapping",
            message=_PENDING_MESSAGE,
        )

    complete_rows = [
        r
        for r in rows
        if str(r.get("status") or "").strip().lower() in {"complete", "completed"}
    ]
    if not complete_rows:
        return GenaiMappedEdaResult(
            status="pending_mapping",
            message=_PENDING_MESSAGE,
        )

    execute_run_id = str(complete_rows[0].get("execute_run_id") or "").strip()
    if not execute_run_id:
        return GenaiMappedEdaResult(
            status="pending_mapping",
            message=_PENDING_MESSAGE,
        )

    try:
        cohort_path, course_path = _pipeline_input_paths(inst_slug, execute_run_id)
        df_cohort = _read_volume_parquet(workspace, cohort_path)
        df_course = _read_volume_parquet(workspace, course_path)
    except Exception:
        LOGGER.exception(
            "GenAI EDA failed loading pipeline_input parquets "
            "(inst=%s execute_run_id=%s batch_id=%s)",
            inst_slug,
            execute_run_id,
            batch_id,
        )
        return GenaiMappedEdaResult(
            status="unavailable",
            message=_UNAVAILABLE_MESSAGE,
            execute_run_id=execute_run_id,
        )

    if df_cohort is None or df_cohort.empty:
        return GenaiMappedEdaResult(
            status="pending_mapping",
            message=_PENDING_MESSAGE,
            execute_run_id=execute_run_id,
        )

    return GenaiMappedEdaResult(
        status="ready",
        df_cohort=df_cohort,
        df_course=df_course if df_course is not None else None,
        execute_run_id=execute_run_id,
    )
