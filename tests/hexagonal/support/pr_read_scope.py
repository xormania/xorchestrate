"""Builtin provider subjects with real HTTP and separately counted read boundaries."""

from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

from tests.hexagonal.support.application import application
from xorchestrate.adapters.github import GitHubHosting
from xorchestrate.adapters.pr import PullRequestProvider
from xorchestrate.mcp import MCPServer
from xorchestrate.model import OrchestrationError

UNSET = object()


class ReadScopeSubject:
    def __init__(self, provider, store, remote):
        self.provider, self.remote = provider, remote
        self.host_reads = 0
        self.token_reads = 0
        self.sequence = 0
        self.request_paths = []
        self.token_paths = []
        self.active_request = None
        self.application = application(store, providers={"pr": provider})
        self.server = MCPServer(self.application)
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}})

    @property
    def io_counts(self):
        return self.host_reads, self.token_reads, len(self.remote.requests)

    def read(self, boundary, operation, repository):
        self.sequence += 1
        arguments = {"repository": repository, "number": 17}
        try:
            if boundary == "direct":
                return {"result": self.provider.inspect(arguments)}
            if boundary == "provider":
                return {"result": self.provider.invoke(operation, arguments, {})}
            invocation = {"capability": "pr/" + operation, "arguments": arguments,
                "idempotency_key": "scope-" + str(self.sequence)}
            if boundary == "application":
                return {"result": self.application.call("invoke", invocation)["invocation"]["result"]}
            response = self.server.handle({"jsonrpc": "2.0", "id": self.sequence + 1, "method": "tools/call",
                "params": {"name": "invoke", "arguments": invocation}})["result"]
            value = response["structuredContent"]
            return value if response["isError"] else {"result": value["invocation"]["result"]}
        except OrchestrationError as error:
            return {"error": {"code": error.code, "message": str(error), "details": deepcopy(error.details)}}


@contextmanager
def scoped_provider(store, remote, api_url, workspace_path, *, workspace_repositories=None, read_repositories=UNSET):
    repositories = [remote.repository] if workspace_repositories is None else workspace_repositories
    options = {"github": {"api_url": api_url, "token_variables": ["PR_TEST_TOKEN"], "token_command": []},
        "git": {"workspaces": {str(index): {"path": str(workspace_path), "repository": repository}
            for index, repository in enumerate(repositories)}}}
    if read_repositories is not UNSET:
        options["read_repositories"] = read_repositories
    provider = PullRequestProvider(options, store=store)
    subject = ReadScopeSubject(provider, store, remote)
    original_snapshot, original_request, original_token = provider.host.snapshot, provider.host._request, GitHubHosting._token

    def snapshot(repository, number):
        subject.host_reads += 1
        return original_snapshot(repository, number)

    def token(connection):
        subject.token_reads += 1
        if subject.active_request is not None:
            subject.token_paths.append(subject.active_request)
        return original_token(connection)

    def request(method, path, body=None):
        subject.request_paths.append(path)
        previous = subject.active_request
        subject.active_request = path
        try:
            return original_request(method, path, body)
        finally:
            subject.active_request = previous

    with patch.object(provider.host, "snapshot", snapshot), patch.object(provider.host, "_request", request), \
            patch.object(GitHubHosting, "_token", token):
        yield subject
