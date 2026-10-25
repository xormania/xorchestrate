"""Named project operations sharing source identity and receipts with code work.

Framework commands, schemas and evidence interpretation belong to the selected
provider. Coordination and source accounting remain independent of its runtime.
"""

import hashlib
import json
from pathlib import Path

import instrument
import project_providers
import source_work
from code_providers.serena import bounded
from instrument import refuse


class ProjectWork:
    def __init__(self, config=None, workspace=None, *, provider=None):
        self.provider = provider or project_providers.load(config)
        self.workspace = Path(workspace).resolve() if workspace else None
        self.owner = None
        self.capabilities = self.provider.capabilities
        self.instructions = getattr(self.provider, "instructions", "") if config else ""
        self.context_capability = getattr(self.provider, "context_capability", None) if config else None

    def unavailable(self, name):
        if name == "project_status":
            return None
        return self.provider.unavailable(self.workspace, name)

    def call(self, binding, name, arguments):
        self.owner = source_work.bind(binding, name, self.workspace, self.owner)
        self.workspace = Path(self.owner[0])
        if name == "project_status":
            return {**source_work.status(self.workspace), **self.provider.status(self.workspace),
                    "workspace": str(self.workspace), "participant": f"PT-{self.owner[1]}",
                    "assignment": self.owner[3]}
        if reason := self.unavailable(name):
            refuse("project", "available operation prerequisites", "unavailable", reason)
        with source_work.workspace_lock(self.workspace):
            before = source_work.snapshot(self.workspace)
            runtime_identity = getattr(self.provider, "runtime_identity", lambda root: None)
            runtime_before = runtime_identity(self.workspace)
            mutates = self.capabilities[name][3]
            if mutates and arguments.get("expected_source_fingerprint") != before["fingerprint"]:
                refuse("stale", "the fingerprint from project_status or code_status", "working source changed",
                       "inspect current source before retrying")
            key = hashlib.sha256(str(self.workspace).encode()).hexdigest()
            runtime = instrument.state_dir() / "project-operations" / key
            if runtime.resolve().is_relative_to(self.workspace):
                refuse("project", "runtime artifacts outside source", str(runtime), "repair the state directory")
            epoch = self.provider.identity
            event = source_work.begin(self.workspace, self.owner[1], self.owner[3], epoch, before) if mutates else None
            outcome, result, after = "failed", {}, None
            try:
                result = self.provider.invoke(self.workspace, runtime, name, arguments)
                outcome = result.get("outcome", "failed")
            except Exception as error:  # noqa: BLE001 - account for partial writes on failure
                result = {"outcome": "failed", "diagnostic": bounded(str(error), 1800)[0]}
            finally:
                try:
                    after = source_work.snapshot(self.workspace)
                except Exception:  # noqa: BLE001 - retain failed source accounting
                    outcome = "source_unknown"
                if after and outcome == "success" and before["fingerprint"] != after["fingerprint"]:
                    outcome = "source_changed"
                    result.update(outcome=outcome, diagnostic="Source changed during an observation or check; inspect and repeat.")
                if after and outcome == "success" and runtime_before != runtime_identity(self.workspace):
                    outcome = "runtime_changed"
                    result.update(outcome=outcome, diagnostic="Runtime inputs changed during execution; repeat the named operation.")
                changes = source_work.finish(event, before, after, outcome) if event else None
            if after is None:
                result.update(outcome="source_unknown", source_fingerprint=None,
                              diagnostic="Source could not be read after the operation; inspect the worktree before retrying.")
            else:
                result.update(source_fingerprint=after["fingerprint"], revision=after["revision"])
                if result["outcome"] == "success" and before["fingerprint"] == after["fingerprint"]:
                    checks = self.provider.receipts(self.workspace, name, result)
                    for check, evidence in checks.items():
                        current = source_work.status(self.workspace, after)
                        source_work.record_check(self.workspace, check, fingerprint=after["fingerprint"],
                                                 generation=current["generation"], passed=True,
                                                 evidence=json.dumps(evidence, sort_keys=True))
            if event:
                result.update(source_event=event, verification_invalidated=True, changed_count=len(changes) if changes is not None else None,
                              changed_paths=changes[:20] if changes is not None else None,
                              changes_truncated=bool(changes and len(changes) > 20))
            return result
