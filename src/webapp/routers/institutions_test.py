"""Test file for the institutions.py file and constituent API functions."""

import uuid
import os
from datetime import datetime
from typing import Generator
from unittest import mock
from typing import Any
import pytest
import sqlalchemy
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from . import institutions
from ..test_helper import (
    INSTITUTION_REQ,
    INSTITUTION_REQ_BAREBONES,
    INSTITUTION_OBJ,
    USR,
    DATAKINDER,
)

from ..utilities import uuid_to_str, get_current_active_user
from ..main import app
from ..database import InstTable, Base, get_session
from ..gcsutil import StorageControl
from ..databricks import DatabricksControl

DATETIME_TESTING = datetime.today()
UUID_1 = uuid.uuid4()
UUID_2 = uuid.uuid4()
UUID_3 = uuid.uuid4()  # For Edvise test institution
USER_UUID = uuid.UUID("5301a352-c03d-4a39-beec-16c5668c4700")
USER_VALID_INST_UUID = uuid.UUID("1d7c75c3-3eda-4294-9c66-75ea8af97b55")
INVALID_UUID = uuid.UUID("27316b89-5e04-474a-9ea4-97beaf72c9af")

MOCK_STORAGE = mock.Mock()
MOCK_DATABRICKS = mock.Mock()


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
    try:
        with sqlalchemy.orm.Session(engine) as session:
            session.add_all(
                [
                    InstTable(
                        id=UUID_1,
                        name="school_1",
                        state="GA",
                        pdp_id="456",
                        edvise_id=None,
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                    ),
                    InstTable(
                        id=UUID_2,
                        name="school_2",
                        pdp_id=None,
                        edvise_id=None,
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                    ),
                    InstTable(
                        id=USER_VALID_INST_UUID,
                        name="valid_school",
                        pdp_id="12345",
                        edvise_id=None,
                        state="NY",
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                    ),
                    InstTable(
                        id=UUID_3,
                        name="edvise_test_school",
                        state="CA",
                        pdp_id=None,
                        edvise_id="edvise456",
                        created_at=DATETIME_TESTING,
                        updated_at=DATETIME_TESTING,
                    ),
                ]
            )
            session.commit()
            yield session
    finally:
        Base.metadata.drop_all(engine)


@pytest.fixture(name="client")
def client_fixture(
    session: sqlalchemy.orm.Session,
) -> Generator[TestClient, None, None]:
    """Unit test mocks setup for a non-DATAKINDER type."""

    def get_session_override():
        return session

    def get_current_active_user_override():
        return USR

    def storage_control_override():
        return MOCK_STORAGE

    def databricks_control_override():
        return MOCK_DATABRICKS

    app.include_router(institutions.router)
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_current_active_user] = get_current_active_user_override
    app.dependency_overrides[StorageControl] = storage_control_override
    app.dependency_overrides[DatabricksControl] = databricks_control_override

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="datakinder_client")
def datakinder_client_fixture(
    session: sqlalchemy.orm.Session,
) -> Generator[TestClient, None, None]:
    """Unit test mocks setup for a DATAKINDER type."""

    def get_session_override():
        return session

    def get_current_active_user_override():
        return DATAKINDER

    def storage_control_override():
        return MOCK_STORAGE

    def databricks_control_override():
        return MOCK_DATABRICKS

    app.include_router(institutions.router)
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_current_active_user] = get_current_active_user_override
    app.dependency_overrides[StorageControl] = storage_control_override
    app.dependency_overrides[DatabricksControl] = databricks_control_override

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_read_all_inst(client: TestClient) -> None:
    """Test GET /institutions."""

    # Unauthorized.
    response = client.get("/institutions")
    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this resource. Select a specific institution."}'
    )


