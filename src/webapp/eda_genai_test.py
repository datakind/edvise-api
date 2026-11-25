"""Unit tests for GenAI EDA API status / message mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pandas as pd

from . import eda_genai


def test_resolve_pending_when_edvise_returns_none() -> None:
    workspace = mock.Mock()
    with mock.patch.object(
        eda_genai, "resolve_genai_mapped_inputs_for_batch", return_value=None
    ):
        result = eda_genai.resolve_genai_mapped_eda(
            workspace,
            warehouse_id="wh",
            catalog="dev_sst_02",
            inst_name="Test School",
            batch_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
    assert result.status == "pending_mapping"
    assert result.inputs is None
    assert result.message == eda_genai._PENDING_MESSAGE


def test_resolve_unavailable_when_edvise_raises() -> None:
    workspace = mock.Mock()
    with mock.patch.object(
        eda_genai,
        "resolve_genai_mapped_inputs_for_batch",
        side_effect=RuntimeError("warehouse down"),
    ):
        result = eda_genai.resolve_genai_mapped_eda(
            workspace,
            warehouse_id="wh",
            catalog="dev_sst_02",
            inst_name="Test School",
            batch_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
    assert result.status == "unavailable"
    assert result.inputs is None
    assert result.message == eda_genai._UNAVAILABLE_MESSAGE


def test_resolve_ready_maps_edvise_inputs() -> None:
    workspace = mock.Mock()
    cohort = pd.DataFrame({"learner_id": ["L1"]})
    course = pd.DataFrame({"learner_id": ["L1"], "grade": ["3.0"]})
    mapped = SimpleNamespace(
        execute_run_id="ex-ready",
        df_cohort=cohort,
        df_course=course,
    )
    with mock.patch.object(
        eda_genai, "resolve_genai_mapped_inputs_for_batch", return_value=mapped
    ):
        result = eda_genai.resolve_genai_mapped_eda(
            workspace,
            warehouse_id="wh",
            catalog="dev_sst_02",
            inst_name="Test School",
            batch_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )

    assert result.status == "ready"
    assert result.message is None
    assert result.inputs is not None
    assert result.inputs.execute_run_id == "ex-ready"
    assert list(result.inputs.df_cohort["learner_id"]) == ["L1"]
