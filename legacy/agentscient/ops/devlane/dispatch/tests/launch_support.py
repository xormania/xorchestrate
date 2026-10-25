"""Shared fixtures for the launch.py / record.py suite.

Import boundary (RED design): launch.py and record.py are absent at
the contract-only head. Tests never import them at collection time —
that would be a collection error, which is not a red. Each test loads
through ``require_module``. When the file is missing, that helper
returns an empty-stub stand-in whose ``main`` / ``build`` / ``validate``
do nothing useful, so the test fails on the contracted assertion, not
on "file absent". An empty ``launch.py`` / ``record.py`` on disk fails
the same way. A skip that later flips to a behavioural red is not used.

No test here reads a wall clock for a result. Stream timestamps come
from stores.py with a caller-chosen epoch. Fake harness CLIs always
write a start-witness (path baked into the script, not an env var the
launcher can strip) so a refusal can prove the child did not run.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import time
import types
import unittest
import urllib.parse
import uuid
from pathlib import Path

import support

from ops.testkit import (
    PlantFailed,
    RefusalCase,
    Result,
    World,
    hermetic_env,
    refusal_wire,
    run,
)
from ops.testkit import plant_bytes as kit_plant_bytes

APP = support.APP
REPO = support.REPO
HARNESS_DIR = APP.parents[0] / "harness"
FIXTURES_DIR = APP.parents[0] / "fixtures"
STORES_PATH = FIXTURES_DIR / "stores.py"
WIRES_PATH = HARNESS_DIR / "wires.py"
WALL_PY = REPO / "ops" / "devlane" / "workflow" / "checks" / "vocabulary_wall.py"
# jobs.json is owned by the task app; dispatch reads it by path.
JOBS_PATH = APP.parents[0] / "task" / "jobs.json"

AGENT = "Launch Test <noreply@example.invalid>"
REFUSAL_EXIT = 3
STREAM_EPOCH = 1_700_000_000
REQUESTED_MODEL = "alias-requested"
RAN_MODEL = "stream-ran-model"
SCOPE_CAP = 1024
DEFAULT_GRACE = 120

# Id form from CONTRACT.md §Dispatch: <UTC stamp>-<stage>-<harness>-<6 hex>
ID_RE = re.compile(
    r"^\d{8}T\d{6}Z-"
    r"(plan|tests|check-tests|code|review|adjudicate)-"
    r"(claude|codex|grok)-[0-9a-f]{6}$"
)

RECORD_FIELDS = (
    "id", "lane", "stage", "unit", "lineage", "follows", "answers", "job",
    "role", "dispatched_by", "at", "snapshot", "harness", "model",
    "session", "brief", "caps", "overrides", "attempts", "result",
    "status",
)

# A subprocess harness. Records argv/cwd/env/stdin/prompt, optionally
# writes a harness-shaped stream, optionally mutates the snapshot.
# The start-witness path is interpolated at install time so a launcher
# that unsets TASK_LAUNCH_WITNESS still leaves evidence it ran.
# No network. No clock read — stream epochs come from the environment.
_FAKE_CLI = (Path(__file__).parent / "fixtures/harness_cli.py.in").read_text(encoding="utf-8")


class FakeClock:
    """Injected monotonic/sleep seam. Tests assign these onto launch.py."""

    def __init__(self, start=0.0):
        self.now = float(start)
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        seconds = float(seconds)
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.0)


def plant_bytes(path, mutate, *, expect="edit", recognisable=None) -> bytes:
    """Rewrite ``path`` through ``mutate``, proving the fault landed intact."""
    path = Path(path)
    before = path.read_bytes()
    if not before:
        raise PlantFailed(f"fixture {path} was already empty before planting")
    after = mutate(before)
    if not isinstance(after, (bytes, bytearray)):
        raise PlantFailed(f"mutate() returned {type(after).__name__}, not bytes")
    after = bytes(after)
    if after == before:
        raise PlantFailed(f"plant changed nothing in {path}")
    if not after:
        raise PlantFailed(f"plant emptied {path}")
    if expect == "edit" and len(after) != len(before):
        raise PlantFailed(f"plant claimed an in-place edit of {path}")
    if expect == "shrink" and len(after) >= len(before):
        raise PlantFailed(f"plant claimed to shrink {path}")
    if expect == "grow" and len(after) <= len(before):
        raise PlantFailed(f"plant claimed to grow {path}")
    if expect not in ("edit", "shrink", "grow"):
        raise PlantFailed(f"unknown expect={expect!r}")
    if recognisable is not None and not recognisable(after):
        raise PlantFailed(f"plant left {path} unrecognisable")
    kit_plant_bytes(path, after)
    return before


def _absent_launch():
    """Empty-stub stand-in: main returns 0 and writes nothing."""
    mod = types.ModuleType("task_launch_absent")

    def main(argv=None):
        return 0

    mod.main = main
    mod.JOBS_PATH = None
    mod.monotonic = time.monotonic
    mod.sleep = time.sleep
    return mod


def _absent_record():
    """Empty-stub stand-in: build echoes, validate is a no-op."""
    mod = types.ModuleType("task_record_absent")

    def build(payload=None, **kwargs):
        if payload is None:
            payload = kwargs
        if isinstance(payload, dict):
            return dict(payload)
        return {}

    def validate(rec):
        return rec

    mod.build = build
    mod.validate = validate
    mod.FIELDS = ()
    return mod


def strip_job_caps(test, job):
    """Remove ``caps`` from one job in the fixture catalog, so the layers
    below the job can be read. Every job in the real catalogue declares
    its caps, so the resolution order's lower layers are reached by
    planting their condition, never by finding a job that happens to lack
    one."""
    test.assertIsInstance(test.world, World)
    path = test.jobs_file
    before = path.read_text(encoding="utf-8")
    jobs = json.loads(before)
    test.assertIsInstance(jobs, dict)
    test.assertIn(job, jobs)
    test.assertIsInstance(jobs[job].get("caps"), dict,
                          f"{job} carried no caps to strip; the catalogue changed under this test")
    spec = {k: v for k, v in jobs[job].items() if k != "caps"}
    jobs[job] = spec
    content = json.dumps(jobs, indent=2) + "\n"
    test.assertIn(job, content)
    test.world.plant_text(path, content, recognisable=f'"{job}"')
    landed = json.loads(path.read_text(encoding="utf-8"))
    test.assertNotIn("caps", landed[job])
    test.ref = test._commit(f"strip {job} caps")


def require_module(test, name):
    """Load ``ops/devlane/task/<name>.py``, or an empty stub if it is absent.

    A missing file is not the red. The red is the test's contracted
    assertion against a module that does not yet implement the
    behaviour. An empty file on disk is wired the same way: missing
    ``main`` / ``build`` / ``validate`` become no-ops so the assertion
    the test is for is the one that fails.
    """
    path = APP / f"{name}.py"
    if not path.is_file():
        if name == "launch":
            return _absent_launch()
        if name == "record":
            return _absent_record()
        test.fail(f"{name}.py is not present at {path}")
    try:
        module = support.load(name)
    except Exception as exc:
        test.fail(
            f"{name}.py exists but did not load: "
            f"{type(exc).__name__}: {exc}"
        )
    if name == "launch" and not hasattr(module, "main"):
        module.main = lambda argv=None: 0
    if name == "record":
        if not hasattr(module, "build"):
            def _echo(payload=None, **kwargs):
                if payload is None:
                    payload = kwargs
                return dict(payload) if isinstance(payload, dict) else {}
            module.build = _echo
        if not hasattr(module, "validate"):
            module.validate = lambda rec: rec
        if not hasattr(module, "FIELDS"):
            module.FIELDS = ()
    return module


def load_path(test, path, name):
    path = Path(path)
    test.assertTrue(path.is_file(), f"required module is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    test.assertIsNotNone(spec, f"could not create an import spec for {path}")
    test.assertIsNotNone(spec.loader, f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_env(home: Path) -> dict:
    return hermetic_env(home)


def resolve_record_paths(rec):
    """A record on disk carries the two harness encodings of its snapshot
    path as ``<snapshot-slug>`` and ``<snapshot-encoded>`` (the launcher's
    ``_portable_paths``; the term wall refuses the encoded account name).
    Mirror of the launcher's ``_absolute_paths`` for tests that read the
    record file directly and then open the stream it names."""
    snapshot = str((rec.get("snapshot") or {}).get("root") or "")
    if not snapshot.startswith("/"):
        return rec
    subs = [("<snapshot-slug>", re.sub(r"[/.]", "-", snapshot)),
            ("<snapshot-encoded>", urllib.parse.quote(snapshot, safe=""))]

    def walk(value):
        if isinstance(value, str):
            for old, new in subs:
                if old in value:
                    value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [walk(x) for x in value]
        if isinstance(value, dict):
            return {k: walk(x) for k, x in value.items()}
        return value

    return walk(rec)


def claude_slug(cwd: str) -> str:
    return cwd.replace("/", "-").replace(".", "-")


def residual_entries(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [ln for ln in value.splitlines() if ln.strip()]
    return list(value)


def changed_entries(value):
    return residual_entries(value)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pid_is_alive(pid: int) -> bool:
    # A group SIGKILL can leave a descendant as a zombie until the host's
    # init process reaps it.  ``kill(pid, 0)`` still succeeds for zombies,
    # even though they cannot execute and therefore are not survivors of the
    # isolation boundary.  Check procfs first so the process-group assertion
    # measures live workers rather than the reaping behaviour of PID 1 (which
    # is notably delayed in some CI containers).
    stat = Path(f"/proc/{pid}/stat")
    try:
        if stat.read_text(encoding="utf-8").split()[2] == "Z":
            return False
    except (FileNotFoundError, IndexError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_if_alive(pid) -> None:
    if not pid:
        return
    with contextlib.suppress(OSError, ProcessLookupError, ValueError):
        os.kill(int(pid), 9)


class _TempLaunch(unittest.TestCase):
    """Throwaway repo on branch ``work``, isolated HOME, fake CLIs."""

    def setUp(self):
        self.world = World(prefix="dispatch-launch-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.home = self.world.home
        self.repo = self.world.cwd
        self.bin = self.world.bin
        self.jobs_root = self.world.root / "jobs"
        self.jobs_root.mkdir()
        os.environ.update({
            "WF_AGENT": AGENT,
            "GRAFT_INSTALL": "",
            "DISPATCH_JOBS": str(self.jobs_root),
            "TASK_LAUNCH_STREAM_EPOCH": str(STREAM_EPOCH),
            "TASK_LAUNCH_WRITE_STREAM": "1",
            "TASK_LAUNCH_STDOUT": "envelope",
            "TASK_LAUNCH_VERDICT": "approve",
        })

        self.env = self.world.env()
        self._git("init", "-b", "work")
        self._git("config", "user.name", "launch-test")
        self._git("config", "user.email", "launch-test@example.test")
        self._git("config", "commit.gpgsign", "false")

        jobs = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        jobs["withheld-whole"] = {
            "adapter": "harness",
            "deliverable": "fixture for history-vs-withheld",
            "role": "read",
            "snapshot": "whole",
            "withheld": ["secret.py"],
            "prompt": "Read {ref}. Aim at: {scope}",
            "constraints": ["read only"],
        }
        jobs["fileset-job"] = {
            "adapter": "harness",
            "deliverable": "fixture for mode-unavailable",
            "role": "read",
            "snapshot": "fileset",
            "prompt": "Read {ref}. Aim at: {scope}",
            "constraints": ["read only"],
        }
        jobs["needs-into"] = {
            "adapter": "harness",
            "deliverable": "fixture for template into/base/diff",
            "role": "read",
            "snapshot": "whole",
            "prompt": (
                "into={into} base={base} diff={diff} ref={ref} "
                "Aim at: {scope}"
            ),
            "constraints": ["read only"],
        }
        jobs["needs-hole"] = {
            "adapter": "harness",
            "deliverable": "fixture for a missing template value",
            "role": "read",
            "snapshot": "whole",
            "prompt": "this names {not_a_slot} and {scope}",
            "constraints": ["read only"],
        }
        self.jobs_file = self.repo / ".dev" / "app" / "task" / "jobs.json"
        self.world.plant_text(
            self.jobs_file, json.dumps(jobs, indent=2) + "\n",
        )
        self._write("alpha.py", "alpha v1\n")
        self._write("README.md", "fixture tree\n")
        self.root_sha = self._commit("root")
        self._write("alpha.py", "alpha v2\n")
        self.mid_sha = self._commit("mid")
        self._write("alpha.py", "alpha v3\n")
        self.ref = self._commit("tip")
        self.lineage = "work"

        self._git("checkout", "-b", "side", self.root_sha)
        self._write("side.py", "off the lineage\n")
        self.side_sha = self._commit("side")
        self._git("checkout", "work")

        self.start_witness = self.home / "started.json"
        self.witness = self.home / "witness.json"
        self.grandchild = self.home / "grandchild.pid"
        os.environ["TASK_LAUNCH_WITNESS"] = str(self.witness)
        os.environ.pop("TASK_LAUNCH_DONE", None)
        os.environ.pop("TASK_LAUNCH_SLEEP", None)
        os.environ.pop("TASK_LAUNCH_IGNORE_SESSION", None)
        os.environ.pop("TASK_LAUNCH_STREAM_ID", None)
        os.environ.pop("TASK_LAUNCH_COMMIT", None)
        os.environ.pop("TASK_LAUNCH_EDIT", None)
        os.environ.pop("TASK_LAUNCH_ORPHAN", None)
        os.environ.pop("TASK_LAUNCH_HEAD_COMMIT", None)
        os.environ.pop("TASK_LAUNCH_EXIT", None)
        os.environ.pop("TASK_LAUNCH_OVER_OUT", None)
        os.environ.pop("TASK_LAUNCH_GRANDCHILD", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_FIELD", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_SUBTYPE", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_IS_ERROR", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_INVALID", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_NARRATION", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_RESULT", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_DECOY", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_DECOY_FIRST", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_USAGE", None)
        os.environ.pop("TASK_LAUNCH_WRAPPER_AGENT_NOTE", None)
        os.environ.pop("TASK_LAUNCH_NOTE", None)
        os.environ.pop("TASK_LAUNCH_STATUS", None)
        os.environ.pop("TASK_LAUNCH_ENVELOPE_COMMIT", None)
        os.environ.pop("TASK_LAUNCH_GROK_USAGE", None)
        os.environ.pop("TASK_LAUNCH_COMMIT_MSG", None)
        os.environ.pop("TASK_LAUNCH_RENEW_AUTH", None)

        self.world.plant_text(self.home / ".codex" / "auth.json", "{}\n")
        self.world.plant_text(self.home / ".grok" / "auth.json", "{}\n")

        self._install_cli("claude")
        self._install_cli("codex")
        self._install_cli("grok")

        # Loaded on first use so a missing launch.py is an empty stub
        # whose main is a no-op — the test method's assertion is the red.
        self.launch = None
        self._saved_jobs_path = None

    def load_launch(self):
        if self.launch is not None:
            return self.launch
        self.launch = require_module(self, "launch")
        if hasattr(self.launch, "JOBS_PATH"):
            self._saved_jobs_path = self.launch.JOBS_PATH
            self.launch.JOBS_PATH = self.jobs_file
        return self.launch

    def tearDown(self):
        if self.start_witness.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
                info = json.loads(
                    self.start_witness.read_text(encoding="utf-8")
                )
                kill_if_alive(info.get("pid"))
                kill_if_alive(info.get("pgid"))
        if self.grandchild.is_file():
            with contextlib.suppress(OSError, ValueError):
                kill_if_alive(
                    self.grandchild.read_text(encoding="utf-8").strip()
                )
        if self.launch is not None and self._saved_jobs_path is not None:
            self.launch.JOBS_PATH = self._saved_jobs_path
        self.world.close()

    def _git(self, *args, repo=None):
        r = run(
            ["git", *args], cwd=repo or self.repo, env=self.env,
            timeout=60,
        )
        if r.exit != 0:
            raise RuntimeError(
                f"git {args} failed ({r.exit}): {r.stderr}")
        return r

    def _write(self, rel, content):
        p = self.repo / rel
        return self.world.plant_text(p, content)

    def _commit(self, msg):
        self._git("add", "-A")
        self._git("commit", "-m", msg)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _install_cli(self, name):
        script = (
            _FAKE_CLI
            .replace("@@START_WITNESS@@", json.dumps(str(self.start_witness)))
            .replace("@@RAN_MODEL@@", json.dumps(RAN_MODEL))
            .replace("@@STORES_PATH@@", json.dumps(str(STORES_PATH)))
        )
        return self.world.tools.plant(name, script=script)

    def set_grace(self, seconds):
        """External seam: DISPATCH_STREAM_GRACE, default 120 seconds."""
        os.environ["DISPATCH_STREAM_GRACE"] = str(seconds)

    def attach_clock(self):
        """Injected clock/poller seam: launch.monotonic and launch.sleep."""
        launch = self.load_launch()
        clock = FakeClock()
        launch.monotonic = clock.monotonic
        launch.sleep = clock.sleep
        return clock

    def force_id(self, job_id):
        launch = self.load_launch()
        launch.mint_id = lambda *a, **k: job_id
        return job_id

    def fixture_assignment(self):
        """An assignment in this world's store, and the dispatcher that drafted it.

        The launcher refuses a write job that names no assignment (LD-116),
        so a row that launches one without --for is given one here. The
        dispatcher is bound once per test; each launch gets its own draft.
        """
        key = getattr(self, "_fixture_dispatcher", None)
        if key is None:
            key = str(uuid.uuid4())
            bound = CoordinationCore.core(self, "bind", "--roles", "lead,dispatcher", session=key)
            self.assertEqual(bound.returncode, 0, f"fixture dispatcher bind refused: {bound.stderr!r}")
            self._fixture_dispatcher = key
        drafted = CoordinationCore.core(
            self, "draft", "a fixture write job", "--outcome", "the launcher binds its worker",
            session=key)
        self.assertEqual(drafted.returncode, 0, f"fixture draft refused: {drafted.stderr!r}")
        return key, drafted.stdout.split()[-1]

    def run_main(self, argv, *, cwd=None, assign=True):
        argv = list(argv)
        restore = {}
        if assign and argv and "--for" not in argv:
            try:
                spec = json.loads(self.jobs_file.read_text(encoding="utf-8")).get(argv[0]) or {}
            except (OSError, ValueError):
                spec = {}
            if spec.get("role") == "write":
                key, aid = self.fixture_assignment()
                argv.extend(["--for", aid])
                for name, value in ((SESSION_KEY, key),
                                    ("COORDINATION_CAPABILITY", hashlib.sha256(key.encode()).hexdigest())):
                    restore[name] = os.environ.get(name)
                    os.environ[name] = value
        try:
            return self._run_main(argv, cwd=cwd)
        finally:
            for name, value in restore.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def _run_main(self, argv, *, cwd=None):
        launch = self.load_launch()
        out, err = io.StringIO(), io.StringIO()
        cwd = cwd or self.repo
        try:
            old = os.getcwd()
        except FileNotFoundError:
            # A predecessor test left the process inside a directory it then
            # removed (2026-09-12: every test of the next module erroring at
            # getcwd in suite order). Stand on the repository root instead.
            old = str(Path(__file__).resolve().parents[4])
            os.chdir(old)
        try:
            os.chdir(cwd)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = launch.main(list(argv))
                except SystemExit as exc:
                    code = int(exc.code) if exc.code is not None else 0
        finally:
            os.chdir(old)
        if code is None:
            code = 0
        return int(code), out.getvalue(), err.getvalue()

    def argv_for(
        self,
        job="plan",
        *,
        harness="grok",
        model=REQUESTED_MODEL,
        ref=None,
        lineage=None,
        stage=None,
        scope="pin the launcher",
        extra=(),
    ):
        argv = [job, "--harness", harness]
        if model is not None:
            argv.extend(["--model", model])
        argv.extend(["--ref", ref or self.ref])
        if lineage is not None:
            argv.extend(["--lineage", lineage])
        if stage is not None:
            argv.extend(["--stage", stage])
        if scope is not None:
            argv.extend(["--scope", scope])
        argv.extend(list(extra))
        return argv

    def dispatch(self, argv=None, *, assign=True, **kwargs):
        if argv is None:
            argv = self.argv_for(**kwargs)
        # A write envelope must declare reuse (launch.py, grant 158); the
        # fixture default declares none unless a test plants its own note.
        planted = False
        if "TASK_LAUNCH_NOTE" not in os.environ and argv:
            try:
                spec = json.loads(self.jobs_file.read_text(encoding="utf-8")).get(argv[0]) or {}
            except (OSError, ValueError):
                spec = {}
            if spec.get("role") == "write":
                os.environ["TASK_LAUNCH_NOTE"] = "reused: nothing applied (fixture default)"
                planted = True
        try:
            return self.run_main(argv, assign=assign)
        finally:
            if planted:
                os.environ.pop("TASK_LAUNCH_NOTE", None)

    def combined(self, out, err):
        return (out or "") + (err or "")

    def assert_refusal(self, code, out, err, *, ident, phrases, named=()):
        text = self.combined(out, err)
        self.assertEqual(
            code, REFUSAL_EXIT,
            f"{ident} refuses with exit 3, got {code}: {text!r}",
        )
        lower = text.lower()
        self.assertIn("expected", lower, f"{ident} names expected: {text!r}")
        self.assertIn("found", lower, f"{ident} names found: {text!r}")
        self.assertIn("satisfy", lower, f"{ident} names satisfy: {text!r}")
        self.assertRegex(
            text,
            rf"{re.escape(ident)}:\s*expected",
            f"{ident}: expected is the refusal class token: {text!r}",
        )
        for phrase in phrases:
            self.assertIn(
                phrase.lower(), lower,
                f"{ident} text must name {phrase!r}: {text!r}",
            )
        for item in named:
            self.assertIn(
                str(item), text,
                f"{ident} names {item!r} (session id, path, or value): "
                f"{text!r}",
            )
        return text

    def assert_wire_refusal(self, code, out, err, *, ident, named=(), phrases=()):
        """The common refusal wire, followed by the family's content checks."""
        token = ident.lower().replace("_", "-")
        # Unwrap the CLI's declared label before checking the common wire.
        wire = (err or "").removeprefix("launch.py: refusal: ")
        result = Result((), code, out or "", wire, 0.0, str(self.repo))
        case = RefusalCase(ident, (), token, exit=REFUSAL_EXIT, named=tuple(map(str, named)))
        line = refusal_wire(case, result).line
        for phrase in phrases:
            self.assertIn(phrase.lower(), line.lower(), f"{ident} names {phrase!r}: {line!r}")
        return line

    def assert_not_started(self):
        self.assertFalse(
            self.start_witness.is_file(),
            "refusal must not start a child; baked start-witness exists: "
            + (self.start_witness.read_text(encoding="utf-8")
               if self.start_witness.is_file() else ""),
        )

    def job_dirs(self):
        if not self.jobs_root.exists():
            return []
        return [p for p in self.jobs_root.iterdir() if p.is_dir()]

    def record_files(self):
        root = self.repo / ".dev" / "records" / "dispatches"
        if not root.exists():
            return []
        return sorted(root.glob("*.json"))

    def the_job_dir(self):
        dirs = self.job_dirs()
        self.assertEqual(
            len(dirs), 1,
            f"expected exactly one job dir, found {len(dirs)}: {dirs}",
        )
        return dirs[0]

    def the_record_path(self):
        files = self.record_files()
        self.assertEqual(
            len(files), 1,
            f"expected exactly one record file, found {len(files)}: {files}",
        )
        return files[0]

    def read_record(self, path=None):
        path = path or self.the_record_path()
        self.assertTrue(path.is_file(), f"record missing: {path}")
        body = path.read_text(encoding="utf-8")
        self.assertTrue(body.strip(), "record file is empty")
        data = json.loads(body)
        self.assertIsInstance(data, dict)
        return data

    def read_start_witness(self):
        self.assertTrue(
            self.start_witness.is_file(),
            "the harness CLI must have been launched (start-witness missing)",
        )
        data = json.loads(self.start_witness.read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("argv"), list)
        self.assertTrue(data["argv"], "recorded argv is empty")
        return data

    def read_witness(self):
        self.assertTrue(
            self.witness.is_file(),
            "the harness CLI must have been launched (witness missing)",
        )
        data = json.loads(self.witness.read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("argv"), list)
        self.assertTrue(data["argv"], "recorded argv is empty")
        return data

    def launch_ok(self, argv=None, **kwargs):
        code, out, err = self.dispatch(argv, **kwargs)
        text = self.combined(out, err)
        self.assertNotEqual(
            code, REFUSAL_EXIT,
            f"happy path must not refuse (exit 3): {text!r}",
        )
        self.assertEqual(code, 0, f"happy path exits 0, got {code}: {text!r}")
        rec = self.read_record()
        self.assertEqual(rec.get("status"), "closed")
        return rec, self.read_witness(), out, err

    def snapshot_of(self, rec=None):
        rec = rec or self.read_record()
        snap = rec.get("snapshot") if isinstance(rec.get("snapshot"), dict) else {}
        root = snap.get("root")
        self.assertIsInstance(root, str)
        self.assertTrue(root.strip(), "snapshot.root must name a directory")
        path = Path(root)
        self.assertTrue(path.is_dir(), f"snapshot.root is not a dir: {root!r}")
        return path

    def refs_map(self, repo=None):
        raw = self._git(
            "for-each-ref", "--format=%(refname) %(objectname)",
            repo=repo,
        ).stdout.splitlines()
        out = {}
        for line in raw:
            if not line.strip():
                continue
            name, sha = line.split(" ", 1)
            out[name] = sha
        self.assertTrue(out, "fixture must have at least one ref")
        return out

    def fetch_head_bytes(self):
        p = self.repo / ".git" / "FETCH_HEAD"
        if not p.exists():
            return None
        return p.read_bytes()

    def porcelain(self, repo=None):
        return self._git(
            "status", "--porcelain=v1", "-uall", repo=repo,
        ).stdout

    def index_blob(self, repo=None):
        return self._git("ls-files", "-s", repo=repo).stdout

    def worktree_bytes(self, repo=None):
        """Index + porcelain + untracked file bytes, for exact delta."""
        root = Path(repo or self.repo)
        return {
            "porcelain": self.porcelain(repo=root),
            "index": self.index_blob(repo=root),
        }

    def worktree_paths(self, repo=None):
        raw = self._git("worktree", "list", "--porcelain", repo=repo).stdout
        paths = []
        for line in raw.splitlines():
            if line.startswith("worktree "):
                paths.append(line.split(" ", 1)[1])
        self.assertTrue(paths, "worktree list must name the invoking repo")
        return paths

    def git_dir_mentions(self, snapshot: Path, needle: bytes) -> list:
        git = snapshot / ".git"
        hits = []
        if not git.exists():
            return hits
        for p in git.rglob("*"):
            if p.is_symlink():
                try:
                    target = os.fsencode(os.readlink(p))
                except OSError:
                    continue
                if needle in target:
                    hits.append(str(p.relative_to(git)) + " (symlink)")
                continue
            if not p.is_file():
                continue
            try:
                body = p.read_bytes()
            except OSError:
                continue
            if needle in body:
                hits.append(str(p.relative_to(git)))
        return hits

    def assert_objects_not_shared(self, snapshot: Path):
        snap_obj = snapshot / ".git" / "objects"
        src_obj = self.repo / ".git" / "objects"
        self.assertTrue(snap_obj.exists(), "snapshot has an object store")
        self.assertFalse(
            snap_obj.is_symlink(),
            ".git/objects must not be a symlink to the invoking store",
        )
        git = snapshot / ".git"
        self.assertFalse(
            git.is_file(),
            "snapshot .git is a file (a worktree pointer), not a repo",
        )
        if snap_obj.is_dir() and src_obj.is_dir():
            self.assertFalse(
                os.path.samefile(snap_obj, src_obj),
                "snapshot objects dir is the invoking objects dir",
            )
        src_inodes = set()
        for p in src_obj.rglob("*"):
            if p.is_symlink() or not p.is_file():
                continue
            st = p.stat()
            src_inodes.add((st.st_dev, st.st_ino))
        for p in snap_obj.rglob("*"):
            self.assertFalse(
                p.is_symlink(),
                f"snapshot object is a symlink: {p}",
            )
            if not p.is_file():
                continue
            st = p.stat()
            self.assertNotIn(
                (st.st_dev, st.st_ino), src_inodes,
                f"hardlinked object {p} shares inode with the invoking store",
            )

    def dispatch_reflog(self, job_id):
        r = run(
            ["git", "reflog", "show", f"refs/dispatch/{job_id}"],
            cwd=self.repo, env=self.env, timeout=60,
        )
        return r.stdout

    def dead_pid(self):
        result = run(
            [sys.executable, "-c", "import os; print(os.getpid())"],
            cwd=self.repo, env=self.world.env(), timeout=60, expect=0,
        )
        pid = int(result.stdout.strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        return pid

    def plant_new_file(self, path, content, *, must_contain=None):
        path = Path(path)
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.assertTrue(data, "planted file must not be empty")
        self.world.plant_bytes(path, data)
        landed = path.read_bytes()
        self.assertEqual(landed, data, "plant did not reach disk")
        self.assertGreater(len(landed), 0)
        if must_contain is not None:
            needle = (must_contain.encode("utf-8")
                      if isinstance(must_contain, str) else must_contain)
            self.assertIn(needle, landed, "plant missing its marker")
        return landed


# The coordination core, reached the way the launcher reaches it: a lever
# run as a subprocess whose wire is read, never an import. An import would
# put the core's module state and its store inside every case that touches
# it.
CORE_LEVER = REPO / "ops" / "conductor" / "tools" / "coordination.py"
SESSION_KEY = "CLAUDE_SESSION_ID"
FIXTURE_DISPATCHER = "Launch Test Dispatcher <noreply@example.invalid>"


class CoordinationCore:
    """Drive the coordination core against the world's own store.

    Mixed into a `_TempLaunch` subclass. The store lives under the world's
    XDG_STATE_HOME, so nothing here touches the host's.
    """

    def core(self, *args, session=None, agent=FIXTURE_DISPATCHER, profile=None):
        env = dict(os.environ)
        lever = CORE_LEVER
        env["WF_AGENT"] = agent
        if session:
            env[SESSION_KEY] = session
            env["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        else:
            env.pop(SESSION_KEY, None)
            env.pop("COORDINATION_CAPABILITY", None)
        if profile:
            env["AGENTSCIENT_HOOK_PROFILE"] = str(profile)
            # A dispatched worker uses the core from its issued runtime,
            # just like its guard and acting MCP server. The installation's
            # core deliberately refuses a different source's profile.
            lever = Path(profile).parent.parent / "runtime/ops/conductor/tools/coordination.py"
        return run(
            [sys.executable, str(lever), *args],
            timeout=60,
            cwd=str(REPO), env=env)

    def dispatcher_session(self):
        """A bound dispatcher in this world's store, and its session id.

        The first bind is the bootstrap: the core makes the first
        participant a lead, and a lead may grant itself the dispatcher role
        it needs to mint a worker.
        """
        key = str(uuid.uuid4())
        done = self.core("bind", "--roles", "lead,dispatcher", session=key)
        self.assertEqual(done.returncode, 0,
                         f"bootstrap bind refused: {done.stderr!r}")
        os.environ["COORDINATION_CAPABILITY"] = hashlib.sha256(key.encode()).hexdigest()
        return key

    def assignment(self, session, title="a fixture assignment",
                   outcome="the launcher binds a worker for it"):
        drafted = self.core("draft", title, "--outcome", outcome,
                            session=session)
        self.assertEqual(drafted.returncode, 0,
                         f"draft refused: {drafted.stderr!r}")
        return drafted.stdout.split()[-1]

    def participants(self, session):
        shown = self.core("participants", session=session)
        self.assertEqual(shown.returncode, 0,
                         f"participants refused: {shown.stderr!r}")
        return shown.stdout