def test_read_all_inst_datakinder(datakinder_client: TestClient) -> None:
    """Test GET /institutions using DATAKINDER type."""
    # Authorized.
    response = datakinder_client.get("/institutions")
    assert response.status_code == 200
    data = response.json()
    # Verify all institutions have edvise_id field
    for inst in data:
        assert "edvise_id" in inst
        assert "pdp_id" in inst
    # Verify specific expected values
    assert len(data) == 4  # UUID_1, UUID_2, UUID_3, USER_VALID_INST_UUID
    school_1 = next(i for i in data if i["name"] == "school_1")
    assert school_1["pdp_id"] == "456"
    assert school_1["edvise_id"] is None
    edvise_school = next(i for i in data if i["name"] == "edvise_test_school")
    assert edvise_school["edvise_id"] == "edvise456"
    assert edvise_school["pdp_id"] is None
    assert response.json() == [
        {
            "inst_id": uuid_to_str(UUID_1),
            "name": "school_1",
            "pdp_id": "456",
            "retention_days": None,
            "state": "GA",
        },
        {
            "inst_id": uuid_to_str(UUID_2),
            "name": "school_2",
            "pdp_id": None,
            "retention_days": None,
            "state": None,
        },
        {
            "inst_id": uuid_to_str(USER_VALID_INST_UUID),
            "name": "valid_school",
            "pdp_id": "12345",
            "retention_days": None,
            "state": "NY",
        },
    ]


def test_read_inst_by_name(client: TestClient) -> None:
    """Test GET /institutions/name/<name>. For various user access types."""
    # Unauthorized.
    response = client.get("/institutions/name/school_1")

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )

    # Authorized.
    response = client.get("/institutions/name/valid_school")
    assert response.status_code == 200
    assert response.json() == INSTITUTION_OBJ


def test_read_inst_by_name_case_insensitive(client: TestClient) -> None:
    """Test GET /institutions/name/<name> with case-insensitive matching."""
    # Test with different case variations - should all match
    test_cases = [
        "valid_school",  # Original case
        "Valid_School",  # Title case
        "VALID_SCHOOL",  # All uppercase
        "vAlId_ScHoOl",  # Mixed case
    ]

    for name_variant in test_cases:
        response = client.get(f"/institutions/name/{name_variant}")
        assert response.status_code == 200, f"Failed for variant: {name_variant}"
        assert response.json() == INSTITUTION_OBJ, (
            f"Response mismatch for variant: {name_variant}"
        )


def test_read_inst_by_name_case_insensitive_lowercase(
    datakinder_client: TestClient,
) -> None:
    """Test GET /institutions/name/<name> with lowercase input when DB has mixed case."""
    # Test that lowercase input matches mixed case in database
    # Using datakinder_client since regular client doesn't have access to school_1
    response = datakinder_client.get("/institutions/name/school_1")
    assert response.status_code == 200
    # Verify it matches the institution with name "school_1" (lowercase in DB)
    assert response.json()["name"] == "school_1"


def test_read_inst_by_name_case_insensitive_uppercase(
    datakinder_client: TestClient,
) -> None:
    """Test GET /institutions/name/<name> with uppercase input."""
    # Test that uppercase input matches lowercase in database
    # Using datakinder_client since regular client doesn't have access to school_1
    response = datakinder_client.get("/institutions/name/SCHOOL_1")
    assert response.status_code == 200
    assert response.json()["name"] == "school_1"


def test_read_inst_by_pdp_id(client: TestClient) -> None:
    """Test GET /institutions/pdp-id/<pdp_id>. For various user access types."""
    # Unauthorized.
    response = client.get("/institutions/pdp-id/456")

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )

    # Authorized.
    response = client.get("/institutions/pdp-id/12345")
    assert response.status_code == 200
    assert response.json() == INSTITUTION_OBJ


def test_read_inst(client: TestClient) -> None:
    """Test GET /institutions/<uuid>. For various user access types."""
    # Unauthorized.
    response = client.get("/institutions/" + uuid_to_str(UUID_1))

    assert str(response) == "<Response [401 Unauthorized]>"
    assert (
        response.text
        == '{"detail":"Not authorized to read this institution\'s resources."}'
    )

    # Authorized.
    response = client.get("/institutions/" + uuid_to_str(USER_VALID_INST_UUID))
    assert response.status_code == 200
    assert response.json() == INSTITUTION_OBJ


def test_create_inst_unauth(client: TestClient) -> None:
    """Test POST /institutions. For various user access types."""
    os.environ["ENV"] = "DEV"
    # Unauthorized.
    response = client.post("/institutions", json=INSTITUTION_REQ)
    assert str(response) == "<Response [401 Unauthorized]>"
    assert response.text == '{"detail":"Not authorized to create an institution."}'


