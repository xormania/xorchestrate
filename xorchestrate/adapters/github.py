"""``RepositoryHosting`` for GitHub through its REST API.

Options:

- ``api_url`` (default ``https://api.github.com``; ``https`` only, or ``http``
  to a loopback host) and ``host`` (default
  ``github.com``, the host remote URLs must name);
- ``token_variables`` (default ``["GH_TOKEN", "GITHUB_TOKEN"]``): environment
  variables tried for a token, then ``token_command`` (default
  ``["gh", "auth", "token"]``; ``[]`` disables it);
- ``timeout`` seconds (default 5).

It answers ``unknown`` with a reason instead of failing when the remote is not
on GitHub, no token is available, the request fails or is offline, or GitHub
does not show the repository (a private repository without access looks
missing).
"""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from ..model import OrchestrationError
from ..ports import HostingVisibility
from .process import positive_number

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments, **options):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
_PATTERNS = (
    re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>[^?#]+?)(?:\.git)?/?$"),
    re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:]+):(?P<path>[^?#]+?)(?:\.git)?/?$"),
)


def repository_of(remote_url, host):
    """``owner/name`` of a remote URL on ``host``, or ``None``."""
    for pattern in _PATTERNS:
        match = pattern.match(remote_url.strip())
        if match and match.group("host").lower() == host.lower():
            parts = match.group("path").strip("/").split("/")
            if len(parts) == 2 and all(re.match(r"^[A-Za-z0-9_.-]+$", part) for part in parts):
                return "/".join(parts)
    return None


class GitHubHosting:
    def __init__(self, options=None):
        options = {} if options is None else options
        allowed = {"api_url", "host", "token_variables", "token_command", "timeout"}
        if not isinstance(options, dict) or options.keys() - allowed:
            raise OrchestrationError("The github adapter accepts " + ", ".join(sorted(allowed)) + ".",
                                     "invalid_configuration")
        api_url = options.get("api_url", "https://api.github.com")
        self.api_url = api_url.rstrip("/") if isinstance(api_url, str) else api_url
        self.host = options.get("host", "github.com")
        self.token_variables = options.get("token_variables", ["GH_TOKEN", "GITHUB_TOKEN"])
        self.token_command = options.get("token_command", ["gh", "auth", "token"])
        self.timeout = positive_number(options, "timeout", 5)
        if (not isinstance(self.api_url, str) or not self._secure(self.api_url)
                or not isinstance(self.host, str) or not self.host
                or not isinstance(self.token_variables, list) or not all(isinstance(v, str) for v in self.token_variables)
                or not isinstance(self.token_command, list) or not all(isinstance(v, str) for v in self.token_command)):
            raise OrchestrationError("Invalid github adapter options; api_url must be https (http only for a "
                                     "loopback host).", "invalid_configuration")

    @staticmethod
    def _secure(url):
        """``https``, or ``http`` to a loopback host: the token is never sent in clear over a network."""
        parsed = urllib.parse.urlsplit(url)
        return parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOOPBACK)

    def _token(self):
        for name in self.token_variables:
            if os.environ.get(name):
                return os.environ[name]
        if not self.token_command:
            return None
        try:
            result = subprocess.run(self.token_command, capture_output=True, text=True, timeout=self.timeout,
                                    check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        token = result.stdout.strip()
        return token if result.returncode == 0 and token else None

    def visibility(self, remote_url):
        repository = repository_of(remote_url, self.host) if isinstance(remote_url, str) else None
        if repository is None:
            return HostingVisibility("unknown", None, f"not a {self.host} repository")
        token = self._token()
        if token is None:
            return HostingVisibility("unknown", repository, "not authenticated (set GH_TOKEN or run gh auth login)")
        request = urllib.request.Request(f"{self.api_url}/repos/{repository}", headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "xorchestrate-doctor"})
        try:
            # Redirects are not followed, so the token never reaches another origin or plain http.
            with _OPENER.open(request, timeout=self.timeout) as response:
                data = json.loads(response.read(1_048_576))
        except urllib.error.HTTPError as error:
            reason = ("not visible to this token" if error.code == 404 else
                      "redirect not followed" if 300 <= error.code < 400 else f"HTTP {error.code}")
            return HostingVisibility("unknown", repository, reason)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return HostingVisibility("unknown", repository, "GitHub could not be reached")
        except http.client.HTTPException:
            # A malformed or truncated response (for example a bad status line): never fail, as the port requires.
            return HostingVisibility("unknown", repository, "GitHub returned a malformed response")
        private = data.get("private") if isinstance(data, dict) else None
        if not isinstance(private, bool):
            return HostingVisibility("unknown", repository, "GitHub did not report the visibility")
        return HostingVisibility("private" if private else "public", repository)
