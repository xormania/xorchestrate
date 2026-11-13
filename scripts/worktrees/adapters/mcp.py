"""MCP streamable HTTP with urllib: JSON or server-sent-event replies."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from scripts.worktrees.ports import TransportError

SESSION_HEADER = "Mcp-Session-Id"
DELETE_TIMEOUT = 10.0


class UrllibMcp:
    def __init__(self, timeout: float = 600.0) -> None:
        self.timeout = timeout

    def _headers(self, session: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers[SESSION_HEADER] = session
        return headers

    def post(self, url: str, body: Mapping[str, Any], session: str | None,
             timeout: float | None = None) -> tuple[str | None, list[dict[str, Any]]]:
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=self._headers(session),
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                session = response.headers.get(SESSION_HEADER) or session
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            raise TransportError(str(error)) from error
        return session, _messages(raw)

    def delete(self, url: str, session: str) -> None:
        request = urllib.request.Request(url, headers=self._headers(session), method="DELETE")
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, DELETE_TIMEOUT)):
                pass
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            raise TransportError(str(error)) from error


def _messages(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        if raw.lstrip().startswith(("{", "[")):
            parsed = json.loads(raw)
            messages = parsed if isinstance(parsed, list) else [parsed]
        else:
            messages = [json.loads(line[5:]) for line in raw.splitlines()
                        if line.startswith("data:") and line[5:].strip()]
    except ValueError as error:
        raise TransportError(f"unreadable reply: {error}") from error
    if not all(isinstance(message, dict) for message in messages):
        raise TransportError("reply is not a JSON-RPC message object")
    return messages