def test_create_inst(datakinder_client: TestClient) -> None:
    """Test POST /institutions. For various user access types."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Authorized.
    response = datakinder_client.post("/institutions", json=INSTITUTION_REQ)
    assert response.status_code == 200
    assert response.json()["name"] == "foobar school"
    assert response.json()["state"] == "NY"
    assert response.json()["pdp_id"] == "12345"
    assert response.json()["retention_days"] == 1
    assert response.json()["inst_id"] is not None

    response = datakinder_client.post("/institutions", json=INSTITUTION_REQ_BAREBONES)
    assert response.status_code == 200
    assert response.json()["name"] == "testing school"

    response = datakinder_client.post(
        "/institutions", json={"name": "Testing A & M - Main Campus _ hello"}
    )
    assert response.status_code == 200

    response = datakinder_client.post(
        "/institutions", json={"name": "Testing (invalid)"}
    )
    assert response.status_code == 400
    assert (
        response.text
        == '{"detail":"Only alphanumeric characters, -, _, &, and a space are allowed in institution names."}'
    )


def test_edit_inst(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions/<uuid>. For various user access types."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Authorized.
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_1),
        json={"name": "Testing A & M - Main Campus _ hello"},
    )
    assert response.status_code == 400
    assert response.text == '{"detail":"Institution names cannot be changed."}'

    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_1), json={"state": "NY", "pdp_id": "123"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "school_1"
    assert response.json()["state"] == "NY"
    assert response.json()["pdp_id"] == "123"
    assert "edvise_id" in response.json()


def test_delete_inst(datakinder_client: TestClient) -> None:
    """Test DELETE /institutions/<uuid>. For various user access types."""
    MOCK_STORAGE.delete_bucket.return_value = None
    MOCK_DATABRICKS.delete_inst.return_value = None

    response = datakinder_client.get("/institutions/" + uuid_to_str(UUID_1))
    assert response.status_code == 200
    assert response.json()["name"] == "school_1"

    # Authorized.
    response_delete = datakinder_client.delete("/institutions/" + uuid_to_str(UUID_1))
    assert response_delete.status_code == 200

    response2 = datakinder_client.get("/institutions/" + uuid_to_str(UUID_1))
    assert response2.status_code == 404


# ============================================================================
# CREATE INSTITUTION TESTS - Edvise Functionality
# ============================================================================


def test_create_inst_with_edvise_success(datakinder_client: TestClient) -> None:
    """Test POST /institutions with Edvise ID - happy path."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    request_data = {
        "name": "new_edvise_school",
        "state": "TX",
        "edvise_id": "edvise789",
        "is_edvise": True,  # Should be ignored but accepted
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "new_edvise_school"
    assert data["state"] == "TX"
    assert data["edvise_id"] == "edvise789"
    assert data["pdp_id"] is None
    assert "inst_id" in data


def test_create_inst_with_edvise_id_only(datakinder_client: TestClient) -> None:
    """Test POST /institutions with edvise_id but no is_edvise flag."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    request_data = {
        "name": "edvise_only_test",
        "edvise_id": "edvise999",
        # Note: is_edvise not provided - should still work
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["edvise_id"] == "edvise999"
    assert data["pdp_id"] is None


def test_create_inst_mutual_exclusivity_error(datakinder_client: TestClient) -> None:
    """Test POST /institutions with both PDP and Edvise - should fail."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    request_data = {
        "name": "conflict_school",
        "pdp_id": "pdp123",
        "edvise_id": "edvise456",
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 400
    assert "cannot be both PDP and Edvise" in response.json()["detail"]


def test_create_inst_empty_string_normalization(datakinder_client: TestClient) -> None:
    """Test POST /institutions - empty strings normalized to None."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Empty string should be normalized to None
    request_data = {
        "name": "normalization_test",
        "pdp_id": "",  # Empty string
        "edvise_id": "   ",  # Whitespace only
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["pdp_id"] is None
    assert data["edvise_id"] is None


def test_create_inst_whitespace_stripping(datakinder_client: TestClient) -> None:
    """Test POST /institutions - whitespace is stripped from IDs."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    request_data = {
        "name": "whitespace_test",
        "edvise_id": "  edvise123  ",  # Has whitespace
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["edvise_id"] == "edvise123"  # Whitespace stripped


