"""Map GenAI batch EDA resolution into API status / user-facing messages.

Pipeline knowledge (``pipeline_runs`` lookup, silver ``pipeline_input`` paths) lives
in ``edvise.dataio.genai_eda_inputs``. This module only translates that result into
the webapp ``ready`` / ``pending_mapping`` / ``unavailable`` response shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd
from databricks.sdk import WorkspaceClient
from edvise.dataio.genai_eda_inputs import resolve_genai_mapped_inputs_for_batch

LOGGER = logging.getLogger(__name__)

GenaiEdaStatus = Literal["ready", "pending_mapping", "unavailable"]

_PENDING_MESSAGE = "EDA will be available when GenAI mapping finishes for this batch."
_UNAVAILABLE_MESSAGE = (
    "Could not load GenAI mapped data for EDA. Try again after mapping completes."
)


@dataclass(frozen=True)
class GenaiMappedEdaResult:
    """Outcome of resolving mapped GenAI files for a webapp batch."""

    status: GenaiEdaStatus
    message: Optional[str] = None
    df_cohort: Optional[pd.DataFrame] = None
    df_course: Optional[pd.DataFrame] = None
    execute_run_id: Optional[str] = None


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
    try:
        mapped = resolve_genai_mapped_inputs_for_batch(
            workspace,
            warehouse_id=warehouse_id,
            catalog=catalog,
            institution_name=inst_name,
            batch_id=batch_id,
        )
    except Exception:
        LOGGER.exception(
            "GenAI EDA mapped-input resolution failed (inst=%s batch_id=%s)",
            inst_name,
            batch_id,
        )
        return GenaiMappedEdaResult(
            status="unavailable",
            message=_UNAVAILABLE_MESSAGE,
        )

    if mapped is None:
        return GenaiMappedEdaResult(
            status="pending_mapping",
            message=_PENDING_MESSAGE,
        )

    return GenaiMappedEdaResult(
        status="ready",
        df_cohort=mapped.df_cohort,
        df_course=mapped.df_course,
        execute_run_id=mapped.execute_run_id,
    )
