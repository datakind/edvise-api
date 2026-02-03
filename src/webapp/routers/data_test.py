"""Test file for the data.py file and constituent API functions."""

import uuid
import time
from unittest import mock
from collections import Counter
from fastapi.testclient import TestClient
from typing import Any
import pytest
import sqlalchemy
from sqlalchemy.pool import StaticPool
from sqlalchemy.future import select
from ..test_helper import (
    USR,
    USER_VALID_INST_UUID,
    USER_UUID,
    UUID_INVALID,
    DATETIME_TESTING,
    SAMPLE_UUID,
)
from ..main import app
from ..database import (
    FileTable,
    BatchTable,
    InstTable,
    SchemaRegistryTable,
    DocType,
    Base,
    get_session,
)
from ..utilities import uuid_to_str, get_current_active_user, SchemaType
from .data import (
    router,
    DataOverview,
    DataInfo,
    filter_to_most_recent_cohort,
    get_ordered_cohort_terms,
    get_latest_batch_with_student_and_course,
    apply_student_criteria_count,
)
from ..gcsutil import StorageControl
from ..databricks import DatabricksControl

MOCK_STORAGE = mock.Mock()

UUID_2 = uuid.UUID("9bcbc782-2e71-4441-afa2-7a311024a5ec")
FILE_UUID_1 = uuid.UUID("f0bb3a20-6d92-4254-afed-6a72f43c562a")
FILE_UUID_2 = uuid.UUID("cb02d06c-2a59-486a-9bdd-d394a4fcb833")
FILE_UUID_3 = uuid.UUID("fbe67a2e-50e0-40c7-b7b8-07043cb813a5")
BATCH_UUID = uuid.UUID("5b2420f3-1035-46ab-90eb-74d5df97de43")
CREATOR_UUID = uuid.UUID("0ad8b77c-49fb-459a-84b1-8d2c05722c4a")


def counter_repr(x):
    """Orderless comparison of two iterables."""
    return {frozenset(Counter(item).items()) for item in x}


def same_file_orderless(a_elem: DataInfo, b_elem: DataInfo):  # type: ignore
    """Compares two DataInfo objects."""
    if (
        a_elem["inst_id"] != b_elem["inst_id"]  # type: ignore
        or counter_repr(a_elem["batch_ids"]) != counter_repr(b_elem["batch_ids"])  # type: ignore
        or a_elem["name"] != b_elem["name"]  # type: ignore
        or a_elem["uploader"] != b_elem["uploader"]  # type: ignore
        or a_elem["deleted"] != b_elem["deleted"]  # type: ignore
        or a_elem["source"] != b_elem["source"]  # type: ignore
        or a_elem["deletion_request_time"] != b_elem["deletion_request_time"]  # type: ignore
        or a_elem["retention_days"] != b_elem["retention_days"]  # type: ignore
        or a_elem["sst_generated"] != b_elem["sst_generated"]  # type: ignore
        or a_elem["valid"] != b_elem["valid"]  # type: ignore
        or a_elem["uploaded_date"] != b_elem["uploaded_date"]  # type: ignore
    ):
        return False
    return True


def same_orderless(a: DataOverview, b: DataOverview) -> bool:
    """Compares two DataOverview objects."""
    for a_elem in a["batches"]:  # type: ignore
        found = False
        for b_elem in b["batches"]:  # type: ignore
            if a_elem["batch_id"] != b_elem["batch_id"]:
                continue
            found = True
            if (
                a_elem["inst_id"] != b_elem["inst_id"]
                or a_elem["file_names_to_ids"] == b_elem["file_names_to_ids"]
                or a_elem["name"] != b_elem["name"]
                or a_elem["created_by"] != b_elem["created_by"]
                or a_elem["deleted"] != b_elem["deleted"]
                or a_elem["completed"] != b_elem["completed"]
                or a_elem["deletion_request_time"] != b_elem["deletion_request_time"]
                or a_elem["created_at"] != b_elem["created_at"]
            ):
                return False
        if not found:
            return False
    for a_elem in a["files"]:  # type: ignore
        found = False
        for b_elem in b["files"]:  # type: ignore
            if a_elem["data_id"] != b_elem["data_id"]:
                continue
            found = True
            if not same_file_orderless(a_elem, b_elem):
                return False
        if not found:
            return False
    return True


@pytest.fixture(name="session")
def session_fixture():
    """Unit test database setup."""
    engine = sqlalchemy.create_engine(
        "sqlite://",
        echo=True,
        echo_pool="debug",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    batch_1 = BatchTable(
        id=BATCH_UUID,
        inst_id=USER_VALID_INST_UUID,
        name="batch_foo",
        created_by=CREATOR_UUID,
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
    )
    file_1 = FileTable(
        id=FILE_UUID_1,
        inst_id=USER_VALID_INST_UUID,
        name="file_input_one",
        source="MANUAL_UPLOAD",
        batches={batch_1},
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        sst_generated=False,
        valid=True,
        schemas=[SchemaType.STUDENT],
    )
    file_3 = FileTable(
        id=FILE_UUID_3,
        inst_id=USER_VALID_INST_UUID,
        name="file_output_three",
        batches={batch_1},
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        sst_generated=True,
        valid=True,
        schemas=[SchemaType.STUDENT],
    )
    file_4 = FileTable(
        id=SAMPLE_UUID,
        inst_id=USER_VALID_INST_UUID,
        name="file_output_four",
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        sst_generated=True,
        valid=True,
        schemas=[SchemaType.STUDENT],
    )
    try:
        with sqlalchemy.orm.Session(engine) as session:
            session.add_all(
                [
                    InstTable(
                        id=USER_VALID_INST_UUID,
                        name="school_1",
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                    ),
                    SchemaRegistryTable(
                        doc_type=DocType.base,  # ✅ fix this
                        is_pdp=False,
                        version_label="1.0.0",
                        json_doc={"version": "1.0.0", "base": {"data_models": {}}},
                        is_active=True,
                        created_at=DATETIME_TESTING,
                    ),
                    batch_1,
                    file_1,
                    FileTable(
                        id=FILE_UUID_2,
                        inst_id=USER_VALID_INST_UUID,
                        name="file_input_two",
                        source="PDP_SFTP",
                        batches={batch_1},
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                        sst_generated=False,
                        valid=False,
                        schemas=[SchemaType.COURSE],
                    ),
                    file_3,
                    file_4,
                ]
            )
            session.commit()
            yield session
    finally:
        Base.metadata.drop_all(engine)


@pytest.fixture(name="client")
def client_fixture(session: sqlalchemy.orm.Session, monkeypatch: Any) -> Any:
    """Unit test mocks setup."""
    monkeypatch.setenv("SST_SKIP_EXT_GEN", "1")

    def get_session_override():
        return session

    def get_current_active_user_override():
        return USR

    def storage_control_override():
        return MOCK_STORAGE

    app.include_router(router)
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_current_active_user] = get_current_active_user_override
    app.dependency_overrides[StorageControl] = storage_control_override

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_read_inst_all_input_files(client: TestClient) -> Any:
    """Test GET /institutions/<uuid>/input."""
    response = client.get("/institutions/" + uuid_to_str(UUID_INVALID) + "/input")

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response = client.get(
        "/institutions/" + uuid_to_str(USER_VALID_INST_UUID) + "/input"
    )
    assert response.status_code == 200
    assert same_orderless(  # type: ignore
        response.json(),
        {  # type: ignore
            "batches": [
                {
                    "batch_id": "5b2420f3103546ab90eb74d5df97de43",
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "file_names_to_ids": [
                        {"file_input_one": "f0bb3a206d924254afed6a72f43c562a"},
                        {"file_output_one": "fbe67a2e50e040c7b7b807043cb813a5"},
                    ],
                    "name": "batch_foo",
                    "created_by": "0ad8b77c49fb459a84b18d2c05722c4a",
                    "deleted": False,
                    "completed": False,
                    "deletion_request_time": None,
                    "created_at": "2024-12-24T20:22:20.132022",
                }
            ],
            "files": [
                {
                    "name": "file_input_one",
                    "data_id": "f0bb3a206d924254afed6a72f43c562a",
                    "batch_ids": ["5b2420f3103546ab90eb74d5df97de43"],
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "uploader": "",
                    "source": "MANUAL_UPLOAD",
                    "deleted": False,
                    "deletion_request_time": None,
                    "retention_days": None,
                    "sst_generated": False,
                    "valid": True,
                    "uploaded_date": "2024-12-24T20:22:20.132022",
                },
                {
                    "name": "file_input_two",
                    "data_id": "cb02d06c2a59486a9bddd394a4fcb833",
                    "batch_ids": ["5b2420f3103546ab90eb74d5df97de43"],
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "uploader": "",
                    "source": "PDP_SFTP",
                    "deleted": False,
                    "deletion_request_time": None,
                    "retention_days": None,
                    "sst_generated": False,
                    "valid": False,
                    "uploaded_date": "2024-12-24T20:22:20.132022",
                },
            ],
        },
    )


