"""Real Serena beta over the real dev-lane stdio entry, using disposable PHP.

Run with --serena-python /absolute/isolated/bin/python. This verifies the
provider route, not native harness restrictions or a running Symfony kernel.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ops/conductor/tools"))

import source_work  # noqa: E402
from session_adapters.channel import Channel  # noqa: E402

from ops.testkit import World, run  # noqa: E402


def scenario(python):
    with World(prefix="serena-php-") as world:
        root = world.cwd
        shutil.copytree(Path(__file__).parent / "fixtures/php", root, dirs_exist_ok=True)
        conf = world.plant_text("lane.conf", "LANE_NAME='php-provider-smoke'\n")
        env = world.env(extra={"LANE_CONF": str(conf), "WF_AGENT": "Codex <smoke@example.test>",
                              "CLAUDE_CODE_SESSION_ID": "php-smoke", "COORDINATION_CAPABILITY": hashlib.sha256(b"php-smoke").hexdigest()})
        # Preserve package-transport settings for the LSP's first npm installation.
        from code_providers.serena import ENVIRONMENT, PIN
        env.update({key: os.environ[key] for key in ENVIRONMENT if key in os.environ})
        def command(*argv):
            result = run(argv, cwd=root, env=env, timeout=30)
            if result.exit:
                raise RuntimeError(result.stderr)
            return result.stdout
        for args in (("init", "-q", "-b", "work"), ("config", "user.name", "PHP Fixture"),
                     ("config", "user.email", "php@example.test"), ("add", "."), ("commit", "-qm", "fixture")):
            command("git", *args)
        coord = ROOT / "ops/conductor/tools/coordination.py"
        command(sys.executable, "-B", str(coord), "bind", "--roles", "coordinator")
        draft = command(sys.executable, "-B", str(coord), "draft", "Normalize prod-lane names",
                        "--outcome", "Trim surrounding whitespace before normalizing names")
        assignment = re.search(r"\bA-[0-9]+", draft).group()
        binding = world.plant_text("binding.json", json.dumps({
            "version": 1, "participant": "PT-1", "assignment": assignment, "branch": "work",
            "worktree": str(root), "record": None, "issued_at": "fixture",
            "capabilities": ["code_status", "code_repl", "code_restart", "coord_show"]}))
        entry = ROOT / "ops/conductor/tools/dev_mcp.py"
        rpc = Channel([sys.executable, "-B", str(entry), "--binding", str(binding),
                       "--serena-python", str(python), "--workspace", str(root), "serve"], env=env, cwd=root)
        try:
            rpc.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "php-smoke", "version": "1"}}, timeout=30)
            rpc.notify("notifications/initialized")
            tools = rpc.request("tools/list", timeout=30)["tools"]
            if len(tools) != 3:
                raise AssertionError("initial tool surface expanded")
            def invoke(name, **arguments):
                reply = rpc.request("tools/call", {"name": "capability_invoke", "arguments": {
                    "capability": name, "arguments": arguments}}, timeout=220)
                if reply.get("isError"):
                    raise AssertionError(reply)
                if len(json.dumps(reply).encode()) > 16384:
                    raise AssertionError("unbounded response")
                return reply["structuredContent"]
            def cell(code, expected="returned"):
                state = invoke("code_status")
                result = invoke("code_repl", code=code, expected_epoch=state["provider_epoch"],
                                expected_source_fingerprint=state["source_fingerprint"])
                if result["cell_outcome"] != expected:
                    logs = list((world.xdg_state / "php-provider-smoke/providers").rglob("provider.log"))
                    tail = logs[-1].read_text(errors="replace")[-5000:] if logs else "no provider log"
                    raise AssertionError({"result": result, "provider_log_tail": tail})
                return result
            before_assignment = invoke("coord_show", assignment=assignment)
            docs = cell('s.info("lsp.find_symbol")')
            if "find_symbol" not in docs["text"]:
                raise AssertionError("Serena API discovery missing")
            symbols = cell('selected = s.lsp.find_symbol("NameNormalizer", relative_path="src/Service/NameNormalizer.php", include_body=True)\nlen(selected)')
            if symbols["text"] != "1" or cell("len(selected)")["text"] != "1":
                raise AssertionError("PHP symbol or persistent variable missing")
            references = cell('references = s.lsp.find_referencing_symbols("NameNormalizer/normalize", "src/Service/NameNormalizer.php")\nlen(references)')
            if int(references["text"]) < 1:
                raise AssertionError("PHP reference was not found")
            cell('s.edit.replace_content("src/Service/NameNormalizer.php", "return strtolower($name);", "return strtolower(trim($name));", mode="literal")')
            changed = cell('s.fs.read_file("src/Service/NameNormalizer.php")')
            if "strtolower(trim($name))" not in changed["text"]:
                raise AssertionError("PHP edit/read did not complete")
            state = invoke("code_status")
            with patch.dict(os.environ, env, clear=True):
                source_work.record_check(root, "observed-php-edit", fingerprint=state["source_fingerprint"],
                                         generation=state["generation"], passed=True, evidence="read changed PHP through Serena")
            partial = cell('s.fs.create_text_file("partial.php", "<?php // partial edit")\nraise ValueError("controlled failure")', expected="error")
            if "partial.php" not in partial["changed_paths"] or not (root / "partial.php").exists():
                raise AssertionError("partial change was not accounted")
            if invoke("code_status")["verified_at_current_source"]:
                raise AssertionError("partial error retained stale check success")
            old_epoch = invoke("code_status")["provider_epoch"]
            restarted = invoke("code_restart", expected_epoch=old_epoch)
            if restarted["provider_epoch"] == old_epoch or not restarted["previous_namespace_lost"]:
                raise AssertionError("provider restart was not visible")
            if cell("globals().get('selected')")["text"] != "None":
                raise AssertionError("temporary variables unexpectedly survived restart")
            if invoke("coord_show", assignment=assignment) != before_assignment:
                raise AssertionError("code provider changed the workflow assignment")
            if (root / ".serena").exists():
                raise AssertionError("provider metadata was written into the prod-lane worktree")
            with sqlite3.connect(world.xdg_state / "php-provider-smoke/state.db") as conn:
                events = conn.execute("SELECT count(*) FROM source_events WHERE finished_at IS NOT NULL").fetchone()[0]
            return {"verdict": "demonstrated", "serena_pin": PIN, "language_backend": "Intelephense 1.14.4",
                    "transport": "dev-lane MCP -> private Serena MCP", "initial_tools": len(tools),
                    "php_symbols": True, "php_references": True, "php_edit_and_read": True,
                    "persistent_variables": True, "partial_error_accounted": True,
                    "verification_invalidated": True, "restart_reports_lost_variables": True,
                    "assignment_unchanged": True, "source_events": events,
                    "native_harness": "not exercised", "php_runtime": "not exercised"}
        finally:
            rpc.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serena-python", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(scenario(args.serena_python.absolute()), indent=2))
