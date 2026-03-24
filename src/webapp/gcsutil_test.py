"""Tests for gcsutil.StorageControl validation and normalized/raw archive flow."""

import errno
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.webapp.gcsutil import StorageControl
from src.webapp.validation import HardValidationError


# --------------------------------------------------------------------------- #
# validate_file: input validation
# --------------------------------------------------------------------------- #


def test_validate_file_raises_on_empty_file_name() -> None:
    """Rejects empty file_name with clear ValueError."""
    control = StorageControl()
    with pytest.raises(ValueError, match="file_name is required and must be non-empty"):
        control.validate_file(
            bucket_name="test-bucket",
            file_name="",
            allowed_schemas=["STUDENT"],
            base_schema={},
        )


def test_validate_file_raises_on_whitespace_only_file_name() -> None:
    """Rejects whitespace-only file_name."""
    control = StorageControl()
    with pytest.raises(ValueError, match="file_name is required and must be non-empty"):
        control.validate_file(
            bucket_name="test-bucket",
            file_name="   ",
            allowed_schemas=["STUDENT"],
            base_schema={},
        )


def test_validate_file_raises_on_file_name_with_slash() -> None:
    """Rejects file_name containing '/'."""
    control = StorageControl()
    with pytest.raises(ValueError, match="file_name must not contain"):
        control.validate_file(
            bucket_name="test-bucket",
            file_name="path/to/file.csv",
            allowed_schemas=["STUDENT"],
            base_schema={},
        )


def test_validate_file_raises_on_empty_allowed_schemas() -> None:
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


def test_validate_file_raises_when_unvalidated_blob_not_found() -> None:
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


def test_validate_file_raises_when_normalized_df_none() -> None:
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


def test_validate_file_raises_when_validated_blob_already_exists() -> None:
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


def test_validate_file_success_archives_raw_writes_validated_deletes_unvalidated() -> (
    None
):
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
    uploaded_chunks: list[bytes] = []

    def capture_validated_upload(path: str, **kwargs: Any) -> None:
        with open(path, "rb") as f:
            uploaded_chunks.append(f.read())

    mock_validated_blob.upload_from_filename.side_effect = capture_validated_upload

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
    # _write_dataframe_to_gcs_as_csv uploads from a temp file via upload_from_filename
    assert mock_bucket.blob.call_count >= 2
    mock_validated_blob.upload_from_filename.assert_called_once()
    assert mock_validated_blob.upload_from_filename.call_args.kwargs[
        "content_type"
    ] == ("text/csv; charset=utf-8")
    assert len(uploaded_chunks) == 1
    uploaded = uploaded_chunks[0]
    assert b"col_a,col_b" in uploaded
    assert b"1,x" in uploaded


# --------------------------------------------------------------------------- #
# validate_file: HardValidationError propagates
# --------------------------------------------------------------------------- #


def test_validate_file_propagates_hard_validation_error() -> None:
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