def test_read_inst_all_output_files(client: TestClient) -> Any:
    """Test GET /institutions/<uuid>/output."""
    MOCK_STORAGE.list_blobs_in_folder.return_value = []
    response = client.get("/institutions/" + uuid_to_str(UUID_INVALID) + "/output")

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response = client.get(
        "/institutions/" + uuid_to_str(USER_VALID_INST_UUID) + "/output"
    )
    assert response.status_code == 200
    assert same_orderless(  # type: ignore
        response.json(),
        {  # type: ignore
            "batches": [
                {
                    "batch_id": "5b2420f3103546ab90eb74d5df97de43",
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "file_names_to_ids": [
                        {"file_input_one": "f0bb3a206d924254afed6a72f43c562a"},
                        {"file_output_three": "fbe67a2e50e040c7b7b807043cb813a5"},
                    ],
                    "name": "batch_foo",
                    "created_by": "0ad8b77c49fb459a84b18d2c05722c4a",
                    "deleted": False,
                    "completed": False,
                    "deletion_request_time": None,
                    "created_at": "2024-12-24T20:22:20.132022",
                }
            ],
            "files": [
                {
                    "name": "file_output_three",
                    "data_id": "fbe67a2e50e040c7b7b807043cb813a5",
                    "batch_ids": ["5b2420f3103546ab90eb74d5df97de43"],
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "uploader": "",
                    "source": None,
                    "deleted": False,
                    "deletion_request_time": None,
                    "retention_days": None,
                    "sst_generated": True,
                    "valid": True,
                    "uploaded_date": "2024-12-24T20:22:20.132022",
                },
                {
                    "name": "file_output_four",
                    "data_id": "e4862c62829440d8ab4c9c298f02f619",
                    "batch_ids": [],
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "uploader": "",
                    "source": None,
                    "deleted": False,
                    "deletion_request_time": None,
                    "retention_days": None,
                    "sst_generated": True,
                    "valid": True,
                    "uploaded_date": "2024-12-24T20:22:20.132022",
                },
            ],
        },
    )


def test_read_batch_info(client: TestClient) -> Any:
    """Test GET /institutions/<uuid>/batch/<uuid>."""
    response = client.get(
        "/institutions/"
        + uuid_to_str(UUID_INVALID)
        + "/batch/"
        + uuid_to_str(BATCH_UUID)
    )

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response = client.get(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/batch/"
        + uuid_to_str(BATCH_UUID)
    )
    assert response.status_code == 200
    assert same_orderless(  # type: ignore
        response.json(),
        {  # type: ignore
            "batches": [
                {
                    "batch_id": "5b2420f3103546ab90eb74d5df97de43",
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "file_names_to_ids": [
                        {"file_input_one": "f0bb3a206d924254afed6a72f43c562a"},
                        {"file_input_two": "cb02d06c2a59486a9bddd394a4fcb833"},
                        {"file_output_three": "fbe67a2e50e040c7b7b807043cb813a5"},
                    ],
                    "name": "batch_foo",
                    "created_by": "0ad8b77c49fb459a84b18d2c05722c4a",
                    "deleted": False,
                    "completed": False,
                    "deletion_request_time": None,
                    "created_at": "2024-12-24T20:22:20.132022",
                }
            ],
            "files": [
                {
                    "name": "file_output_three",
                    "data_id": "fbe67a2e50e040c7b7b807043cb813a5",
                    "batch_ids": ["5b2420f3103546ab90eb74d5df97de43"],
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "uploader": "",
                    "source": None,
                    "deleted": False,
                    "deletion_request_time": None,
                    "retention_days": None,
                    "sst_generated": True,
                    "valid": True,
                    "uploaded_date": "2024-12-24T20:22:20.132022",
                },
                {
                    "name": "file_input_one",
                    "data_id": "f0bb3a206d924254afed6a72f43c562a",
                    "batch_ids": ["5b2420f3103546ab90eb74d5df97de43"],
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "uploader": "",
                    "source": "MANUAL_UPLOAD",
                    "deleted": False,
                    "deletion_request_time": None,
                    "retention_days": None,
                    "sst_generated": False,
                    "valid": True,
                    "uploaded_date": "2024-12-24T20:22:20.132022",
                },
                {
                    "name": "file_input_two",
                    "data_id": "cb02d06c2a59486a9bddd394a4fcb833",
                    "batch_ids": ["5b2420f3103546ab90eb74d5df97de43"],
                    "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
                    "uploader": "",
                    "source": "PDP_SFTP",
                    "deleted": False,
                    "deletion_request_time": None,
                    "retention_days": None,
                    "sst_generated": False,
                    "valid": False,
                    "uploaded_date": "2024-12-24T20:22:20.132022",
                },
            ],
        },
    )


def test_read_file_id_info(client: TestClient) -> Any:
    """Test GET /institutions/<uuid>/file-id/<uuid>."""
    response = client.get(
        "/institutions/"
        + uuid_to_str(UUID_INVALID)
        + "/file-id/"
        + uuid_to_str(FILE_UUID_1)
    )

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response = client.get(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/file-id/"
        + uuid_to_str(FILE_UUID_1)
    )
    assert response.status_code == 200
    assert same_file_orderless(  # type: ignore
        response.json(),
        {  # type: ignore
            "name": "file_input_one",
            "data_id": "f0bb3a206d924254afed6a72f43c562a",
            "batch_ids": ["5b2420f3103546ab90eb74d5df97de43"],
            "inst_id": "1d7c75c33eda42949c6675ea8af97b55",
            "uploader": "",
            "source": "MANUAL_UPLOAD",
            "deleted": False,
            "deletion_request_time": None,
            "retention_days": None,
            "sst_generated": False,
            "valid": True,
            "uploaded_date": "2024-12-24T20:22:20.132022",
        },
    )


def test_retrieve_file_as_bytes(client: TestClient) -> Any:
    """Test GET /institutions/<uuid>/output-file-contents/<file_name>."""
    response = client.get(
        "/institutions/"
        + uuid_to_str(UUID_INVALID)
        + "/output-file-contents/"
        + "val%2Ffile_does_not_exist.csv"
    )

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response = client.get(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/output-file-contents/"
        + "val%2Ffile_does_not_exist.csv"
    )
    assert str(response) == "<Response [404 Not Found]>"
    assert response.text == '{"detail":"No such output file exists."}'


