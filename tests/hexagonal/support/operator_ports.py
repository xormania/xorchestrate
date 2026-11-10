"""Fakes for the operator ports and a local stand-in for the GitHub REST API.

The fakes are selected like real adapters (``module:factory`` with options), so
tests exercise the same selection path as configured adapters.
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from xorchestrate.model import OrchestrationError
from xorchestrate.ports import HostingVisibility, WorktreeLocation

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _name(value, what):
    if not isinstance(value, str) or not NAME.match(value):
        raise OrchestrationError(f"Invalid companion record {what}.", "invalid_arguments", {what: value})
    return value


def _key(value):
    if not isinstance(value, str) or not all(NAME.match(part) for part in value.split("/")):
        raise OrchestrationError("Invalid companion record key.", "invalid_arguments", {"key": value})
    return value


class _MemoryRecords:
    def __init__(self, store, project):
        self.store = store
        self.project = None if project is None else _name(project, "project")

    def keys(self, kind):
        _name(kind, "kind")
        return sorted(key for (project, stored_kind, key) in self.store
                      if project == self.project and stored_kind == kind)

    def read(self, kind, key):
        return self.store.get((self.project, _name(kind, "kind"), _key(key)))

    def write(self, kind, key, data, *, create_only=False):
        address = (self.project, _name(kind, "kind"), _key(key))
        # A record cannot also be the prefix of another key (a directory in a file-backed adapter).
        if any(stored[:2] == address[:2] and (stored[2].startswith(key + "/") or key.startswith(stored[2] + "/"))
               for stored in self.store):
            raise OrchestrationError("A companion record cannot be the prefix of another.", "invalid_arguments",
                                     {"key": key})
        if create_only and address in self.store:
            return False
        self.store[address] = bytes(data)
        return True


class MemoryCompanion:
    """A ``Companion`` held in memory; ``unsafe`` makes its location check fail."""

    def __init__(self, options=None, *, version_control=None):
        options = options or {}
        self.location = options.get("name", "memory")
        self.unsafe = options.get("unsafe")
        self.remote_url = options.get("remote")
        self.revision_id = options.get("revision")
        self.store = {}
        self.initialized = False

    def validate_location(self):
        return self.unsafe

    def exists(self):
        return self.initialized

    def initialize(self):
        if self.initialized:
            return []
        self.initialized = True
        return [self.location]

    def records(self, project):
        return _MemoryRecords(self.store, project)

    def remote(self):
        return self.remote_url

    def revision(self):
        return self.revision_id


class FakeVersionControl:
    """A ``VersionControl`` over declared repositories: ``{"repositories": {name: {"worktrees": [...], "roots": [...]}}}``."""

    def __init__(self, options=None):
        self.repositories = (options or {}).get("repositories", {})

    def worktree(self, path):
        for name, repository in self.repositories.items():
            for index, root in enumerate(repository["worktrees"]):
                if path == root or path.startswith(root.rstrip("/") + "/"):
                    common = f"/fake/{name}/.git"
                    return WorktreeLocation(root, common if index == 0 else f"{common}/worktrees/{index}", common,
                                            repository.get("branch"))
        return None

    def root_commits(self, path):
        location = self.worktree(path)
        if location is None:
            return frozenset()
        name = location.common_dir.split("/")[2]
        return frozenset(self.repositories[name].get("roots", []))

    def remote_url(self, path, name="origin"):
        location = self.worktree(path)
        if location is None:
            return None
        return self.repositories[location.common_dir.split("/")[2]].get("remotes", {}).get(name)

    def revision(self, path):
        location = self.worktree(path)
        if location is None:
            return None
        return self.repositories[location.common_dir.split("/")[2]].get("head")

    def initialize(self, path):
        self.repositories[path] = {"worktrees": [path], "roots": []}


class FakeHosting:
    """A ``RepositoryHosting`` answering from ``{"visibility": {remote_url: "private"|"public"}}``; others are unknown."""

    def __init__(self, options=None):
        self.answers = (options or {}).get("visibility", {})

    def visibility(self, remote_url):
        answer = self.answers.get(remote_url)
        if answer is None:
            return HostingVisibility("unknown", None, "the fake has no answer")
        return HostingVisibility(answer, remote_url.rsplit(":", 1)[-1].removesuffix(".git"))


@contextmanager
def github_api(repositories, token="test-token", redirects=None, requests=None):
    """A local HTTP stand-in for ``GET /repos/{owner}/{name}``; no external network.

    ``repositories`` maps ``owner/name`` to ``private`` (bool). Requests without
    ``Bearer <token>`` get 401; unknown repositories 404. ``redirects`` maps a
    repository to a URL answered with 302. Each request's path and
    ``Authorization`` header is appended to ``requests`` when given.
    """
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            if requests is not None:
                requests.append((self.path, self.headers.get("Authorization")))
            name = self.path.removeprefix("/repos/")
            if redirects and name in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[name])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if self.headers.get("Authorization") != f"Bearer {token}":
                return self._reply(401, {"message": "Requires authentication"})
            if not self.path.startswith("/repos/") or name not in repositories:
                return self._reply(404, {"message": "Not Found"})
            return self._reply(200, {"full_name": name, "private": repositories[name]})

        def _reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *arguments):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
