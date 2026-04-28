import pytest
from unittest.mock import MagicMock

from .databricks import DatabricksControl, _resolve_pipeline_job


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


def _job_named(full_name: str, job_id: int = 42) -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.settings = MagicMock()
    j.settings.name = full_name
    return j


def test_resolve_pipeline_job_exact_match_skips_scan():
    canonical = "edvise_github_sourced_pdp_inference_pipeline"
    hit = _job_named(canonical, job_id=7)

    def list_jobs(name=None):
        if name == canonical:
            return iter([hit])
        return iter([])

    w = MagicMock()
    w.jobs.list.side_effect = list_jobs

    assert _resolve_pipeline_job(w, canonical, "test").job_id == 7
    w.jobs.list.assert_called_once()


def test_resolve_pipeline_job_substring_dev_prefix():
    canonical = "edvise_github_sourced_pdp_inference_pipeline"
    hit = _job_named(f"[dev vishakh] {canonical}", job_id=11)

    def list_jobs(name=None):
        if name is not None:
            return iter([])
        return iter([hit])

    w = MagicMock()
    w.jobs.list.side_effect = list_jobs

    assert _resolve_pipeline_job(w, canonical, "test").job_id == 11
    assert w.jobs.list.call_count == 2


def test_resolve_pipeline_job_ambiguous_substring_raises():
    canonical = "edvise_github_sourced_pdp_inference_pipeline"
    a = _job_named(f"[dev a] {canonical}", job_id=1)
    b = _job_named(f"[dev b] {canonical}", job_id=2)

    def list_jobs(name=None):
        if name is not None:
            return iter([])
        return iter([a, b])

    w = MagicMock()
    w.jobs.list.side_effect = list_jobs

    with pytest.raises(ValueError, match="Multiple jobs match substring"):
        _resolve_pipeline_job(w, canonical, "test")