def test_create_batch(client: TestClient) -> None:
    """Test POST /institutions/<uuid>/batch."""
    response = client.post(
        "/institutions/" + uuid_to_str(UUID_INVALID) + "/batch",
        json={"name": "batch_name_foo"},
    )
    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response = client.post(
        "/institutions/" + uuid_to_str(USER_VALID_INST_UUID) + "/batch",
        json={
            "name": "batch_foobar",
            "batch_disabled": "False",
            "file_ids": [uuid_to_str(FILE_UUID_1)],
            "file_names": ["file_input_one", "file_input_two", "file_input_four"],
        },
    )
    assert response.status_code == 200
    assert response.json()["name"] == "batch_foobar"
    assert response.json()["created_by"] == uuid_to_str(USER_UUID)
    assert response.json()["deleted"] is False
    assert response.json()["completed"] is False
    assert response.json()["deletion_request_time"] is None
    assert response.json()["inst_id"] == uuid_to_str(USER_VALID_INST_UUID)
    # file_input_two isn't valid so it shouldn't be addable to a batch.
    assert "file_input_two" not in response.json()["file_names_to_ids"]
    assert "file_input_one" in response.json()["file_names_to_ids"]
    assert (
        uuid_to_str(FILE_UUID_1)
        in response.json()["file_names_to_ids"]["file_input_one"]
    )
    assert len(response.json()["file_names_to_ids"]) == 1


def test_update_batch(client: TestClient) -> None:
    """Test PATCH /institutions/<uuid>/batch."""
    response = client.patch(
        "/institutions/"
        + uuid_to_str(UUID_INVALID)
        + "/batch/"
        + uuid_to_str(BATCH_UUID),
        json={"name": "batch_name_updated_foo"},
    )
    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response = client.patch(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/batch/"
        + uuid_to_str(BATCH_UUID),
        json={
            "name": "batch_name_updated_foo",
            "completed": True,
            "file_ids": [uuid_to_str(FILE_UUID_2)],
        },
    )
    assert response.status_code == 200
    assert response.json()["name"] == "batch_name_updated_foo"
    assert response.json()["created_by"] == uuid_to_str(CREATOR_UUID)
    assert response.json()["deleted"] is None
    assert response.json()["completed"] is True
    assert response.json()["deletion_request_time"] is None
    assert response.json()["inst_id"] == uuid_to_str(USER_VALID_INST_UUID)
    assert response.json()["file_names_to_ids"] == {
        "file_input_two": uuid_to_str(FILE_UUID_2)
    }


def test_validate_success_batch(client: TestClient) -> None:
    """Test PATCH /institutions/<uuid>/batch."""
    MOCK_STORAGE.validate_file.return_value = ["UNKNOWN"]

    # Use validate for manual upload
    response_upload = client.post(
        "/institutions/"
        + uuid_to_str(UUID_INVALID)
        + "/input/validate-upload/file_name.csv",
    )
    assert str(response_upload) == "<Response [401 Unauthorized]>"
    assert (
        response_upload.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response_upload = client.post(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/input/validate-upload/pdp_course_deidentified.csv",
    )
    assert response_upload.status_code == 200
    assert response_upload.json()["name"] == "pdp_course_deidentified.csv"
    assert response_upload.json()["file_types"] == ["COURSE"]
    assert response_upload.json()["inst_id"] == uuid_to_str(USER_VALID_INST_UUID)
    assert response_upload.json()["source"] == "MANUAL_UPLOAD"

    # Use validate for SFTP
    response_sftp = client.post(
        "/institutions/"
        + uuid_to_str(UUID_INVALID)
        + "/input/validate-sftp/pdp_ar_deidentified.csv",
    )
    assert str(response_sftp) == "<Response [401 Unauthorized]>"
    assert (
        response_sftp.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )
    # Authorized.
    response_sftp = client.post(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/input/validate-sftp/pdp_ar_deidentified.csv",
    )
    assert response_sftp.status_code == 200
    assert response_sftp.json()["name"] == "pdp_ar_deidentified.csv"
    assert response_sftp.json()["file_types"] == ["STUDENT"]
    assert response_sftp.json()["inst_id"] == uuid_to_str(USER_VALID_INST_UUID)
    assert response_sftp.json()["source"] == "PDP_SFTP"


def test_validate_failure_batch(client: TestClient) -> None:
    """Test PATCH /institutions/<uuid>/batch."""
    MOCK_STORAGE.validate_file.return_value = ["COURSE"]
    # Authorized.
    # Use validate upload
    response_upload = client.post(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/input/validate-upload/file_name_course.csv",
    )
    assert response_upload.status_code == 200
    assert response_upload.json()["name"] == "file_name_course.csv"
    assert response_upload.json()["file_types"] == ["COURSE"]
    assert response_upload.json()["inst_id"] == uuid_to_str(USER_VALID_INST_UUID)
    assert response_upload.json()["source"] == "MANUAL_UPLOAD"

    # Use valiate sftp
    response_sftp = client.post(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/input/validate-upload/file_name_course.csv",
    )
    assert response_sftp.status_code == 200
    assert response_sftp.json()["name"] == "file_name_course.csv"
    assert response_sftp.json()["file_types"] == ["COURSE"]
    assert response_sftp.json()["inst_id"] == uuid_to_str(USER_VALID_INST_UUID)
    assert response_sftp.json()["source"] == "MANUAL_UPLOAD"


def test_get_eda_data_unauthorized(client: TestClient) -> None:
    """Test GET /institutions/<uuid>/batch/<uuid>/eda with unauthorized access."""
    response = client.get(
        "/institutions/"
        + uuid_to_str(UUID_INVALID)
        + "/batch/"
        + uuid_to_str(BATCH_UUID)
        + "/eda"
    )
    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )


