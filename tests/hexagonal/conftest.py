"""Common adapter selection and lifecycle for the active behavioral framework."""

import pytest

from tests.hexagonal.support.application import STORAGE_ADAPTERS, make_store
from tests.hexagonal.support.projects import ProjectFixture


@pytest.fixture(params=STORAGE_ADAPTERS)
def store(request, tmp_path):
    """Every dependent contract runs against both declared storage adapters."""
    instance = make_store(request.param, tmp_path / "state.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def project(tmp_path):
    """A provisioned fixture project: git repository, private home, companion and approval.

    It returns the project's context for ``build(config, project=...)``, its
    paths and a CLI runner that runs inside the project (see
    ``tests/hexagonal/support/projects.py``).
    """
    return ProjectFixture(tmp_path / "project")


@pytest.fixture
def worktree(project):
    """A second, linked worktree of the ``project`` fixture on branch ``feature``."""
    return project.add_worktree("feature")
