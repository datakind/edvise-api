from unittest import mock

import pytest

from .databricks import (
    DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV,
    DatabricksControl,
    _resolve_validated_bronze_sync_job_id,
)


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


def test_resolve_bronze_sync_job_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, "12345")
    w = mock.Mock()
    assert _resolve_validated_bronze_sync_job_id(w) == 12345
    w.jobs.list.assert_not_called()


def test_resolve_bronze_sync_job_id_env_invalid_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, "not-a-number")
    w = mock.Mock()
    with pytest.raises(ValueError, match="positive integer"):
        _resolve_validated_bronze_sync_job_id(w)


def test_resolve_bronze_sync_job_id_by_name_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    job = mock.Mock(job_id=99)
    w = mock.Mock()
    w.jobs.list.return_value = [job]
    assert _resolve_validated_bronze_sync_job_id(w) == 99


def test_resolve_bronze_sync_job_id_by_name_ambiguous_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    w = mock.Mock()
    w.jobs.list.return_value = [mock.Mock(job_id=1), mock.Mock(job_id=2)]
    with pytest.raises(ValueError, match="Multiple"):
        _resolve_validated_bronze_sync_job_id(w)


def test_resolve_bronze_sync_job_id_by_name_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    w = mock.Mock()
    w.jobs.list.return_value = []
    with pytest.raises(ValueError, match="not found"):
        _resolve_validated_bronze_sync_job_id(w)
