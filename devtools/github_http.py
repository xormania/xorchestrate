"""Small GitHub API transport. Credentials and arbitrary HTTP are never MCP inputs."""

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class GitHubError(RuntimeError):
    def __init__(self, status, message, retry_at=None):
        self.status = status
        self.retry_at = retry_at
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubHTTP:
    def __init__(self, config=None, *, api_root="https://api.github.com", credential=None):
        self.config = config or {}
        self.api_root = api_root.rstrip("/")
        self.credential = credential or self.token
        self.opener = urllib.request.build_opener(NoRedirect())
        self.secrets = set()

    def token(self):
        path = self.config.get("token_file")
        if path:
            try:
                value = Path(path).expanduser().read_text().strip()
            except OSError:
                raise GitHubError(401, "Cannot read the configured GitHub credential file.") from None
        else:
            value = os.environ.get("AGENTSCIENT_GITHUB_TOKEN", "")
            if not value:
                try:
                    result = subprocess.run(
                        ["gh", "auth", "token", "--hostname", "github.com"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=True,
                    )
                    value = result.stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    raise GitHubError(
                        401,
                        "GitHub authentication is unavailable. The operator can configure a refreshed "
                        "App/OAuth token_file or an authenticated GitHub CLI session.",
                    ) from None
        if not value or any(character.isspace() for character in value):
            raise GitHubError(401, "The GitHub credential is empty or malformed.")
        return value

    def clean(self, value):
        if isinstance(value, dict):
            return {key: self.clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            for secret in tuple(self.secrets):
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
            return re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b", "[REDACTED]", value)
        return value

    def _open(self, method, url, body=None, *, authenticated=True):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "Agentscient-MCP",
        }
        if authenticated:
            token = self.credential()
            self.secrets.add(token)
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            return self.opener.open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=30)
        except urllib.error.HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                raise
            try:
                message = json.loads(error.read(32768)).get("message", "Request rejected")
            except (ValueError, AttributeError):
                message = "Request rejected"
            retry_at = None
            if error.code in {403, 429}:
                try:
                    after = error.headers.get("Retry-After")
                    retry_at = (
                        time.time() + int(after) if after else int(error.headers.get("X-RateLimit-Reset", "0")) or None
                    )
                except ValueError:
                    pass
                if retry_at:
                    message += f"; retry after Unix time {int(retry_at)}"
            raise GitHubError(error.code, self.clean(f"GitHub {error.code}: {message}")[:1200], retry_at) from None
        except (OSError, urllib.error.URLError):
            detail = (
                "Read failed; retry when connectivity returns."
                if method == "GET"
                else ("Write outcome is unknown; inspect GitHub state before repeating the action.")
            )
            raise GitHubError(0, "GitHub connection failed. " + detail) from None

    def request(self, method, path, body=None, **query):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Expected an internal GitHub API path")
        url = self.api_root + path
        if query:
            url += "?" + urllib.parse.urlencode({key: value for key, value in query.items() if value is not None})
        try:
            with self._open(method, url, body) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise GitHubError(0, "GitHub response is too large; request a smaller page.")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError:
            raise GitHubError(
                0, "GitHub redirected this repository request; refresh its repository identity."
            ) from None

    def graphql(self, query, **variables):
        result = self.request("POST", "/graphql", {"query": query, "variables": variables})
        if result.get("errors"):
            raise GitHubError(
                0,
                self.clean("GitHub: " + "; ".join(e.get("message", "GraphQL error") for e in result["errors"]))[:1200],
            )
        return result["data"]

    def download(self, path, *, maximum=256 * 1024 * 1024):
        """Return a seekable private temporary file, following signed redirects without credentials."""
        url, authenticated = self.api_root + path, True
        response = None
        for _ in range(5):
            try:
                response = self._open("GET", url, authenticated=authenticated)
                break
            except urllib.error.HTTPError as error:
                location = error.headers.get("Location", "")
                error.close()
                parsed = urllib.parse.urlsplit(location)
                host = parsed.hostname or ""
                same_api = location.startswith(self.api_root + "/")
                trusted = host in {"github.com", "api.github.com"} or host.endswith(
                    (".githubusercontent.com", ".blob.core.windows.net", ".amazonaws.com")
                )
                if not same_api and (parsed.scheme != "https" or not trusted or parsed.username or parsed.password):
                    raise GitHubError(0, "GitHub download redirected to an unsupported destination.") from None
                url, authenticated = location, False
        if response is None:
            raise GitHubError(0, "GitHub download exceeded its redirect limit.")
        target = tempfile.TemporaryFile()
        try:
            size = 0
            with response:
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > maximum:
                        raise GitHubError(0, "GitHub download exceeds the configured byte limit.")
                    target.write(block)
            target.seek(0)
            return target
        except BaseException:
            target.close()
            raise
