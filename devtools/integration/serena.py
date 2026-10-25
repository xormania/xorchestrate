#!/usr/bin/env python3
"""Real Serena v2 container acceptance over stdio MCP; no model credentials are needed."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "devtools/tests"))

from support import Client, adopt_plan, initialize, until  # noqa: E402

from devtools.api import API  # noqa: E402
from devtools.retirement import key, lease  # noqa: E402
from devtools.role_packages import default_role  # noqa: E402
from devtools.workspaces import git  # noqa: E402


def repl(client, code):
    ident = client.begin("tools/call", {"name": "serena_repl", "arguments": {"code": code}})
    value = client.finish(ident, timeout=115)
    assert not value.get("isError"), value
    return "\n".join(row["text"] for row in value["content"] if row["type"] == "text")


def audit_rows(state):
    return [json.loads(line) for line in (state / "telemetry/coder.jsonl").read_text().splitlines(keepends=True)
            if line.endswith("\n")]


def main(curated=False):
    image = os.environ.get("AGENTSCIENT_SERENA_IMAGE", "agentscient-serena:v2-beta")
    with tempfile.TemporaryDirectory(prefix="agentscient-serena-") as temporary:
        root = Path(temporary)
        checkout = root / "checkout"
        (checkout / "src").mkdir(parents=True)
        (checkout / "devtools").mkdir()
        (checkout / "src/Maker").mkdir()
        (checkout / "src/Example.php").write_text(
            "<?php\nnamespace App;\nclass Example { public function greeting(): string { return 'hello'; } }\n"
        )
        (checkout / "devtools/protected.txt").write_text("unchanged")
        (checkout / "src/Maker/FixtureMaker.php").write_text("<?php // Maintainer-owned Maker\n")
        (checkout / "src/Unrelated.php").write_text("<?php // Outside the assignment\n")
        subprocess.run(["git", "init", "-b", "dev", str(checkout)], check=True, capture_output=True)
        git(checkout, "config", "user.name", "Fixture")
        git(checkout, "config", "user.email", "fixture@example.invalid")
        git(checkout, "add", ".")
        git(checkout, "commit", "-m", "Initial")
        state = root / "state"
        initialize(state, checkout, ["owner:external", "xo:external", "coder:external"], co="owner")
        config = json.loads((state / "team.json").read_text())
        config["project"].update({
            "checkout": str(checkout),
            "target": "dev",
            "repository": "example/fixture",
            "serena": {"image": image},
            "assignment_policy": "assignment-policy.json",
        })
        policy = json.loads((REPO / "devtools/assignment-policy.json").read_text())
        policy["protected_paths"] += ["src/Maker/PlannedMaker.php", "src/Reserved.php", "src/FutureMaker/"]
        (state / "assignment-policy.json").write_text(json.dumps(policy))
        (state / "team.json").write_text(json.dumps(config))
        API(state, "owner").call(
            "direct",
            {
                "title": "Serena acceptance",
                "objective": "Use v2 REPL",
                "done_when": "Symbol discovery, persistent variables and bounded edits work",
                "constraints": "Disposable job",
            },
        )
        xo = API(state, "xo")
        adopt_plan(xo, [{"name": "Serena example", "objective": "Inspect and edit", "done_when": "Evidence", "constraints": "Source only"}])
        xo.call(
            "orchestrate",
            {
                "work": [
                    {
                        "name": "Serena example",
                        "role": default_role("coder"),
                        "objective": "Inspect and edit",
                        "done_when": "Evidence",
                        "constraints": "Source only",
                        **({"editable_paths": ["src/Example.php", "src/NewService.php", "src/NOTICE", "src/NewDirectory/"],
                            "context_paths": ["src/Example.php"]} if curated else {}),
                    }
                ]
            },
        )
        client = Client(state, "coder", provider="serena")
        try:
            assert {r["name"] for r in client.call("tools/list")["tools"]} == {"serena_repl"}
            assert "42" in repl(client, "answer = 42; answer")
            assert "42" in repl(client, "answer")
            workspace = Path(API(state, "coder").workspaces.ensure()["root"])
            assert not (workspace / "src/Maker/PlannedMaker.php").exists()
            assert not (workspace / "src/Reserved.php").exists()
            assert not (workspace / "src/FutureMaker").exists()
            if not curated:
                assert not git(workspace, "status", "--porcelain").stdout, "Inspection manufactured source changes"
            documentation = repl(client, "s.info('lsp.get_symbols_overview', 'edit.replace_content')")
            assert "get_symbols_overview" in documentation, documentation
            symbols = repl(client, "s.lsp.get_symbols_overview('src/Example.php', depth=1)")
            assert "Example" in symbols and "greeting" in symbols, symbols
            # Maintainer-authorized, harmless canary: observation must neither
            # block the launch nor confuse it with ordinary PHPactor startup.
            canary = repl(client, "import subprocess\nsubprocess.run(['/bin/true'], check=True).returncode")
            assert "0" in canary, canary

            def process_evidence():
                rows = audit_rows(state)
                attempts = [row["data"] for row in rows if row["kind"] == "process_audit"]
                return any(row.get("executable") == "/bin/true" and row["origin"] == "repl"
                           and row["evidence"] == "attempt" and row.get("request_id") for row in attempts)

            until(process_evidence, timeout=5)
            rows = audit_rows(state)
            assert any(row["kind"] == "process_audit_status" and row["data"].get("status") == "ready" for row in rows)
            assert any(row["kind"] == "process_audit" and row["data"]["origin"] == "backend" for row in rows), rows
            report = subprocess.run([sys.executable, str(REPO / "devtools/scripts/serena-audit.py"),
                                     "--state", str(state), "--json", "--fail-on-findings"],
                                    capture_output=True, text=True, timeout=10)
            assert report.returncode == 1, report.stdout + report.stderr
            edited = repl(client, "s.edit.replace_content('src/Example.php', 'hello', 'welcome', 'literal')")
            assert "Error" not in edited, edited
            assert "welcome" in (workspace / "src/Example.php").read_text(), edited
            blocked = repl(
                client,
                "from pathlib import Path\nprotected = False\ntry:\n Path('/workspace/devtools/protected.txt').write_text('wrong')\nexcept OSError:\n protected = True\nprotected",
            )
            assert "True" in blocked, blocked
            assert (workspace / "devtools/protected.txt").read_text() == "unchanged"
            blocked = repl(
                client,
                "protected = False\ntry:\n Path('/workspace/src/Maker/FixtureMaker.php').write_text('wrong')\nexcept OSError:\n protected = True\nprotected",
            )
            assert "True" in blocked, blocked
            assert (workspace / "src/Maker/FixtureMaker.php").read_text() == "<?php // Maintainer-owned Maker\n"
            for path in ("src/Maker/PlannedMaker.php", "src/Reserved.php", "src/FutureMaker"):
                blocked = repl(client, "protected = False\ntry:\n Path(" + repr("/workspace/" + path)
                               + ").write_text('wrong')\nexcept OSError:\n protected = True\nprotected")
                assert "True" in blocked, blocked
                assert not (workspace / path).exists()
            assert "False" in repl(client, "Path('/workspace/.serena/view').exists()")
            if curated:
                assert "False" in repl(client, "Path('/workspace/src/Unrelated.php').exists()")
                for path, content in (("src/NewService.php", "<?php // New assigned service"),
                                      ("src/NOTICE", "Extensionless file"),
                                      ("src/NewDirectory/Child.php", "<?php // New assigned directory")):
                    repl(client, "Path(" + repr("/workspace/" + path) + ").write_text(" + repr(content) + ")")
                    assert (workspace / path).read_text() == content
                blocked = repl(client, "blocked = False\ntry:\n Path('/workspace/src/Unassigned.php').write_text('wrong')\nexcept OSError:\n blocked = True\nblocked")
                assert "True" in blocked, blocked
                assert not (workspace / "src/Unassigned.php").exists()
            assert "False" in repl(client, "Path('/var/run/docker.sock').exists()")
            xo.call("steer", {"action": "pause", "work": "Serena example"})
            response = client.call("tools/call", {"name": "serena_repl", "arguments": {"code": "answer"}})
            assert response.get("isError") and "paused" in json.dumps(response), response
            xo.call("steer", {"action": "resume", "work": "Serena example"})
            assert "42" in repl(client, "answer")
            if curated:
                replacement = workspace / "src/replacement.php"
                replacement.write_text((workspace / "src/Example.php").read_text().replace("welcome", "current"))
                replacement.replace(workspace / "src/Example.php")
                response = client.call("tools/call", {"name": "serena_repl", "arguments": {"code": "s.info()"}})
                assert response.get("isError") and "paths changed" in json.dumps(response), response
                assert "current" in repl(client, "from pathlib import Path\nPath('/workspace/src/Example.php').read_text()")
            API(state, "coder").call("work_save", {"note": "Serena-generated change"})
            xo.call("steer", {"action": "cancel", "work": "Serena example", "direction": "Fixture complete"})

            def stopped():
                containers = subprocess.run(
                    ["docker", "ps", "--all", "--quiet", "--filter", "label=agentscient.workspace=" + key(workspace)],
                    capture_output=True,
                    check=True,
                    timeout=10,
                ).stdout
                if containers.strip():
                    return False
                try:
                    with lease(xo.store, workspace, exclusive=True):
                        return True
                except ValueError:
                    return False

            until(stopped, timeout=20)
            assert client.process.poll() is None, "External MCP connection should remain open"
            assert workspace.exists(), "Cancellation must preserve unfinished source"
            print(
                json.dumps(
                    {
                        "accepted": [
                            "Serena v2 REPL",
                            "persistent variables",
                            "PHPactor symbol navigation",
                            "observe-only process canary and backend classification",
                            "operator process audit report",
                            "Serena edit facade",
                            "protected source mount",
                            "maintainer-owned Maker protection",
                            "absent protected paths stay absent and unwritable",
                            "assigned new files, extensionless files and directories" if curated else "clean inspection startup",
                            "curated file mounts and refresh" if curated else "operator role paths",
                            "no Docker socket",
                            "live pause/resume",
                            "control-plane checkpoint",
                            "released provider shutdown with external MCP still connected",
                        ],
                        "symbols": symbols,
                    }
                )
            )
        except Exception:
            path = state / "telemetry/coder-serena.log"
            if path.exists():
                print(path.read_text()[-18000:], file=sys.stderr)
            raise
        finally:
            client.close()


if __name__ == "__main__":
    main()
    main(curated=True)
