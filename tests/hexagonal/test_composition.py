"""Actual composition, extension, configuration, and ownership boundaries."""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.composition import build, load_config, read_json
from xorchestrate.model import OrchestrationError

_EXTENSION = '''
from xorchestrate.adapters.storage import MemoryStore
from xorchestrate.model import Capability

constructed = []
closed = []

class Store(MemoryStore):
    def __init__(self, options):
        super().__init__(options)
        self.label = options.get("label", "store")
        constructed.append(self)

    def close(self):
        closed.append(self.label)
        super().close()

class Provider:
    def __init__(self, options):
        if set(options) - {"label", "nested", "fail_close"}:
            raise ValueError("Factory received options outside its declared contract.")
        self.options = options
        self.label = options.get("label", "provider")
        self.calls = 0
        constructed.append(self)

    def discover(self):
        return [Capability("echo", "Echo an object.", {"type": "object"})]

    def invoke(self, name, arguments, context):
        self.calls += 1
        return {"name": name, "arguments": arguments, "actor": context["actor"]}

    def close(self):
        closed.append(self.label)
        if self.options.get("fail_close"):
            raise RuntimeError("Intentional close failure.")

class Session:
    def __init__(self, options):
        if set(options) - {"label", "application_tools"}:
            raise ValueError("Factory received options outside its declared contract.")
        self.label = options.get("label", "session")
        self.options = options
        constructed.append(self)

    def bind_application_tools(self, capabilities, callback):
        self.tools = capabilities
        self.callback = callback

    def close(self):
        closed.append(self.label)

def fail(options):
    raise ValueError("Intentional construction failure.")
'''


class CompositionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def extension(self):
        name = "xor_test_extension_" + uuid4().hex
        (self.root / f"{name}.py").write_text(_EXTENSION, encoding="utf-8")
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.addCleanup(sys.modules.pop, name, None)
        importlib.invalidate_caches()
        return importlib.import_module(name)

    def runtime(self, config):
        runtime = build(config, self.root)
        self.addCleanup(runtime.close)
        return runtime

    def test_memory_build_runs_use_cases_without_files(self):
        runtime = self.runtime({"store": {"adapter": "memory"}})
        self.assertIsInstance(runtime.application.store, MemoryStore)
        work = runtime.application.call("create_work", {"goal": "Deliver a feature"})["work"]
        fetched = runtime.application.call("get_work", {"work_id": work["id"]})["work"]
        self.assertEqual(fetched, work)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_sqlite_build_persists_across_runtime_restarts(self):
        config = {"store": {"adapter": "sqlite", "options": {"path": "state/test.db"}}}
        runtime = self.runtime(config)
        self.assertIsInstance(runtime.application.store, SQLiteStore)
        work = runtime.application.call("create_work", {"goal": "Persist this goal"})["work"]
        runtime.close()
        restarted = self.runtime(config)
        self.assertEqual(restarted.application.call("get_work", {"work_id": work["id"]})["work"], work)
        self.assertTrue((self.root / "state" / "test.db").is_file())

    def test_sqlite_memory_path_remains_ephemeral(self):
        config = {"store": {"adapter": "sqlite", "options": {"path": ":memory:"}}}
        runtime = self.runtime(config)
        runtime.application.call("create_work", {"goal": "Temporary work"})
        self.assertEqual(list(self.root.iterdir()), [])
        runtime.close()
        restarted = self.runtime(config)
        self.assertEqual(restarted.application.call("list_work", {}), {"work": []})

    def test_installed_factory_extends_capabilities_without_source_changes(self):
        extension = self.extension()
        runtime = self.runtime({
            "store": {"adapter": "memory"},
            "providers": {"custom": {"adapter": extension.__name__ + ":Provider", "options": {"label": "custom"}}},
        })
        capabilities = runtime.application.call("capabilities", {})["capabilities"]
        self.assertEqual([capability["name"] for capability in capabilities], ["custom/echo"])
        result = runtime.application.call("invoke", {
            "capability": "custom/echo", "arguments": {"message": "hello"}, "idempotency_key": "call-one",
        }, actor="caller")["invocation"]["result"]
        self.assertEqual(result, {"name": "echo", "arguments": {"message": "hello"}, "actor": "caller"})
        runtime.close()
        self.assertEqual(extension.closed, ["custom"])

    def test_disabled_adapters_do_not_load_or_validate_unused_options(self):
        runtime = self.runtime({
            "store": {"adapter": "memory"},
            "providers": {"unused": {"enabled": False, "adapter": "absent_package:factory", "options": []}},
            "sessions": {"unused": {"enabled": False, "options": {"cwd": None, "application_tools": True}}},
            "evaluators": {"unused": {"enabled": False, "adapter": "not-a-real-adapter"}},
        })
        self.assertEqual(runtime.application.providers, {})
        self.assertEqual(runtime.application.sessions, {})
        self.assertEqual(runtime.application.evaluators, {})
        self.assertEqual(runtime.application.call("capabilities", {}), {"capabilities": []})

    def test_relative_config_paths_select_storage_and_actual_cli_working_directory(self):
        project = self.root / "project"
        working = project / "tools"
        working.mkdir(parents=True)
        config = {
            "store": {"adapter": "sqlite", "options": {"path": "state/runtime.db"}},
            "providers": {"location": {"adapter": "cli", "options": {
                "cwd": "tools",
                "command": [sys.executable, "-c", "import json, os; print(json.dumps({'cwd': os.getcwd()}))"],
                "capabilities": [{"name": "cwd", "input_schema": {"type": "object"}}],
            }}},
        }
        source = project / "config.json"
        source.write_text(json.dumps(config), encoding="utf-8")
        loaded, base = load_config(source)
        self.assertEqual(base, project)
        with build(loaded, base) as runtime:
            result = runtime.application.call("invoke", {
                "capability": "location/cwd", "arguments": {}, "idempotency_key": "where-am-i",
            })["invocation"]["result"]
            self.assertEqual(Path(result["cwd"]), working)
        self.assertTrue((project / "state" / "runtime.db").is_file())
        self.assertEqual(loaded, config)

    def test_falsey_nonobject_config_is_rejected_without_creating_state(self):
        for value in ([], False, 0, ""):
            with self.subTest(value=value), self.assertRaises(OrchestrationError) as failure:
                build(value, self.root)
            self.assertEqual(failure.exception.code, "configuration_error")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_paths_raise_configuration_errors(self):
        configurations = [
            {"store": {"adapter": "sqlite", "options": {"path": None}}},
            {"store": {"adapter": "memory"}, "providers": {"bad": {"adapter": "cli", "options": {"cwd": []}}}},
            {"store": {"adapter": "memory"}, "sessions": {"bad": {"adapter": "codex", "options": {"cwd": False}}}},
        ]
        for config in configurations:
            with self.subTest(config=config), self.assertRaises(OrchestrationError) as failure:
                build(config, self.root)
            self.assertEqual(failure.exception.code, "configuration_error")

    def test_failed_construction_closes_all_previously_created_resources_in_reverse_order(self):
        extension = self.extension()
        with self.assertRaises(OrchestrationError) as failure:
            build({
                "store": {"adapter": extension.__name__ + ":Store"},
                "providers": {
                    "first": {"adapter": extension.__name__ + ":Provider", "options": {"label": "first"}},
                    "broken": {"adapter": extension.__name__ + ":fail"},
                },
            }, self.root)
        self.assertEqual(failure.exception.code, "configuration_error")
        self.assertEqual(extension.closed, ["first", "store"])
        with self.assertRaises(RuntimeError):
            with extension.constructed[0].transaction():
                self.fail("The failed runtime left its store open.")

    def test_cleanup_continues_after_close_failure_and_is_idempotent(self):
        extension = self.extension()
        runtime = self.runtime({
            "store": {"adapter": extension.__name__ + ":Store"},
            "providers": {
                "first": {"adapter": extension.__name__ + ":Provider", "options": {"label": "first"}},
                "second": {"adapter": extension.__name__ + ":Provider", "options": {"label": "second", "fail_close": True}},
            },
        })
        with self.assertRaises(OrchestrationError) as failure:
            runtime.close()
        self.assertEqual(failure.exception.code, "cleanup_failed")
        self.assertEqual(extension.closed, ["second", "first", "store"])
        runtime.close()
        self.assertEqual(extension.closed, ["second", "first", "store"])

    def test_cleanup_failure_does_not_replace_original_construction_failure(self):
        extension = self.extension()
        with self.assertRaises(OrchestrationError) as failure:
            build({
                "store": {"adapter": extension.__name__ + ":Store"},
                "providers": {
                    "first": {"adapter": extension.__name__ + ":Provider", "options": {"fail_close": True}},
                    "broken": {"adapter": extension.__name__ + ":fail"},
                },
            }, self.root)
        self.assertEqual(failure.exception.code, "configuration_error")
        self.assertEqual(extension.closed, ["provider", "store"])

    def test_config_is_a_deep_snapshot_and_runtime_instances_are_independent(self):
        extension = self.extension()
        config = {
            "store": {"adapter": "memory"},
            "providers": {"custom": {
                "adapter": extension.__name__ + ":Provider", "options": {"nested": {"values": ["original"]}},
            }},
            "guardrail": {"adapter": "rules", "options": {"rules": [{"effect": "allow", "actions": ["*"]}]}},
        }
        first = self.runtime(config)
        second = self.runtime(config)
        config["providers"]["custom"]["options"]["nested"]["values"].append("caller mutation")
        config["guardrail"]["options"]["rules"][0]["effect"] = "deny"
        first_provider = first.application.providers["custom"]
        second_provider = second.application.providers["custom"]
        self.assertEqual(first_provider.options["nested"]["values"], ["original"])
        first_provider.options["nested"]["values"].append("first runtime mutation")
        self.assertEqual(second_provider.options["nested"]["values"], ["original"])
        first.application.call("create_work", {"goal": "The running snapshot still permits this"})
        self.assertEqual(second.application.call("list_work", {}), {"work": []})
        third = self.runtime(config)
        with self.assertRaises(OrchestrationError) as failure:
            third.application.call("create_work", {"goal": "The next snapshot denies this"})
        self.assertEqual(failure.exception.code, "denied")

    def test_custom_session_tools_use_neutral_callbacks_and_bound_principals(self):
        extension = self.extension()
        runtime = self.runtime({
            "store": {"adapter": "memory"},
            "providers": {"custom": {"adapter": extension.__name__ + ":Provider"}},
            "sessions": {"agent": {"adapter": extension.__name__ + ":Session", "options": {"application_tools": True}}},
        })
        session = runtime.application.sessions["agent"]
        self.assertIn("create_work", {tool.name for tool in session.tools})
        self.assertFalse(hasattr(session, "set_request_handler"))
        work = session.callback("create_work", {"goal": "Native work"},
                                {"actor": "bound-agent", "work_id": "unused-for-create"})["work"]
        self.assertEqual(work["created_by"], "bound-agent")
        evidence = session.callback("record_result", {"result": "evidence"},
                                    {"actor": "bound-agent", "work_id": work["id"]})["evidence"]
        self.assertEqual(evidence["work_id"], work["id"])
        arguments = {"capability": "custom/echo", "arguments": {"actor": "forged"}, "idempotency_key": "caller-key"}
        invocation = session.callback("invoke", arguments, {"actor": "bound-agent"}, "adapter-invocation")
        replay = session.callback("invoke", {**arguments, "idempotency_key": "changed-caller-key"},
                                  {"actor": "bound-agent"}, "adapter-invocation")
        self.assertEqual(invocation, replay)
        self.assertEqual(invocation["invocation"]["result"]["actor"], "bound-agent")
        self.assertEqual(runtime.application.providers["custom"].calls, 1)
        self.assertEqual(arguments["idempotency_key"], "caller-key")
        with self.assertRaises(OrchestrationError):
            session.callback("create_work", {"goal": "A missing principal must not become owner"}, {})
        with self.assertRaises(OrchestrationError):
            session.callback("create_work", '{"goal":"Adapters must decode their native protocol"}', {"actor": "bound-agent"})
        with self.assertRaises(OrchestrationError):
            session.callback("invoke", arguments, {"actor": "bound-agent"})
        self.assertEqual(runtime.application.providers["custom"].calls, 1)

    def test_application_tool_keys_are_scoped_by_configured_driver_without_delimiter_collisions(self):
        extension = self.extension()
        runtime = self.runtime({
            "store": {"adapter": "memory"},
            "providers": {"custom": {"adapter": extension.__name__ + ":Provider"}},
            "sessions": {
                name: {"adapter": extension.__name__ + ":Session", "options": {"application_tools": True}}
                for name in ("first", "second", "a:b", "a")
            },
        })
        arguments = {"capability": "custom/echo", "arguments": {}}
        results = []
        for name, key in (("first", "same-native-key"), ("second", "same-native-key"), ("a:b", "c"), ("a", "b:c")):
            callback = runtime.application.sessions[name].callback
            result = callback("invoke", arguments, {"actor": "same-actor"}, key)
            self.assertEqual(result, callback("invoke", arguments, {"actor": "same-actor"}, key))
            results.append(result["invocation"]["id"])
        self.assertEqual(len(set(results)), 4)
        self.assertEqual(runtime.application.providers["custom"].calls, 4)

    def test_application_tool_key_requirement_comes_from_descriptor_and_binding_is_detached(self):
        from xorchestrate.composition import _wire_application_tools
        from xorchestrate.model import Capability

        extension = self.extension()
        driver = extension.Session({})
        calls = []

        class Application:
            def describe(self):
                return [Capability("extension_action", "Custom effect", {
                    "type": "object", "properties": {"idempotency_key": {"type": "string"}},
                    "required": ["idempotency_key"],
                })]

            def call(self, action, arguments, actor):
                calls.append((action, arguments, actor))
                return {"called": True}

        _wire_application_tools("custom", driver, Application())
        driver.tools[0].input_schema["required"].clear()
        with self.assertRaises(OrchestrationError):
            driver.callback("extension_action", {"idempotency_key": "untrusted"}, {"actor": "bound"})
        self.assertEqual(calls, [])
        driver.callback("extension_action", {"idempotency_key": "untrusted"}, {"actor": "bound"}, "trusted")
        self.assertEqual(calls, [("extension_action", {"idempotency_key": 'session:["custom","trusted"]'}, "bound")])

    def test_invalid_assessment_configuration_closes_its_store(self):
        extension = self.extension()
        for assessment in (False, 0, [], "bad", {"confidence_threshold": "high"}, {"confidence_threshold": True}, {"confidence_threshold": 2}, {"uncertain_outcomes": "review"}):
            with self.subTest(assessment=assessment), self.assertRaises(OrchestrationError):
                build({"store": {"adapter": extension.__name__ + ":Store"}, "assessment": assessment}, self.root)
            self.assertEqual(len(extension.closed), len(extension.constructed))
        runtime = self.runtime({
            "store": {"adapter": "memory"},
            "assessment": {"uncertain_outcomes": ["project-specific-outcome"], "confidence_threshold": 0.65},
        })
        self.assertEqual(runtime.application.assessment["uncertain_outcomes"], ["project-specific-outcome"])

    def test_json_reader_rejects_duplicates_nonfinite_and_overflowing_numbers(self):
        for value in ('{"a": 1, "a": 2}', '{"nested": {"a": 1, "a": 2}}', "NaN", "Infinity", "-Infinity", "1e9999", "[1e9999]"):
            with self.subTest(value=value), self.assertRaises(OrchestrationError) as failure:
                read_json(value)
            self.assertEqual(failure.exception.code, "invalid_json")
        self.assertEqual(read_json('{"value": [null, true, 1, 0.5]}'), {"value": [None, True, 1, 0.5]})

    def test_configured_tool_triage_escalates_through_real_cli_processes(self):
        bridge = self.root / "assessment_bridge.py"
        marker = self.root / "assessments.jsonl"
        bridge.write_text('''import json, sys
request = json.load(sys.stdin)
arguments = request["arguments"]
with open(sys.argv[1], "a") as marker:
    marker.write(json.dumps(arguments) + "\\n")
triage = arguments["model"] == "triage-model"
print(json.dumps({"outcome": "uncertain" if triage else "ready",
                  "confidence": 0.5 if triage else 0.95,
                  "evidence": [{"source": "configured project tooling"}]}))
''', encoding="utf-8")
        runtime = self.runtime({
            "store": {"adapter": "memory"},
            "providers": {"judgment": {"adapter": "cli", "options": {
                "command": [sys.executable, str(bridge), str(marker)],
                "capabilities": [{"name": "assess", "input_schema": {"type": "object"}}],
            }}},
            "evaluators": {
                "triage": {"adapter": "tool", "options": {"provider": "judgment", "capability": "assess", "arguments": {"model": "triage-model"}}},
                "heavy": {"adapter": "tool", "options": {"provider": "judgment", "capability": "assess", "arguments": {"model": "reasoning-model"}}},
            },
            "assessment": {"triage": "triage", "escalate": "heavy", "confidence_threshold": 0.8},
        })
        app = runtime.application
        work = app.call("create_work", {"goal": "Add a complex feature", "inputs": {"declared_stack": "from project tooling"}})["work"]
        assessment = app.call("assess", {"question": "Does this meet the declared standard?", "work_id": work["id"]})["assessment"]
        self.assertEqual([item["evaluator"] for item in assessment["evaluations"]], ["triage", "heavy"])
        self.assertFalse(assessment["unresolved"])
        requests = [json.loads(line) for line in marker.read_text().splitlines()]
        self.assertEqual([item["model"] for item in requests], ["triage-model", "reasoning-model"])
        self.assertEqual(requests[1]["context"]["triage"]["outcome"], "uncertain")
        retained = app.call("get_work", {"work_id": work["id"]})
        self.assertEqual(retained["work"]["status"], "open")
        self.assertEqual(len(retained["evidence"]), 1)

    def test_load_config_rejects_nonobjects_and_unreadable_paths(self):
        source = self.root / "config.json"
        source.write_text("[]", encoding="utf-8")
        with self.assertRaises(OrchestrationError) as failure:
            load_config(source)
        self.assertEqual(failure.exception.code, "configuration_error")
        with self.assertRaises(OrchestrationError) as failure:
            load_config(self.root / "missing.json")
        self.assertEqual(failure.exception.code, "configuration_error")


if __name__ == "__main__":
    unittest.main()
