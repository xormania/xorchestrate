"""Selected environment facts around existing setup and doctor services."""

import inspect
import json
import shlex
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from scripts.testkit.adapters import profiles
from scripts.testkit.adapters.configuration import isolated_config
from tests.hexagonal.support.managed_profiles import ROOT, profile_file, run_shell, shell_subject


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

