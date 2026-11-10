"""Configured PR capability provider assembled from Git and hosting adapters."""
from datetime import datetime, timezone

from ..model import OrchestrationError
from ..pr import PullRequestOperations
from .pr_github import GitHubPRHost
from .pr_workspace import GitPRWorkspace


class PullRequestProvider(PullRequestOperations):
    def __init__(self, options=None, *, store, state_engine=None):
        options = options or {}
        if not isinstance(options, dict) or options.keys() - {"github", "git", "read_repositories"}:
            raise OrchestrationError("The PR provider accepts github, git and read_repositories options.", "invalid_configuration")
        read_repositories = options.get("read_repositories", [])
        if not isinstance(read_repositories, list):
            raise OrchestrationError("PR read_repositories must be a list of repository names.", "invalid_configuration")
        host = GitHubPRHost(options.get("github"), read_admission=self._authorize_read_repository)
        workspace = GitPRWorkspace(options.get("git"), credential=host.connection._token,
            hosting_host=host.connection.host)
        super().__init__(host, workspace,
            store, lambda: datetime.now(timezone.utc).isoformat(), state_engine=state_engine,
            read_repositories=[*read_repositories, *(item["repository"] for item in workspace.workspaces.values())])