def test_get_eda_data_batch_not_found(client: TestClient) -> None:
    """Test GET /institutions/<uuid>/batch/<uuid>/eda with non-existent batch."""
    fake_batch_uuid = uuid.UUID("00000000-0000-0000-0000-000000000000")
    response = client.get(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/batch/"
        + uuid_to_str(fake_batch_uuid)
        + "/eda"
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Batch not found."


def test_get_eda_data_no_student_files(
    client: TestClient, session: sqlalchemy.orm.Session
) -> None:
    """Test GET /institutions/<uuid>/batch/<uuid>/eda with batch containing no STUDENT files."""
    # Create a batch with only COURSE files
    batch_with_course = BatchTable(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        inst_id=USER_VALID_INST_UUID,
        name="batch_course_only",
        created_by=CREATOR_UUID,
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
    )
    course_file = FileTable(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        inst_id=USER_VALID_INST_UUID,
        name="course_file.csv",
        source="MANUAL_UPLOAD",
        batches={batch_with_course},
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        sst_generated=False,
        valid=True,
        schemas=[SchemaType.COURSE],
    )
    session.add_all([batch_with_course, course_file])
    session.commit()

    # Mock storage to return empty (no files found)
    MOCK_STORAGE.read_csv_as_dataframe.side_effect = ValueError("File not found")

    response = client.get(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/batch/"
        + uuid_to_str(batch_with_course.id)
        + "/eda"
    )
    assert response.status_code == 404
    # When files can't be loaded from GCS, we get "No valid input files found"
    # The "No STUDENT schema files found" error only occurs after files are loaded
    assert "No valid input files found" in response.json()["detail"]


def test_get_eda_data_success(
    client: TestClient, session: sqlalchemy.orm.Session
) -> None:
    """Test GET /institutions/<uuid>/batch/<uuid>/eda with valid data."""
    import pandas as pd

    # Create a batch with STUDENT and COURSE files
    eda_batch = BatchTable(
        id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        inst_id=USER_VALID_INST_UUID,
        name="batch_eda_test",
        created_by=CREATOR_UUID,
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        completed=True,
    )
    student_file = FileTable(
        id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        inst_id=USER_VALID_INST_UUID,
        name="student_file.csv",
        source="MANUAL_UPLOAD",
        batches={eda_batch},
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        sst_generated=False,
        valid=True,
        schemas=[SchemaType.STUDENT],
    )
    course_file = FileTable(
        id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
        inst_id=USER_VALID_INST_UUID,
        name="course_file.csv",
        source="MANUAL_UPLOAD",
        batches={eda_batch},
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        sst_generated=False,
        valid=True,
        schemas=[SchemaType.COURSE],
    )
    session.add_all([eda_batch, student_file, course_file])
    session.commit()

    # Create mock DataFrames
    df_student = pd.DataFrame(
        {
            "study_id": ["S001", "S002", "S003", "S001"],  # S001 appears twice
            "cohort": ["2020", "2020", "2021", "2021"],
            "cohort_term": ["FALL", "FALL", "SPRING", "SPRING"],
            "enrollment_type": [
                "First-Time",
                "Transfer-In",
                "First-Time",
                "Transfer-In",
            ],
            "enrollment_intensity_first_term": [
                "Full-Time",
                "Part-Time",
                "Full-Time",
                "Part-Time",
            ],
            "gpa_group_year_1": [3.5, 3.2, 3.8, 2.9],
            "credential_type_sought_year_1": [
                "Bachelor",
                "Associate",
                "Bachelor",
                "Associate",
            ],
            "pell_status_first_year": ["Y", "N", "Y", "N"],
            "first_gen": ["Y", "N", "Y", "N"],
            "gender": ["Female", "Male", "Female", "Male"],
            "race": ["White", "Black or African American", "Asian", "White"],
            "student_age": ["20 - 24", "20 or younger", "Older than 24", "20 - 24"],
        }
    )

    df_course = pd.DataFrame(
        {
            "study_id": ["S001", "S002", "S003"],
            "cohort": ["2020", "2020", "2021"],
            "cohort_term": ["FALL", "FALL", "SPRING"],
        }
    )

    # Mock storage to return our test DataFrames
    def mock_read_csv(bucket_name: str, blob_path: str) -> pd.DataFrame:
        if "student" in blob_path.lower():
            return df_student
        elif "course" in blob_path.lower():
            return df_course
        else:
            raise ValueError(f"File not found: {blob_path}")

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = mock_read_csv

    response = client.get(
        "/institutions/"
        + uuid_to_str(USER_VALID_INST_UUID)
        + "/batch/"
        + uuid_to_str(eda_batch.id)
        + "/eda"
    )

    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "summary_stats" in data
    assert "gpa_by_enrollment_type" in data
    assert "gpa_by_enrollment_intensity" in data
    assert "students_by_cohort_term" in data
    assert "course_enrollments" in data
    assert "degree_types" in data
    assert "enrollment_type_by_intensity" in data
    assert "pell_recipient_by_first_gen" in data
    assert "student_age_by_gender" in data
    assert "race_by_pell_status" in data

    # Check summary stats
    assert data["summary_stats"]["total_students"] == "3"  # 3 unique study_ids
    assert data["summary_stats"]["transfer_students"] == "2"  # 2 Transfer-In

    # Check GPA charts have cohort years
    assert "cohort_years" in data["gpa_by_enrollment_type"]
    assert len(data["gpa_by_enrollment_type"]["cohort_years"]) == 2  # 2020, 2021
    assert "2020" in data["gpa_by_enrollment_type"]["cohort_years"]
    assert "2021" in data["gpa_by_enrollment_type"]["cohort_years"]

    # Check term data structure
    assert "fall" in data["students_by_cohort_term"]
    assert "spring" in data["students_by_cohort_term"]
    assert len(data["students_by_cohort_term"]["fall"]) == 2  # One per cohort year

    # Check enrollment type by intensity has categories and series
    assert "categories" in data["enrollment_type_by_intensity"]
    assert "series" in data["enrollment_type_by_intensity"]
    assert len(data["enrollment_type_by_intensity"]["series"]) > 0

    # Check pell recipient chart structure
    assert "categories" in data["pell_recipient_by_first_gen"]
    assert "series" in data["pell_recipient_by_first_gen"]

    # Check student age by gender structure
    assert "categories" in data["student_age_by_gender"]
    assert "series" in data["student_age_by_gender"]

    # Check race by pell status structure
    assert "categories" in data["race_by_pell_status"]
    assert "series" in data["race_by_pell_status"]


# ==================== EDVISE VALIDATION TESTS ====================

EDVISE_INST_UUID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
EDVISE_INST_2_UUID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
EDVISE_SCHEMA_UUID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


@pytest.fixture(name="edvise_session")
def edvise_session_fixture():
    """Unit test database setup for Edvise tests."""
    engine = sqlalchemy.create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # Mock Edvise schema extension
    edvise_schema_doc = {
        "version": "1.0.0",
        "institutions": {
            "edvise": {
                "data_models": {
                    "student": {
                        "columns": {
                            "student_id": {"type": "string", "required": True},
                            "cohort": {"type": "string", "required": True},
                        }
                    },
                    "course": {
                        "columns": {
                            "student_id": {"type": "string", "required": True},
                            "course_id": {"type": "string", "required": True},
                        }
                    },
                }
            }
        },
    }

    try:
        with sqlalchemy.orm.Session(engine) as session:
            session.add_all(
                [
                    InstTable(
                        id=EDVISE_INST_UUID,
                        name="edvise_school",
                        edvise_id="edvise123",
                        pdp_id=None,
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                    ),
                    InstTable(
                        id=EDVISE_INST_2_UUID,
                        name="edvise_school_2",
                        edvise_id="edvise456",
                        pdp_id=None,
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                    ),
                    SchemaRegistryTable(
                        doc_type=DocType.base,
                        is_pdp=False,
                        is_edvise=False,
                        version_label="1.0.0",
                        json_doc={"version": "1.0.0", "base": {"data_models": {}}},
                        is_active=True,
                        created_at=DATETIME_TESTING,
                    ),
                    SchemaRegistryTable(
                        doc_type=DocType.extension,
                        is_pdp=False,
                        is_edvise=True,
                        version_label="edvise-1.0.0",
                        json_doc=edvise_schema_doc,
                        is_active=True,
                        created_at=DATETIME_TESTING,
                    ),
                    # Note: Edvise extension uses version_label="edvise-1.0.0" to avoid violating
                    # uq_pdp_version constraint (is_pdp, version_label) in MySQL, which requires
                    # unique (is_pdp, version_label) combinations across all rows. The base schema
                    # uses version_label="1.0.0" with is_pdp=False, so Edvise must use a different
                    # version_label. The JSON doc's "version": "1.0.0" field maintains semantic
                    # versioning, while the database version_label is prefixed for constraint uniqueness.
                ]
            )
            session.commit()
            yield session
    finally:
        Base.metadata.drop_all(engine)


@pytest.fixture(name="edvise_client")
def edvise_client_fixture(
    edvise_session: sqlalchemy.orm.Session, monkeypatch: Any
) -> Any:
    """Unit test mocks setup for Edvise tests."""
    monkeypatch.setenv("SST_SKIP_EXT_GEN", "1")

    def get_session_override():
        return edvise_session

    def get_current_active_user_override():
        # Create DATAKINDER user with access to all institutions (needed for tests
        # that access multiple Edvise institutions)
        from ..utilities import AccessType, BaseUser

        return BaseUser(
            uuid_to_str(USER_UUID),
            None,  # DATAKINDER has no specific institution
            AccessType.DATAKINDER,
            "abc@example.com",
        )

    def storage_control_override():
        return MOCK_STORAGE

    app.include_router(router)
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_current_active_user] = get_current_active_user_override
    app.dependency_overrides[StorageControl] = storage_control_override

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    # Clear Edvise cache between tests
    from .data import STATE

    STATE._edvise_cache = (0.0, None)


def test_validate_file_with_edvise_schema(edvise_client: TestClient) -> None:
    """Test file upload validation uses Edvise schema when edvise_id is set."""
    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/edvise_student_file.csv",
    )

    assert response.status_code == 200
    assert response.json()["name"] == "edvise_student_file.csv"
    assert response.json()["file_types"] == ["STUDENT"]
    assert response.json()["inst_id"] == uuid_to_str(EDVISE_INST_UUID)
    assert response.json()["source"] == "MANUAL_UPLOAD"

    # Verify that validate_file was called (Edvise schema was used)
    assert MOCK_STORAGE.validate_file.called


