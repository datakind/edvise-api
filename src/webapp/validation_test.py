import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import patch
from src.webapp.validation import validate_file_reader, HardValidationError

# Minimal schema for testing
MOCK_BASE_SCHEMA = {
    "base": {
        "data_models": {
            "test_model": {
                "columns": {
                    "foo_col": {
                        "dtype": "int",
                        "nullable": False,
                        "required": True,
                        "aliases": ["foo"],
                    },
                    "bar_col": {
                        "dtype": "str",
                        "nullable": True,
                        "required": False,
                        "aliases": ["bar"],
                    },
                }
            }
        }
    }
}

MOCK_EXT_SCHEMA: dict = {"institutions": {"pdp": {"data_models": {}}}}

# Extension with "edvise" block only; test_model has required "baz_col" in edvise
MOCK_EXT_SCHEMA_EDVISE: dict = {
    "institutions": {
        "edvise": {
            "data_models": {
                "test_model": {
                    "columns": {
                        "baz_col": {
                            "dtype": "str",
                            "nullable": False,
                            "required": True,
                            "aliases": ["baz"],
                        },
                    }
                }
            }
        }
    }
}


@pytest.fixture
def tmp_csv_file(tmp_path: Path) -> str:
    df = pd.DataFrame({"foo_col": [1, 2], "bar_col": ["a", "b"]})
    file_path = tmp_path / "test.csv"
    df.to_csv(file_path, index=False)
    return str(file_path)


def test_validate_file_reader_passes(tmp_csv_file):
    with (
        patch("src.webapp.validation.load_json") as mock_load,
        patch("os.path.exists", return_value=True),
    ):
        mock_load.side_effect = lambda path: (
            MOCK_BASE_SCHEMA if "base" in path else MOCK_EXT_SCHEMA
        )
        result = validate_file_reader(
            tmp_csv_file,
            ["test_model"],
            base_schema=MOCK_BASE_SCHEMA,
            inst_schema=MOCK_EXT_SCHEMA,
        )
        assert result["validation_status"] == "passed"
        assert result["schemas"] == ["test_model"]


def test_validate_file_reader_return_normalized_df(tmp_csv_file):
    """On success, result includes normalized_df (canonical columns)."""
    with (
        patch("src.webapp.validation.load_json") as mock_load,
        patch("os.path.exists", return_value=True),
    ):
        mock_load.side_effect = lambda path: (
            MOCK_BASE_SCHEMA if "base" in path else MOCK_EXT_SCHEMA
        )
        result = validate_file_reader(
            tmp_csv_file,
            ["test_model"],
            base_schema=MOCK_BASE_SCHEMA,
            inst_schema=MOCK_EXT_SCHEMA,
        )
        assert result["validation_status"] == "passed"
        assert "normalized_df" in result
        assert result["normalized_df"] is not None
        assert list(result["normalized_df"].columns) == ["foo_col", "bar_col"]


def test_validate_file_reader_empty_schema_returns_normalized_df_none(tmp_csv_file):
    """When allowed_schema is empty, short-circuit returns normalized_df: None."""
    result = validate_file_reader(
        tmp_csv_file,
        [],
        base_schema=MOCK_BASE_SCHEMA,
        inst_schema=MOCK_EXT_SCHEMA,
    )
    assert result["validation_status"] == "passed"
    assert result["schemas"] == []
    assert "normalized_df" in result
    assert result["normalized_df"] is None


def test_validate_file_reader_fails_missing_required(tmp_path):
    df = pd.DataFrame({"bar_col": ["x", "y"]})  # Missing "foo_col"
    file_path = tmp_path / "invalid.csv"
    df.to_csv(file_path, index=False)

    with (
        patch("src.webapp.validation.load_json") as mock_load,
        patch("os.path.exists", return_value=True),
    ):
        mock_load.side_effect = lambda path: (
            MOCK_BASE_SCHEMA if "base" in path else MOCK_EXT_SCHEMA
        )
        # Use an arbitrary institutions.* key (not pdp/edvise/legacy) so base+extension merge
        # applies; unrelated to the removed "custom institution" product type.
        with pytest.raises(HardValidationError) as exc_info:
            validate_file_reader(
                str(file_path),
                ["test_model"],
                base_schema=MOCK_BASE_SCHEMA,
                inst_schema=MOCK_EXT_SCHEMA,
                institution_id="custom-inst-id",
            )
        assert "Missing required columns" in str(exc_info.value)


