"""Fakes for the operator ports and a local stand-in for the GitHub REST API.

The fakes are xcontracts' reference fakes, which pass its conformance kit. They
are re-exported here so configurations select them as
``tests.hexagonal.support.operator_ports:<Name>``, through the same selection
path as configured adapters.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from xcontracts.testing.fakes import FakeHosting, FakeVersionControl, MemoryCompanion

__all__ = ["FakeHosting", "FakeVersionControl", "MemoryCompanion", "MutatingHosting", "github_api"]

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


class MutatingHosting(FakeHosting):
    """A hosting factory that changes the nested options it is given, as a careless adapter might."""

    def __init__(self, options=None):
        super().__init__(options)
        (options or {}).setdefault("visibility", {})["https://example.test/mutated"] = "public"
