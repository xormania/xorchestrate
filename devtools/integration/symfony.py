#!/usr/bin/env python3
"""Exercise actual installed Makers/Mate through normal MCP, in a disposable job worktree."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "devtools/tests"))

from support import Client, adopt_plan, initialize  # noqa: E402

from devtools.api import API  # noqa: E402
from devtools.development import DEFAULT_CHECKS, Development  # noqa: E402
from devtools.readiness import platform  # noqa: E402
from devtools.role_packages import default_role  # noqa: E402
from devtools.role_packages import role as role_package  # noqa: E402
from devtools.workspaces import git  # noqa: E402


def completed(client, tool, arguments):
    value = client.tool(tool, arguments)
    reference = value["reference"]
    deadline = time.monotonic() + 700
    while value["state"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.2)
        parts, offset = [], 0
        while offset is not None:
            page = client.tool("work_context", {"reference": reference, "offset": offset})
            parts.append(page["content"])
            offset = page["next_offset"]
        value = json.loads("".join(parts))
    assert value["state"] == "completed", value
    return value["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, help="Retain results and test events even when the fixture fails")
    parser.add_argument("--full-checks", action="store_true", help="Diagnose with the normal agent's full check suite")
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve() if args.evidence_dir else None
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
        for name in ("result.json", "phpunit-events.log"):
            (evidence / name).unlink(missing_ok=True)
    platform_result = platform(REPO, {})
    with tempfile.TemporaryDirectory(prefix="agentscient-symfony-") as temporary:
        root = Path(temporary)
        checkout = root / "checkout"
        subprocess.run(["git", "clone", "--no-hardlinks", str(REPO), str(checkout)], check=True, capture_output=True)
        git(checkout, "branch", "fixture-base", "HEAD")
        git(checkout, "config", "user.name", "Integration fixture")
        git(checkout, "config", "user.email", "fixture@example.invalid")
        state = root / "state"
        events = root / "phpunit-events.log"
        checks = (
            {name: list(argv) for name, argv in DEFAULT_CHECKS.items()}
            if args.full_checks else {"generated-test": ["php", "vendor/bin/phpunit", "tests/FixtureGeneratedTest.php"]}
        )
        test_check = checks["tests" if args.full_checks else "generated-test"]
        # Populate both PHP environment arrays so a runtime APP_ENV override cannot
        # hide behind an installation whose variables_order omits $_ENV.
        test_check[1:1] = ["-d", "variables_order=EGPCS"]
        test_check.extend(["--log-events-text", str(events)])
        initialize(state, checkout, ["owner:external", "xo:external", "coder:external"], co="owner")
        config = json.loads((state / "team.json").read_text())
        config["project"].update({
            "checkout": str(checkout),
            "target": "fixture-base",
            "repository": "example/fixture",
            "fetch": False,
            "development": {
                "checks": checks,
            },
        })
        (state / "team.json").write_text(json.dumps(config))
        co, xo, coder = [Client(state, member) for member in ("owner", "xo", "coder")]
        coder_role = default_role("coder")
        report = {"platform": platform_result, "full_checks": args.full_checks}
        try:
            co.tool(
                "direct",
                {
                    "title": "Development provider acceptance",
                    "objective": "Use installed framework conventions",
                    "done_when": "Generation, diagnostics and checks have evidence",
                    "constraints": "Disposable workspace only",
                },
            )
            adopt_plan(API(state, "xo"), [{"name": "Exercise Symfony", "objective": "Generate with native Makers",
                                          "done_when": "Attributes and autoconfiguration work", "constraints": "No handwritten replacement generator"}])
            xo.tool(
                "orchestrate",
                {
                    "work": [
                        {
                            "name": "Exercise Symfony",
                            "role": coder_role,
                            "objective": "Generate with native Makers",
                            "done_when": "Attributes and autoconfiguration work",
                            "constraints": "No handwritten replacement generator",
                        }
                    ]
                },
            )
            workspace = Path(API(state, "coder").workspaces.ensure()["root"])
            runtime = Development(API(state, "coder").store, "coder").run(
                workspace, ["php", str(REPO / "devtools/integration/php-runtime.php")]
            )
            assert runtime["exit_code"] == 0, runtime
            report["runtime"] = json.loads(runtime["output"])
            makers = completed(coder, "framework_inspect", {"kind": "makers"})["result"]["makers"]
            assert "make:command" in {row["name"] for row in makers}, makers
            metadata = completed(coder, "framework_inspect", {"kind": "makers", "name": "make:command"})["result"]
            assert "name" in metadata["definition"]["arguments"], metadata
            console = completed(coder, "framework_inspect", {"kind": "console"})["result"]["commands"]
            assert "debug:container" in {row["name"] for row in console}, console
            services = completed(coder, "framework_inspect", {"kind": "console", "name": "debug:container", "parameters": {"arguments": {"name": "kernel"}}})["result"]
            assert services["exit_code"] == 0, services
            report["introspection"] = services
            generated = completed(
                coder, "framework_generate", {"maker": "make:command", "arguments": {"name": "app:fixture-command"}}
            )["result"]
            assert generated["exit_code"] == 0, generated
            command = next((workspace / "src/Command").glob("*.php")).read_text()
            assert "#[AsCommand" in command and "#[Argument" in command and "#[Option" in command, command
            actual = subprocess.run(
                ["php", "bin/console", "app:fixture-command", "--no-interaction"],
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            assert actual.returncode == 0, actual.stderr
            mate = completed(coder, "framework_inspect", {"kind": "mate", "name": "server-info", "parameters": {}})
            assert mate["result"], mate
            onboarding = coder.tool("onboard", {})
            skills = role_package(coder_role)["skills"]
            expected_skills = [skills["shared"], skills["role"], *skills["framework"]]
            assert onboarding["role"] == coder_role, onboarding
            assert [entry["name"] for entry in onboarding["skills"]] == expected_skills, onboarding
            coder.tool("skill_read", {"name": "mate-system-information"})
            package = completed(coder, "framework_inspect", {"kind": "packages", "package": "symfony/console"})
            assert package["result"]["name"] == "symfony/console", package
            tests = completed(
                coder,
                "framework_generate",
                {"maker": "make:test", "arguments": {"type": "KernelTestCase", "name": "FixtureGeneratedTest"}},
            )["result"]
            assert tests["exit_code"] == 0, tests
            generated_test = workspace / "tests/FixtureGeneratedTest.php"
            test_source = generated_test.read_text()
            environment_assertion = "$this->assertSame('test', $kernel->getEnvironment());"
            assert test_source.count(environment_assertion) == 1, test_source
            # Extend the maintained Maker's kernel test with actual access to the
            # test-only container that application integration tests depend on.
            test_source = test_source.replace(
                environment_assertion,
                environment_assertion + "\n"
                "        $this->assertSame($kernel->getContainer()->get('test.service_container'), static::getContainer());",
            )
            generated_test.write_text(test_source)
            validation = completed(coder, "work_check", {})
            report["validation"] = validation
            assert validation["passed"], validation
            assert [row["check"] for row in validation["checks"]] == list(checks), validation
            print(
                json.dumps(
                    {
                        "accepted": [
                            "isolated dependency install",
                            "installed Maker metadata",
                            "installed Symfony Console introspection",
                            "attribute command generation",
                            "autoconfigured command execution",
                            "Mate server-info",
                            "test generation",
                            "test environment and service container through work_check",
                            "configured checks",
                        ],
                        "mate": mate["result"],
                        **report,
                    },
                    indent=2,
                )
            )
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            try:
                if evidence:
                    (evidence / "result.json").write_text(json.dumps(report, indent=2) + "\n")
                    if events.is_file():
                        shutil.copyfile(events, evidence / "phpunit-events.log")
            finally:
                for client in (coder, xo, co):
                    client.close()


if __name__ == "__main__":
    main()
