"""Private persistent Serena v2 REPL, using the existing stdio RPC primitive.

Private-session pattern adapted from the MCP PoC; provenance is in the
commit Source trailer. No Serena implementation is vendored. This process
serves prod-lane source work only.
"""

import contextlib
import json
import os
import re
import signal
import subprocess
import threading
import uuid
from pathlib import Path

from session_adapters.channel import RPC

PIN = "b79e2a55f9d4072084977dd35bd07d31146d88b9"
VERSION = "2.0.0.dev0"
TIMEOUT = 180
ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TZ", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
               "https_proxy", "http_proxy", "all_proxy", "no_proxy",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
               "NPM_CONFIG_REGISTRY", "NPM_CONFIG_CACHE", "NPM_CONFIG_OFFLINE")
POLICY = (
    "Work on this project's Symfony prod-lane source. Do not develop ops/ or .dev/. "
    "Use s.info to discover fs, lsp and edit APIs, then return selected results. "
    "Keep Git, tests, dependencies, Mate, Makers, messaging and workflow state in "
    "their named dev-lane capabilities; do not implement those operations in Python cells. "
    "REPL Python has provider-process permissions; these instructions are not a sandbox."
)


class ProviderLost(RuntimeError):
    """A call may have partially executed; never retry a cell automatically."""


class BoundedLines:
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        while line := self.stream.readline(2 * 1024 * 1024 + 1):
            if len(line) > 2 * 1024 * 1024:
                raise ValueError("Serena response exceeds the private transport bound; return selected results")
            yield line


def bounded(text, limit=2600):
    """Bound serialized text, including JSON escapes, before the outer MCP envelope."""
    left, right = 0, len(text)
    while left < right:
        middle = (left + right + 1) // 2
        if len(json.dumps(text[:middle]).encode()) <= limit:
            left = middle
        else:
            right = middle - 1
    return text[:left], left < len(text)


