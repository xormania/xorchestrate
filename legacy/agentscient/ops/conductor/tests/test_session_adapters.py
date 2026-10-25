"""Managed sessions slice 1: framing parser and the claude adapter.

Contract: `ops/conductor/sessions-lever.md` §The adapter boundary.
`ops/conductor/tools/session_adapters/framing.py` is the real parser;
`session_adapters/claude.py` is the one live adapter. Exercised in-process.
A missing adapter fails the row's own method, argv, probe, stop or
diagnostics assertion, not an import error.

Calibration gap: planted faults follow the implementation.
ops/testkit/cases.py calibrate anchors each mutant on the subject's text,
and the adapter modules are absent, so this module has no mutants file.

The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_session_adapters.py`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
ADAPTERS = REPO / "ops" / "conductor" / "tools" / "session_adapters"
FRAMING = ADAPTERS / "framing.py"
CLAUDE = ADAPTERS / "claude.py"
PROFILES = REPO / "ops" / "devlane" / "hooks" / "profiles.py"
ORIGIN = "sessions-lever.md"
REVIEW = "20260922T215604Z-review-codex-f6777b"
METHODS = ("argv", "observe_identity", "probe", "stop", "diagnostics")
# sessions-lever.md §The adapter boundary, extended: slice 2's five.
EXTENDED = ("transport", "channel", "handshake", "submit", "observe")
EXTENDED_ORIGIN = f"{ORIGIN}#the-adapter-boundary-extended"
LAUNCH_ID = "11111111-1111-4111-8111-111111111111"
PROBE_TOKEN = "session-slice1-version-9.9.9"
FAKES = Path(__file__).resolve().parent / "fakes"
HOLDER_SRC = FAKES / "session_holder.py"
PROBE_SRC = FAKES / "session_probe.py"


def fake_text(path: Path, **values: str) -> str:
    """A program under fakes/, its shebang this interpreter, each
    @@NAME@@ replaced by its value; an anchor that is not there once is
    INVALID. origin: 20260923T003033Z-review-codex-c99cc9#7"""
    if not path.is_file():
        raise AssertionError(f"INVALID: planted program missing: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    for name, value in values.items():
        anchor = f"@@{name}@@"
        if text.count(anchor) != 1:
            raise AssertionError(f"INVALID: {path.name} anchor {anchor}")
        text = text.replace(anchor, value)
    return f"#!{sys.executable}\n" + text


def origin_finding(number: int) -> str:
    return f"{REVIEW}#{number}"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reading_of(exc: BaseException) -> str:
    return str(getattr(exc, "reading", None) or exc)


def start_ticks(pid: int) -> str:
    text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return text.rsplit(")", 1)[1].split()[19]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return Path(f"/proc/{pid}").exists()


class TheFramingParser(unittest.TestCase):
    """Each framing fault through the real parser, a row with origin.

    Declared gap: the faults file follows the implementation.
    """

    def parser(self):
        self.assertTrue(FRAMING.is_file(), f"RED: framing is absent: {FRAMING}")
        module = load_module(FRAMING, "session_adapters_framing")
        self.assertTrue(hasattr(module, "Parser"), "framing.Parser is the real parser")
        self.assertTrue(hasattr(module, "Refused"), "framing.Refused names the reading")
        return module.Parser(), module

    def refuse_line(self, parser, module, line: str) -> str:
        with self.assertRaises(module.Refused) as caught:
            parser.feed(line)
        text = reading_of(caught.exception)
        self.assertIn(line, text)
        return text

    def test_a_non_json_line_is_refused_naming_the_line(self):
        with self.subTest(id="framing-non-json", origin=ORIGIN):
            parser, module = self.parser()
            line = "this is not json"
            self.refuse_line(parser, module, line)

    def test_a_response_whose_id_matches_no_request_is_refused_naming_the_line(self):
        with self.subTest(id="framing-unknown-response", origin=ORIGIN):
            parser, module = self.parser()
            line = '{"id":"no-such-request","result":{}}'
            self.refuse_line(parser, module, line)

    def test_two_requests_with_one_id_are_refused_naming_the_line(self):
        with self.subTest(id="framing-duplicate-id", origin=ORIGIN):
            parser, module = self.parser()
            first = '{"id":"1","method":"ping"}'
            parser.feed(first)
            line = '{"id":"1","method":"ping-again"}'
            self.refuse_line(parser, module, line)

    def test_a_result_missing_its_id_is_refused_naming_the_line(self):
        with self.subTest(id="framing-result-missing-id", origin=ORIGIN):
            parser, module = self.parser()
            line = '{"result":{"ok":true}}'
            self.refuse_line(parser, module, line)


class TheClaudeAdapter(unittest.TestCase):
    """The adapter's ten methods, the profile launch argv, probe, stop, diagnostics.

    Declared gap: planted faults follow the implementation; no mutants file.
    """

    def setUp(self):
        self.world = World(prefix="session-adapter-")
        self.addCleanup(self.world.close)

    def adapter(self):
        if not CLAUDE.is_file():
            return None
        try:
            module = load_module(CLAUDE, "session_adapters_claude")
        except Exception:
            return None
        cls = getattr(module, "Adapter", None)
        if not isinstance(cls, type):
            return None
        try:
            return cls()
        except Exception:
            return None

    def supervisor_methods(self, adapter) -> set[str]:
        if adapter is None:
            return set()
        found: set[str] = set()
        for klass in type(adapter).mro():
            if klass is object:
                continue
            for name, value in klass.__dict__.items():
                if name.startswith("_"):
                    continue
                if callable(value):
                    found.add(name)
        return found

    def test_the_adapter_exposes_the_five_methods_and_no_others_the_supervisor_calls(self):
        """origin: 20260922T215604Z-review-codex-f6777b#22, and the extended
        boundary: "Every adapter carries slice 1's five methods and these
        five; the supervisor and the bridge call no others." The name is
        slice 1's, kept because P-sessions1-adapter-five-methods greps it."""
        with self.subTest(id="adapter-five-methods", origin=f"{origin_finding(22)}; {EXTENDED_ORIGIN}"):
            found = self.supervisor_methods(self.adapter())
            self.assertEqual(found, set(METHODS + EXTENDED), f"adapter methods: {sorted(found)}")

    def test_argv_is_the_profile_run_interactive_launch(self):
        """origin: 20260922T215604Z-review-codex-f6777b#23"""
        with self.subTest(id="claude-argv-launch-id", origin=origin_finding(23)):
            adapter = self.adapter()
            exe = self.world.tools.plant("claude", stdout="")
            binding_path = str(self.world.root / "binding.json")
            binding = {
                "executable": str(exe),
                "binding": binding_path,
                "harness": "claude",
            }
            argv = []
            if adapter is not None:
                try:
                    argv = list(adapter.argv({"name": "co", "harness": "claude"}, binding, LAUNCH_ID))
                except Exception:
                    argv = []
            parts = [str(part) for part in argv]
            native_ok = parts == [str(exe), "--session-id", LAUNCH_ID]
            session_value = None
            if "--session-id" in parts:
                index = parts.index("--session-id")
                if index + 1 < len(parts):
                    session_value = parts[index + 1]
            profile_ok = (
                "run-interactive" in parts
                and any(part.endswith("profiles.py") or part == str(PROFILES) for part in parts)
                and session_value == LAUNCH_ID
                and "echo" not in parts
                and not any("operator text" in part for part in parts)
            )
            self.assertTrue(
                native_ok or profile_ok,
                f"argv must be the pinned executable plus --session-id or "
                f"profiles.py run-interactive, found {parts}",
            )

    def test_probe_runs_the_executable_and_reports_version_and_capability(self):
        """origin: 20260922T215604Z-review-codex-f6777b#24, and the extended
        boundary: the claude adapter's probe "measures the channel
        capability it left `unmeasured` in slice 1 and reads `unsupported
        channel` on the installed version, which is not a refusal." The
        planted executable's help does not name the channel flag."""
        with self.subTest(id="claude-probe-channel", origin=f"{origin_finding(24)}; {EXTENDED_ORIGIN}"):
            marker = self.world.root / "probe-ran"
            script = fake_text(PROBE_SRC, MARKER=str(marker), TOKEN=PROBE_TOKEN)
            exe = self.world.tools.plant("claude-probe", script=script)
            adapter = self.adapter()
            reading: object = {}
            if adapter is not None:
                try:
                    reading = adapter.probe(str(exe))
                except Exception as exc:
                    reading = {"error": str(exc)}
            self.assertTrue(marker.is_file(), "probe did not execute the planted executable")
            self.assertIsInstance(reading, dict)
            self.assertIn("capability", reading)
            self.assertNotIn("error", reading, "probe raised; unsupported is a reading, not a refusal")
            self.assertEqual(reading["capability"], "unsupported channel")
            self.assertIn(PROBE_TOKEN, json.dumps(reading))

    def owned_holder(self) -> tuple[int, callable]:
        ready = self.world.root / "holder.ready"
        stop = self.world.root / "holder.stop"
        self.world.tools.plant("holder", script=fake_text(HOLDER_SRC))
        box: dict = {}

        def invoke() -> None:
            try:
                box["result"] = run(
                    [str(self.world.bin / "holder"), str(ready), str(stop)],
                    cwd=self.world.cwd,
                    env=self.world.env(),
                    timeout=30,
                )
            except BaseException as exc:  # the test asserts the outcome
                box["error"] = exc

        thread = threading.Thread(target=invoke)
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not ready.is_file():
            time.sleep(0.02)
        self.assertTrue(ready.is_file(), "holder was not started through ops.testkit.run")
        pid = int(ready.read_text(encoding="utf-8"))

        def release() -> None:
            stop.write_text("stop\n", encoding="utf-8")
            thread.join(timeout=15)
            if alive(pid):
                os.kill(pid, 15)

        self.addCleanup(release)
        return pid, release

    def test_stop_does_not_signal_a_pid_whose_start_ticks_do_not_match(self):
        """origin: 20260922T215604Z-review-codex-f6777b#25"""
        with self.subTest(id="adapter-stop-stale-pid", origin=origin_finding(25)):
            adapter = self.adapter()
            outcome = ""
            still_alive = False
            if adapter is None:
                outcome = ""
            else:
                pid, release = self.owned_holder()
                try:
                    try:
                        outcome = adapter.stop({"pid": pid, "start": "0"}, True)
                    except Exception as exc:
                        outcome = str(exc)
                    still_alive = alive(pid)
                finally:
                    release()
            text = str(outcome)
            self.assertTrue(text.startswith("unresolved"), text)
            self.assertIn("stale pid", text)
            self.assertTrue(still_alive, "the world-owned pid was signalled")

    def test_diagnostics_returns_the_retained_pane_log(self):
        """origin: 20260922T215604Z-review-codex-f6777b#26"""
        with self.subTest(id="adapter-diagnostics", origin=origin_finding(26)):
            member_dir = self.world.root / "member"
            pane = member_dir / "pane.log"
            self.world.plant_text(pane, "pane\n", recognisable="pane")
            adapter = self.adapter()
            paths = []
            if adapter is not None:
                try:
                    paths = list(adapter.diagnostics(member_dir))
                except Exception:
                    paths = []
            names = [Path(str(path)).name for path in paths]
            self.assertIn("pane.log", names, paths)
            self.assertTrue(all(Path(str(path)).is_file() for path in paths), paths)


if __name__ == "__main__":
    unittest.main()
