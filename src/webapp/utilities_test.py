"""Test file for utilities.py."""

import pytest

from fastapi import HTTPException
from .utilities import (
    has_access_to_inst_or_err,
    has_full_data_access_or_err,
    uuid_to_str,
    databricksify_inst_name,
)

from .test_helper import USR, DATAKINDER, VIEWER, UUID_INVALID, USER_VALID_INST_UUID


def test_base_user_class_functions():
    """Run tests on various BaseUser class functions."""
    assert DATAKINDER.is_datakinder()
    assert not USR.is_datakinder()

    assert DATAKINDER.has_access_to_inst(uuid_to_str(USER_VALID_INST_UUID))
    assert USR.has_access_to_inst(uuid_to_str(USER_VALID_INST_UUID))
    assert not USR.has_access_to_inst(uuid_to_str(UUID_INVALID))

    assert DATAKINDER.has_full_data_access()
    assert USR.has_full_data_access()
    assert not VIEWER.has_full_data_access()


def test_has_access_to_inst_or_err():
    """Testing valid check for access to institution."""
    with pytest.raises(HTTPException) as err:
        has_access_to_inst_or_err("456", USR)
    assert err.value.status_code == 401
    assert err.value.detail == "Not authorized to read this institution's resources."


def test_has_full_data_access_or_err():
    """Testing valid check for access to full data."""
    with pytest.raises(HTTPException) as err:
        has_full_data_access_or_err(VIEWER, "models")
    assert err.value.status_code == 401
    assert err.value.detail == "Not authorized to view models for this institution."


def test_databricksify_inst_name():
    """
    Testing databricksifying institution name
    """
    assert (
        databricksify_inst_name("The University of Mildly Impressive Achievements")
        == "the_uni_of_mildly_impressive_achievements"
    )
    assert (
        databricksify_inst_name("Dandelion Technical & Tractor College")
        == "dandelion_technical_tractor_col"
    )
    assert (
        databricksify_inst_name("Fernwood & Finch Academy") == "fernwood_finch_academy"
    )
    assert (
        databricksify_inst_name("The Center for Applied Napping")
        == "the_center_for_applied_napping"
    )
    assert (
        databricksify_inst_name("Harrisville University of Science and Technology")
        == "harrisville_uni_st"
    )
    assert (
        databricksify_inst_name("University of Questionable Decisions")
        == "uni_of_questionable_decisions"
    )
    assert (
        databricksify_inst_name("Badger Hollow University of Science & Scones")
        == "badger_hollow_uni_of_science_scones"
    )

    with pytest.raises(ValueError) as err:
        databricksify_inst_name("Northwest (invalid)")
    assert str(err.value) == "Unexpected character found in Databricks compatible name."