def test_validation_helper_edvise_schema_not_found(
    edvise_client: TestClient, edvise_session: sqlalchemy.orm.Session
) -> None:
    """Test error when edvise_id is set but no active Edvise schema exists."""
    # Deactivate the Edvise schema
    edvise_schema = edvise_session.execute(
        select(SchemaRegistryTable).where(
            SchemaRegistryTable.is_edvise.is_(True),
            SchemaRegistryTable.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if edvise_schema:
        edvise_schema.is_active = False
        edvise_session.commit()

    # Clear cache to force reload
    from .data import STATE

    STATE._edvise_cache = (0.0, None)

    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/test_student_file.csv",
    )

    assert response.status_code == 500
    assert "Edvise schema not found" in response.json()["detail"]
    assert "edvise_id" in response.json()["detail"]


def test_validation_helper_pdp_and_edvise_mutual_exclusivity(
    edvise_client: TestClient, edvise_session: sqlalchemy.orm.Session
) -> None:
    """Test that validation_helper rejects institutions with both pdp_id and edvise_id."""
    # Corrupt the institution data to have both pdp_id and edvise_id
    corrupted_inst = edvise_session.execute(
        select(InstTable).where(InstTable.id == EDVISE_INST_UUID)
    ).scalar_one_or_none()
    corrupted_inst.pdp_id = "pdp999"  # type: ignore
    edvise_session.commit()

    # Clear cache
    from .data import STATE

    STATE._edvise_cache = (0.0, None)

    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/test_student_file.csv",
    )

    assert response.status_code == 500
    assert "cannot have both pdp_id and edvise_id set" in response.json()["detail"]

    # Restore for other tests
    corrupted_inst.pdp_id = None  # type: ignore
    edvise_session.commit()


def test_edvise_schema_cache(
    edvise_client: TestClient, edvise_session: sqlalchemy.orm.Session
) -> None:
    """Test that Edvise schema is cached and reused."""
    from .data import STATE

    # Clear cache
    STATE._edvise_cache = (0.0, None)

    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    # First call: Should load from DB and set cache
    response1 = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/test_student1.csv",
    )
    assert response1.status_code == 200

    # Verify cache was set
    cache_exp, cache_doc = STATE._edvise_cache
    assert cache_doc is not None
    assert cache_exp > time.monotonic()

    # Second call: Should use cached value (same expiration time)
    response2 = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_2_UUID)  # Different institution, same schema
        + "/input/validate-upload/test_student2.csv",
    )
    assert response2.status_code == 200

    # Verify cache expiration time is the same (cache was reused)
    cache_exp2, cache_doc2 = STATE._edvise_cache
    assert cache_doc2 is not None
    assert cache_exp2 == cache_exp  # Same expiration means cache was reused

    # Both institutions should use the same cached Edvise schema
    assert STATE._edvise_cache[1] is not None
    assert STATE._edvise_cache[1] is cache_doc


def test_validate_file_edvise_schema_validation_errors(
    edvise_client: TestClient,
) -> None:
    """Test that validation errors are returned correctly for Edvise schema."""
    from ..validation import HardValidationError

    # Mock validation to raise an error
    def mock_validate_file(*args, **kwargs):
        raise HardValidationError(
            missing_required=["student_id"],
            extra_columns=[],
            schema_errors=None,
            failure_cases=None,
        )

    MOCK_STORAGE.validate_file.side_effect = mock_validate_file

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/invalid_student_file.csv",
    )

    assert response.status_code == 400
    # Check for user-friendly error message (Phase 4: Error Message Improvements)
    detail = response.json()["detail"]
    assert "Missing required columns" in detail or "student_id" in detail
    # The message should be user-friendly, not technical "VALIDATION_FAILED"
    assert "VALIDATION_FAILED" not in detail or "missing_required=" not in detail

    # Reset mock
    MOCK_STORAGE.validate_file.side_effect = None
    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]


def test_edvise_schema_takes_precedence_over_custom(
    edvise_client: TestClient, edvise_session: sqlalchemy.orm.Session
) -> None:
    """Test that Edvise schema is used instead of custom when edvise_id is set."""
    # Add a custom extension for this institution with unique version_label
    custom_schema = SchemaRegistryTable(
        doc_type=DocType.extension,
        inst_id=EDVISE_INST_UUID,
        is_pdp=False,
        is_edvise=False,
        version_label="1.0.1",  # Use different version to avoid unique constraint
        json_doc={"version": "1.0.1", "custom": {"data_models": {}}},
        is_active=True,
        created_at=DATETIME_TESTING,
    )
    edvise_session.add(custom_schema)
    edvise_session.commit()

    # Clear cache
    from .data import STATE

    STATE._edvise_cache = (0.0, None)

    # Capture schema and institution_id passed to validate_file
    captured_schema = None
    captured_institution_id = None

    def capture_schema(*args, **kwargs):
        nonlocal captured_schema, captured_institution_id
        # validate_file(bucket, file_name, allowed_schemas, base_schema, inst_schema, institution_id=...)
        if len(args) >= 5:
            captured_schema = args[4]
        elif "inst_schema" in kwargs:
            captured_schema = kwargs["inst_schema"]
        captured_institution_id = kwargs.get("institution_id")
        return ["STUDENT"]

    MOCK_STORAGE.validate_file.side_effect = capture_schema

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/test_student_file.csv",
    )

    # Should succeed using Edvise schema, not custom
    assert response.status_code == 200

    # Verify Edvise schema was passed to validation (not custom)
    assert captured_schema is not None
    # Edvise schema should have "edvise" or "institutions" structure
    assert isinstance(captured_schema, dict)
    assert (
        "edvise" in str(captured_schema).lower()
        or captured_schema.get("institutions") is not None
    )
    # Custom schema should NOT be in the captured schema
    assert (
        "custom" not in str(captured_schema).lower()
        or captured_schema.get("custom") is None
    )
    # Verify correct institution_id so merge_model_columns uses institutions["edvise"]
    assert captured_institution_id == "edvise"

    # Reset mock
    MOCK_STORAGE.validate_file.side_effect = None
    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]


def test_validate_sftp_with_edvise_schema(edvise_client: TestClient) -> None:
    """Test SFTP file validation uses Edvise schema when edvise_id is set."""
    MOCK_STORAGE.validate_file.return_value = ["COURSE"]

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-sftp/edvise_course_file.csv",
    )

    assert response.status_code == 200
    assert response.json()["name"] == "edvise_course_file.csv"
    assert response.json()["file_types"] == ["COURSE"]
    assert response.json()["inst_id"] == uuid_to_str(EDVISE_INST_UUID)
    assert response.json()["source"] == "PDP_SFTP"

    # Verify that validate_file was called
    assert MOCK_STORAGE.validate_file.called


def test_validate_edvise_unauthorized(
    edvise_session: sqlalchemy.orm.Session, monkeypatch: Any
) -> None:
    """Test validation endpoint with unauthorized access."""
    monkeypatch.setenv("SST_SKIP_EXT_GEN", "1")

    # Create a test client with a MODEL_OWNER user who only has access to EDVISE_INST_UUID
    # This user should NOT have access to EDVISE_INST_2_UUID
    def get_session_override():
        return edvise_session

    def get_current_active_user_override():
        # User belongs to EDVISE_INST_UUID, not DATAKINDER, so access is restricted
        from ..utilities import AccessType, BaseUser

        return BaseUser(
            uuid_to_str(USER_UUID),
            uuid_to_str(EDVISE_INST_UUID),  # User belongs to this institution
            AccessType.MODEL_OWNER,  # Not DATAKINDER, so access is restricted
            "abc@example.com",
        )

    def storage_control_override():
        return MOCK_STORAGE

    app.include_router(router)
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_current_active_user] = get_current_active_user_override
    app.dependency_overrides[StorageControl] = storage_control_override

    client = TestClient(app)
    try:
        # Try to access EDVISE_INST_2_UUID which exists but user doesn't have access to
        response = client.post(
            "/institutions/"
            + uuid_to_str(
                EDVISE_INST_2_UUID
            )  # Institution exists but user is unauthorized
            + "/input/validate-upload/test_student.csv",
        )
        assert response.status_code == 401
        assert "Not authorized" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_validate_edvise_invalid_filename(edvise_client: TestClient) -> None:
    """Test validation rejects file names with '/'."""
    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/invalid/file.csv",
    )
    assert response.status_code == 422
    assert "can't contain '/'" in response.json()["detail"]


