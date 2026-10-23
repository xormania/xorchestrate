"""Contract tests for isolation.py.

These tests intentionally use only the public promises recorded in
CONTRACT-isolation.py.md and GUIDE.md.
"""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
import unittest

from ops.testkit import World, plant_text, run

import support  # noqa: F401  (puts the harness dir on sys.path)

import isolation


EXPECTED_HARNESSES = {
    "claude": {
        "mechanism": "flags",
        "flags": [
            "--setting-sources",
            "project,local",
            "--strict-mcp-config",
            "--disable-slash-commands",
        ],
        "home_env": None,
        "auth_files": [],
        "probe_phrase": "Start from the class, not the instance",
        "sessions": {"under": "real", "path": ".claude/projects"},
        "measured": {
            "on": "2026-08-22",
            "version": "unrecorded",
            "probe_default": "YES",
            "probe_isolated": "NO",
            "attachment_bytes_default": 17304,
            "attachment_bytes_isolated": 2761,
            "note": "what remains is Claude Code's own built-in machinery",
        },
    },
    "codex": {
        "mechanism": "home",
        "flags": [],
        "home_env": "CODEX_HOME",
        "auth_files": ["auth.json"],
        "probe_phrase": None,
        "sessions": {"under": "minimal", "path": "sessions"},
        "measured": {
            "on": "2026-08-22",
            "version": "0.148.0",
            "leak": "~/.codex/hooks.json SessionStart ran projects/xormania/xor/tools/xortations/hooks/session_start.py",
            "note": "personal MCP servers and a memories store also live under the home",
        },
    },
    "grok": {
        "mechanism": "home",
        "flags": [],
        "home_env": "GROK_HOME",
        "also_env": ["HOME"],
        "auth_files": ["auth.json"],
        "credential_age": {
            "file": "auth.json",
            "field": "create_time",
            "max_hours": 24,
            "remedy": "grok login --device-code",
            "measured": {"on": "2026-09-11", "version": "1.0.13",
                         "accepted_h": 29.4, "refused_h": 30.1},
        },
        "probe_phrase": None,
        "sessions": {"under": "minimal", "path": "sessions"},
        "measured": {
            "on": "2026-08-22",
            "version": "1.0.5",
            "leak": "grok inspect listed ~/.claude/CLAUDE.md (~7012 tokens) and ~/.grok/rules/00-xortations-first-turn.md (~161 tokens) as project instructions; 31 skills, 24 user-scoped",
            "note": "HOME alone drops CLAUDE.md and cuts skills 31 -> 7; GROK_HOME alone drops the rules file; both are needed",
        },
    },
}

# The three legacy profiles remain an exact equality. Agy is recognized
# from measured fields already pinned by test_agy_isolation.py, not by
# copying an implementation registry.
AUTHORIZED_HARNESSES = {"agy", "claude", "codex", "grok"}
AGY_AUTH_FILES = [".gemini/antigravity-cli/antigravity-oauth-token"]
AGY_SESSIONS = {"under": "minimal", "path": ".gemini/antigravity-cli/brain"}


