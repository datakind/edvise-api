"""Unit tests for GenAI mapped EDA resolution helpers."""

from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest

from . import eda_genai


def test_sql_string_literal_escapes_quotes() -> None:
    assert eda_genai._sql_string_literal("a'b") == "'a''b'"


def test_resolve_pending_when_no_pipeline_rows() -> None:
    workspace = mock.Mock()
    with mock.patch.object(
        eda_genai, "lookup_genai_execute_runs_for_batch", return_value=[]
    ):
        result = eda_genai.resolve_genai_mapped_eda(
            workspace,
            warehouse_id="wh",
            catalog="dev_sst_02",
            inst_name="Test School",
            batch_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
    assert result.status == "pending_mapping"
    assert result.df_cohort is None
    assert "mapping finishes" in (result.message or "")


def test_resolve_pending_when_only_running_rows() -> None:
    workspace = mock.Mock()
    rows = [
        {
            "execute_run_id": "ex-1",
            "status": "running",
            "updated_at": "2026-01-01",
            "input_file_paths": "gcs_uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
    ]
    with mock.patch.object(
        eda_genai, "lookup_genai_execute_runs_for_batch", return_value=rows
    ):
        result = eda_genai.resolve_genai_mapped_eda(
            workspace,
            warehouse_id="wh",
            catalog="dev_sst_02",
            inst_name="Test School",
            batch_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
    assert result.status == "pending_mapping"


def test_resolve_ready_loads_parquets() -> None:
    workspace = mock.Mock()
    rows = [
        {
            "execute_run_id": "ex-ready",
            "status": "complete",
            "updated_at": "2026-01-02",
            "input_file_paths": ".../gcs_uploads/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/...",
        }
    ]
    cohort = pd.DataFrame({"learner_id": ["L1"], "entry_year": ["2020-21"]})
    course = pd.DataFrame({"learner_id": ["L1"], "grade": ["3.0"]})

    with (
        mock.patch.object(
            eda_genai, "lookup_genai_execute_runs_for_batch", return_value=rows
        ),
        mock.patch.object(
            eda_genai,
            "_read_volume_parquet",
            side_effect=[cohort, course],
        ),
        mock.patch.dict(eda_genai.env_vars, {"ENV": "DEV"}, clear=False),
    ):
        result = eda_genai.resolve_genai_mapped_eda(
            workspace,
            warehouse_id="wh",
            catalog="dev_sst_02",
            inst_name="Test School",
            batch_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )

    assert result.status == "ready"
    assert result.execute_run_id == "ex-ready"
    assert result.df_cohort is not None
    assert list(result.df_cohort["learner_id"]) == ["L1"]
    assert result.df_course is not None


def test_lookup_rejects_unsafe_batch_id() -> None:
    with pytest.raises(ValueError, match="Invalid batch_id"):
        eda_genai.lookup_genai_execute_runs_for_batch(
            mock.Mock(),
            warehouse_id="wh",
            catalog="dev_sst_02",
            institution_id="test_school",
            batch_id="not-a-batch",
        )


def test_pipeline_input_paths_use_env_volume_schema() -> None:
    with mock.patch.dict(eda_genai.env_vars, {"ENV": "DEV"}, clear=False):
        cohort, course = eda_genai._pipeline_input_paths("hostos_cc", "ex-1")
    assert cohort.startswith("/Volumes/dev_sst_02/hostos_cc_silver/silver_volume/")
    assert cohort.endswith("/pipeline_input/cohort.parquet")
    assert course.endswith("/pipeline_input/course.parquet")