def test_run_validation_and_get_normalized_df_returns_names_and_df() -> None:
    """Returns (inferred_schema_names, normalized_df) when validation succeeds."""
    mock_blob = MagicMock()

    def download_to_path(path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("foo_col,bar_col\n1,a\n2,b\n")

    mock_blob.download_to_filename.side_effect = download_to_path

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


def test_run_validation_and_get_normalized_df_propagates_hard_validation_error() -> (
    None
):
    """HardValidationError is re-raised without wrapping."""
    mock_blob = MagicMock()
    mock_blob.download_to_filename.side_effect = lambda p: open(p, "w").close()

    control = StorageControl()
    with patch(
        "src.webapp.gcsutil.validate_file_reader", side_effect=HardValidationError()
    ):
        with pytest.raises(HardValidationError):
            control._run_validation_and_get_normalized_df(
                mock_blob, "f.csv", ["STUDENT"], {}, None, "pdp", None
            )


def test_run_validation_and_get_normalized_df_propagates_value_error() -> None:
    """ValueError from validate_file_reader (e.g. encoding) is re-raised."""
    mock_blob = MagicMock()
    mock_blob.download_to_filename.side_effect = lambda p: open(p, "w").close()

    control = StorageControl()
    with patch(
        "src.webapp.gcsutil.validate_file_reader",
        side_effect=ValueError("Invalid file format"),
    ):
        with pytest.raises(ValueError, match="Invalid file format"):
            control._run_validation_and_get_normalized_df(
                mock_blob, "f.csv", ["STUDENT"], {}, None, "pdp", None
            )


def test_run_validation_and_get_normalized_df_propagates_unicode_error() -> None:
    """UnicodeError from validate_file_reader (e.g. decode) is re-raised."""
    mock_blob = MagicMock()
    mock_blob.download_to_filename.side_effect = lambda p: open(p, "w").close()

    control = StorageControl()
    with patch(
        "src.webapp.gcsutil.validate_file_reader",
        side_effect=UnicodeDecodeError("utf-8", b"x", 0, 1, "invalid"),
    ):
        with pytest.raises(UnicodeDecodeError):
            control._run_validation_and_get_normalized_df(
                mock_blob, "f.csv", ["STUDENT"], {}, None, "pdp", None
            )


# --------------------------------------------------------------------------- #
# _write_dataframe_to_gcs_as_csv
# --------------------------------------------------------------------------- #


def test_write_dataframe_to_gcs_as_csv_uploads_utf8_csv() -> None:
    """Writes DataFrame as UTF-8 CSV with correct content_type."""
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    df = pd.DataFrame({"A": [1, 2], "B": ["a", "b"]})

    def assert_csv_at_path(path: str, **kwargs: Any) -> None:
        with open(path, "r", encoding="utf-8") as f:
            assert f.read().strip() == "A,B\n1,a\n2,b"

    mock_blob.upload_from_filename.side_effect = assert_csv_at_path

    control = StorageControl()
    control._write_dataframe_to_gcs_as_csv(mock_bucket, "validated/out.csv", df)

    mock_bucket.blob.assert_called_once_with("validated/out.csv")
    mock_blob.upload_from_filename.assert_called_once()
    assert mock_blob.upload_from_filename.call_args.kwargs["content_type"] == (
        "text/csv; charset=utf-8"
    )


def test_run_validation_download_oserror_unlinks_temp_and_skips_validate() -> None:
    """If GCS download fails, temp file is removed and validate_file_reader is not run."""
    fd, real_path = tempfile.mkstemp(suffix=".csv", prefix="test_dl_oserr_")
    mock_blob = MagicMock()
    mock_blob.download_to_filename.side_effect = OSError(
        errno.ENOSPC, "No space left on device"
    )

    control = StorageControl()
    with patch("src.webapp.gcsutil.tempfile.mkstemp", return_value=(fd, real_path)):
        with patch("src.webapp.gcsutil.validate_file_reader") as mock_validate:
            with pytest.raises(OSError, match="No space left"):
                control._run_validation_and_get_normalized_df(
                    mock_blob,
                    "school_course.csv",
                    ["STUDENT"],
                    {},
                    None,
                    "pdp",
                    None,
                )
            mock_validate.assert_not_called()

    assert not os.path.exists(real_path)
    mock_blob.download_to_filename.assert_called_once_with(real_path)


def test_run_validation_download_oserror_logs_errno() -> None:
    """OSError from download_to_filename is logged with errno before re-raise."""
    fd, real_path = tempfile.mkstemp(suffix=".csv", prefix="test_dl_log_")
    mock_blob = MagicMock()
    mock_blob.download_to_filename.side_effect = OSError(
        errno.ENOSPC, "No space left on device"
    )

    control = StorageControl()
    with patch("src.webapp.gcsutil.tempfile.mkstemp", return_value=(fd, real_path)):
        with patch("src.webapp.gcsutil.logger") as mock_logger:
            with pytest.raises(OSError):
                control._run_validation_and_get_normalized_df(
                    mock_blob,
                    "f.csv",
                    ["STUDENT"],
                    {},
                    None,
                    "pdp",
                    None,
                )
            mock_logger.error.assert_called_once()
            msg = mock_logger.error.call_args[0][0]
            assert "download_to_filename failed" in msg
            assert mock_logger.error.call_args[0][3] == errno.ENOSPC

    assert not os.path.exists(real_path)


def test_write_dataframe_to_csv_oserror_unlinks_temp() -> None:
    """If to_csv fails (e.g. disk full), temp file is removed and upload is not attempted."""
    fd, real_path = tempfile.mkstemp(suffix=".csv", prefix="test_csv_oserr_")
    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    control = StorageControl()
    with patch("src.webapp.gcsutil.tempfile.mkstemp", return_value=(fd, real_path)):
        with patch.object(
            pd.DataFrame,
            "to_csv",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        ):
            with patch("src.webapp.gcsutil.logger") as mock_logger:
                with pytest.raises(OSError, match="No space left"):
                    control._write_dataframe_to_gcs_as_csv(
                        mock_bucket,
                        "validated/out.csv",
                        pd.DataFrame({"a": [1]}),
                    )
                mock_logger.error.assert_called_once()
                assert "to_csv failed" in mock_logger.error.call_args[0][0]
                assert mock_logger.error.call_args[0][3] == errno.ENOSPC

    assert not os.path.exists(real_path)
    mock_blob.upload_from_filename.assert_not_called()


def test_write_dataframe_upload_failure_still_unlinks_temp() -> None:
    """If GCS upload fails after to_csv, the local temp file is still deleted."""
    fd, real_path = tempfile.mkstemp(suffix=".csv", prefix="test_upload_fail_")
    mock_blob = MagicMock()
    mock_blob.upload_from_filename.side_effect = RuntimeError("upload failed")
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    control = StorageControl()
    with patch("src.webapp.gcsutil.tempfile.mkstemp", return_value=(fd, real_path)):
        with pytest.raises(RuntimeError, match="upload failed"):
            control._write_dataframe_to_gcs_as_csv(
                mock_bucket,
                "validated/out.csv",
                pd.DataFrame({"x": [1]}),
            )

    assert not os.path.exists(real_path)