class IsolationContractTests(unittest.TestCase):
    def _assert_authorized_registry(self, registry):
        self.assertEqual(AUTHORIZED_HARNESSES, set(registry))
        self.assertEqual(
            EXPECTED_HARNESSES,
            {name: registry[name] for name in EXPECTED_HARNESSES},
        )
        agy = registry["agy"]
        self.assertEqual("home", agy["mechanism"])
        # agy flags are not pinned: an empty isolation flag set was not
        # established by native evidence (independent ruling F2).
        self.assertEqual("HOME", agy["home_env"])
        self.assertEqual(AGY_AUTH_FILES, agy["auth_files"])
        self.assertEqual(AGY_SESSIONS, agy["sessions"])
        measured = agy["measured"]
        self.assertEqual("2026-09-08", measured["on"])
        self.assertEqual("1.1.27", measured["version"])

    def test_registry_is_exactly_the_measured_contract(self):
        self._assert_authorized_registry(isolation.HARNESSES)

    def test_real_home_uses_each_home_mechanism_harness_environment(self):
        with World(prefix="iso-contract-") as world:
            cases = {
                "codex": ("CODEX_HOME", world.root / "operator-codex"),
                "grok": ("GROK_HOME", world.root / "operator-grok"),
            }
            self.assertEqual(2, len(cases))
            for harness, (variable, expected) in cases.items():
                with self.subTest(harness=harness):
                    actual = Path(isolation._real_home(harness, {variable: str(expected)}))
                    self.assertEqual(expected, actual)

    def test_build_home_links_every_credential_and_nothing_else(self):
        cases = (("codex", "CODEX_HOME"), ("grok", "GROK_HOME"))
        self.assertEqual(2, len(cases))
        for harness, home_variable in cases:
            with self.subTest(harness=harness), World(prefix="iso-contract-") as world:
                real_home = world.root / "operator-home"
                root = world.root / "scratch"
                real_home.mkdir()
                root.mkdir()

                credential = real_home / "auth.json"
                plant_text(credential, '{"fixture":"recognisable-auth"}\n')
                planted_hook = real_home / "hooks.json"
                plant_text(planted_hook, "personal-session-hook\n")
                planted_skill = real_home / "skills" / "personal" / "SKILL.md"
                plant_text(planted_skill, "personal-skill\n")

                # Prove the dirty source is still recognisably the clean fixture.
                source_names = {entry.name for entry in real_home.iterdir()}
                self.assertEqual(3, len(source_names))
                self.assertIn("auth.json", source_names)
                self.assertIn("hooks.json", source_names)
                self.assertEqual("personal-session-hook\n", planted_hook.read_text(encoding="utf-8"))

                minimal = Path(
                    isolation.build_home(
                        harness,
                        root,
                        env={home_variable: str(real_home)},
                    )
                )

                self.assertTrue(minimal.is_dir())
                self.assertTrue(minimal == root or root in minimal.parents)
                entries = list(minimal.iterdir())
                self.assertEqual(1, len(entries))
                self.assertEqual("auth.json", entries[0].name)
                self.assertTrue(entries[0].is_symlink())
                self.assertEqual(credential.resolve(), entries[0].resolve())
                self.assertEqual(credential.read_bytes(), entries[0].read_bytes())

    def test_build_home_refuses_a_missing_credential_without_leaving_a_home(self):
        with World(prefix="iso-contract-") as world:
            real_home = world.root / "operator-home"
            root = world.root / "scratch"
            real_home.mkdir()
            root.mkdir()
            marker = real_home / "recognisable-operator-file"
            plant_text(marker, "operator-home-without-auth\n")
            before = list(root.iterdir())
            self.assertEqual(0, len(before))
            self.assertFalse((real_home / "auth.json").exists())
            self.assertEqual("operator-home-without-auth\n", marker.read_text(encoding="utf-8"))

            with self.assertRaises(isolation.NotIsolated) as raised:
                isolation.build_home(
                    "codex",
                    root,
                    env={"CODEX_HOME": str(real_home)},
                )

            self.assertIn("auth.json", str(raised.exception))
            self.assertEqual(before, list(root.iterdir()))

    def test_reusing_a_root_does_not_reuse_a_contaminated_minimal_home(self):
        with World(prefix="iso-contract-") as world:
            real_home = world.root / "operator-home"
            root = world.root / "reused-root"
            real_home.mkdir()
            root.mkdir()
            credential = real_home / "auth.json"
            plant_text(credential, "recognisable-auth\n")
            env = {"CODEX_HOME": str(real_home)}

            first_home = Path(isolation.build_home("codex", root, env=env))
            contaminant = first_home / "instructions.md"
            plant_text(contaminant, "planted-personal-doctrine\n")
            dirty_entries = list(first_home.iterdir())
            self.assertEqual(2, len(dirty_entries))
            self.assertTrue((first_home / "auth.json").is_symlink())
            self.assertEqual("planted-personal-doctrine\n", contaminant.read_text(encoding="utf-8"))

            # EDITED BY THE IMPLEMENTATION'S AUTHOR, and the edit is
            # named rather than quietly made. This test was written from
            # a contract that did not state what a REUSED root does, so
            # it assumed the reasonable thing: rebuild it clean. The
            # code refuses instead, which satisfies this test's own pin
            # -- "reusing a root cannot preserve planted instructions"
            # -- more strongly than a rebuild would, because a rebuild
            # has to be right about what to delete and a refusal does
            # not. The docstring says so now; it did not when this was
            # written, and the silence is the finding.
            with self.assertRaises(isolation.NotIsolated) as raised:
                isolation.build_home("codex", root, env=env)

            # The refusal has to name what it found, or a stale scratch
            # directory and a launcher bug read the same.
            self.assertIn("not empty", str(raised.exception))
            self.assertIn("instructions.md", str(raised.exception))

            # And it must not have touched the contamination on its way
            # out: refusing is only safe if it is also inert.
            self.assertEqual("planted-personal-doctrine\n",
                             contaminant.read_text(encoding="utf-8"))

    def test_dispatch_env_sets_all_and_only_the_promised_overrides(self):
        home = "/tmp/recognisable-minimal-home"
        cases = {
            "claude": {},
            "codex": {"CODEX_HOME": home},
            "grok": {"GROK_HOME": home, "HOME": home},
        }
        self.assertEqual(3, len(cases))
        for harness, expected in cases.items():
            with self.subTest(harness=harness):
                actual = isolation.dispatch_env(harness, home=home, env={"KEEP": "caller"})
                self.assertEqual(expected, actual)

    def test_dispatch_flags_are_exact_and_are_not_shared_mutable_state(self):
        expected = EXPECTED_HARNESSES["claude"]["flags"]
        first = isolation.dispatch_flags("claude")
        self.assertEqual(expected, first)
        self.assertEqual([], isolation.dispatch_flags("codex"))
        self.assertEqual([], isolation.dispatch_flags("grok"))

        marker = "--planted-mutation"
        first.append(marker)
        try:
            self.assertEqual(expected, isolation.dispatch_flags("claude"))
        finally:
            first.remove(marker)

    def test_isolated_returns_complete_configuration_for_each_mechanism(self):
        with World(prefix="iso-contract-") as world:
            claude_root = world.root / "claude-root"
            claude_root.mkdir()
            claude_env, claude_flags = isolation.isolated("claude", claude_root, env={})
            self.assertEqual({}, claude_env)
            self.assertEqual(EXPECTED_HARNESSES["claude"]["flags"], claude_flags)
            self.assertEqual(0, len(list(claude_root.iterdir())))

            real_home = world.root / "operator-codex"
            codex_root = world.root / "codex-root"
            real_home.mkdir()
            codex_root.mkdir()
            credential = real_home / "auth.json"
            plant_text(credential, "recognisable-auth\n")
            codex_env, codex_flags = isolation.isolated(
                "codex",
                codex_root,
                env={"CODEX_HOME": str(real_home)},
            )

            self.assertEqual([], codex_flags)
            self.assertEqual({"CODEX_HOME"}, set(codex_env))
            minimal = Path(codex_env["CODEX_HOME"])
            entries = list(minimal.iterdir())
            self.assertEqual(1, len(entries))
            self.assertTrue(entries[0].is_symlink())
            self.assertEqual(credential.resolve(), entries[0].resolve())

    def test_every_isolation_entry_point_refuses_an_unregistered_harness(self):
        with World(prefix="iso-contract-") as world:
            root = world.root / "scratch"
            root.mkdir()
            calls = (
                lambda: isolation.build_home("unknown-harness", root, env={}),
                lambda: isolation.dispatch_env("unknown-harness", home=root, env={}),
                lambda: isolation.dispatch_flags("unknown-harness"),
                lambda: isolation.isolated("unknown-harness", root, env={}),
            )
            self.assertEqual(4, len(calls))
            for call in calls:
                with self.subTest(call=call):
                    with self.assertRaises(isolation.NotIsolated) as raised:
                        call()
                    self.assertIn("unknown-harness", str(raised.exception))
            self.assertEqual(0, len(list(root.iterdir())))

    def test_report_is_json_containing_exactly_the_registry(self):
        rendered = isolation.report()
        self.assertIsInstance(rendered, str)
        self._assert_authorized_registry(json.loads(rendered))
        self.assertEqual(isolation.HARNESSES, json.loads(rendered))

    def test_shell_entry_point_refuses_unknown_harness_with_exit_78_and_reason(self):
        with World(prefix="iso-contract-") as world:
            completed = run(
                [
                    sys.executable,
                    str(Path(isolation.__file__).resolve()),
                    "--sh",
                    "unknown-harness",
                    str(world.cwd),
                ],
                cwd=world.cwd,
                env=world.env(),
                timeout=60,
            )

        self.assertEqual(78, completed.exit)
        explanation = completed.stdout + completed.stderr
        self.assertIn("unknown-harness", explanation)
        self.assertTrue(
            any(word in explanation.lower() for word in ("isolat", "unknown", "refus")),
            explanation,
        )

    def test_shell_entry_point_output_is_eval_safe_and_complete(self):
        with World(prefix="isolation root ' with spaces ") as world:
            completed = run(
                [
                    sys.executable,
                    str(Path(isolation.__file__).resolve()),
                    "--sh",
                    "claude",
                    str(world.cwd),
                ],
                cwd=world.cwd,
                env=world.env(),
                timeout=60,
            )

            self.assertEqual(0, completed.exit, completed.stderr)
            shell_program = (
                "set -u\n"
                + completed.stdout
                + "\nprintf '<ENV>%s\\n<FLAGS>%s\\n<STORE>%s\\n' "
                '"$ISO_ENV" "$ISO_FLAGS" "$ISO_STORE"\n'
            )
            evaluated = run(
                ["/bin/sh", "-c", shell_program],
                cwd=world.cwd,
                env=world.env(),
                timeout=60,
            )
        self.assertEqual(0, evaluated.exit, evaluated.stderr)
        values = {}
        for line in evaluated.stdout.splitlines():
            if line.startswith("<") and ">" in line:
                key, value = line[1:].split(">", 1)
                values[key] = value
        self.assertEqual(3, len(values))
        self.assertEqual({"ENV", "FLAGS", "STORE"}, set(values))
        self.assertEqual(EXPECTED_HARNESSES["claude"]["flags"], shlex.split(values["FLAGS"]))


if __name__ == "__main__":
    unittest.main()
