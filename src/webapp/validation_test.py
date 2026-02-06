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
        # Use a custom institution_id so we merge base + extension (not extension-only).
        # PDP/Edvise use extension-only and this test's extension has empty pdp data_models.
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
