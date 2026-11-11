"""Common adapter selection and lifecycle for the active behavioral framework."""

import pytest

from tests.hexagonal.support import host_processes
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


def pytest_sessionfinish(session, exitstatus):
    """Fail the run when any process placed under this run's temporary roots is still alive.

    Terminal hosts are built to outlive their controller, so a test that forgets
    its teardown leaves a host running for good. Tests register their roots with
    ``host_processes.track``; pytest's own base temporary directory is always
    included.
    """
    roots = set(host_processes.ROOTS)
    factory = getattr(session.config, "_tmp_path_factory", None)
    if getattr(factory, "_basetemp", None) is not None:
        roots.add(str(factory._basetemp))
    leaked = host_processes.processes_under(roots)
    if leaked:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = [f"{pid}: {command}" for pid, command in leaked]
        if reporter is not None:
            reporter.write_sep("=", "processes left running by this test run", red=True)
            for line in lines:
                reporter.write_line(line)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