def test_create_inst_backward_compatibility_is_pdp_ignored(
    datakinder_client: TestClient,
) -> None:
    """Test POST /institutions - is_pdp flag is accepted but ignored."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Send is_pdp=True but no pdp_id - should work (is_pdp ignored)
    request_data = {
        "name": "backward_compat_test",
        "is_pdp": True,  # Should be ignored
        "pdp_id": None,  # No actual PDP ID
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["pdp_id"] is None  # No PDP schema assigned


def test_create_inst_backward_compatibility_is_edvise_ignored(
    datakinder_client: TestClient,
) -> None:
    """Test POST /institutions - is_edvise flag is accepted but ignored."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Send is_edvise=True but no edvise_id - should work (is_edvise ignored)
    request_data = {
        "name": "backward_compat_edvise_test",
        "is_edvise": True,  # Should be ignored
        "edvise_id": None,  # No actual Edvise ID
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["edvise_id"] is None  # No Edvise schema assigned


# ============================================================================
# UPDATE INSTITUTION TESTS - Edvise Functionality
# ============================================================================


def test_update_inst_add_edvise_id(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - add edvise_id to existing institution."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Update custom school (UUID_2) to Edvise
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"edvise_id": "new_edvise_id"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["edvise_id"] == "new_edvise_id"
    assert data["pdp_id"] is None


def test_update_inst_switch_pdp_to_edvise(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - switch from PDP to Edvise."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # First clear PDP, then add Edvise
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_1),
        json={"pdp_id": None, "edvise_id": "switched_to_edvise"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pdp_id"] is None
    assert data["edvise_id"] == "switched_to_edvise"


def test_update_inst_switch_edvise_to_pdp(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - switch from Edvise to PDP."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Switch UUID_3 (Edvise) to PDP
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_3),
        json={"edvise_id": None, "pdp_id": "switched_to_pdp"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pdp_id"] == "switched_to_pdp"
    assert data["edvise_id"] is None


def test_update_inst_mutual_exclusivity_error(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - cannot set both PDP and Edvise."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Try to set both on custom school
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"pdp_id": "pdp123", "edvise_id": "edvise456"},
    )
    assert response.status_code == 400
    assert "cannot be both PDP and Edvise" in response.json()["detail"]


def test_update_inst_clear_edvise_id(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - clear edvise_id (set to None)."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # First set edvise_id, then clear it
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"edvise_id": "temp_edvise"},
    )
    assert response.status_code == 200

    # Now clear it
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"edvise_id": None},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["edvise_id"] is None


def test_update_inst_empty_string_normalization(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - empty strings normalized to None."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Send empty string - should be normalized to None
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"edvise_id": ""},  # Empty string
    )
    assert response.status_code == 200
    data = response.json()
    assert data["edvise_id"] is None  # Normalized


def test_update_inst_whitespace_stripping(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - whitespace is stripped from IDs."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"edvise_id": "  trimmed_id  "},  # Has whitespace
    )
    assert response.status_code == 200
    data = response.json()
    assert data["edvise_id"] == "trimmed_id"  # Whitespace stripped


# ============================================================================
# GET ENDPOINT TESTS - edvise_id in Responses
# ============================================================================


def test_read_inst_by_id_includes_edvise_id(client: TestClient) -> None:
    """Test GET /institutions/{inst_id} - response includes edvise_id."""
    # Authorized access
    response = client.get("/institutions/" + uuid_to_str(USER_VALID_INST_UUID))
    assert response.status_code == 200
    data = response.json()
    assert "edvise_id" in data
    assert data["edvise_id"] is None  # This institution doesn't have Edvise


def test_read_inst_by_name_includes_edvise_id(client: TestClient) -> None:
    """Test GET /institutions/name/{name} - response includes edvise_id."""
    # Authorized access
    response = client.get("/institutions/name/valid_school")
    assert response.status_code == 200
    data = response.json()
    assert "edvise_id" in data