def test_validate_file_reader_uses_institution_id_for_extension_block(
    tmp_path: Path,
    tmp_csv_file: str,
) -> None:
    """Passing institution_id selects the correct extension block (e.g. edvise vs pdp)."""
    # File has base columns only; extension has required "baz_col" only under institutions["edvise"]
    df = pd.DataFrame({"foo_col": [1], "bar_col": ["a"]})
    file_path = tmp_path / "no_baz.csv"
    df.to_csv(file_path, index=False)

    with (
        patch("src.webapp.validation.load_json") as mock_load,
        patch("os.path.exists", return_value=True),
    ):
        mock_load.side_effect = lambda path: (
            MOCK_BASE_SCHEMA if "base" in path else MOCK_EXT_SCHEMA_EDVISE
        )
        # institution_id="edvise" -> merge_model_columns uses institutions["edvise"] -> baz_col required -> missing
        with pytest.raises(HardValidationError) as exc_info:
            validate_file_reader(
                str(file_path),
                ["test_model"],
                base_schema=MOCK_BASE_SCHEMA,
                inst_schema=MOCK_EXT_SCHEMA_EDVISE,
                institution_id="edvise",
            )
        assert "baz_col" in str(exc_info.value) or "Missing required" in str(
            exc_info.value
        )
        # institution_id="pdp" -> institutions["pdp"] missing -> no extra columns merged -> only base required (foo_col present)
        result = validate_file_reader(
            str(file_path),
            ["test_model"],
            base_schema=MOCK_BASE_SCHEMA,
            inst_schema=MOCK_EXT_SCHEMA_EDVISE,
            institution_id="pdp",
        )
        assert result["validation_status"] == "passed"

        # institution_identifier is accepted and does not affect non-Edvise path
        mock_load.side_effect = lambda path: (
            MOCK_BASE_SCHEMA if "base" in path else MOCK_EXT_SCHEMA
        )
        result_with_id = validate_file_reader(
            tmp_csv_file,
            ["test_model"],
            base_schema=MOCK_BASE_SCHEMA,
            inst_schema=MOCK_EXT_SCHEMA,
            institution_identifier="optional-uuid-for-edvise-only",
        )
        assert result_with_id["validation_status"] == "passed"


def test_validate_file_reader_legacy_accepts_any_format(tmp_path: Path) -> None:
    """Legacy institutions: any CSV format is accepted (encoding + read only, no schema)."""
    csv_path = tmp_path / "any_columns.csv"
    csv_path.write_text("a,b,c\n1,2,hello\n3,4,world", encoding="utf-8")
    result = validate_file_reader(
        str(csv_path),
        ["STUDENT"],
        base_schema=MOCK_BASE_SCHEMA,
        inst_schema=None,
        institution_id="legacy",
    )
    assert result["validation_status"] == "passed"
    assert result["schemas"] == ["STUDENT"]
    assert result["normalized_df"] is not None
    df = result["normalized_df"]
    assert list(df.columns) == ["a", "b", "c"]
    assert len(df) == 2


def test_validate_file_reader_legacy_accepts_student_id_column(tmp_path: Path) -> None:
    """Legacy institutions: student_id is an allowed de-identified identifier column."""
    csv_path = tmp_path / "with_student_id.csv"
    csv_path.write_text(
        "student_id,grade,term\nabc123,A,Fall\nxyz789,B,Spring", encoding="utf-8"
    )
    result = validate_file_reader(
        str(csv_path),
        ["STUDENT"],
        base_schema=MOCK_BASE_SCHEMA,
        inst_schema=None,
        institution_id="legacy",
    )
    assert result["validation_status"] == "passed"
    assert list(result["normalized_df"].columns) == ["student_id", "grade", "term"]


