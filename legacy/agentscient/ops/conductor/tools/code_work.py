"""Prod-lane code work through Serena; dev-lane owns identity and source receipts."""

import hashlib
import os
from pathlib import Path

import instrument
import source_work
from code_providers.serena import PIN, POLICY, Provider, bounded
from instrument import refuse

CAPABILITIES = {
    "code_status": ("Inspect the prod-lane source binding, Serena REPL epoch and current check coverage.", {}, ()),
    "code_repl": (
        ("Discover and edit Symfony prod-lane code through persistent Serena v2 Python. Start with s.info('fs', 'lsp', 'edit'). "
        "Every cell invalidates working-source checks, even if it returns an error. Git, tests, Mate, Makers and workflow "
        "operations use their named dev-lane capabilities."),
        {"code": {"type": "string", "maxLength": 32000},
         "expected_source_fingerprint": {"type": "string", "maxLength": 64},
         "expected_epoch": {"type": "string", "maxLength": 32}},
        ("code", "expected_source_fingerprint", "expected_epoch")),
    "code_restart": (
        "Restart this participant's private Serena provider; REPL variables are lost, assignment records survive.",
        {"expected_epoch": {"type": "string", "maxLength": 32}}, ("expected_epoch",)),
}


class CodeWork:
    def __init__(self, python=None, workspace=None):
        self.python = os.path.abspath(python) if python else None
        self.workspace = Path(workspace).resolve() if workspace else None
        self.provider = None
        self.owner = None

    def unavailable(self):
        if not self.python:
            return "Serena is not configured; select the isolated environment with --serena-python FILE."
        if not Path(self.python).is_file() or not os.access(self.python, os.X_OK):
            return "The configured Serena Python executable is unavailable; repair the isolated provider environment."
        return None

    def bind(self, config, name):
        self.owner = source_work.bind(config, name, self.workspace, self.owner)
        self.workspace = Path(self.owner[0])
        if self.provider is None and not self.unavailable():
            key = hashlib.sha256(repr(self.owner).encode()).hexdigest()
            runtime = instrument.state_dir() / "providers" / "serena" / key
            if runtime.resolve().is_relative_to(self.workspace):
                refuse("provider", "provider state outside the prod-lane worktree", str(runtime),
                       "configure the lane's state directory outside the worktree")
            self.provider = Provider(self.python, self.workspace, runtime)

    def status(self):
        result = source_work.status(self.workspace)
        provider = self.provider
        last = result["last_event"]
        result.update(workspace=str(self.workspace), participant=f"PT-{self.owner[1]}",
                      assignment=self.owner[3], scope="prod-lane", configured=not self.unavailable(),
                      unavailable_reason=self.unavailable(), serena_pin=PIN,
                      provider_epoch=provider.epoch if provider else None,
                      repl_state=("active" if provider and provider.session_started else "not_started"),
                      repl_note=provider.reason if provider else "No provider is configured.",
                      previous_namespace_lost=bool(last and provider and last["provider_epoch"] != provider.epoch))
        return result

    def call(self, config, name, arguments):
        self.bind(config, name)
        if name == "code_status":
            return self.status()
        if reason := self.unavailable():
            refuse("provider", "the isolated Serena v2 environment", "unavailable", reason)
        if arguments["expected_epoch"] != self.provider.epoch:
            refuse("repl-state", "the current provider epoch", "provider restarted or context changed",
                   "read code_status and rebuild required temporary variables")
        if name == "code_restart":
            self.provider.restart()
            return self.status()
        with source_work.workspace_lock(self.workspace):
            before = source_work.snapshot(self.workspace)
            if arguments["expected_source_fingerprint"] != before["fingerprint"]:
                refuse("stale", "the source fingerprint from code_status", "working source changed",
                       "refresh code_status and inspect the change before retrying")
            event = source_work.begin(self.workspace, self.owner[1], self.owner[3], self.provider.epoch, before)
            result = {"cell_outcome": "unknown"}
            try:
                result = self.provider.execute(arguments["code"])
            except Exception as error:  # noqa: BLE001 - preserve partial edits on every provider failure
                text, truncated = bounded(str(error))
                result = {"cell_outcome": "provider_lost", "text": text, "truncated": truncated}
                self.provider.restart("provider failed; previous REPL variables are lost; inspect source before retrying")
            finally:
                # Even a normal MCP result may be a Python error after a write.
                outcome = result["cell_outcome"]
                try:
                    after = source_work.snapshot(self.workspace)
                except Exception as error:  # noqa: BLE001 - report unknown source without losing the durable event
                    after, outcome = None, "source_unknown"
                    result["source_error"] = bounded(str(error), limit=500)[0]
                changes = source_work.finish(event, before, after, outcome)
            if after is None:
                return {**result, "source_event": event, "source_fingerprint": None,
                        "source_observation": "failed; inspect and repair the worktree before another cell",
                        "provider_epoch": self.provider.epoch, "verification_invalidated": True}
            result.update(source_event=event, source_fingerprint=after["fingerprint"],
                          provider_epoch=self.provider.epoch, verification_invalidated=True,
                          changed_paths=[path[:160] for path in changes[:10]], changed_count=len(changes),
                          changes_truncated=len(changes) > 10 or any(len(path) > 160 for path in changes),
                          outside_prod_lane=[path[:160] for path in changes if path.split("/")[0] in {"ops", ".dev", ".xor"}][:10])
            return result

    def close(self):
        if self.provider:
            self.provider.close()


INSTRUCTIONS = POLICY