def test_read_inst_by_pdp_id_includes_edvise_id(client: TestClient) -> None:
    """Test GET /institutions/pdp-id/{pdp_id} - response includes edvise_id."""
    # Authorized access
    response = client.get("/institutions/pdp-id/12345")
    assert response.status_code == 200
    data = response.json()
    assert "edvise_id" in data
    assert data["edvise_id"] is None


# ============================================================================
# EDGE CASES AND ERROR HANDLING
# ============================================================================


def test_create_inst_none_values(datakinder_client: TestClient) -> None:
    """Test POST /institutions - explicit None values handled correctly."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    request_data = {
        "name": "none_values_test",
        "pdp_id": None,
        "edvise_id": None,
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["pdp_id"] is None
    assert data["edvise_id"] is None


def test_update_inst_partial_update_preserves_existing(
    datakinder_client: TestClient,
) -> None:
    """Test PATCH /institutions - partial update preserves existing edvise_id."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # First set edvise_id
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"edvise_id": "preserved_edvise"},
    )
    assert response.status_code == 200

    # Update only state, edvise_id should be preserved
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"state": "FL"},  # Only update state
    )
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "FL"
    assert data["edvise_id"] == "preserved_edvise"  # Preserved


def test_update_inst_final_state_validation(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - validates final state, not just update data."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Institution already has pdp_id, try to add edvise_id
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_1),  # Has pdp_id="456"
        json={"edvise_id": "edvise999"},  # Try to add Edvise
    )
    assert response.status_code == 400
    assert "cannot be both PDP and Edvise" in response.json()["detail"]


# ============================================================================
# AUTHORIZATION TESTS
# ============================================================================


def test_create_inst_edvise_unauthorized(client: TestClient) -> None:
    """Test POST /institutions with Edvise - unauthorized user."""
    os.environ["ENV"] = "DEV"
    request_data = {
        "name": "unauthorized_test",
        "edvise_id": "edvise123",
    }

    response = client.post("/institutions", json=request_data)
    assert response.status_code == 401
    assert "Not authorized to create" in response.json()["detail"]


def test_update_inst_edvise_unauthorized(client: TestClient) -> None:
    """Test PATCH /institutions with Edvise - unauthorized user."""
    # Try to update institution user doesn't have access to
    response = client.patch(
        "/institutions/" + uuid_to_str(UUID_1),
        json={"edvise_id": "edvise123"},
    )
    assert response.status_code == 401
    assert "Not authorized" in response.json()["detail"]


# ============================================================================
# TENANT ISOLATION TESTS
# ============================================================================


def test_read_inst_edvise_tenant_isolation(client: TestClient) -> None:
    """Test GET /institutions/{inst_id} - cannot access other institution's Edvise data."""
    # Try to access institution user doesn't belong to
    response = client.get("/institutions/" + uuid_to_str(UUID_2))
    assert response.status_code == 401
    assert "Not authorized" in response.json()["detail"]


# ============================================================================
# BACKWARD COMPATIBILITY TESTS
# ============================================================================


def test_create_inst_old_format_still_works(datakinder_client: TestClient) -> None:
    """Test POST /institutions - old request format with is_pdp still works."""
    os.environ["ENV"] = "DEV"
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Old format: is_pdp flag (should be ignored, pdp_id used instead)
    request_data = {
        "name": "old_format_test",
        "is_pdp": True,
        "pdp_id": "pdp_old_format",
    }

    response = datakinder_client.post("/institutions", json=request_data)
    assert response.status_code == 200
    data = response.json()
    # Should use pdp_id, not is_pdp flag
    assert data["pdp_id"] == "pdp_old_format"


def test_update_inst_old_format_still_works(datakinder_client: TestClient) -> None:
    """Test PATCH /institutions - old request format with is_pdp still works."""
    MOCK_STORAGE.create_bucket.return_value = None
    MOCK_STORAGE.create_folders.return_value = None
    MOCK_DATABRICKS.setup_new_inst.return_value = None

    # Old format: is_pdp flag (should be ignored)
    response = datakinder_client.patch(
        "/institutions/" + uuid_to_str(UUID_2),
        json={"is_pdp": True, "pdp_id": "pdp_update"},  # is_pdp ignored
    )
    assert response.status_code == 200
    data = response.json()
    # Should use pdp_id value
    assert data["pdp_id"] == "pdp_update"
