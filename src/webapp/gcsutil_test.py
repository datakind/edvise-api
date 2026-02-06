"""Tests for gcsutil.StorageControl validation and normalized/raw archive flow."""

import io
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.webapp.gcsutil import StorageControl
from src.webapp.validation import HardValidationError


# --------------------------------------------------------------------------- #
# validate_file: input validation
# --------------------------------------------------------------------------- #


def test_validate_file_raises_on_empty_file_name():
    """Rejects empty file_name with clear ValueError."""
    control = StorageControl()
    with pytest.raises(ValueError, match="file_name is required and must be non-empty"):
        control.validate_file(
            bucket_name="test-bucket",
            file_name="",
            allowed_schemas=["STUDENT"],
            base_schema={},
        )


def test_validate_file_raises_on_whitespace_only_file_name():
    """Rejects whitespace-only file_name."""
    control = StorageControl()
    with pytest.raises(ValueError, match="file_name is required and must be non-empty"):
        control.validate_file(
            bucket_name="test-bucket",
            file_name="   ",
            allowed_schemas=["STUDENT"],
            base_schema={},
        )


def test_validate_file_raises_on_file_name_with_slash():
    """Rejects file_name containing '/'."""
    control = StorageControl()
    with pytest.raises(ValueError, match="file_name must not contain"):
        control.validate_file(
            bucket_name="test-bucket",
            file_name="path/to/file.csv",
            allowed_schemas=["STUDENT"],
            base_schema={},
        )


def test_validate_file_raises_on_empty_allowed_schemas():
    """Rejects empty allowed_schemas."""
    control = StorageControl()
    with pytest.raises(ValueError, match="allowed_schemas must not be empty"):
        control.validate_file(
            bucket_name="test-bucket",
            file_name="cohort.csv",
            allowed_schemas=[],
            base_schema={},
        )


# --------------------------------------------------------------------------- #
# validate_file: blob exists and validated-already-exists
# --------------------------------------------------------------------------- #


def test_validate_file_raises_when_unvalidated_blob_not_found():
    """Raises ValueError with clear message when file not in unvalidated/."""
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_bucket.blob.return_value = mock_blob

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    control = StorageControl()
    with patch("src.webapp.gcsutil.storage.Client", return_value=mock_client):
        with pytest.raises(ValueError, match="File not found: unvalidated/cohort.csv"):
            control.validate_file(
                bucket_name="test-bucket",
                file_name="cohort.csv",
                allowed_schemas=["STUDENT"],
                base_schema={},
            )


def test_validate_file_raises_when_normalized_df_none():
    """Raises ValueError when validation returns normalized_df None (e.g. empty schema)."""
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_bucket.blob.return_value = mock_blob

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    control = StorageControl()
    with patch("src.webapp.gcsutil.storage.Client", return_value=mock_client):
        with patch.object(
            control,
            "_run_validation_and_get_normalized_df",
            return_value=(["STUDENT"], None),
        ):
            with pytest.raises(
                ValueError,
                match="normalized_df was not returned",
            ):
                control.validate_file(
                    bucket_name="test-bucket",
                    file_name="cohort.csv",
                    allowed_schemas=["STUDENT"],
                    base_schema={},
                )


def test_validate_file_raises_when_validated_blob_already_exists():
    """Raises ValueError when validated/{file_name} already exists."""
    mock_bucket = MagicMock()
    mock_unvalidated_blob = MagicMock()
    mock_unvalidated_blob.exists.return_value = True
    mock_validated_blob = MagicMock()
    mock_validated_blob.exists.return_value = True

    def blob_side_effect(name: str) -> Any:
        if "unvalidated" in name:
            return mock_unvalidated_blob
        return mock_validated_blob

    mock_bucket.blob.side_effect = blob_side_effect

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    small_df = pd.DataFrame({"a": [1], "b": [2]})
    control = StorageControl()
    with patch("src.webapp.gcsutil.storage.Client", return_value=mock_client):
        with patch.object(
            control,
            "_run_validation_and_get_normalized_df",
            return_value=(["STUDENT"], small_df),
        ):
            with pytest.raises(
                ValueError, match="validated/cohort.csv: File already exists"
            ):
                control.validate_file(
                    bucket_name="test-bucket",
                    file_name="cohort.csv",
                    allowed_schemas=["STUDENT"],
                    base_schema={},
                )


# --------------------------------------------------------------------------- #
# validate_file: success path (archive raw, write validated, delete unvalidated)
# --------------------------------------------------------------------------- #


