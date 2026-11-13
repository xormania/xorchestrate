"""Loopback OSV service and isolated lock subjects for dependency-audit contracts."""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.hexagonal.support.static_checks import ROOT

VULNERABILITY_ID = "OSV-TEST-0001"
ALIAS = "CVE-2099-0001"
SUMMARY_CANARY = "dependency-audit-summary-secret-canary"
DETAIL_CANARY = "dependency-audit-detail-secret-canary"
URL_CANARY = "https://dependency-audit-secret.invalid/details"


def audit_subject(root):
    """Copy only the declared project and lock inputs into an isolated candidate."""
    root = Path(root)
    root.mkdir(parents=True)
    for relative in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / relative, root / relative)
    return root


class OsvService:
    """A real loopback HTTP boundary with controlled OSV batch/detail outcomes."""

    def __init__(self, mode="clean", vulnerable="attrs"):
        self.mode = mode
        self.vulnerable = vulnerable
        self.queries = []
        self.paths = []
        self.headers = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, payload, content_type="application/json"):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                owner.paths.append(("POST", self.path))
                owner.headers.append(dict(self.headers))
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                owner.queries.extend(request["queries"])
                if owner.mode == "unavailable":
                    self._send(503, {"error": "service-unavailable-canary"})
                    return
                if owner.mode == "malformed":
                    self._send(200, b"{", "application/json")
                    return
                results = []
                for query in request["queries"]:
                    if owner.mode == "vulnerable" and query["package"]["name"] == owner.vulnerable:
                        results.append({"vulns": [{
                            "id": VULNERABILITY_ID,
                            "modified": "2026-01-02T00:00:00Z",
                        }]})
                    else:
                        results.append({})
                self._send(200, {"results": results})

            def do_GET(self):
                owner.paths.append(("GET", self.path))
                if owner.mode != "vulnerable" or not self.path.endswith("/" + VULNERABILITY_ID):
                    self._send(404, {"error": "not-found"})
                    return
                package = {"ecosystem": "PyPI", "name": owner.vulnerable}
                version = next(query["version"] for query in owner.queries
                               if query["package"]["name"] == owner.vulnerable)
                self._send(200, {
                    "id": VULNERABILITY_ID,
                    "modified": "2026-01-02T00:00:00Z",
                    "published": "2026-01-01T00:00:00Z",
                    "aliases": [ALIAS],
                    "summary": SUMMARY_CANARY,
                    "details": DETAIL_CANARY,
                    "affected": [{
                        "package": package,
                        "ranges": [{"type": "ECOSYSTEM", "events": [
                            {"introduced": "0"}, {"fixed": "99.0.0"},
                        ]}],
                        "versions": [version],
                    }],
                    "references": [{"type": "ADVISORY", "url": URL_CANARY}],
                })

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
