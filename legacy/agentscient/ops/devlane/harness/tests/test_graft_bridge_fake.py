"""Protocol tests for the verified Graft Node/bridge fake.

These pin the closed stdin JSON variants in graft/CONTRACT.md. They do
not import the adapter. Ordinary runs need neither Node nor network.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support  # noqa: F401  # sys.path: harness dir
from ops.testkit import World, run

FAKE = Path(__file__).resolve().parent / "fakes" / "graft_bridge.py"


def load_fake():
    import importlib.util

    spec = importlib.util.spec_from_file_location("graft_bridge_fake", FAKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BridgeFakeSpeaksTheClosedProtocol(unittest.TestCase):
    def setUp(self):
        self.fake = load_fake()
        self.world = World(prefix="graft-bridge-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.install = self.fake.plant_install(self.home / "install")
        self.root = self.home / "src"
        self.world.plant_text(self.root / "ops" / "lane.md", "ops source\n")
        self.cache = self.home / "cache"
        self.build_req = {
            "op": "build",
            "root": str(self.root),
            "cache": str(self.cache),
        }
        self.query_req = {
            "op": "query",
            "root": str(self.root),
            "cache": str(self.cache),
            "tool": "graft_repo_map",
            "arguments": {},
        }

    def transcript(self):
        path = self.fake.transcript_path(self.install)
        self.assertTrue(path.is_file(), "transcript must exist after an invoke")
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(rows, "transcript must contain at least one arm")
        return rows

    def test_build_returns_a_nonempty_ops_graph_and_writes_cache(self):
        code, out, err = self.fake.invoke(self.install, self.build_req)
        self.assertEqual(code, 0, err)
        self.assertEqual((err or "").strip(), "")
        body = json.loads(out)
        self.assertEqual(body["ok"], True)
        result = body["result"]
        self.assertEqual(result["errors"], [])
        self.assertGreater(result["files"], 0)
        self.assertTrue(result["nodes"])
        self.assertIsInstance(result["nodes"], int)
        graph = json.loads(Path(result["graphPath"]).read_text(encoding="utf-8"))
        self.assertEqual(len(graph["nodes"]), result["nodes"])
        for node in graph["nodes"]:
            self.assertTrue(
                str(node["path"]).startswith("ops/"),
                f"node escaped ops/: {node}",
            )
        self.assertTrue((self.cache / ".graph" / "wiring.json").is_file())
        row = self.transcript()[-1]
        self.assertEqual(row["request"]["op"], "build")
        self.assertEqual(row["response"]["ok"], True)

    def test_query_returns_text_for_an_admitted_tool(self):
        self.fake.invoke(self.install, self.build_req)
        code, out, err = self.fake.invoke(self.install, self.query_req)
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["ok"], True)
        self.assertIsInstance(body["result"]["text"], str)
        self.assertIs(body["result"]["isError"], False)
        self.assertIn("graft_repo_map", body["result"]["text"])
        ops = {row["request"]["op"] for row in self.transcript() if row.get("request")}
        self.assertEqual(ops, {"build", "query"})

    def test_empty_stdin_is_a_provider_error_in_the_transcript(self):
        node = self.install / "node" / "bin" / "node"
        result = run(
            [str(node), str(self.install / "graft" / "bridge.mjs"),
             str(self.install / "graft")],
            cwd=self.home,
            env=self.world.env(),
            stdin="",
            timeout=60,
        )
        self.assertEqual(result.exit, 0)
        body = json.loads(result.stdout)
        self.assertEqual(body["ok"], False)
        self.assertIn("empty", body["error"])
        self.assertIsNone(self.transcript()[-1]["request"])

    def test_unknown_op_is_the_nearest_invalid_of_build(self):
        req = dict(self.build_req)
        req["op"] = "Build"
        code, out, err = self.fake.invoke(self.install, req)
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["ok"], False)
        self.assertIn("unknown op", body["error"])
        self.assertEqual(self.transcript()[-1]["request"]["op"], "Build")

    def test_relative_root_is_the_nearest_invalid_of_an_absolute_path(self):
        req = dict(self.build_req)
        req["root"] = "ops"
        code, out, _err = self.fake.invoke(self.install, req)
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertEqual(body["ok"], False)
        self.assertIn("absolute", body["error"])

    def test_extra_build_key_is_refused(self):
        req = dict(self.build_req)
        req["onlyDirs"] = ["ops"]
        body = json.loads(self.fake.invoke(self.install, req)[1])
        self.assertEqual(body["ok"], False)
        self.assertIn("admits only", body["error"])

    def test_unknown_tool_is_the_nearest_invalid_of_an_admitted_name(self):
        req = dict(self.query_req)
        req["tool"] = "graft_repo_map_all"
        body = json.loads(self.fake.invoke(self.install, req)[1])
        self.assertEqual(body["ok"], False)
        self.assertIn("unknown tool", body["error"])

    def test_zero_ops_files_is_unavailable_not_an_empty_success(self):
        (self.root / "ops" / "lane.md").unlink()
        body = json.loads(self.fake.invoke(self.install, self.build_req)[1])
        self.assertEqual(body["ok"], False)
        self.assertIn("zero files", body["error"])
        self.assertFalse(
            (self.cache / ".graph" / "wiring.json").exists(),
            "unavailable build must not publish a graph",
        )

    def test_ambient_graft_config_is_refused(self):
        cfg = self.root / ".graft" / "config.json"
        self.world.plant_text(cfg, "{}\n")
        body = json.loads(self.fake.invoke(self.install, self.build_req)[1])
        self.assertEqual(body["ok"], False)
        self.assertIn("ambient", body["error"])

    def test_cache_inside_root_is_refused(self):
        req = dict(self.build_req)
        req["cache"] = str(self.root / ".graft-cache")
        body = json.loads(self.fake.invoke(self.install, req)[1])
        self.assertEqual(body["ok"], False)
        self.assertIn("outside", body["error"])

    def test_escaped_query_path_is_refused(self):
        req = dict(self.query_req)
        req["arguments"] = {"path": "../secret.py"}
        body = json.loads(self.fake.invoke(self.install, req)[1])
        self.assertEqual(body["ok"], False)
        self.assertIn("escaped", body["error"])

    def test_truncated_mode_does_not_yield_a_json_object(self):
        broken = self.fake.plant_install(self.home / "trunc", mode="truncated")
        code, out, _err = self.fake.invoke(broken, self.build_req)
        self.assertEqual(code, 0)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)

    def test_wrong_type_mode_returns_ok_with_a_non_object_result(self):
        broken = self.fake.plant_install(self.home / "type", mode="wrong-type")
        body = json.loads(self.fake.invoke(broken, self.build_req)[1])
        self.assertEqual(body["ok"], True)
        self.assertIsInstance(body["result"], str)

    def test_query_iserror_mode_returns_text_with_iserror_true(self):
        broken = self.fake.plant_install(self.home / "iserr", mode="query-iserror")
        body = json.loads(self.fake.invoke(broken, self.query_req)[1])
        self.assertEqual(body["ok"], True)
        self.assertIs(body["result"]["isError"], True)
        self.assertTrue(body["result"]["text"])

    def test_argv_records_absolute_node_bridge_and_graft_dir(self):
        self.fake.invoke(self.install, self.build_req)
        argv = self.transcript()[-1]["argv"]
        self.assertEqual(len(argv), 3)
        self.assertEqual(argv[0], str((self.install / "node" / "bin" / "node").resolve()))
        self.assertTrue(argv[1].endswith("bridge.mjs"))
        self.assertEqual(argv[2], str(self.install / "graft"))


if __name__ == "__main__":
    unittest.main()
