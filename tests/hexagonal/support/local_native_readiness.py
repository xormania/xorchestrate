"""Selected environment facts around existing setup and doctor services."""

import hashlib
import inspect
import json
import shlex
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.testkit.adapters import profiles
from scripts.testkit.adapters.configuration import isolated_config
from scripts.testkit.adapters.managed_store import ManagedStore
from scripts.testkit.adapters.native_evidence import collection_supplement
from tests.hexagonal.support.managed_profiles import ROOT, profile_file, run_shell, shell_subject
from xorchestrate.composition import build


class LocalNativeReadiness:
    expected_driver = "codex"

    def __init__(self, root):
        self.root = root
        self.profile = profile_file(root / "settings", {"mode": "native", "suite": "stories",
            "stories": ["native-callback"], "drivers": ["codex"], "config": "native.json"})
        self.native = self.profile.parent / "native.json"

    @staticmethod
    def probe(collector=True):
        return {"version": [3, 12, 14], "virtual": True, "executable": sys.executable,
            "dependencies": {"jsonschema": True, "pytest": True, "ruff": True, "agent_telemetry": collector},
            "collector": {"status": "ready" if collector else "unavailable", "sqlite_version": "3.53.1"}}

    def setup_native(self):
        with patch.object(profiles, "_interpreter_details", return_value=self.probe()):
            result = profiles.setup(self.profile, root=ROOT, install=False)
        assert result["status"] == "passed"
        return json.loads(self.native.read_text())

    def setup_existing_disabled(self):
        self.native.write_text(json.dumps({"sessions": {"codex": {"adapter": "codex",
            "options": {"command": ["operator-codex"], "cwd": str(self.root / "project")}}},
            "telemetry": {"enabled": False}, "operator_marker": {"preserve": "chosen settings"}}, indent=2) + "\n")
        before = self.native.read_bytes()
        self.setup_native()
        return before, self.native.read_bytes()

    def doctor_with_missing_collector(self, enabled):
        self.native.write_text(json.dumps({"sessions": {"codex": {"adapter": "codex",
            "options": {"command": ["operator-codex"], "cwd": str(self.root / "project")}}},
            "telemetry": ({"adapter": "local", "options": {"runtime_root": str(self.root / "corpus")}}
                if enabled else {"enabled": False})}))
        before = self.profile.read_bytes(), self.native.read_bytes()
        with patch.object(profiles, "_interpreter_details", return_value=self.probe(False)), \
                patch.object(profiles.shutil, "which", side_effect=lambda name: "/available/" + name):
            result = profiles.doctor(self.profile, root=ROOT)
        unchanged = before == (self.profile.read_bytes(), self.native.read_bytes()) and not (self.profile.parent / "runs").exists()
        return result, unchanged

    def project_readiness(self, language, missing=None, requires=None):
        project = self.root / "project workspace"
        project.mkdir()
        values = json.loads(self.profile.read_text())
        values["project"] = {"path": "../project workspace", "language": language}
        if requires is not None:
            values["project"]["requires"] = requires
        self.profile.write_text(json.dumps(values))
        self.native.write_text(json.dumps({"sessions": {name: {"adapter": adapter,
            "options": {"command": [command], "cwd": str(project)}} for name, adapter, command in
            (("codex", "codex", "selected-codex"), ("claude", "claude", "unselected-claude"),
             ("grok", "grok", "unselected-grok"))}, "telemetry": {"enabled": False}}))
        before = self.profile.read_bytes(), self.native.read_bytes()
        looked_up = []
        tools = self.root / "installed tool stubs"
        tools.mkdir()
        def which(name):
            looked_up.append(name)
            if name in {missing, "unselected-claude", "unselected-grok"}:
                return None
            executable = tools / name
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            return str(executable)
        with patch.object(profiles, "_interpreter_details", return_value=self.probe(False)), \
                patch.object(profiles.shutil, "which", side_effect=which):
            try:
                result = profiles.doctor(self.profile, root=ROOT)
            except ValueError as error:
                raise AssertionError("LOCAL_PROFILE_MUST_SUPPORT_SELECTED_PROJECT_PREREQUISITES") from error
        unchanged = before == (self.profile.read_bytes(), self.native.read_bytes())
        return result, looked_up, str(project), unchanged

    def derive_capture(self, configured):
        configuration = {"sessions": {"codex": {"adapter": "codex", "options": {"cwd": "."}}}}
        policies = {"inline": {"adapter": "local", "options": {"runtime_root": "/operator/long-lived-corpus",
            "dataset_id": "old-dataset", "collector_id": "old-collector"}},
            "local-default": {"adapter": "local", "options": {"project_id": "operator-project"}},
            "disabled": {"enabled": False}, "custom": {"adapter": "operator.capture:Factory", "options": {"chosen": True}},
            "profile": {"adapter": "local", "options": {"profile": "/operator/private-capture.toml"}}}
        if configured in policies:
            configuration["telemetry"] = policies[configured]
        original = deepcopy(configuration)
        directories = [self.root / "first run", self.root / "second run"]
        keywords = {"capture": True} if "capture" in inspect.signature(isolated_config).parameters else {}
        copies = [isolated_config(configuration, self.profile.parent, directory, **keywords) for directory in directories]
        descriptors = [json.loads((directory / "telemetry-runtime.json").read_text())
            if (directory / "telemetry-runtime.json").exists() else None for directory in directories]
        return original, *copies, [str(directory / "telemetry") for directory in directories], configuration == original, descriptors

    def selected_interpreter_readiness(self, mode):
        interpreter = self.root / "selected interpreter"
        interpreter.write_text("#!" + sys.executable + "\n"
            "import importlib.util,sqlite3,sys\n"
            + ("sqlite3.sqlite_version_info=(3,44,0)\nsqlite3.sqlite_version='3.44.0'\n"
               if mode == "incompatible" else "")
            + ("finder=importlib.util.find_spec\n"
               "importlib.util.find_spec=lambda name,*args,**kwargs: None if name=='agent_telemetry' else finder(name,*args,**kwargs)\n"
               if mode == "missing" else "")
            + "assert sys.argv[1]=='-c'\nexec(sys.argv[2], {'__name__':'__main__'})\n")
        interpreter.chmod(0o700)
        values = json.loads(self.profile.read_text())
        values["interpreter"] = str(interpreter)
        self.profile.write_text(json.dumps(values))
        self.native.write_text(json.dumps({"sessions": {}, "telemetry": {"adapter": "local",
            "options": {"runtime_root": str(self.root / "corpus")}}}))
        with patch.object(profiles.shutil, "which", return_value=sys.executable):
            result = profiles.doctor(self.profile, root=ROOT)
        return result, str(interpreter)

    def public_project_setup(self):
        project = self.root / "selected project with spaces"
        project.mkdir()
        interpreter = self.root / "selected setup interpreter"
        interpreter.write_text("#!/bin/sh\nif [ \"$1\" = -m ] && [ \"$2\" = pip ]; then exit 0; fi\nexec "
            + shlex.quote(sys.executable) + " \"$@\"\n")
        interpreter.chmod(0o700)
        values = json.loads(self.profile.read_text())
        values.update(mode="protocol-fixture", suite="all", stories=[], drivers=["claude"], interpreter=str(interpreter))
        self.profile.write_text(json.dumps(values))
        arguments = ["setup", "--profile", str(self.profile), "--mode", "native", "--suite", "stories",
            "--story", "native-callback", "--driver", self.expected_driver, "--project", str(project), "--language", "python"]
        result = run_shell(ROOT / "scripts" / "proof.sh", arguments, cache=self.root / "setup cache")
        if result.returncode:
            return result.returncode, {}, {}, False, str(project)
        native = json.loads(self.native.read_text())
        native["operator_marker"] = {"retain": "chosen settings"}
        self.native.write_text(json.dumps(native, indent=2))
        before = self.native.read_bytes()
        repeated = run_shell(ROOT / "scripts" / "proof.sh", arguments, cache=self.root / "setup cache")
        return result.returncode, json.loads(self.profile.read_text()), native, repeated.returncode == 0 and before == self.native.read_bytes(), str(project)

    def setup_selected_config(self, enabled):
        chosen = self.profile.parent / "chosen application.json"
        local = {"adapter": "local", "options": {"runtime_root": str(self.root / "chosen corpus")}}
        chosen.write_text(json.dumps({"sessions": {}, "telemetry": local if enabled else {"enabled": False}}))
        self.native.write_text(json.dumps({"sessions": {}, "telemetry": {"enabled": False} if enabled else local}))
        values = json.loads(self.profile.read_text())
        values["config"] = chosen.name
        self.profile.write_text(json.dumps(values))
        before = chosen.read_bytes(), self.native.read_bytes()
        installed = [False]
        def install(command, **kwargs):
            extras = next(argument for argument in command if argument.startswith(str(ROOT) + "["))
            installed[0] = "telemetry" in extras.split("[", 1)[1].rstrip("]").split(",")
            return SimpleNamespace(returncode=0)
        with patch.object(profiles, "_interpreter_details", side_effect=lambda _: self.probe(installed[0])), \
                patch.object(profiles.subprocess, "run", side_effect=install):
            result = profiles.setup(self.profile, root=ROOT, install=True)
        return result, before == (chosen.read_bytes(), self.native.read_bytes())

    def verify_saved_interpreter(self):
        script = shell_subject(self.root / "bootstrap candidate")
        run = self.root / "saved run"
        run.mkdir()
        marker = self.root / "selected interpreter marker"
        interpreter = self.root / "configured interpreter"
        interpreter.write_text("#!/bin/sh\nprintf selected >> " + shlex.quote(str(marker))
            + "\nexec " + shlex.quote(sys.executable) + " \"$@\"\n")
        interpreter.chmod(0o700)
        plan = run / "plan.json"
        plan.write_text(json.dumps({"interpreter": str(interpreter)}))
        before = plan.read_bytes()
        result = run_shell(script, ["verify", str(run)], cache=self.root / "external cache")
        return result.returncode, marker.read_text() if marker.exists() else "", before == plan.read_bytes()

    def _repeat_settings(self, policy):
        workspace = self.profile.parent / "relative workspace"
        workspace.mkdir()
        configuration = {"sessions": {self.expected_driver: {"adapter": "codex", "options": {
            "command": [sys.executable], "cwd": "relative workspace", "application_tools": True}}}}
        policies = {"local-default": {"adapter": "local", "options": {"project_id": "chosen-project"}},
            "shared-root": {"adapter": "local", "options": {"runtime_root": "../operator shared corpus",
                "project_id": "chosen-project", "dataset_id": "chosen-dataset", "collector_id": "chosen-collector"}},
            "profile": {"adapter": "local", "options": {"profile": "../operator-capture.toml"}},
            "custom": {"adapter": "operator.capture:Factory", "options": {"opaque": "../operator-selected"}},
            "disabled": {"enabled": False}}
        if policy in policies:
            configuration["telemetry"] = policies[policy]
        self.native.write_text(json.dumps(configuration, indent=2))
        values = json.loads(self.profile.read_text())
        values.update(brief=None, output=str(self.root / "prepared native runs"))
        self.profile.write_text(json.dumps(values))
        settings = profiles.load_profile(self.profile, root=ROOT)
        expected = deepcopy(configuration.get("telemetry"))
        if policy == "shared-root":
            expected["options"]["runtime_root"] = str(self.root / "operator shared corpus")
        if policy == "profile":
            expected["options"]["profile"] = str(self.root / "operator-capture.toml")
        return settings, expected, str(workspace), self.native.read_bytes()

    @staticmethod
    def _corpus_files(root):
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}

    @staticmethod
    def _populate_repeat(configuration, label):
        with build(configuration, ROOT) as runtime:
            runtime.application.call("create_work", {"goal": "actual repeat corpus " + label})
            sink = runtime.application.telemetry
            database = sink.path
            corpus = sink.root
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count > 0, "repeat witness needs actual captured application records"
        return corpus, count

    def repeat_capture(self, policy, *, prepare, repeat, damage_second=False):
        settings, expected, workspace, raw = self._repeat_settings(policy)
        directories, configurations, descriptors, collections, counts, pipeline = [], [], [], [], [], []
        first_files = None
        first_corpus = None
        changed = b'{"operator_live_file_changed":true}'
        for number in range(3):
            if number:
                settings = repeat(directories[-1], {})
                pipeline.append("repeat_settings")
            directory = prepare(settings, **({"parent": directories[-1]} if number else {}))
            pipeline.append("prepare_run")
            directories.append(directory)
            plan = json.loads((directory / "plan.json").read_text())
            configuration = json.loads(Path(plan["config"]).read_text())
            if damage_second and number == 1:
                configuration["telemetry"] = {"adapter": "local", "options": {"runtime_root": "wrong-policy"}}
                Path(plan["config"]).write_text(json.dumps(configuration))
            configurations.append(configuration)
            descriptors.append(json.loads((directory / "derived" / "telemetry-runtime.json").read_text()))
            if policy in {"absent", "local-default"}:
                corpus, count = self._populate_repeat(configuration, str(number))
                counts.append(count)
                if number == 0:
                    first_corpus = corpus
                    first_files = self._corpus_files(corpus)
                collection = ManagedStore(directory).collect(supplemental=collection_supplement)
                collections.append(collection["telemetry"]["status"])
            if number == 0:
                self.native.write_bytes(changed)
        plans = [json.loads((directory / "plan.json").read_text()) for directory in directories]
        return {"public_pipeline": pipeline, "selected_checks": [plan["selected_check_ids"] for plan in plans],
            "cwd_preserved": all(item["sessions"][self.expected_driver]["options"]["cwd"] == workspace for item in configurations),
            "capture_options": [item.get("telemetry", {}).get("options", {}) for item in configurations],
            "original_file_edit_retained": self.native.read_bytes() == changed,
            "owned_descriptors": all(item["status"] == "configured" and item["runtime_root"] == str(directory / "derived" / "telemetry")
                for item, directory in zip(descriptors, directories, strict=True)),
            "collection_status": collections, "source_event_counts": counts,
            "first_corpus_unchanged": first_corpus is not None and first_files == self._corpus_files(first_corpus),
            "original_recipe_reused": all(Path(plan["input_snapshots"]["config"]["path"]).read_bytes() == raw for plan in plans),
            "operator_policy_preserved": all(item.get("telemetry") == expected for item in configurations),
            "withholding_preserved": all(item["status"] == ("disabled" if policy == "disabled" else "external") and item.get("reason")
                for item in descriptors), "prepared_runs": [str(path) for path in directories]}

    def repeat_with_changed_snapshot(self, *, prepare, repeat):
        settings, _, _, _ = self._repeat_settings("absent")
        directory = prepare(settings)
        plan = json.loads((directory / "plan.json").read_text())
        Path(plan["input_snapshots"]["config"]["path"]).write_text("changed frozen operator input")
        try:
            repeated = repeat(directory, {})
            prepare(repeated, parent=directory)
        except (ValueError, OSError):
            return True
        return False

