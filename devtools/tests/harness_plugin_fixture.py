"""A fourth, independent harness implementation for offline adapter lifecycle tests."""

import json
import os
from pathlib import Path

from devtools.api import validate
from devtools.store import Store


class FixtureHarness:
    vendor = "fixture-vendor"
    supports_provider = True

    def prepare(self, directory, member, config=None):
        store = Store(directory)
        private = Path(directory) / "profiles" / member / store.member(member)["generation"]
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        (private / "fixture.json").write_text(json.dumps({"harness": self.name}))
        return private, dict(os.environ), {"fixture": True}

    def permitted(self, tool, arguments, *, catalogue=None):
        if not isinstance(arguments, dict):
            return False
        for rows in (catalogue or {}).values():
            for row in rows:
                if tool == "fixture::" + row["name"]:
                    try:
                        validate(arguments, row["inputSchema"])
                    except ValueError:
                        return False
                    return True
        return False

    def hook(self, directory, member, payload):
        from devtools.profiles import tool_catalogue

        return {"fixture_allowed": self.permitted(payload.get("tool"), payload.get("arguments"),
                                                  catalogue=tool_catalogue(directory, member))}

    def command(self, directory, member, config, private, server):
        return [config["binary"], "--fixture-model", config.get("model", "fixture-default")], "fixture-session"

    def channel(self, private):
        return None

    def readiness(self, binary, probe, config=None):
        return {"protocol": "fixture", "binary": binary}

    def retain_login(self, directory, member, generation):
        Store(directory).event(member, "fixture_login_retained", generation=generation)

    def run(self, directory, member, config, generation):
        from devtools.console import command
        from devtools.profiles import _hook, prepare

        private, env, server = prepare(directory, member, self.name, config=config)
        argv, session = command(directory, member, config, private, server)
        result = _hook(directory, member, self.name, {"tool": "fixture::work_context", "arguments": {}})
        store = Store(directory)
        store.session(member, generation=generation, state="idle", native_id=session)
        store.event(member, "fixture_harness_ran", harness=self.name, argv=argv, permission=result,
                    profile=json.loads((private / "fixture.json").read_text()))


class PromptFixtureHarness(FixtureHarness):
    supports_initial_prompt = True

    def command(self, directory, member, config, private, server):
        argv, session = super().command(directory, member, config, private, server)
        if config.get("initial_prompt") is not None:
            argv += ["--", config["initial_prompt"]]
        return argv, session