def test_validate_edvise_course_file(edvise_client: TestClient) -> None:
    """Test COURSE file validation with Edvise schema."""
    MOCK_STORAGE.validate_file.return_value = ["COURSE"]

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/edvise_course.csv",
    )

    assert response.status_code == 200
    assert response.json()["file_types"] == ["COURSE"]
    assert response.json()["name"] == "edvise_course.csv"
    assert response.json()["inst_id"] == uuid_to_str(EDVISE_INST_UUID)


def test_edvise_cache_expiration(
    edvise_client: TestClient, edvise_session: sqlalchemy.orm.Session
) -> None:
    """Test that expired cache reloads from database."""
    from .data import STATE

    # Set cache with expired TTL
    old_exp = time.monotonic() - 1
    STATE._edvise_cache = (old_exp, {"old": "schema"})

    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    # Should reload from DB (cache expired) and update cache
    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/test_student.csv",
    )
    assert response.status_code == 200

    # Verify cache was updated with new expiration
    cache_exp, cache_doc = STATE._edvise_cache
    assert cache_doc is not None
    assert cache_exp > old_exp  # New expiration time means cache was reloaded


def test_edvise_cache_none_reloads(edvise_client: TestClient) -> None:
    """Test that None in expired cache doesn't prevent reload."""
    from .data import STATE

    # Set cache with None but expired TTL
    STATE._edvise_cache = (time.monotonic() - 1, None)

    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    # Should reload from DB (not use None)
    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/test_student.csv",
    )
    # Should succeed (schema exists in DB)
    assert response.status_code == 200
    # Cache should now have the schema
    assert STATE._edvise_cache[1] is not None


def test_edvise_cache_shared_across_institutions(edvise_client: TestClient) -> None:
    """Test that all Edvise institutions share the same cached schema."""
    from .data import STATE

    STATE._edvise_cache = (0.0, None)

    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    # First institution
    response1 = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_UUID)
        + "/input/validate-upload/test_student1.csv",
    )
    assert response1.status_code == 200

    # Get cached schema
    cache_exp, cache_doc = STATE._edvise_cache
    assert cache_doc is not None

    # Second institution should use same cache
    response2 = edvise_client.post(
        "/institutions/"
        + uuid_to_str(EDVISE_INST_2_UUID)
        + "/input/validate-upload/test_student2.csv",
    )
    assert response2.status_code == 200

    # Cache should be unchanged (same object reference)
    assert STATE._edvise_cache[1] is cache_doc


def test_validate_edvise_inst_not_found(edvise_client: TestClient) -> None:
    """Test validation with non-existent institution."""
    fake_uuid = uuid.UUID("00000000-0000-0000-0000-000000000000")
    MOCK_STORAGE.validate_file.return_value = ["STUDENT"]

    response = edvise_client.post(
        "/institutions/"
        + uuid_to_str(fake_uuid)
        + "/input/validate-upload/test_student.csv",
    )
    # Should fail - either 401 (unauthorized) or 404 (not found)
    assert response.status_code in [401, 404]


# ---- Latest inference cohort (Task 1) ----


def test_apply_student_criteria_count_empty_df() -> None:
    """apply_student_criteria_count returns (0, None) for empty DataFrame."""
    import pandas as pd

    df = pd.DataFrame(columns=["student_id", "enrollment_type"])
    count, err = apply_student_criteria_count(df, {})
    assert count == 0
    assert err is None


def test_apply_student_criteria_count_no_criteria() -> None:
    """apply_student_criteria_count returns nunique when no student_criteria."""
    import pandas as pd

    df = pd.DataFrame({"student_id": ["a", "b", "a"], "x": [1, 2, 3]})
    count, err = apply_student_criteria_count(df, {})
    assert count == 2
    assert err is None


def test_apply_student_criteria_count_with_criteria() -> None:
    """apply_student_criteria_count filters by student_criteria and returns count."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "student_id": ["a", "b", "c", "d"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME", "Transfer", "FIRST-TIME"],
        }
    )
    config = {"student_criteria": {"enrollment_type": "FIRST-TIME"}}
    count, err = apply_student_criteria_count(df, config)
    assert count == 3
    assert err is None


def test_apply_student_criteria_count_list_value() -> None:
    """apply_student_criteria_count supports list values (isin)."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "student_id": ["a", "b", "c"],
            "cohort_term": ["FALL", "SPRING", "SUMMER"],
        }
    )
    config = {"student_criteria": {"cohort_term": ["FALL", "SPRING"]}}
    count, err = apply_student_criteria_count(df, config)
    assert count == 2
    assert err is None


def test_apply_student_criteria_count_missing_column_fails() -> None:
    """apply_student_criteria_count returns error when criterion column is missing."""
    import pandas as pd

    df = pd.DataFrame(
        {"student_id": ["a", "b"], "enrollment_type": ["FIRST-TIME", "Transfer"]}
    )
    config = {
        "student_criteria": {
            "enrollment_type": "FIRST-TIME",
            "credential_type_sought_year_1": "BACHELOR'S DEGREE",
        }
    }
    count, err = apply_student_criteria_count(df, config)
    assert count == 0
    assert err is not None
    assert "Missing columns for selection criteria" in err
    assert "credential_type_sought_year_1" in err


def test_apply_student_criteria_count_restricts_to_students_with_course_data() -> None:
    """apply_student_criteria_count only counts students that appear in course data."""
    import pandas as pd

    df_student = pd.DataFrame(
        {
            "study_id": ["a", "b", "c"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME", "FIRST-TIME"],
        }
    )
    df_course = pd.DataFrame(
        {"study_id": ["a", "c"], "course_id": [1, 2]}
    )  # b has no course rows
    config = {"student_criteria": {"enrollment_type": "FIRST-TIME"}}
    count, err = apply_student_criteria_count(df_student, config, df_course=df_course)
    assert err is None
    assert count == 2  # a and c only; b excluded for having no course data


def test_apply_student_criteria_count_empty_course_returns_zero() -> None:
    """When df_course is provided but empty (no course data for cohort), count is 0."""
    import pandas as pd

    df_student = pd.DataFrame(
        {"study_id": ["a", "b"], "enrollment_type": ["FIRST-TIME", "FIRST-TIME"]}
    )
    df_course = pd.DataFrame(columns=["study_id", "course_id"])  # empty
    config = {"student_criteria": {"enrollment_type": "FIRST-TIME"}}
    count, err = apply_student_criteria_count(df_student, config, df_course=df_course)
    assert err is None
    assert count == 0


def test_filter_to_most_recent_cohort_uses_cohort_term() -> None:
    """filter_to_most_recent_cohort selects most recent cohort term (e.g. Spring 2025 > Fall 2024)."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "study_id": ["a", "b", "c", "d"],
            "cohort": ["2024-25", "2024-25", "2024-25", "2023-24"],
            "cohort_term": ["FALL", "FALL", "SPRING", "SPRING"],
        }
    )
    filtered, label, err = filter_to_most_recent_cohort(df)
    assert err is None
    assert label == "Spring 2025"
    assert len(filtered) == 1
    assert filtered["study_id"].iloc[0] == "c"


def test_filter_to_most_recent_cohort_respects_allowed_cohort_terms() -> None:
    """filter_to_most_recent_cohort only considers terms in preprocessing.selection cohort_term."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "study_id": ["a", "b", "c"],
            "cohort": ["2024-25", "2024-25", "2024-25"],
            "cohort_term": ["FALL", "SPRING", "SUMMER"],
        }
    )
    # Allowed FALL, SPRING -> most recent among those is Spring 2025 (c excluded as SUMMER)
    filtered, label, err = filter_to_most_recent_cohort(
        df, allowed_cohort_terms=["FALL", "SPRING"]
    )
    assert err is None
    assert label == "Spring 2025"
    assert len(filtered) == 1
    assert filtered["study_id"].iloc[0] == "b"