class Provider:
    def __init__(self, python, workspace, runtime):
        self.python = str(python)
        self.workspace = Path(workspace)
        self.runtime = Path(runtime) / uuid.uuid4().hex
        self.epoch = uuid.uuid4().hex
        self.reason = "new provider; no previous REPL variables are restored"
        self.rpc = None
        self.process = None
        self.log = None
        self.session = uuid.uuid4().hex
        self.session_started = False
        self.responses = None

    def environment(self):
        # Package transport settings are allowed; harness identities, publication
        # credentials, SSH agents and lane/store environment are not inherited.
        env = {key: os.environ[key] for key in ENVIRONMENT if key in os.environ}
        # The log is beside the private home; protect their common directory too.
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        home = self.runtime / "home"
        project_data = self.runtime / "project"
        for directory in (home, project_data):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        env.update(HOME=str(home), SERENA_HOME=str(home / "serena"), SERENA_USAGE_REPORTING="false",
                   XDG_CACHE_HOME=str(home / "cache"), XDG_CONFIG_HOME=str(home / "config"),
                   XDG_DATA_HOME=str(home / "data"), XDG_STATE_HOME=str(home / "state"),
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        # Intelephense 1.14.4 bundles Application Insights 2.7.3. Its supported
        # JSON configuration disables both telemetry transmission and Statsbeat
        # before the LSP starts; upstream Serena does not forward telemetry.enabled.
        env["APPLICATIONINSIGHTS_CONFIGURATION_CONTENT"] = json.dumps({
            "disableAppInsights": True, "disableStatsbeat": True,
            "disableAllExtendedMetrics": True, "noDiagnosticChannel": True})
        env.update(NPM_CONFIG_AUDIT="false", NPM_CONFIG_FUND="false", NPM_CONFIG_UPDATE_NOTIFIER="false")
        Path(env["SERENA_HOME"]).mkdir(exist_ok=True)
        config = {"projects": [], "project_serena_folder_location": str(project_data),
                  "trusted_project_path_patterns": [str(self.workspace)],
                  "web_dashboard_open_on_launch": False}
        project = {"project_name": "prod-lane", "language_servers": ["php"], "encoding": "utf-8",
                   "ignore_all_files_in_gitignore": True, "read_only": False,
                   "ignored_paths": ["ops/**", ".dev/**", ".xor/**", ".serena/**"],
                   "default_modes": ["interactive", "editing", "no-memories"],
                   "excluded_tools": ["execute_shell_command"], "initial_prompt": POLICY,
                   "ls_specific_settings": {"php": {"intelephense_version": "1.14.4"}}}
        # JSON is YAML: use stdlib serialization without adding a dev-lane dependency.
        (Path(env["SERENA_HOME"]) / "serena_config.yml").write_text(json.dumps(config))
        (project_data / "project.yml").write_text(json.dumps(project))
        return env

    def _answer_requests(self):
        rpc = self.rpc
        while not rpc.dead.is_set():
            frame = rpc.next_frame(timeout=0.2)
            if frame is not None and "id" in frame and "method" in frame:
                if frame["method"] == "ping":
                    rpc.answer(frame, result={})
                else:
                    rpc.answer(frame, error={"code": -32601, "message": "Private code provider has no client-side tools"})

    def ensure(self):
        if self.rpc is not None:
            if self.rpc.dead.is_set():
                raise ProviderLost("Serena exited; inspect source before retrying")
            return
        env = self.environment()
        self.log = (self.runtime / "provider.log").open("ab")
        # A fixed entry point, never caller-supplied Python or argv. The package
        # lives in the operator-selected isolated environment.
        boot = ("import json; from importlib.metadata import distribution; "
                "d = distribution('serena-agent'); "
                f"assert d.version == {VERSION!r}, 'expected pinned Serena v2 beta'; "
                "origin = json.loads(d.read_text('direct_url.json') or '{}'); "
                f"assert origin.get('vcs_info', {{}}).get('commit_id') == {PIN!r}, 'install the pinned Serena requirement'; "
                "from serena.cli import top_level; top_level()")
        argv = [self.python, "-I", "-B", "-c", boot, "start-mcp-server",
                "--project", str(self.workspace), "--agent-interface", "repl",
                "--enable-web-dashboard", "false", "--enable-gui-log-window", "false",
                "--open-web-dashboard", "false", "--tool-timeout", str(TIMEOUT)]
        self.process = subprocess.Popen(  # noqa: S603 - fixed provider entry point
            argv, cwd=self.workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, start_new_session=True)
        self.rpc = RPC(BoundedLines(self.process.stdout), self.process.stdin, log=self.log)
        self.responses = threading.Thread(target=self._answer_requests, daemon=True)
        self.responses.start()
        self.rpc.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "dev-lane-code", "version": "1"}}, timeout=TIMEOUT)
        self.rpc.notify("notifications/initialized")
        tools = self.rpc.request("tools/list", timeout=TIMEOUT).get("tools", [])
        repl = next((tool for tool in tools if tool.get("name") == "serena_repl"), None)
        if repl is None or not {"session", "code"} <= set(repl.get("inputSchema", {}).get("properties", {})):
            raise ProviderLost("Pinned Serena serena_repl(session, code) is unavailable")

    def _cell(self, code):
        result = self.rpc.request("tools/call", {"name": "serena_repl",
                                  "arguments": {"session": self.session, "code": code}}, timeout=TIMEOUT)
        text = "\n".join(block["text"] for block in result.get("content", [])
                         if block.get("type") == "text" and isinstance(block.get("text"), str))
        return text, bool(result.get("isError"))

    def execute(self, code):
        self.ensure()
        marker, error = self._cell("globals().get('_dev_lane_epoch')")
        if error or (self.session_started and marker != self.epoch):
            raise ProviderLost("Serena evicted or cleared the REPL namespace; previous variables are lost")
        if not self.session_started:
            _, error = self._cell(f"_dev_lane_epoch = {self.epoch!r}")
            if error:
                raise ProviderLost("Could not initialize the private Serena session")
            self.session_started = True
        text, error = self._cell(code)
        # At the pinned beta, Python exceptions are returned as normal MCP text.
        # The formatter includes cell locations; a protocol success is not a
        # successful Python cell. Preserve the text, even when it looks like an error.
        error = error or bool(re.search(r"(?m)^  line [0-9]+:", text))
        selected, truncated = bounded(text)
        return {"text": selected, "truncated": truncated, "characters": len(text),
                "cell_outcome": "error" if error else "returned"}

    def restart(self, reason="provider restarted; previous REPL variables are lost"):
        self.close()
        self.epoch = uuid.uuid4().hex
        self.session = uuid.uuid4().hex
        self.session_started = False
        self.reason = reason

    def close(self):
        if self.rpc:
            self.rpc.close()
        if self.process:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=2)
            # This process group contains only this private Serena and its LSPs.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=2)
            # The parent may already have exited while an LSP ignored SIGTERM.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=2)
            if self.process.stdout:
                self.process.stdout.close()
        if self.responses:
            self.responses.join(timeout=1)
        if self.log:
            self.log.close()
        self.rpc = self.process = self.responses = self.log = None