def test_validate_file_reader_legacy_header_only_csv_passes(tmp_path: Path) -> None:
    """Legacy institutions: CSV with only a header row (no data) passes; PII check runs on column names only."""
    csv_path = tmp_path / "header_only.csv"
    csv_path.write_text("col_a,col_b,col_c\n", encoding="utf-8")
    result = validate_file_reader(
        str(csv_path),
        ["STUDENT"],
        base_schema=MOCK_BASE_SCHEMA,
        inst_schema=None,
        institution_id="legacy",
    )
    assert result["validation_status"] == "passed"
    df = result["normalized_df"]
    assert list(df.columns) == ["col_a", "col_b", "col_c"]
    assert len(df) == 0


def test_validate_file_reader_legacy_rejects_header_only_pii_columns(
    tmp_path: Path,
) -> None:
    """Legacy institutions: header-only CSV with PII column names is rejected (no data rows)."""
    csv_path = tmp_path / "header_only_pii.csv"
    csv_path.write_text("email,ssn\n", encoding="utf-8")
    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            base_schema=MOCK_BASE_SCHEMA,
            inst_schema=None,
            institution_id="legacy",
        )
    err = exc_info.value
    assert "PII" in (err.schema_errors or "")
    failure_cases = err.failure_cases or []
    assert "email" in failure_cases
    assert "ssn" in failure_cases


def test_validate_file_reader_legacy_rejects_pii_columns(tmp_path: Path) -> None:
    """Legacy institutions: files with column names indicating PII are rejected before raw/validated."""
    csv_path = tmp_path / "with_pii.csv"
    csv_path.write_text(
        "id,email,score\n1,user@example.com,85\n2,other@test.org,90", encoding="utf-8"
    )
    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            base_schema=MOCK_BASE_SCHEMA,
            inst_schema=None,
            institution_id="legacy",
        )
    err = exc_info.value
    assert "PII" in (err.schema_errors or "")
    assert "email" in (err.failure_cases or [])


def test_validate_file_reader_legacy_rejects_multiple_pii_columns(
    tmp_path: Path,
) -> None:
    """Legacy institutions: all detected PII column names are listed in the error."""
    csv_path = tmp_path / "multi_pii.csv"
    csv_path.write_text(
        "first_name,last_name,grade\nAlice,Smith,A\nBob,Jones,B",
        encoding="utf-8",
    )
    with pytest.raises(HardValidationError) as exc_info:
        validate_file_reader(
            str(csv_path),
            ["STUDENT"],
            base_schema=MOCK_BASE_SCHEMA,
            inst_schema=None,
            institution_id="legacy",
        )
    err = exc_info.value
    assert "PII" in (err.schema_errors or "")
    failure_cases = err.failure_cases or []
    assert "first_name" in failure_cases
    assert "last_name" in failure_cases


def test_validate_file_reader_csv_read_failure_raises_hard_validation_error(
    tmp_csv_file: str,
) -> None:
    """When the CSV body cannot be read (e.g. malformed), HardValidationError is raised with a clear message."""
    with (
        patch("src.webapp.validation.load_json") as mock_load,
        patch("os.path.exists", return_value=True),
        patch("src.webapp.validation.pd.read_csv") as mock_read_csv,
    ):
        mock_load.side_effect = lambda path: (
            MOCK_BASE_SCHEMA if "base" in path else MOCK_EXT_SCHEMA
        )
        # First call is header-only (nrows=0); second is full read. Fail the full read.
        call_count = 0

        def read_csv_side_effect(*args: object, **kwargs: object) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            if kwargs.get("nrows") == 0:
                return pd.DataFrame({"foo_col": [], "bar_col": []})
            raise ValueError("Bad CSV data")

        mock_read_csv.side_effect = read_csv_side_effect

        with pytest.raises(HardValidationError, match="valid CSV file") as exc_info:
            validate_file_reader(
                tmp_csv_file,
                ["test_model"],
                base_schema=MOCK_BASE_SCHEMA,
                inst_schema=MOCK_EXT_SCHEMA,
            )
        assert "could not be read" in str(exc_info.value).lower() or "valid CSV" in str(
            exc_info.value
        )