def test_filter_to_most_recent_cohort_allowed_terms_no_match() -> None:
    """filter_to_most_recent_cohort returns error when no data matches allowed cohort_term."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "study_id": ["a", "b"],
            "cohort": ["2024-25", "2024-25"],
            "cohort_term": ["SUMMER", "SUMMER"],
        }
    )
    filtered, label, err = filter_to_most_recent_cohort(
        df, allowed_cohort_terms=["FALL", "SPRING"]
    )
    assert err is not None
    assert "preprocessing.selection" in err or "cohort_term" in err
    assert filtered.empty


def test_filter_to_most_recent_cohort_requires_cohort_term() -> None:
    """filter_to_most_recent_cohort returns error when cohort_term column is missing."""
    import pandas as pd

    df = pd.DataFrame(
        {"study_id": ["a", "b", "c"], "cohort": ["2023-24", "2024-25", "2024-25"]}
    )
    filtered, label, err = filter_to_most_recent_cohort(df)
    assert err is not None
    assert "cohort_term" in err.lower()
    assert filtered.empty


def test_filter_to_most_recent_cohort_requires_cohort_term_with_entry_year() -> None:
    """filter_to_most_recent_cohort returns error when only entry_year (no cohort_term)."""
    import pandas as pd

    df = pd.DataFrame({"study_id": ["a", "b", "c"], "entry_year": [2022, 2024, 2024]})
    filtered, label, err = filter_to_most_recent_cohort(df)
    assert err is not None
    assert "cohort_term" in err.lower()
    assert filtered.empty


def test_filter_to_most_recent_cohort_missing_column() -> None:
    """filter_to_most_recent_cohort returns error when no cohort/entry_year column."""
    import pandas as pd

    df = pd.DataFrame({"study_id": ["a", "b"], "enrollment_type": ["FT", "PT"]})
    filtered, label, err = filter_to_most_recent_cohort(df)
    assert err is not None
    assert "cohort" in err.lower() or "entry_year" in err.lower()
    assert filtered.empty


def test_get_ordered_cohort_terms_cohort_and_cohort_term() -> None:
    """get_ordered_cohort_terms returns terms most recent first when using cohort+cohort_term."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "study_id": ["a", "b", "c", "d"],
            "cohort": ["2024-25", "2024-25", "2023-24", "2023-24"],
            "cohort_term": ["SPRING", "FALL", "SPRING", "FALL"],
        }
    )
    ordered, err = get_ordered_cohort_terms(df)
    assert err is None
    assert ordered is not None
    assert len(ordered) == 4  # Spring 2025, Fall 2024, Spring 2024, Fall 2023
    assert (
        ordered[0]["term_upper"] == "SPRING" and ordered[0]["cohort_str"] == "2024-25"
    )
    assert ordered[1]["term_upper"] == "FALL" and ordered[1]["cohort_str"] == "2024-25"


def test_get_ordered_cohort_terms_entry_year_and_cohort_term() -> None:
    """get_ordered_cohort_terms returns terms most recent first when using entry_year+cohort_term."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "study_id": ["a", "b", "c"],
            "entry_year": [2024, 2024, 2023],
            "cohort_term": ["SPRING", "FALL", "FALL"],
        }
    )
    ordered, err = get_ordered_cohort_terms(df)
    assert err is None
    assert ordered is not None
    assert len(ordered) == 3  # Spring 2024, Fall 2024, Fall 2023
    assert ordered[0]["term_upper"] == "SPRING" and ordered[0]["entry_year"] == 2024


def test_get_ordered_cohort_terms_empty_dataframe() -> None:
    """get_ordered_cohort_terms returns error when DataFrame is empty."""
    import pandas as pd

    df = pd.DataFrame(columns=["study_id", "cohort", "cohort_term"])
    ordered, err = get_ordered_cohort_terms(df)
    assert err is not None
    assert "no student data" in err.lower()
    assert ordered is None


def test_get_ordered_cohort_terms_requires_cohort_term_column() -> None:
    """get_ordered_cohort_terms returns error when cohort_term column is missing."""
    import pandas as pd

    df = pd.DataFrame({"study_id": ["a"], "cohort": ["2024-25"]})
    ordered, err = get_ordered_cohort_terms(df)
    assert err is not None
    assert "cohort_term" in err.lower()
    assert ordered is None


def test_get_ordered_cohort_terms_respects_allowed_terms() -> None:
    """get_ordered_cohort_terms only includes terms in allowed_cohort_terms."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "study_id": ["a", "b", "c"],
            "cohort": ["2024-25", "2024-25", "2024-25"],
            "cohort_term": ["FALL", "SPRING", "SUMMER"],
        }
    )
    ordered, err = get_ordered_cohort_terms(df, allowed_cohort_terms=["FALL", "SPRING"])
    assert err is None
    assert ordered is not None
    assert len(ordered) == 2  # Spring 2025, Fall 2024 (SUMMER excluded)
    terms = [c["term_upper"] for c in ordered]
    assert "SUMMER" not in terms


def test_get_ordered_cohort_terms_no_cohort_or_entry_year() -> None:
    """get_ordered_cohort_terms returns error when neither cohort nor entry_year present."""
    import pandas as pd

    df = pd.DataFrame({"study_id": ["a", "b"], "cohort_term": ["FALL", "FALL"]})
    ordered, err = get_ordered_cohort_terms(df)
    assert err is not None
    assert (
        "cohort" in err.lower() and "entry_year" in err.lower()
    ) or "cohort_term" in err.lower()
    assert ordered is None


def test_get_latest_batch_with_student_and_course_returns_batch(
    session: sqlalchemy.orm.Session,
) -> None:
    """get_latest_batch_with_student_and_course returns batch when it has STUDENT and COURSE."""
    batch = get_latest_batch_with_student_and_course(
        session, uuid_to_str(USER_VALID_INST_UUID)
    )
    # Session fixture: batch_1 has file_1 (STUDENT), file_2 (COURSE), file_3 (STUDENT, sst_generated)
    assert batch is not None
    assert batch.name == "batch_foo"


def test_get_latest_batch_with_student_and_course_no_batch(
    session: sqlalchemy.orm.Session,
) -> None:
    """get_latest_batch_with_student_and_course returns None for inst with no such batch."""
    batch = get_latest_batch_with_student_and_course(session, uuid_to_str(UUID_INVALID))
    assert batch is None


def test_get_latest_batch_with_student_and_course_returns_none_when_no_batch_has_both_student_and_course(
    session: sqlalchemy.orm.Session,
) -> None:
    """get_latest_batch_with_student_and_course returns None when inst has batches but none have both STUDENT and COURSE."""
    inst_only_student_uuid = uuid.UUID("a1b2c3d4-e5f6-4789-a012-345678901234")
    batch_student_only = BatchTable(
        inst_id=inst_only_student_uuid,
        name="batch_student_only",
        created_by=CREATOR_UUID,
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
    )
    file_student_only = FileTable(
        inst_id=inst_only_student_uuid,
        name="file_student_only",
        source="MANUAL_UPLOAD",
        batches={batch_student_only},
        created_at=DATETIME_TESTING,
        updated_at=DATETIME_TESTING,
        sst_generated=False,
        valid=True,
        schemas=[SchemaType.STUDENT],
    )
    session.add_all(
        [
            InstTable(
                id=inst_only_student_uuid,
                name="inst_student_only",
                created_at=DATETIME_TESTING,
                updated_at=DATETIME_TESTING,
            ),
            batch_student_only,
            file_student_only,
        ]
    )
    session.commit()
    batch = get_latest_batch_with_student_and_course(
        session, uuid_to_str(inst_only_student_uuid)
    )
    assert batch is None


def test_get_latest_inference_cohort_unauthorized(client: TestClient) -> None:
    """GET latest-inference-cohort returns 401 for wrong institution."""
    response = client.get(
        "/institutions/" + uuid_to_str(UUID_INVALID) + "/latest-inference-cohort"
    )
    assert response.status_code == 401


