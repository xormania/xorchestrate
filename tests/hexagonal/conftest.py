"""Common adapter selection and lifecycle for the active behavioral framework."""

import pytest

from tests.hexagonal.support.application import STORAGE_ADAPTERS, make_store


@pytest.fixture(params=STORAGE_ADAPTERS)
def store(request, tmp_path):
    """Every dependent contract runs against both declared storage adapters."""
    instance = make_store(request.param, tmp_path / "state.sqlite3")
    try:
        yield instance
    finally:
        instance.close()
