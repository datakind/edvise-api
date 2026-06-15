from types import SimpleNamespace
from unittest import mock

import pytest
from unittest.mock import MagicMock

from .databricks import (
    BRONZE_SYNC_BRONZE_SUBDIR,
    BRONZE_SYNC_GCS_SOURCE_PREFIX,
    BRONZE_SYNC_MAX_OBJECTS,
    BRONZE_SYNC_REQUIRE_AT_LEAST_ONE_FILE,
    BRONZE_SYNC_STRICT_MODE,
    CLOUDRUN_BUNDLE_JOB_PREFIX,
    DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV,
    DatabricksBronzeSyncRequest,
    DatabricksControl,
    _build_validated_bronze_sync_job_parameters,
    _resolve_pipeline_job,
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


def test_resolve_pipeline_job_ambiguous_substring_uses_first_match():
    canonical = "edvise_github_sourced_pdp_inference_pipeline"
    a = _job_named(f"[dev a] {canonical}", job_id=1)
    b = _job_named(f"[dev b] {canonical}", job_id=2)

    def list_jobs(name=None):
        if name is not None:
            return iter([])
        return iter([b, a])

    w = MagicMock()
    w.jobs.list.side_effect = list_jobs

    assert _resolve_pipeline_job(w, canonical, "test").job_id == 1


def test_resolve_pipeline_job_prefers_cloudrun_bundle_job():
    canonical = "edvise_github_sourced_pdp_inference_pipeline"
    jobs = [
        _job_named(f"[dev kayla] {canonical}", job_id=1),
        _job_named(f"{CLOUDRUN_BUNDLE_JOB_PREFIX} {canonical}", job_id=99),
        _job_named(f"[dev vishakh] {canonical}", job_id=3),
    ]

    def list_jobs(name=None):
        if name is not None:
            return iter([])
        return iter(jobs)

    w = MagicMock()
    w.jobs.list.side_effect = list_jobs

    assert _resolve_pipeline_job(w, canonical, "test").job_id == 99


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


def test_resolve_bronze_sync_job_id_by_name_single(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    monkeypatch.setenv("ENV", "DEV")
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


def test_resolve_bronze_sync_job_id_by_dev_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    monkeypatch.setenv("ENV", "DEV")
    w = mock.Mock()
    w.jobs.list.return_value = []
    assert _resolve_validated_bronze_sync_job_id(w) == 1005654397694881
    w.jobs.list.assert_called_once_with(name="edvise_validated_gcs_to_bronze_sync")


def test_resolve_bronze_sync_job_id_by_staging_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    monkeypatch.setenv("ENV", "STAGING")
    w = mock.Mock()
    w.jobs.list.return_value = []
    assert _resolve_validated_bronze_sync_job_id(w) == 611181637854021


def test_resolve_bronze_sync_job_id_by_prefixed_bundle_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    monkeypatch.setenv("ENV", "LOCAL")
    job = SimpleNamespace(
        job_id=123,
        settings=SimpleNamespace(
            name="[dev dev_cloudrun_sa] edvise_validated_gcs_to_bronze_sync"
        ),
    )
    w = mock.Mock()
    w.jobs.list.side_effect = [[], [job]]
    assert _resolve_validated_bronze_sync_job_id(w) == 123
    assert w.jobs.list.call_args_list == [
        mock.call(name="edvise_validated_gcs_to_bronze_sync"),
        mock.call(),
    ]


def test_resolve_bronze_sync_job_id_by_prefixed_bundle_name_ambiguous_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    monkeypatch.setenv("ENV", "LOCAL")
    w = mock.Mock()
    w.jobs.list.side_effect = [
        [],
        [
            SimpleNamespace(
                job_id=1,
                settings=SimpleNamespace(
                    name="[dev user_a] edvise_validated_gcs_to_bronze_sync"
                ),
            ),
            SimpleNamespace(
                job_id=2,
                settings=SimpleNamespace(
                    name="[dev user_b] edvise_validated_gcs_to_bronze_sync"
                ),
            ),
        ],
    ]
    with pytest.raises(ValueError, match="Multiple"):
        _resolve_validated_bronze_sync_job_id(w)


def test_resolve_bronze_sync_job_id_by_name_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, raising=False)
    monkeypatch.setenv("ENV", "LOCAL")
    w = mock.Mock()
    w.jobs.list.return_value = []
    with pytest.raises(ValueError, match="not found"):
        _resolve_validated_bronze_sync_job_id(w)


def test_build_validated_bronze_sync_job_parameters_shape() -> None:
    req = DatabricksBronzeSyncRequest(
        inst_name="Test School",
        gcp_bucket_name="bucket-a",
        validated_blob_paths=["validated/student.csv"],
    )
    params = _build_validated_bronze_sync_job_parameters(req, "test_school")
    assert params["gcp_bucket_name"] == "bucket-a"
    assert params["databricks_institution_name"] == "test_school"
    assert params["gcs_source_prefix"] == BRONZE_SYNC_GCS_SOURCE_PREFIX
    assert params["bronze_subdir"] == BRONZE_SYNC_BRONZE_SUBDIR
    assert params["max_objects"] == BRONZE_SYNC_MAX_OBJECTS
    assert params["require_at_least_one_file"] == BRONZE_SYNC_REQUIRE_AT_LEAST_ONE_FILE
    assert params["strict_mode"] == BRONZE_SYNC_STRICT_MODE
    assert params["sync_run_id"] == ""
    assert params["include_blob_paths_json"] == '["validated/student.csv"]'


def test_run_validated_gcs_to_bronze_sync_calls_run_now_with_bundle_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DATABRICKS_VALIDATED_BRONZE_SYNC_JOB_ID_ENV, "42")
    workspace = mock.Mock()
    run_response = mock.Mock()
    run_response.response.run_id = 9001
    workspace.jobs.run_now.return_value = run_response

    with mock.patch("src.webapp.databricks.WorkspaceClient", return_value=workspace):
        ctrl = DatabricksControl()
        req = DatabricksBronzeSyncRequest(
            inst_name="My Inst",
            gcp_bucket_name="my-bucket",
            validated_blob_paths=["validated/foo.csv"],
        )
        resp = ctrl.run_validated_gcs_to_bronze_sync(req)

    assert resp.job_run_id == 9001
    workspace.jobs.run_now.assert_called_once()
    run_args, run_kwargs = workspace.jobs.run_now.call_args
    assert run_args[0] == 42
    params = run_kwargs["job_parameters"]
    assert params["include_blob_paths_json"] == '["validated/foo.csv"]'
    assert params["gcs_source_prefix"] == BRONZE_SYNC_GCS_SOURCE_PREFIX