def test_get_latest_inference_cohort_missing_cohort_column(client: TestClient) -> None:
    """GET latest-inference-cohort returns 200 invalid when student file has no cohort column."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {"enrollment_type": "FIRST-TIME"},
    }
    # Student file without cohort or entry_year
    df_student = pd.DataFrame({"study_id": ["s1"], "enrollment_type": ["FIRST-TIME"]})
    df_course = pd.DataFrame({"study_id": ["s1"], "x": [1]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read
    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert "reason" in data and data["reason"]
    assert "cohort_term" in (data["reason"] or "").lower()
    assert data.get("batch_name") == "batch_foo"


def test_get_latest_inference_cohort_missing_cohort_term(client: TestClient) -> None:
    """GET latest-inference-cohort returns 200 invalid when student file has cohort but no cohort_term."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {"enrollment_type": "FIRST-TIME"},
    }
    df_student = pd.DataFrame(
        {
            "study_id": ["s1", "s2"],
            "cohort": ["2024-25", "2024-25"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME"],
        }
    )
    df_course = pd.DataFrame({"study_id": ["s1", "s2"], "x": [1, 2]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read
    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert "reason" in data and data["reason"]
    assert "cohort_term" in (data["reason"] or "").lower()
    assert data.get("batch_name") == "batch_foo"


def test_get_latest_inference_cohort_missing_config(client: TestClient) -> None:
    """GET latest-inference-cohort returns 200 invalid when Databricks config is missing."""
    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = None

    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert "reason" in data and data["reason"]
    assert "Missing config" in (data["reason"] or "")
    assert data.get("batch_name") == "batch_foo"


def test_get_latest_inference_cohort_valid(
    client: TestClient,
) -> None:
    """GET latest-inference-cohort returns 200 valid when batch has STUDENT and config."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {
            "enrollment_type": "FIRST-TIME",
            "cohort_term": ["FALL", "SPRING"],
        },
    }

    df_student = pd.DataFrame(
        {
            "study_id": ["s1", "s2", "s3"],
            "cohort": ["2024-25", "2024-25", "2023-24"],
            "cohort_term": ["FALL", "FALL", "FALL"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME", "Transfer"],
        }
    )
    # Course data for s1 and s2 in most recent cohort term (Fall 2024)
    df_course = pd.DataFrame({"study_id": ["s1", "s2", "s3"], "x": [1, 2, 3]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read

    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "valid"
    assert "batch_name" in data and data["batch_name"] == "batch_foo"
    assert "cohort_label" in data and data["cohort_label"] == "Fall 2024"
    assert (
        "valid_student_count" in data and data["valid_student_count"] == 2
    )  # s1, s2 in Fall 2024 cohort term matching FIRST-TIME
    assert data.get("reason") is None


def test_get_latest_inference_cohort_loops_back_when_no_course_data_for_most_recent(
    client: TestClient,
) -> None:
    """When most recent cohort term has no course data, try next cohort term until one has course data."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {
            "enrollment_type": "FIRST-TIME",
            "cohort_term": ["FALL", "SPRING"],
        },
    }
    # Fall 2025: s1, s2. Fall 2024: s3, s4. Course data only for s3, s4 (Fall 2024).
    df_student = pd.DataFrame(
        {
            "study_id": ["s1", "s2", "s3", "s4"],
            "cohort": ["2025-26", "2025-26", "2024-25", "2024-25"],
            "cohort_term": ["FALL", "FALL", "FALL", "FALL"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME", "FIRST-TIME", "FIRST-TIME"],
        }
    )
    df_course = pd.DataFrame({"study_id": ["s3", "s4"], "x": [1, 2]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read
    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "valid"
    assert (
        data["cohort_label"] == "Fall 2024"
    )  # Fall 2025 had no course data; used Fall 2024
    assert data["valid_student_count"] == 2  # s3, s4


def test_get_latest_inference_cohort_invalid_when_no_cohort_has_course_data(
    client: TestClient,
) -> None:
    """When no cohort term in the batch has course data, return invalid."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {"enrollment_type": "FIRST-TIME", "cohort_term": ["FALL"]},
    }
    df_student = pd.DataFrame(
        {
            "study_id": ["s1", "s2"],
            "cohort": ["2025-26", "2025-26"],
            "cohort_term": ["FALL", "FALL"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME"],
        }
    )
    # Course data for different IDs (no overlap with s1, s2)
    df_course = pd.DataFrame({"study_id": ["other1", "other2"], "x": [1, 2]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read
    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert "reason" in data and data["reason"]
    assert "course data" in (data["reason"] or "").lower()
    assert data.get("batch_name") == "batch_foo"


def test_get_latest_inference_cohort_missing_student_id_column(
    client: TestClient,
) -> None:
    """GET latest-inference-cohort returns 200 invalid when student file has no student id column (study_id/student_id)."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {
            "enrollment_type": "FIRST-TIME",
            "cohort_term": ["FALL", "SPRING"],
        },
    }
    df_student = pd.DataFrame(
        {
            "cohort": ["2024-25", "2024-25"],
            "cohort_term": ["FALL", "FALL"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME"],
        }
    )
    df_course = pd.DataFrame({"study_id": ["s1", "s2"], "x": [1, 2]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read
    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert "reason" in data and data["reason"]
    assert "student id column" in (data["reason"] or "").lower()
    assert data.get("batch_name") == "batch_foo"


def test_get_latest_inference_cohort_valid_student_count_zero_when_no_students_meet_criteria(
    client: TestClient,
) -> None:
    """GET latest-inference-cohort returns 200 valid with valid_student_count=0 when cohort has course data but no students meet criteria."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {
            "enrollment_type": "FIRST-TIME",
            "cohort_term": ["FALL", "SPRING"],
        },
    }
    # All students in Fall 2024 are Transfer; config requires FIRST-TIME
    df_student = pd.DataFrame(
        {
            "study_id": ["s1", "s2"],
            "cohort": ["2024-25", "2024-25"],
            "cohort_term": ["FALL", "FALL"],
            "enrollment_type": ["Transfer", "Transfer"],
        }
    )
    df_course = pd.DataFrame({"study_id": ["s1", "s2"], "x": [1, 2]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read
    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "valid"
    assert data["cohort_label"] == "Fall 2024"
    assert data["valid_student_count"] == 0
    assert data.get("batch_name") == "batch_foo"


def test_get_latest_inference_cohort_invalid_when_student_criteria_references_missing_column(
    client: TestClient,
) -> None:
    """GET latest-inference-cohort returns 200 invalid when student_criteria references a column not in student file."""
    import pandas as pd

    MOCK_DATABRICKS = mock.Mock(spec=DatabricksControl)
    MOCK_DATABRICKS.read_volume_training_config.return_value = {
        "student_criteria": {
            "enrollment_type": "FIRST-TIME",
            "nonexistent_col": "X",
            "cohort_term": ["FALL", "SPRING"],
        },
    }
    df_student = pd.DataFrame(
        {
            "study_id": ["s1", "s2"],
            "cohort": ["2024-25", "2024-25"],
            "cohort_term": ["FALL", "FALL"],
            "enrollment_type": ["FIRST-TIME", "FIRST-TIME"],
        }
    )
    df_course = pd.DataFrame({"study_id": ["s1", "s2"], "x": [1, 2]})

    def storage_read(bucket: str, path: str) -> Any:
        if "input_one" in path:
            return df_student
        if "input_two" in path:
            return df_course
        return df_student

    MOCK_STORAGE.read_csv_as_dataframe.side_effect = storage_read
    app.dependency_overrides[DatabricksControl] = lambda: MOCK_DATABRICKS
    try:
        response = client.get(
            "/institutions/"
            + uuid_to_str(USER_VALID_INST_UUID)
            + "/latest-inference-cohort"
        )
    finally:
        app.dependency_overrides.pop(DatabricksControl, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert "reason" in data and data["reason"]
    assert "Missing columns" in (data["reason"] or "") or "nonexistent_col" in (
        data["reason"] or ""
    )
    assert data.get("batch_name") == "batch_foo"
