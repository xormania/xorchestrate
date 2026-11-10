"""Configured PR capability provider assembled from Git and hosting adapters."""
from datetime import datetime, timezone

from ..model import OrchestrationError
from ..pr import PullRequestOperations
from .pr_github import GitHubPRHost
from .pr_workspace import GitPRWorkspace


class PullRequestProvider(PullRequestOperations):
    def __init__(self, options=None, *, store, state_engine=None):
        options = options or {}
        if not isinstance(options, dict) or options.keys() - {"github", "git"}:
            raise OrchestrationError("The PR provider accepts github and git adapter options.", "invalid_configuration")
        host = GitHubPRHost(options.get("github"))
        workspace = GitPRWorkspace(options.get("git"), credential=host.connection._token,
            hosting_host=host.connection.host)
        super().__init__(host, workspace,
            store, lambda: datetime.now(timezone.utc).isoformat(), state_engine=state_engine)