def test_validate_file_success_archives_raw_writes_validated_deletes_unvalidated():
    """On success: copies to raw/, writes normalized CSV to validated/, deletes unvalidated/."""
    mock_bucket = MagicMock()
    mock_unvalidated_blob = MagicMock()
    mock_unvalidated_blob.exists.return_value = True
    mock_validated_blob = MagicMock()
    mock_validated_blob.exists.return_value = False

    def blob_side_effect(name: str) -> Any:
        if "unvalidated" in name:
            return mock_unvalidated_blob
        return mock_validated_blob

    mock_bucket.blob.side_effect = blob_side_effect

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    small_df = pd.DataFrame({"col_a": [1, 2], "col_b": ["x", "y"]})
    control = StorageControl()
    with patch("src.webapp.gcsutil.storage.Client", return_value=mock_client):
        with patch.object(
            control,
            "_run_validation_and_get_normalized_df",
            return_value=(["STUDENT"], small_df),
        ):
            result = control.validate_file(
                bucket_name="test-bucket",
                file_name="cohort.csv",
                allowed_schemas=["STUDENT"],
                base_schema={},
            )

    assert result == ["STUDENT"]
    mock_bucket.copy_blob.assert_called_once_with(
        mock_unvalidated_blob, mock_bucket, "raw/cohort.csv"
    )
    mock_unvalidated_blob.delete.assert_called_once()
    # _write_dataframe_to_gcs_as_csv is called; it does bucket.blob(validated_blob_name).upload_from_string
    assert mock_bucket.blob.call_count >= 2
    mock_validated_blob.upload_from_string.assert_called_once()
    call_args = mock_validated_blob.upload_from_string.call_args
    assert call_args.kwargs["content_type"] == "text/csv; charset=utf-8"
    uploaded = call_args.args[0]
    assert isinstance(uploaded, bytes)
    assert b"col_a,col_b" in uploaded
    assert b"1,x" in uploaded


# --------------------------------------------------------------------------- #
# validate_file: HardValidationError propagates
# --------------------------------------------------------------------------- #


def test_validate_file_propagates_hard_validation_error():
    """HardValidationError from validation is not wrapped and propagates."""
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_bucket.blob.return_value = mock_blob

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    control = StorageControl()
    with patch("src.webapp.gcsutil.storage.Client", return_value=mock_client):
        with patch.object(
            control,
            "_run_validation_and_get_normalized_df",
            side_effect=HardValidationError(missing_required=["student_id"]),
        ):
            with pytest.raises(HardValidationError, match="student_id"):
                control.validate_file(
                    bucket_name="test-bucket",
                    file_name="cohort.csv",
                    allowed_schemas=["STUDENT"],
                    base_schema={},
                )


# --------------------------------------------------------------------------- #
# _run_validation_and_get_normalized_df
# --------------------------------------------------------------------------- #


def test_run_validation_and_get_normalized_df_returns_names_and_df():
    """Returns (inferred_schema_names, normalized_df) when validation succeeds."""
    mock_blob = MagicMock()
    mock_file = io.StringIO("foo_col,bar_col\n1,a\n2,b\n")
    mock_blob.open.return_value.__enter__ = lambda self: mock_file
    mock_blob.open.return_value.__exit__ = lambda self, *args: None

    control = StorageControl()
    with patch("src.webapp.gcsutil.validate_file_reader") as mock_validate:
        mock_validate.return_value = {
            "validation_status": "passed",
            "schemas": ["STUDENT"],
            "normalized_df": pd.DataFrame({"x": [1]}),
        }
        names, df = control._run_validation_and_get_normalized_df(
            mock_blob,
            "cohort.csv",
            ["STUDENT"],
            {},
            None,
            "pdp",
            None,
        )
    assert names == ["STUDENT"]
    assert df is not None
    assert list(df.columns) == ["x"]


def test_run_validation_and_get_normalized_df_propagates_hard_validation_error():
    """HardValidationError is re-raised without wrapping."""
    mock_blob = MagicMock()
    mock_file = io.StringIO("bad")
    mock_blob.open.return_value.__enter__ = lambda self: mock_file
    mock_blob.open.return_value.__exit__ = lambda self, *args: None

    control = StorageControl()
    with patch(
        "src.webapp.gcsutil.validate_file_reader", side_effect=HardValidationError()
    ):
        with pytest.raises(HardValidationError):
            control._run_validation_and_get_normalized_df(
                mock_blob, "f.csv", ["STUDENT"], {}, None, "pdp", None
            )


# --------------------------------------------------------------------------- #
# _write_dataframe_to_gcs_as_csv
# --------------------------------------------------------------------------- #


def test_write_dataframe_to_gcs_as_csv_uploads_utf8_csv():
    """Writes DataFrame as UTF-8 CSV with correct content_type."""
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    df = pd.DataFrame({"A": [1, 2], "B": ["a", "b"]})
    control = StorageControl()
    control._write_dataframe_to_gcs_as_csv(mock_bucket, "validated/out.csv", df)

    mock_bucket.blob.assert_called_once_with("validated/out.csv")
    mock_blob.upload_from_string.assert_called_once()
    call_args = mock_blob.upload_from_string.call_args
    assert call_args.kwargs["content_type"] == "text/csv; charset=utf-8"
    payload = call_args.args[0]
    assert isinstance(payload, bytes)
    assert payload.decode("utf-8").strip() == "A,B\n1,a\n2,b"
