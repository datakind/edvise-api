import pytest
from unittest import mock

from databricks.sdk.service.files import DirectoryEntry

from . import databricks as databricks_module
from .databricks import DatabricksControl, _parse_config_toml_to_selection


@pytest.fixture
def ctrl():
    return DatabricksControl()


def test_exact_literal_case_insensitive(ctrl):
    mapping = {"student": "student.csv"}
    assert ctrl.get_key_for_file(mapping, "Student.csv") == "student"


def test_literal_with_suffix_and_same_ext(ctrl):
    mapping = {"student": "student.csv"}
    assert ctrl.get_key_for_file(mapping, "student_20240101.csv") == "student"
    assert ctrl.get_key_for_file(mapping, "student-final.csv") == "student"
    # should not match a different extension
    assert ctrl.get_key_for_file(mapping, "student_20240101.tsv") is None


def test_literal_without_ext_allows_suffix_and_optional_ext(ctrl):
    mapping = {"student": "student"}
    assert ctrl.get_key_for_file(mapping, "student") == "student"
    assert ctrl.get_key_for_file(mapping, "student_v2") == "student"
    assert ctrl.get_key_for_file(mapping, "student_v2.csv") == "student"


def test_regex_fullmatch_ignorecase(ctrl):
    mapping = {"course": r"^course(?:[._-].+)?\.csv$"}
    assert ctrl.get_key_for_file(mapping, "Course_20240101.CSV") == "course"
    assert ctrl.get_key_for_file(mapping, "COURSE.csv") == "course"
    # ensure fullmatch (not substring)
    assert ctrl.get_key_for_file(mapping, "my_course_20240101.csv") is None


def test_list_values_mixed_literal_and_regex(ctrl):
    mapping = {"student": ["students.csv", r"^stud\d+\.csv$"]}
    assert ctrl.get_key_for_file(mapping, "STUD123.csv") == "student"
    assert ctrl.get_key_for_file(mapping, "students_2024.csv") == "student"


def test_invalid_regex_is_ignored(ctrl):
    mapping = {"bad": ["(unclosed", "ok.csv"]}
    # bad regex should be skipped; literal should match
    assert ctrl.get_key_for_file(mapping, "OK.csv") == "bad"


def test_returns_none_when_no_match(ctrl):
    mapping = {"student": "student.csv"}
    assert ctrl.get_key_for_file(mapping, "unknown.csv") is None


def test_parse_config_toml_to_selection_returns_preprocessing_selection():
    """_parse_config_toml_to_selection parses TOML bytes and returns [preprocessing.selection] only."""
    toml_bytes = (
        b"[preprocessing]\nsplits = { train = 0.6, test = 0.2, validate = 0.2 }\n"
        b"[preprocessing.selection]\n"
        b'student_criteria = { enrollment_type = "FIRST-TIME", cohort_term = ["FALL", "SPRING"] }\n'
    )
    result = _parse_config_toml_to_selection(toml_bytes)
    assert result is not None
    assert result == {
        "student_criteria": {
            "enrollment_type": "FIRST-TIME",
            "cohort_term": ["FALL", "SPRING"],
        }
    }


def test_parse_config_toml_to_selection_returns_none_for_invalid_or_missing_section():
    """_parse_config_toml_to_selection returns None when TOML is invalid or section missing."""
    assert _parse_config_toml_to_selection(b"not valid toml {{{") is None
    assert _parse_config_toml_to_selection(b"[other]\nx = 1\n") is None
    assert _parse_config_toml_to_selection(b"[preprocessing]\nx = 1\n") is None


MODEL_RUN_ID_TEST = "0b2e206732ce48f6b644149090c9614a"


def test_read_volume_training_config_returns_none_for_empty_inst_name(ctrl):
    """read_volume_training_config returns None when inst_name is empty."""
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        assert ctrl.read_volume_training_config("", MODEL_RUN_ID_TEST) is None
        assert ctrl.read_volume_training_config("   ", MODEL_RUN_ID_TEST) is None


def test_read_volume_training_config_returns_none_for_empty_model_run_id(ctrl):
    """read_volume_training_config returns None when model_run_id is empty."""
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        assert ctrl.read_volume_training_config("Some University", "") is None
        assert ctrl.read_volume_training_config("Some University", "   ") is None


def test_read_volume_training_config_returns_none_when_env_not_dev_or_staging(ctrl):
    """read_volume_training_config returns None when ENV is LOCAL (no volume schema)."""
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "LOCAL"}):
        result = ctrl.read_volume_training_config("Some University", MODEL_RUN_ID_TEST)
    assert result is None


def test_read_volume_training_config_returns_none_when_databricksify_raises(ctrl):
    """read_volume_training_config returns None when databricksify_inst_name raises ValueError."""
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        with mock.patch.object(
            databricks_module,
            "databricksify_inst_name",
            side_effect=ValueError("invalid chars"),
        ):
            result = ctrl.read_volume_training_config(
                "Bad/Name\\Here", MODEL_RUN_ID_TEST
            )
    assert result is None


def test_read_volume_training_config_returns_none_when_workspace_client_raises(ctrl):
    """read_volume_training_config returns None when WorkspaceClient construction fails."""
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        with mock.patch.object(
            databricks_module,
            "WorkspaceClient",
            side_effect=Exception("connection refused"),
        ):
            result = ctrl.read_volume_training_config(
                "Some University", MODEL_RUN_ID_TEST
            )
    assert result is None


def _one_toml_entry(
    path: str = "/Volumes/dev_sst_02/some_uni_silver/silver_volume/run_id/training.toml",
    name: str = "training.toml",
) -> list[DirectoryEntry]:
    """Single .toml file entry as returned by list_directory_contents (any .toml name)."""
    return [
        DirectoryEntry(path=path, name=name, is_directory=False),
    ]


def test_read_volume_training_config_returns_none_when_list_raises(ctrl):
    """read_volume_training_config returns None when list_directory_contents raises."""
    mock_client = mock.Mock()
    mock_client.files.list_directory_contents.side_effect = Exception("not found")
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        with mock.patch.object(
            databricks_module, "WorkspaceClient", return_value=mock_client
        ):
            result = ctrl.read_volume_training_config(
                "Some University", MODEL_RUN_ID_TEST
            )
    assert result is None


def test_read_volume_training_config_returns_none_when_download_raises(ctrl):
    """read_volume_training_config returns None when files.download raises."""
    mock_client = mock.Mock()
    mock_client.files.list_directory_contents.return_value = iter(_one_toml_entry())
    mock_client.files.download.side_effect = Exception("file not found")
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        with mock.patch.object(
            databricks_module, "WorkspaceClient", return_value=mock_client
        ):
            result = ctrl.read_volume_training_config(
                "Some University", MODEL_RUN_ID_TEST
            )
    assert result is None


def test_read_volume_training_config_returns_none_when_toml_missing_selection_section(
    ctrl,
):
    """read_volume_training_config returns None when config file has no [preprocessing.selection]."""
    mock_response = mock.Mock()
    mock_response.contents.read.return_value = b"[other]\nx = 1\n"
    mock_client = mock.Mock()
    mock_client.files.list_directory_contents.return_value = iter(_one_toml_entry())
    mock_client.files.download.return_value = mock_response
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        with mock.patch.object(
            databricks_module, "WorkspaceClient", return_value=mock_client
        ):
            result = ctrl.read_volume_training_config(
                "Some University", MODEL_RUN_ID_TEST
            )
    assert result is None


def test_read_volume_training_config_returns_selection_when_toml_found_under_run_dir(
    ctrl,
):
    """read_volume_training_config returns [preprocessing.selection] when any .toml under run dir has it."""
    toml_bytes = (
        b"[preprocessing]\n[preprocessing.selection]\n"
        b'student_criteria = { enrollment_type = "FIRST-TIME" }\n'
    )
    mock_response = mock.Mock()
    mock_response.contents.read.return_value = toml_bytes
    mock_client = mock.Mock()
    # Any .toml name is accepted (e.g. training.toml, config.toml, preprocessing.toml)
    mock_client.files.list_directory_contents.return_value = iter(
        _one_toml_entry(
            "/Volumes/dev_sst_02/some_uni_silver/silver_volume/run_id/training.toml",
            name="training.toml",
        )
    )
    mock_client.files.download.return_value = mock_response
    with mock.patch.dict(databricks_module.env_vars, {"ENV": "DEV"}):
        with mock.patch.object(
            databricks_module, "WorkspaceClient", return_value=mock_client
        ):
            result = ctrl.read_volume_training_config(
                "Some University", MODEL_RUN_ID_TEST
            )
    assert result is not None
    assert result.get("student_criteria") == {"enrollment_type": "FIRST-TIME"}
