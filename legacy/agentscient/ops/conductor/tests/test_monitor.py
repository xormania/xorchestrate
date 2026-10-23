"""Monitor-lever acceptance tests, written from the contract.

Contracts (only): `ops/conductor/monitor-lever.md` and the inherited
`ops/conductor/instrument-conventions.md`. The example registry
`ops/conductor/monitor.example.json` is the default-config pin. The
tool `ops/conductor/tools/monitor.py` is the subject and is not read
here. Tests invoke it as a subprocess with synthetic stores and a
stub `gh` first on PATH.

The live dispatch store, operator credentials, and this repo must
stay untouched. Tests plant sources under the fixture scratch and
assert those owned bytes; they do not enumerate ambient dispatch
directories. No real GitHub, Codex, or dispatch store is opened.

World owns HOME, the XDG roots and TMPDIR; every subprocess goes
through run with a timeout. A fake gh is planted through world.tools.
When the tool file is absent World.stage plants a no-op stand-in
(exit 0, no output) so each assertion fails on the contract rather
than a missing-file suite error. Absent-entrypoint red is the
presence test alone.

The gh mock is executed as its own witness before any test treats
argv/JSON from it as evidence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ops.testkit import Result, World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "monitor.py"
EXAMPLE = REPO / "ops" / "conductor" / "monitor.example.json"
LANECONF = REPO / "ops" / "conductor" / "tools" / "laneconf.py"
INSTRUMENT = REPO / "ops" / "conductor" / "tools" / "instrument.py"
SIBLINGS = ("laneconf.py", "instrument.py", "dispatch_observation.py")

AGENT = "Monitor Tester <monitor-tester@example.invalid>"
BAD_AGENT = "JustAName"
CONSUMER = "operator"
OTHER = "other-conductor"
HARNESS = "monitor-harness-aa41"
RUN_CAP = 20
LOOP_CAP = 8

NOW = "2026-09-04T12:00:00Z"
NOW_30 = "2026-09-04T12:00:30Z"
NOW_60 = "2026-09-04T12:01:00Z"
NOW_90 = "2026-09-04T12:01:30Z"
NOW_400 = "2026-09-04T12:06:40Z"
LAUNCHED = "2026-09-04T11:00:00Z"
CLOSED = "2026-09-04T11:12:00Z"
LAUNCHED_2 = "2026-09-04T11:20:00Z"

NOOP_ID = "20260904T120000Z-code-grok-aa4101"
DONE_ID = "20260904T120100Z-code-grok-aa4102"
TRIP_ID = "20260904T120200Z-review-grok-aa4103"
DEAD_ID = "20260904T120300Z-code-grok-aa4104"
SETTLE_ID = "20260904T120400Z-code-grok-aa4105"
RUN_ID = "20260904T120500Z-code-grok-aa4106"
MISS_ID = "20260904T120600Z-code-grok-aa4107"
DECOY_ID = "20260904T120700Z-code-grok-aa4108"
DIED_ID = "20260904T120800Z-code-grok-aa4109"
ZERO_ID = "20260904T120900Z-code-grok-aa4110"
BOOL_ID = "20260904T121000Z-code-grok-aa4111"
UNREAD_ID = "20260904T121100Z-code-grok-aa4112"
SPEND_ID = "20260904T121200Z-code-grok-aa4113"
GEN_ID = "20260904T121300Z-code-grok-aa4114"
TOKEN_ID = "20260904T121400Z-code-grok-aa4115"
CWD_ID = "20260904T121500Z-code-grok-aa4116"
SESS_ID = "20260904T121600Z-code-grok-aa4117"
OTHER_TOKEN = "monitor-other-bb42"

RESET_1 = 1700000000
RESET_2 = 1700003600
GH_SECRET = "GH_RAW_STDERR_aa41_must_not_leak"

OMIT = object()

EVENT_KEYS = (
    "id",
    "route",
    "consumer",
    "condition",
    "monitor",
    "subject",
    "state",
    "summary",
    "evidence",
    "observed_at",
    "delivery",
)


def _tree_files(root: Path) -> tuple[str, ...]:
    if not root.exists():
        return ()
    names: list[str] = []
    for path in root.rglob("*"):
        if path.is_file():
            names.append(str(path.relative_to(root)))
    return tuple(sorted(names))


class _MonitorFixture(unittest.TestCase):
    """Scratch HOME, staged tool, synthetic sources, stub gh on PATH."""

    def setUp(self):
        self._example_before = EXAMPLE.read_bytes()
        self._tool_before = TOOL.is_file()
        self.world = World(prefix="monitor-lever-")
        self.addCleanup(self.world.close)
        self.root = self.world.root
        self.home = self.world.home
        self.xdg = self.world.xdg_state
        self.scratch = self.root / "scratch"
        self.bin = self.world.bin
        self.conf_dir = self.root / "conf"
        self.cwd = self.world.cwd
        self.state = self.root / "state"
        self.store = self.conf_dir / "jobs"
        self.cwd_store = self.cwd / "jobs"
        self.rollout = self.conf_dir / "rollout.jsonl"
        for path in (self.scratch, self.conf_dir, self.state):
            path.mkdir()
        self.gh = self.bin / "gh"
        self.argv_log = self.world.tools.log_path("gh")
        self.counter = self.scratch / "gh-calls"
        self.sleep_marker = self.scratch / "gh-sleep-started"
        self.worker_cwd = self.scratch / "worker-cwd"
        self.worker_cwd.mkdir()
        self.staged = self._stage_tool_bytes()
        self.config = self.conf_dir / "monitor.json"
        self._workers: list[subprocess.Popen] = []
        self.assertEqual(
            self.world.tools.calls("gh"), [], "plant: argv log absent"
        )
        self.assertFalse(self.gh.exists(), "plant: gh stub not planted yet")
        self.assertFalse(self.store.exists(), "plant: store starts absent")
        self.plant_lane_conf()

    def tearDown(self):
        for proc in self._workers:
            if proc.poll() is None:
                self.world.reap(proc)
        self.assertEqual(
            EXAMPLE.read_bytes(),
            self._example_before,
            "example registry mutated",
        )
        self.assertEqual(TOOL.is_file(), self._tool_before)

    def _stage_tool_bytes(self) -> Path:
        siblings = SIBLINGS if TOOL.is_file() else ()
        staged = self.world.stage(TOOL, "monitor.py", siblings=siblings)
        if TOOL.is_file():
            self.assertEqual(
                staged.read_bytes(),
                TOOL.read_bytes(),
                "copy-tool-bytes did not land intact",
            )
        self.assertTrue(staged.is_file())
        return staged

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def owned_source_bytes(self) -> dict[str, bytes]:
        """Bytes of planted sources under this fixture, never ambient stores."""
        found: dict[str, bytes] = {}
        for root in (self.store, self.cwd_store, self.conf_dir):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    found[str(path.resolve())] = path.read_bytes()
        return found

    def assert_owned_sources(self, before: dict[str, bytes]) -> None:
        after = self.owned_source_bytes()
        for key, data in before.items():
            self.assertIn(key, after, f"owned source disappeared: {key}")
            self.assertEqual(after[key], data, f"owned source mutated: {key}")

    def path_without_gh(self) -> str:
        empty = self.world.path("no-gh")
        empty.mkdir(exist_ok=True)
        self.assertFalse((empty / "gh").exists(), "plant: empty PATH has gh")
        parts = [str(empty), str(self.bin)]
        for item in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
            if not item:
                continue
            if (Path(item) / "gh").exists():
                continue
            parts.append(item)
        path = os.pathsep.join(parts)
        which = shutil.which("gh", path=path)
        if which is not None:
            self.assertEqual(Path(which).resolve(), self.gh.resolve())
        return path

    def env(
        self,
        *,
        agent: str | None = None,
        extra: dict[str, str | None] | None = None,
    ) -> dict[str, str]:
        additions = {
            "PATH": self.path_without_gh(),
            "LANE_CONF": str(self.conf_path),
        }
        if agent is not None:
            additions["WF_AGENT"] = agent
        env = self.world.env(additions)
        if extra:
            for name, value in extra.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
        return env

    def plant_lane_conf(self, **overrides: object) -> Path:
        omit = tuple(overrides.pop("omit", ()) or ())
        keys = {
            "LANE_NAME": "lanex",
            "TRUNK_BRANCH": "trunktest",
            "RELEASE_BRANCH": "reltest",
            "REPO_SLUG": "org/proj",
            "CHECK_NAMES": "lane app",
            "WF_AGENT_DEFAULT": "Conf Agent <conf@example.invalid>",
            "LANE_AUTHOR": "Lane Owner <lane-owner@example.invalid>",
            "MODEL_GROK": "model-grok",
            "MODEL_CODEX": "model-codex",
            "MODEL_CLAUDE_READ": "model-claude-read",
            "MODEL_CLAUDE_PLAN": "model-claude-plan",
            "EFFORT_GROK": "high",
            "EFFORT_CODEX": "high",
            "EFFORT_CLAUDE": "high",
            "WALL_DEFAULT": "900",
            "SCOPE_CAP": "1024",
        }
        for name, value in overrides.items():
            keys[name] = str(value)
        for name in omit:
            keys.pop(name, None)
        path = self.scratch / "lane.conf"
        text = "".join(f"{name}='{value}'\n" for name, value in keys.items())
        self._plant_bytes(path, text.encode("utf-8"))
        self.conf_path = path
        return path

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = None,
        extra: dict[str, str | None] | None = None,
        cwd: Path | None = None,
        timeout: int | float | None = None,
        new_session: bool = False,
    ) -> Result:
        env = self.env(agent=agent, extra=extra)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        else:
            self.assertEqual(env.get("WF_AGENT"), agent)
        # run() always leads a new session; new_session is accepted for
        # callers that still name the flag.
        _ = new_session
        result = run(
            [sys.executable, str(self.staged), *argv],
            cwd=cwd or self.cwd,
            env=env,
            timeout=RUN_CAP if timeout is None else timeout,
        )
        return result

    def cfg_args(self, config: Path | None = None) -> list[str]:
        path = config if config is not None else self.config
        return ["--config", str(path), "--state", str(self.state)]

    def check(self, config: Path | None = None, **kw):
        path = config if config is not None else self.config
        return self.invoke(["--config", str(path), "check"], agent=None, **kw)

    def run_once(self, *, now: str = NOW, agent: str | None = AGENT, **kw):
        return self.invoke(
            [*self.cfg_args(), "run", "--once", "--now", now],
            agent=agent,
            **kw,
        )

    def digest(
        self,
        consumer: str = CONSUMER,
        *,
        limit: int | None = None,
        max_bytes: int | None = None,
        ack: bool = False,
        agent=OMIT,
        **kw,
    ):
        argv = [*self.cfg_args(), "digest", "--consumer", consumer]
        if limit is not None:
            argv.extend(["--limit", str(limit)])
        if max_bytes is not None:
            argv.extend(["--max-bytes", str(max_bytes)])
        if ack:
            argv.append("--ack")
        invoke_agent = (AGENT if ack else None) if agent is OMIT else agent
        return self.invoke(argv, agent=invoke_agent, **kw)

    def combined(self, proc) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}"

    def parse_json(self, proc, *, expect_exit: int = 0) -> object:
        self.assertEqual(
            proc.returncode,
            expect_exit,
            self.combined(proc),
        )
        text = proc.stdout or ""
        self.assertTrue(text.strip(), f"stdout empty: {self.combined(proc)}")
        self.assertNotIn("\x1b", text)
        self.assertNotIn(GH_SECRET, text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout is not JSON: {text!r} ({exc})")

    def assert_int_count(self, value, name: str) -> int:
        self.assertIsInstance(value, int, f"{name}={value!r}")
        self.assertNotIsInstance(value, bool, f"{name} is bool")
        self.assertGreaterEqual(value, 0, name)
        return value

    def check_ok(self, config: Path | None = None) -> dict:
        proc = self.check(config)
        data = self.parse_json(proc, expect_exit=0)
        self.assertIsInstance(data, dict, data)
        self.assertIs(data.get("valid"), True, data)
        for key in ("monitors", "schedules", "conditions", "routes"):
            self.assertIn(key, data, data)
            self.assert_int_count(data[key], key)
        return data

    def run_counts(self, **kw) -> tuple[Result, dict]:
        proc = self.run_once(**kw)
        data = self.parse_json(proc, expect_exit=0)
        self.assertIsInstance(data, dict, data)
        for key in ("observations", "events", "unavailable"):
            self.assertIn(key, data, data)
            self.assert_int_count(data[key], key)
        return proc, data

    def digest_data(self, **kw) -> tuple[Result, dict]:
        proc = self.digest(**kw)
        data = self.parse_json(proc, expect_exit=0)
        self.assertIsInstance(data, dict, data)
        self.assertIn("events", data, data)
        self.assertIn("more", data, data)
        self.assertIsInstance(data["events"], list, data)
        self.assertIn(data["more"], (True, False), data)
        self.assertIsInstance(data["more"], bool, data)
        return proc, data

    def assert_event_shape(self, event: dict) -> None:
        self.assertIsInstance(event, dict, event)
        for key in EVENT_KEYS:
            self.assertIn(key, event, event)
        summary = event["summary"]
        self.assertIsInstance(summary, str, event)
        self.assertLessEqual(len(summary.encode("utf-8")), 240, summary)
        self.assertNotIn("\x1b", summary)
        self.assertNotIn("\x00", summary)
        self.assertIn(event["state"], ("ok", "unknown", "unavailable"))
        evidence = event["evidence"]
        self.assertIsNotNone(evidence, event)
        if isinstance(evidence, str):
            self.assertNotIn(GH_SECRET, evidence)
            self.assertNotIn("\x1b", evidence)

    def assert_events_ordered(self, events: list) -> None:
        self.assertIsInstance(events, list)
        for event in events:
            self.assert_event_shape(event)
        ids = [event["id"] for event in events]
        self.assertEqual(len(ids), len(set(map(str, ids))), ids)

        def key(value):
            if isinstance(value, int) and not isinstance(value, bool):
                return (0, value)
            text = str(value)
            if text.isdigit():
                return (0, int(text))
            return (1, text)

        self.assertEqual(ids, sorted(ids, key=key), ids)

    def events_for_subject(self, events: list, rec_id: str) -> list:
        found = [event for event in events if rec_id in str(event.get("subject"))]
        self.assertTrue(found, f"no event for {rec_id}: {events}")
        return found

    def assert_subject_mentions(self, events: list, rec_id: str, token: str) -> None:
        blob = json.dumps(self.events_for_subject(events, rec_id))
        self.assertIn(token, blob, f"{rec_id} missing {token}: {blob}")

    def assert_subject_omits(self, events: list, rec_id: str, token: str) -> None:
        blob = json.dumps(self.events_for_subject(events, rec_id))
        self.assertNotIn(token, blob, f"{rec_id} unexpectedly has {token}: {blob}")

    def gh_call_count(self) -> int:
        if not self.counter.is_file():
            return len(self.gh_argvs())
        text = self.counter.read_text(encoding="utf-8").strip()
        self.assertTrue(text.isdigit(), text)
        return int(text)

    def assert_refusal(self, proc, class_name: str) -> None:
        err = proc.stderr or ""
        self.assertEqual(proc.returncode, 2, self.combined(proc))
        self.assertTrue(err.strip(), "refusal was silent")
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        self.assertTrue(
            lines[0].startswith(class_name + ":"),
            f"class {class_name!r} not on wire: {err!r}",
        )
        lower = err.lower()
        self.assertIn("expected", lower)
        self.assertIn("found", lower)
        self.assertIn("needed", lower)
        self.assertEqual((proc.stdout or "").strip(), "", proc.stdout)
        self.assertNotIn("\x1b", err)
        self.assertNotIn(GH_SECRET, err)

    def assert_state_empty(self) -> None:
        files = _tree_files(self.state)
        self.assertEqual(files, (), files)

    def plant_config(self, data: dict | str | bytes) -> Path:
        if isinstance(data, dict):
            raw = (json.dumps(data, indent=2) + "\n").encode("utf-8")
        elif isinstance(data, str):
            raw = data.encode("utf-8")
        else:
            raw = data
        self._plant_bytes(self.config, raw)
        self.assertTrue(self.config.is_file())
        return self.config

    def registry(self, **sections) -> dict:
        body = {
            "version": 1,
            "schedules": {"fast": {"enabled": True, "every_s": 1}},
            "monitors": {},
            "conditions": {},
            "routes": {},
        }
        body.update(sections)
        return body

    def dispatch_reg(
        self,
        ids: list[str],
        *,
        store: str = "./jobs",
        field: str = "status",
        op: str = "changed",
        threshold=None,
        cooldown_s: int = 0,
        consumer: str = CONSUMER,
        enabled: bool = True,
        extra_conditions: dict | None = None,
        extra_routes: dict | None = None,
    ) -> dict:
        routes = {
            "jobs": {
                "enabled": True,
                "condition": "job_change",
                "adapter": "queue",
                "consumer": consumer,
            }
        }
        if extra_routes:
            routes.update(extra_routes)
        conditions = {
            "job_change": {
                "enabled": True,
                "monitor": "jobs",
                "op": op,
                "field": field,
                "threshold": threshold,
                "cooldown_s": cooldown_s,
            }
        }
        if extra_conditions:
            conditions.update(extra_conditions)
        return self.registry(
            monitors={
                "jobs": {
                    "enabled": enabled,
                    "adapter": "dispatch",
                    "schedule": "fast",
                    "params": {"store": store, "ids": list(ids)},
                }
            },
            conditions=conditions,
            routes=routes,
        )

    def codex_reg(
        self,
        *,
        path: str = "./rollout.jsonl",
        max_bytes: int = 262144,
        field: str = "total",
        op: str = "gt",
        threshold=0,
        cooldown_s: int = 0,
    ) -> dict:
        return self.registry(
            monitors={
                "context": {
                    "enabled": True,
                    "adapter": "codex-usage",
                    "schedule": "fast",
                    "params": {"path": path, "max_bytes": max_bytes},
                }
            },
            conditions={
                "context_high": {
                    "enabled": True,
                    "monitor": "context",
                    "op": op,
                    "field": field,
                    "threshold": threshold,
                    "cooldown_s": cooldown_s,
                }
            },
            routes={
                "context": {
                    "enabled": True,
                    "condition": "context_high",
                    "adapter": "queue",
                    "consumer": CONSUMER,
                }
            },
        )

    def github_reg(
        self,
        *,
        timeout_s: float = 10,
        field: str = "remaining_fraction",
        op: str = "lt",
        threshold=0.2,
        cooldown_s: int = 0,
        extra_conditions: dict | None = None,
        extra_routes: dict | None = None,
    ) -> dict:
        conditions = {
            "quota_low": {
                "enabled": True,
                "monitor": "github",
                "op": op,
                "field": field,
                "threshold": threshold,
                "cooldown_s": cooldown_s,
            }
        }
        routes = {
            "quota": {
                "enabled": True,
                "condition": "quota_low",
                "adapter": "queue",
                "consumer": CONSUMER,
            }
        }
        if extra_conditions:
            conditions.update(extra_conditions)
        if extra_routes:
            routes.update(extra_routes)
        return self.registry(
            monitors={
                "github": {
                    "enabled": True,
                    "adapter": "github-rate",
                    "schedule": "fast",
                    "params": {"timeout_s": timeout_s},
                }
            },
            conditions=conditions,
            routes=routes,
        )

    def spend_dict(self, *, total=18, input_n=11, cached=3, output=7):
        return {
            "harness": "grok",
            "input": input_n,
            "cached": cached,
            "output": output,
            "total": total,
            "out": output,
            "runs": 1,
        }

    def plant_job(
        self,
        rec_id: str,
        *,
        store: Path | None = None,
        status: str = "closed",
        role: str = "write",
        env_status: str | None = "ok",
        changed_paths=OMIT,
        pid: int | None = None,
        exit_text: str | None = None,
        spend=OMIT,
        attempts=OMIT,
        launched: str = LAUNCHED,
        closed: str | None = CLOSED,
        harness: str = HARNESS,
        record_as_dir: bool = False,
        state_payload=OMIT,
        snapshot_root=OMIT,
        session_id=OMIT,
        state_session_id=OMIT,
    ) -> Path:
        dest = store if store is not None else self.store
        dest.mkdir(parents=True, exist_ok=True)
        job_dir = dest / rec_id
        job_dir.mkdir(parents=True, exist_ok=True)
        rec_path = job_dir / "record.json"
        sess = "sess-" + rec_id[-6:] if session_id is OMIT else session_id
        if snapshot_root is OMIT:
            snap = self.scratch / "snap" / rec_id
        else:
            snap = Path(snapshot_root)
        snap.mkdir(parents=True, exist_ok=True)
        if record_as_dir:
            rec_path.mkdir()
            self.assertTrue(rec_path.is_dir())
            self.assertFalse(rec_path.is_file())
        else:
            rec = {
                "id": rec_id,
                "status": status,
                "role": role,
                "job": "author-tests",
                "harness": {"name": harness, "argv": [harness]},
                "snapshot": {"mode": "whole", "root": str(snap)},
                "at": {"launched": launched, "closed": closed},
                "result": None,
                "session": {"id": sess},
            }
            if attempts is OMIT:
                rec["attempts"] = [{}]
            elif attempts is not None:
                rec["attempts"] = attempts
            if spend is OMIT:
                rec["session"]["spend"] = self.spend_dict()
            elif spend is not None:
                rec["session"]["spend"] = spend
            if env_status is not None or changed_paths is not OMIT:
                result: dict = {"envelope": {}}
                if env_status is not None:
                    result["envelope"]["status"] = env_status
                if changed_paths is not OMIT:
                    result["changed_paths"] = list(changed_paths)
                rec["result"] = result
            raw = json.dumps(rec, indent=2) + "\n"
            self._plant_bytes(rec_path, raw.encode("utf-8"))
            landed = json.loads(rec_path.read_text(encoding="utf-8"))
            self.assertEqual(landed["id"], rec_id)
            self.assertEqual(landed["status"], status)
            self.assertEqual(landed["session"]["id"], sess)
            self.assertEqual(landed["snapshot"]["root"], str(snap))
            self.assertEqual(landed["harness"]["argv"], [harness])
        if state_payload is not OMIT:
            if state_payload is not None:
                raw_state = json.dumps(state_payload) + "\n"
                self._plant_bytes(
                    job_dir / "state.json", raw_state.encode("utf-8")
                )
        elif pid is not None:
            state_sess = sess if state_session_id is OMIT else state_session_id
            raw_state = json.dumps(
                {
                    "pid": pid,
                    "pgid": pid,
                    "session": {"id": state_sess},
                }
            ) + "\n"
            self._plant_bytes(job_dir / "state.json", raw_state.encode("utf-8"))
            landed_state = json.loads(
                (job_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(landed_state["pid"], pid)
            self.assertEqual(landed_state["session"]["id"], state_sess)
        if exit_text is not None:
            self._plant_bytes(job_dir / "exit", exit_text.encode("utf-8"))
            self.assertTrue((job_dir / "exit").is_file())
        else:
            self.assertFalse(
                (job_dir / "exit").exists(),
                f"INVALID: exit present for {rec_id}",
            )
        return job_dir

    def token_info(
        self,
        *,
        input_n: int = 400,
        cached: int = 300,
        output: int = 90,
        total: int = 490,
        last_input=OMIT,
        last_cached=OMIT,
        window=OMIT,
        extra_total: dict | None = None,
    ) -> dict:
        usage = {
            "input_tokens": input_n,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "total_tokens": total,
        }
        if extra_total:
            usage.update(extra_total)
        info: dict = {"total_token_usage": usage}
        last: dict = {}
        if last_input is not OMIT:
            last["input_tokens"] = last_input
        if last_cached is not OMIT:
            last["cached_input_tokens"] = last_cached
        if last:
            info["last_token_usage"] = last
        if window is not OMIT:
            info["model_context_window"] = window
        return info

    def token_row(self, info: dict, *, ts: str = "2026-09-04T12:00:00.000Z") -> dict:
        return {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {"type": "token_count", "info": info},
        }

    def plant_rollout(
        self,
        rows: list[dict],
        *,
        path: Path | None = None,
        prefix: bytes = b"",
        suffix: bytes = b"",
    ) -> bytes:
        dest = path if path is not None else self.rollout
        chunks = [json.dumps(row, separators=(",", ":")) for row in rows]
        body = ("\n".join(chunks) + ("\n" if chunks else "")).encode("utf-8")
        data = prefix + body + suffix
        self._plant_bytes(dest, data)
        self.assertEqual(dest.read_bytes(), data)
        return data

    def _gh_preamble(self) -> str:
        log = str(self.world.tools.log_path("gh"))
        return (
            f"#!{sys.executable}\n"
            "from __future__ import annotations\n"
            "import json, os, sys, time\n"
            "from pathlib import Path\n"
            f"log = Path({log!r})\n"
            "line = json.dumps({\"at\": time.time_ns(), \"pid\": os.getpid(),"
            " \"argv\": sys.argv[1:], \"cwd\": os.getcwd(),"
            " \"stdin_sha256\": None}) + \"\\n\"\n"
            "fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT,"
            " 0o644)\n"
            "os.write(fd, line.encode(\"utf-8\"))\n"
            "os.close(fd)\n"
        )

    def _plant_gh(self, body: str) -> None:
        planted = self.world.tools.plant("gh", script=body)
        self.assertEqual(planted, self.gh)
        landed = self.gh.read_text(encoding="utf-8")
        self.assertEqual(landed, body, "gh stub plant did not land")
        self.assertTrue(
            landed.startswith(f"#!{sys.executable}\n"),
            "gh stub shebang is not sys.executable",
        )

    def plant_gh_rate(self, payload: dict, *, exit_code: int = 0, stderr: str = "") -> None:
        planted = self.world.tools.plant(
            "gh",
            stdout=json.dumps(payload) + "\n",
            stderr=stderr,
            exit=exit_code,
        )
        self.assertEqual(planted, self.gh)
        shebang = self.gh.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(shebang, f"#!{sys.executable}")
        self.assertNotIn("/usr/bin/env", shebang)

    def plant_gh_raw(self, stdout: str, *, exit_code: int = 0, stderr: str = "") -> None:
        planted = self.world.tools.plant(
            "gh",
            stdout=stdout,
            stderr=stderr,
            exit=exit_code,
        )
        self.assertEqual(planted, self.gh)

    def plant_gh_sequence(self, payloads: list[dict]) -> None:
        # Kit gap: the fake spec answers by argv match, not call order,
        # so a same-argv sequence stays a planted script.
        encoded = json.dumps(payloads)
        body = (
            self._gh_preamble()
            + f"counter = Path({str(self.counter)!r})\n"
            + "n = int(counter.read_text(encoding='utf-8'))"
            " if counter.is_file() else 0\n"
            + "n += 1\n"
            + "counter.write_text(str(n), encoding='utf-8')\n"
            + f"seq = json.loads({encoded!r})\n"
            + "row = seq[n - 1] if n <= len(seq) else seq[-1]\n"
            + "sys.stdout.write(json.dumps(row) + '\\n')\n"
            + "raise SystemExit(0)\n"
        )
        self._plant_gh(body)
        self.assertIn("seq = json.loads(", body)
        self.assertFalse(self.counter.exists(), "plant: counter still absent")

    def plant_gh_sleep(self, seconds: float, payload: dict) -> None:
        # Kit gap: delay= sleeps but does not write the owned marker
        # the witness and timeout cases assert.
        marker = self.sleep_marker
        body = (
            self._gh_preamble()
            + "import time\n"
            + f"counter = Path({str(self.counter)!r})\n"
            + "n = int(counter.read_text(encoding='utf-8'))"
            " if counter.is_file() else 0\n"
            + "n += 1\n"
            + "counter.write_text(str(n), encoding='utf-8')\n"
            + f"Path({str(marker)!r}).write_text(str(n), encoding='utf-8')\n"
            + f"time.sleep({seconds!r})\n"
            + f"sys.stdout.write({json.dumps(payload)!r} + '\\n')\n"
            + "raise SystemExit(0)\n"
        )
        if marker.exists():
            marker.unlink()
        if self.counter.exists():
            self.counter.unlink()
        self._plant_gh(body)
        self.assertFalse(marker.exists(), "plant: sleep marker still absent")
        self.assertFalse(self.counter.exists(), "plant: counter still absent")

    def run_gh_mock(self, *argv: str, timeout: float = 5) -> Result:
        self.assertTrue(self.gh.is_file(), "INVALID: gh mock not planted")
        self.assertTrue(os.access(self.gh, os.X_OK), "INVALID: gh mock not executable")
        result = run(
            [str(self.gh), *argv],
            cwd=self.cwd,
            env=self.world.env({"PATH": self.path_without_gh()}),
            timeout=timeout,
        )
        return result

    def rate_payload(self, **buckets) -> dict:
        return {"resources": buckets}

    def bucket(self, limit: int, remaining: int, reset: int) -> dict:
        return {"limit": limit, "remaining": remaining, "reset": reset}

    def gh_argvs(self) -> list[list[str]]:
        return [call["argv"] for call in self.world.tools.calls("gh")]

    def spawn_named(self, token: str, *, cwd: Path | None = None) -> int:
        workdir = cwd if cwd is not None else self.worker_cwd
        workdir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", token],
            cwd=str(workdir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.world.own(proc)
        self._workers.append(proc)
        needle = token.encode("utf-8")
        cmdline = b""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.assertIsNone(proc.poll(), "INVALID: named worker exited")
            path = Path(f"/proc/{proc.pid}/cmdline")
            if path.is_file():
                cmdline = path.read_bytes()
                if needle in cmdline:
                    cwd_link = Path(f"/proc/{proc.pid}/cwd")
                    self.assertEqual(
                        cwd_link.resolve(),
                        workdir.resolve(),
                        "INVALID: worker cwd is not the owned snapshot",
                    )
                    return proc.pid
            time.sleep(0.02)
        self.assertIn(needle, cmdline, "INVALID: worker cmdline missing token")
        return proc.pid

    def dead_pid(self) -> int:
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.world.own(proc)
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll(), "INVALID: dead pid still running")
        with self.assertRaises(ProcessLookupError):
            os.kill(proc.pid, 0)
        return proc.pid


class ToolPresence(_MonitorFixture):
    """Missing tool file counts once; it is not every test's red."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class GhMockWitness(_MonitorFixture):
    """The gh stub must run under path_without_gh before it is a witness."""

    def test_pinned_shebang_logs_argv_and_preserves_json_literals(self):
        payload = {"ok": True, "missing": None, "flag": False, "n": 1}
        self.plant_gh_rate(payload)
        shebang = self.gh.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(shebang, f"#!{sys.executable}")
        self.assertNotIn("/usr/bin/env", shebang)
        proc = self.run_gh_mock("api", "rate_limit")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        landed = json.loads(proc.stdout)
        self.assertIs(landed["ok"], True, landed)
        self.assertIs(landed["flag"], False, landed)
        self.assertIsNone(landed["missing"], landed)
        self.assertEqual(landed["n"], 1, landed)
        self.assertEqual(self.gh_argvs(), [["api", "rate_limit"]])

        if self.argv_log.exists():
            self.argv_log.unlink()
        if self.counter.exists():
            self.counter.unlink()
        self.plant_gh_sequence(
            [
                {"ok": True, "missing": None},
                {"ok": False, "missing": 1},
            ]
        )
        src = self.gh.read_text(encoding="utf-8")
        self.assertIn("seq = json.loads(", src)
        first = self.run_gh_mock("api", "rate_limit")
        self.assertEqual(first.returncode, 0, first.stderr)
        row = json.loads(first.stdout)
        self.assertIs(row["ok"], True, row)
        self.assertIsNone(row["missing"], row)
        second = self.run_gh_mock("api", "rate_limit")
        self.assertEqual(second.returncode, 0, second.stderr)
        row2 = json.loads(second.stdout)
        self.assertIs(row2["ok"], False, row2)
        self.assertEqual(row2["missing"], 1, row2)
        self.assertEqual(
            self.gh_argvs(),
            [["api", "rate_limit"], ["api", "rate_limit"]],
        )

    def test_sleep_mock_writes_marker_logs_argv_and_returns(self):
        payload = {"ok": True, "missing": None}
        self.plant_gh_sleep(0.05, payload)
        shebang = self.gh.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(shebang, f"#!{sys.executable}")
        t0 = time.monotonic()
        proc = self.run_gh_mock("api", "rate_limit")
        elapsed = time.monotonic() - t0
        self.assertEqual(proc.returncode, 0, proc.stderr)
        landed = json.loads(proc.stdout)
        self.assertIs(landed["ok"], True, landed)
        self.assertIsNone(landed["missing"], landed)
        self.assertTrue(
            self.sleep_marker.is_file(), "sleep mock did not write owned marker"
        )
        self.assertEqual(self.sleep_marker.read_text(encoding="utf-8").strip(), "1")
        self.assertEqual(self.gh_call_count(), 1)
        self.assertEqual(self.gh_argvs(), [["api", "rate_limit"]])
        self.assertGreaterEqual(elapsed, 0.04)
        self.assertLess(elapsed, 2.0)


class CheckValid(_MonitorFixture):
    """check validates the registry and does not touch sources or state."""

    def test_valid_registry_emits_counts_including_disabled(self):
        self.plant_config(
            self.registry(
                schedules={
                    "fast": {"enabled": True, "every_s": 1},
                    "slow": {"enabled": False, "every_s": 300},
                },
                monitors={
                    "jobs": {
                        "enabled": False,
                        "adapter": "dispatch",
                        "schedule": "fast",
                        "params": {"store": "./jobs", "ids": []},
                    }
                },
                conditions={
                    "job_change": {
                        "enabled": True,
                        "monitor": "jobs",
                        "op": "changed",
                        "field": "status",
                        "threshold": None,
                        "cooldown_s": 0,
                    }
                },
                routes={
                    "jobs": {
                        "enabled": True,
                        "condition": "job_change",
                        "adapter": "queue",
                        "consumer": CONSUMER,
                    }
                },
            )
        )
        before = self.owned_source_bytes()
        data = self.check_ok()
        self.assertEqual(data["schedules"], 2, data)
        self.assertEqual(data["monitors"], 1, data)
        self.assertEqual(data["conditions"], 1, data)
        self.assertEqual(data["routes"], 1, data)
        self.assert_state_empty()
        self.assertEqual(self.gh_argvs(), [])
        self.assertFalse(self.store.exists())
        self.assert_owned_sources(before)

    def test_example_registry_is_valid_and_counts_every_entry(self):
        self.assertTrue(EXAMPLE.is_file(), f"INVALID: missing {EXAMPLE}")
        data = self.check_ok(EXAMPLE)
        self.assertEqual(data["schedules"], 2, data)
        self.assertEqual(data["monitors"], 3, data)
        self.assertEqual(data["conditions"], 3, data)
        self.assertEqual(data["routes"], 3, data)

    def test_omitted_config_discovers_the_example_registry(self):
        self.assertTrue(EXAMPLE.is_file(), f"INVALID: missing {EXAMPLE}")
        payload = EXAMPLE.read_bytes()
        self._plant_bytes(self.staged.parent / "monitor.example.json", payload)
        self._plant_bytes(
            self.staged.parent.parent / "monitor.example.json", payload
        )
        proc = self.invoke(["check"])
        data = self.parse_json(proc, expect_exit=0)
        self.assertIs(data.get("valid"), True, data)
        self.assertEqual(data["schedules"], 2, data)
        self.assertEqual(data["monitors"], 3, data)
        self.assertEqual(data["conditions"], 3, data)
        self.assertEqual(data["routes"], 3, data)
        self.assertEqual(EXAMPLE.read_bytes(), self._example_before)
        self.assert_state_empty()

    def test_check_does_not_require_identity_or_existing_sources(self):
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.assertFalse(self.store.exists())
        before = self.owned_source_bytes()
        proc = self.check()
        data = self.parse_json(proc, expect_exit=0)
        self.assertIs(data["valid"], True, data)
        self.assertNotIn("WF_AGENT", self.env())
        self.assert_state_empty()
        self.assertEqual(self.gh_argvs(), [])
        self.assert_owned_sources(before)


class ConfigRefusalsBeforeSideEffects(_MonitorFixture):
    """Invalid configuration refuses on the wire before any write."""

    def _refuse_config(self, payload: dict | str | bytes, class_name="config"):
        self.plant_config(payload)
        before_state = _tree_files(self.state)
        before_sources = self.owned_source_bytes()
        proc = self.check()
        self.assert_refusal(proc, class_name)
        self.assertEqual(_tree_files(self.state), before_state)
        proc_run = self.run_once()
        self.assert_refusal(proc_run, class_name)
        self.assertEqual(_tree_files(self.state), before_state)
        self.assertEqual(self.gh_argvs(), [])
        self.assert_owned_sources(before_sources)

    def test_unreadable_and_invalid_json_refuse_config(self):
        missing = self.conf_dir / "no-such-monitor.json"
        self.assertFalse(missing.exists())
        proc = self.check(missing)
        self.assert_refusal(proc, "config")
        self.assert_state_empty()

        raw = b'{"version":1,\n'
        self.plant_config(raw)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(self.config.read_text(encoding="utf-8"))
        proc = self.check()
        self.assert_refusal(proc, "config")
        self.assert_state_empty()

        self.plant_config("[1,2,3]")
        proc = self.check()
        self.assert_refusal(proc, "config")

    def test_duplicate_keys_unknown_fields_and_bool_numbers_refuse(self):
        cases = {
            "duplicate-version": (
                '{"version":1,"version":1,'
                '"schedules":{"fast":{"enabled":true,"every_s":1}},'
                '"monitors":{},"conditions":{},"routes":{}}\n'
            ),
            "unknown-top": self.registry(nope=True),
            "bool-every": self.registry(
                schedules={"fast": {"enabled": True, "every_s": True}}
            ),
            "every-zero": self.registry(
                schedules={"fast": {"enabled": True, "every_s": 0}}
            ),
            "every-nan": (
                '{"version":1,'
                '"schedules":{"fast":{"enabled":true,"every_s":NaN}},'
                '"monitors":{},"conditions":{},"routes":{}}\n'
            ),
            "every-inf": (
                '{"version":1,'
                '"schedules":{"fast":{"enabled":true,"every_s":Infinity}},'
                '"monitors":{},"conditions":{},"routes":{}}\n'
            ),
        }
        nested_schedule = self.registry(
            schedules={"fast": {"enabled": True, "every_s": 1, "nope": 1}}
        )
        nested_monitor = self.dispatch_reg([NOOP_ID])
        nested_monitor["monitors"]["jobs"]["nope"] = True
        nested_params = self.dispatch_reg([NOOP_ID])
        nested_params["monitors"]["jobs"]["params"]["nope"] = "./x"
        nested_condition = self.dispatch_reg([NOOP_ID])
        nested_condition["conditions"]["job_change"]["nope"] = True
        nested_route = self.dispatch_reg([NOOP_ID])
        nested_route["routes"]["jobs"]["nope"] = True
        cases["unknown-nested-schedule"] = nested_schedule
        cases["unknown-nested-monitor"] = nested_monitor
        cases["unknown-nested-params"] = nested_params
        cases["unknown-nested-condition"] = nested_condition
        cases["unknown-nested-route"] = nested_route
        self.assertEqual(len(cases), 11)
        for name, payload in cases.items():
            with self.subTest(name):
                self._refuse_config(payload)

    def test_operand_and_reference_and_adapter_rules(self):
        base_mon = {
            "jobs": {
                "enabled": True,
                "adapter": "dispatch",
                "schedule": "fast",
                "params": {"store": "./jobs", "ids": []},
            }
        }
        cases = {
            "gt-null-threshold": (
                "config",
                self.registry(
                    monitors=base_mon,
                    conditions={
                        "c": {
                            "enabled": True,
                            "monitor": "jobs",
                            "op": "gt",
                            "field": "total",
                            "threshold": None,
                            "cooldown_s": 0,
                        }
                    },
                ),
            ),
            "changed-numeric-threshold": (
                "config",
                self.registry(
                    monitors=base_mon,
                    conditions={
                        "c": {
                            "enabled": True,
                            "monitor": "jobs",
                            "op": "changed",
                            "field": "status",
                            "threshold": 1,
                            "cooldown_s": 0,
                        }
                    },
                ),
            ),
            "unavailable-with-field": (
                "config",
                self.registry(
                    monitors=base_mon,
                    conditions={
                        "c": {
                            "enabled": True,
                            "monitor": "jobs",
                            "op": "unavailable",
                            "field": "status",
                            "threshold": None,
                            "cooldown_s": 0,
                        }
                    },
                ),
            ),
            "changed-empty-field": (
                "config",
                self.registry(
                    monitors=base_mon,
                    conditions={
                        "c": {
                            "enabled": True,
                            "monitor": "jobs",
                            "op": "changed",
                            "field": "",
                            "threshold": None,
                            "cooldown_s": 0,
                        }
                    },
                ),
            ),
            "unknown-op": (
                "config",
                self.registry(
                    monitors=base_mon,
                    conditions={
                        "c": {
                            "enabled": True,
                            "monitor": "jobs",
                            "op": "eq",
                            "field": "total",
                            "threshold": 1,
                            "cooldown_s": 0,
                        }
                    },
                ),
            ),
            "dangling-schedule": (
                "reference",
                self.registry(
                    monitors={
                        "jobs": {
                            "enabled": True,
                            "adapter": "dispatch",
                            "schedule": "missing",
                            "params": {"store": "./jobs", "ids": []},
                        }
                    }
                ),
            ),
            "dangling-monitor": (
                "reference",
                self.registry(
                    conditions={
                        "c": {
                            "enabled": True,
                            "monitor": "nope",
                            "op": "changed",
                            "field": "status",
                            "threshold": None,
                            "cooldown_s": 0,
                        }
                    }
                ),
            ),
            "dangling-condition": (
                "reference",
                self.registry(
                    routes={
                        "r": {
                            "enabled": True,
                            "condition": "nope",
                            "adapter": "queue",
                            "consumer": CONSUMER,
                        }
                    }
                ),
            ),
            "unknown-adapter": (
                "config",
                self.registry(
                    monitors={
                        "jobs": {
                            "enabled": True,
                            "adapter": "webhook",
                            "schedule": "fast",
                            "params": {},
                        }
                    }
                ),
            ),
            "disabled-unknown-adapter": (
                "config",
                self.registry(
                    monitors={
                        "jobs": {
                            "enabled": False,
                            "adapter": "webhook",
                            "schedule": "fast",
                            "params": {},
                        }
                    }
                ),
            ),
        }
        self.assertEqual(len(cases), 10)
        for name, (cls, payload) in cases.items():
            with self.subTest(name):
                self._refuse_config(payload, cls)

    def test_consumer_id_and_adapter_bound_refusals(self):
        long_ok = "c" * 128
        long_bad = "c" * 129
        ids_256 = [f"j{i:03d}" for i in range(256)]
        ids_257 = [f"j{i:03d}" for i in range(257)]
        valid = self.dispatch_reg([NOOP_ID], consumer=long_ok)
        self.plant_config(valid)
        data = self.check_ok()
        self.assertEqual(data["routes"], 1, data)

        self.plant_config(self.dispatch_reg(ids_256))
        self.check_ok()

        consumer_refusals = {
            "empty-consumer": self.dispatch_reg([NOOP_ID], consumer=""),
            "slash-consumer": self.dispatch_reg([NOOP_ID], consumer="a/b"),
            "control-consumer": self.dispatch_reg(
                [NOOP_ID], consumer="op\nerator"
            ),
            "long-consumer": self.dispatch_reg([NOOP_ID], consumer=long_bad),
        }
        self.assertEqual(len(consumer_refusals), 4)
        for name, payload in consumer_refusals.items():
            with self.subTest(name):
                self._refuse_config(payload, "consumer")

        bound_refusals = {
            "bad-id": self.registry(
                schedules={"Fast": {"enabled": True, "every_s": 1}}
            ),
            "timeout-zero": self.github_reg(timeout_s=0),
            "timeout-31": self.github_reg(timeout_s=31),
            "codex-small": self.codex_reg(max_bytes=1023),
            "codex-big": self.codex_reg(max_bytes=8388609),
            "ids-257": self.dispatch_reg(ids_257),
        }
        self.assertEqual(len(bound_refusals), 6)
        for name, payload in bound_refusals.items():
            with self.subTest(name):
                self._refuse_config(payload, "config")

        self.plant_config(self.codex_reg(max_bytes=1024))
        self.check_ok()
        self.plant_config(self.codex_reg(max_bytes=8388608))
        self.check_ok()
        self.plant_config(self.github_reg(timeout_s=30))
        self.check_ok()

    def test_json_array_and_object_in_enum_fields_refuse_config(self):
        def with_monitor_adapter(value):
            body = self.dispatch_reg([NOOP_ID])
            body["monitors"]["jobs"]["adapter"] = value
            return body

        def with_op(value):
            body = self.dispatch_reg([NOOP_ID])
            body["conditions"]["job_change"]["op"] = value
            return body

        def with_route_adapter(value):
            body = self.dispatch_reg([NOOP_ID])
            body["routes"]["jobs"]["adapter"] = value
            return body

        cases = {
            "monitor-adapter-list": with_monitor_adapter([]),
            "monitor-adapter-object": with_monitor_adapter({}),
            "op-list": with_op([]),
            "op-object": with_op({}),
            "route-adapter-list": with_route_adapter([]),
            "route-adapter-object": with_route_adapter({}),
        }
        self.assertEqual(len(cases), 6)
        for name, payload in cases.items():
            with self.subTest(name):
                raw = json.dumps(payload)
                if name.endswith("list"):
                    self.assertIn("[]", raw)
                else:
                    self.assertIn("{}", raw)
                self._refuse_config(payload, "config")


class ArgumentIdentityConsumerState(_MonitorFixture):
    """CLI, identity, consumer and state refusals happen before acting."""

    def test_invalid_verb_and_run_options_refuse_arguments(self):
        self.plant_config(self.dispatch_reg([]))
        cases = [
            (["frobnicate"], None),
            ([*self.cfg_args()], None),
            ([*self.cfg_args(), "run"], AGENT),
            ([*self.cfg_args(), "run", "--once", "--loop"], AGENT),
            ([*self.cfg_args(), "run", "--loop", "--now", NOW], AGENT),
            ([*self.cfg_args(), "run", "--once", "--now", "nope"], AGENT),
            (
                [*self.cfg_args(), "digest", "--consumer", CONSUMER,
                 "--limit", "-1"],
                None,
            ),
        ]
        self.assertEqual(len(cases), 7)
        for argv, agent in cases:
            with self.subTest(argv=argv):
                proc = self.invoke(argv, agent=agent)
                self.assert_refusal(proc, "arguments")
        self.assert_state_empty()

    def test_run_and_ack_without_identity_refuse_before_writing(self):
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.plant_job(NOOP_ID, changed_paths=[])
        sources = self.owned_source_bytes()
        proc = self.run_once(agent=None)
        self.assert_refusal(proc, "identity")
        self.assert_state_empty()
        proc = self.run_once(agent=BAD_AGENT)
        self.assert_refusal(proc, "identity")
        self.assert_state_empty()
        rec = (self.store / NOOP_ID / "record.json").read_bytes()
        env = self.env(agent=None)
        self.assertNotIn("WF_AGENT", env)
        proc = self.digest(ack=True, agent=None)
        self.assert_refusal(proc, "identity")
        self.assertEqual(
            (self.store / NOOP_ID / "record.json").read_bytes(), rec
        )
        self.assert_owned_sources(sources)
        self.assert_state_empty()

        _, counts = self.run_counts()
        self.assertGreaterEqual(counts["events"], 1, counts)
        _, before = self.digest_data()
        before_ids = [event["id"] for event in before["events"]]
        self.assertTrue(before_ids, before)
        proc = self.digest(ack=True, agent=None)
        self.assert_refusal(proc, "identity")
        self.assertEqual(
            (self.store / NOOP_ID / "record.json").read_bytes(), rec
        )
        self.assert_owned_sources(sources)
        _, after = self.digest_data()
        self.assertEqual([event["id"] for event in after["events"]], before_ids)

    def test_digest_missing_or_invalid_consumer_refuses(self):
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        proc = self.invoke([*self.cfg_args(), "digest"])
        self.assert_refusal(proc, "consumer")
        proc = self.invoke([*self.cfg_args(), "digest", "--consumer", ""])
        self.assert_refusal(proc, "consumer")
        proc = self.invoke([*self.cfg_args(), "digest", "--consumer", "a/b"])
        self.assert_refusal(proc, "consumer")
        self.assert_state_empty()

    def test_corrupt_state_refuses_state(self):
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.plant_job(NOOP_ID, changed_paths=[])
        state_file = self.scratch / "state-is-a-file"
        self._plant_bytes(state_file, b"not-a-state-dir\n")
        proc = self.invoke(
            ["--config", str(self.config), "--state", str(state_file),
             "run", "--once", "--now", NOW],
            agent=AGENT,
        )
        self.assert_refusal(proc, "state")

    def test_missing_lane_name_without_state_refuses_conf_key(self):
        self.plant_lane_conf(omit=("LANE_NAME",))
        landed = self.conf_path.read_text(encoding="utf-8")
        self.assertNotIn("LANE_NAME", landed)
        self.plant_config(self.dispatch_reg([]))
        proc = self.invoke(
            ["--config", str(self.config), "run", "--once", "--now", NOW],
            agent=AGENT,
        )
        self.assert_refusal(proc, "conf-key")


class DisabledRemoved(_MonitorFixture):
    """Disabled and removed entries are validated, not evaluated."""

    def test_disabled_monitor_is_not_observed(self):
        self.plant_config(self.dispatch_reg([NOOP_ID], enabled=False))
        self.plant_job(NOOP_ID, changed_paths=[])
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 0, counts)
        self.assertEqual(counts["events"], 0, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 0, data)

    def test_removed_route_keeps_already_queued_events(self):
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.plant_job(NOOP_ID, changed_paths=[])
        _, counts = self.run_counts()
        self.assertGreaterEqual(counts["events"], 1, counts)
        _, first = self.digest_data()
        self.assertGreaterEqual(len(first["events"]), 1, first)
        kept = first["events"]
        self.plant_config(
            self.registry(
                monitors={
                    "jobs": {
                        "enabled": True,
                        "adapter": "dispatch",
                        "schedule": "fast",
                        "params": {"store": "./jobs", "ids": [NOOP_ID]},
                    }
                },
                conditions={
                    "job_change": {
                        "enabled": True,
                        "monitor": "jobs",
                        "op": "changed",
                        "field": "status",
                        "threshold": None,
                        "cooldown_s": 0,
                    }
                },
                routes={},
            )
        )
        self.plant_job(
            NOOP_ID,
            changed_paths=["ops/a.py"],
            role="write",
            env_status="ok",
        )
        _, later = self.run_counts(now=NOW_30)
        self.assertEqual(later["events"], 0, later)
        _, second = self.digest_data()
        self.assertEqual(len(second["events"]), len(kept), second)
        self.assertEqual(
            [e["id"] for e in second["events"]],
            [e["id"] for e in kept],
        )

    def test_unrelated_edit_does_not_duplicate_other_events(self):
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.plant_job(NOOP_ID, changed_paths=[])
        self.plant_job(DECOY_ID, changed_paths=[], store=self.store)
        _, first = self.run_counts()
        self.assertGreaterEqual(first["events"], 1, first)
        _, data = self.digest_data()
        n = len(data["events"])
        self.assertGreaterEqual(n, 1)
        body = json.loads(self.config.read_text(encoding="utf-8"))
        body["schedules"]["slow"] = {"enabled": False, "every_s": 300}
        self.plant_config(body)
        landed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIn("slow", landed["schedules"])
        _, second = self.run_counts(now=NOW_30)
        self.assertEqual(second["events"], 0, second)
        _, again = self.digest_data()
        self.assertEqual(len(again["events"]), n, again)

    def test_disable_then_reenable_condition_rearms_without_dedup_collision(self):
        body = self.dispatch_reg([NOOP_ID])
        self.plant_config(body)
        self.plant_job(NOOP_ID, changed_paths=[])
        _, first = self.run_counts()
        self.assertEqual(first["events"], 1, first)
        _, queued = self.digest_data()
        self.assertEqual(len(queued["events"]), 1, queued)
        body["conditions"]["job_change"]["enabled"] = False
        self.plant_config(body)
        landed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIs(landed["conditions"]["job_change"]["enabled"], False)
        _, disabled = self.run_counts(now=NOW_30)
        self.assertEqual(disabled["events"], 0, disabled)
        body["conditions"]["job_change"]["enabled"] = True
        self.plant_config(body)
        landed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIs(landed["conditions"]["job_change"]["enabled"], True)
        _, reenabled = self.run_counts(now=NOW_60)
        self.assertEqual(reenabled["events"], 1, reenabled)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 2, data)
        ids = [event["id"] for event in data["events"]]
        self.assertEqual(len(ids), len(set(map(str, ids))), ids)

    def test_condition_definition_change_rearms(self):
        body = self.dispatch_reg([NOOP_ID])
        self.plant_config(body)
        self.plant_job(NOOP_ID, changed_paths=[])
        _, first = self.run_counts()
        self.assertEqual(first["events"], 1, first)
        body["conditions"]["job_change"]["field"] = "total"
        self.plant_config(body)
        landed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(landed["conditions"]["job_change"]["field"], "total")
        _, rearmed = self.run_counts(now=NOW_30)
        self.assertEqual(rearmed["events"], 1, rearmed)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 2, data)


class DispatchLifecycle(_MonitorFixture):
    """Dispatch readings: no-op, completion, death, settling, unknown."""

    def test_empty_ids_select_none_and_ignore_decoys(self):
        self.plant_config(self.dispatch_reg([]))
        self.plant_job(DECOY_ID, changed_paths=[])
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 0, counts)
        self.assertEqual(counts["events"], 0, counts)
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.plant_job(NOOP_ID, changed_paths=[])
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 1, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 1, data)
        self.assertIn(NOOP_ID, str(data["events"][0]["subject"]))
        self.assertNotIn(DECOY_ID, json.dumps(data))

    def test_noop_versus_completion_and_invalid_envelope(self):
        self.plant_config(
            self.dispatch_reg([NOOP_ID, DONE_ID, TRIP_ID])
        )
        self.plant_job(NOOP_ID, changed_paths=[], role="write", env_status="ok")
        self.plant_job(
            DONE_ID,
            changed_paths=["ops/a.py"],
            role="write",
            env_status="ok",
        )
        self.plant_job(
            TRIP_ID,
            changed_paths=["ops/b.py"],
            env_status="tripped",
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 3, counts)
        self.assertEqual(counts["events"], 3, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 3, data)
        self.assert_events_ordered(data["events"])
        self.assert_subject_mentions(data["events"], NOOP_ID, "no-op")
        self.assert_subject_omits(data["events"], NOOP_ID, "completion")
        self.assert_subject_mentions(data["events"], DONE_ID, "completion")
        self.assert_subject_omits(data["events"], DONE_ID, "no-op")
        self.assert_subject_mentions(data["events"], TRIP_ID, "invalid")

    def test_death_settling_unknown_pid_and_unreadable(self):
        dead = self.dead_pid()
        ids = [
            DIED_ID, DEAD_ID, SETTLE_ID, ZERO_ID, BOOL_ID, UNREAD_ID, MISS_ID,
        ]
        self.plant_config(
            self.dispatch_reg(
                ids,
                extra_conditions={
                    "down": {
                        "enabled": True,
                        "monitor": "jobs",
                        "op": "unavailable",
                        "field": None,
                        "threshold": None,
                        "cooldown_s": 0,
                    }
                },
                extra_routes={
                    "down": {
                        "enabled": True,
                        "condition": "down",
                        "adapter": "queue",
                        "consumer": CONSUMER,
                    }
                },
            )
        )
        self.plant_job(DIED_ID, status="died", closed=CLOSED, changed_paths=[])
        self.plant_job(
            DEAD_ID,
            status="launched",
            closed=None,
            pid=dead,
            changed_paths=OMIT,
            env_status=None,
        )
        self.plant_job(
            SETTLE_ID,
            status="launched",
            closed=None,
            exit_text="0\n",
            changed_paths=OMIT,
            env_status=None,
        )
        self.plant_job(
            ZERO_ID,
            status="launched",
            closed=None,
            session_id="sess-zero-aa4110",
            state_payload={
                "pid": 0,
                "session": {"id": "sess-zero-aa4110"},
            },
            changed_paths=OMIT,
            env_status=None,
        )
        self.plant_job(
            BOOL_ID,
            status="launched",
            closed=None,
            session_id="sess-bool-aa4111",
            state_payload={
                "pid": False,
                "session": {"id": "sess-bool-aa4111"},
            },
            changed_paths=OMIT,
            env_status=None,
        )
        self.plant_job(UNREAD_ID, record_as_dir=True)
        dead_rec = json.loads(
            (self.store / DEAD_ID / "record.json").read_text(encoding="utf-8")
        )
        dead_state = json.loads(
            (self.store / DEAD_ID / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(dead_rec["harness"]["argv"], [HARNESS])
        self.assertTrue(dead_rec["snapshot"]["root"])
        self.assertEqual(dead_rec["session"]["id"], dead_state["session"]["id"])
        self.assertTrue(dead_rec["session"]["id"])
        _, counts = self.run_counts(new_session=True)
        self.assertEqual(counts["observations"], 7, counts)
        self.assertGreaterEqual(counts["unavailable"], 1, counts)
        _, data = self.digest_data()
        blob = json.dumps(data)
        self.assertIn("death", blob)
        self.assertIn("settling", blob)
        self.assertIn("unknown", blob)
        self.assertIn("unavailable", blob)
        self.assert_subject_mentions(data["events"], DIED_ID, "death")
        self.assert_subject_mentions(data["events"], DEAD_ID, "death")
        self.assert_subject_omits(data["events"], DEAD_ID, "unknown")
        self.assert_subject_mentions(data["events"], SETTLE_ID, "settling")
        self.assert_subject_mentions(data["events"], ZERO_ID, "unknown")
        self.assert_subject_omits(data["events"], ZERO_ID, "death")
        self.assert_subject_mentions(data["events"], BOOL_ID, "unknown")
        self.assert_subject_mentions(data["events"], UNREAD_ID, "unavailable")

    def test_running_requires_live_pid_and_harness_command(self):
        snap = self.worker_cwd
        pid = self.spawn_named(HARNESS, cwd=snap)
        proc_cwd = Path(f"/proc/{pid}/cwd").resolve()
        self.assertEqual(proc_cwd, snap.resolve())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        self.assertIn(HARNESS.encode("utf-8"), cmdline)
        self.assertNotIn(OTHER_TOKEN.encode("utf-8"), cmdline)
        sess = "sess-run-aa4116"
        self.plant_config(self.dispatch_reg([RUN_ID, TOKEN_ID, CWD_ID, SESS_ID]))
        self.plant_job(
            RUN_ID,
            status="launched",
            closed=None,
            pid=pid,
            harness=HARNESS,
            snapshot_root=snap,
            session_id=sess,
            changed_paths=OMIT,
            env_status=None,
        )
        self.plant_job(
            TOKEN_ID,
            status="launched",
            closed=None,
            pid=pid,
            harness=OTHER_TOKEN,
            snapshot_root=snap,
            session_id=sess,
            changed_paths=OMIT,
            env_status=None,
        )
        other_snap = self.scratch / "other-snap"
        other_snap.mkdir()
        self.assertNotEqual(other_snap.resolve(), proc_cwd)
        self.plant_job(
            CWD_ID,
            status="launched",
            closed=None,
            pid=pid,
            harness=HARNESS,
            snapshot_root=other_snap,
            session_id=sess,
            changed_paths=OMIT,
            env_status=None,
        )
        self.plant_job(
            SESS_ID,
            status="launched",
            closed=None,
            pid=pid,
            harness=HARNESS,
            snapshot_root=snap,
            session_id="sess-record-aa4117",
            state_session_id="sess-state-aa4117",
            changed_paths=OMIT,
            env_status=None,
        )
        token_rec = json.loads(
            (self.store / TOKEN_ID / "record.json").read_text(encoding="utf-8")
        )
        self.assertEqual(token_rec["harness"]["argv"], [OTHER_TOKEN])
        cwd_rec = json.loads(
            (self.store / CWD_ID / "record.json").read_text(encoding="utf-8")
        )
        self.assertEqual(Path(cwd_rec["snapshot"]["root"]).resolve(), other_snap.resolve())
        sess_rec = json.loads(
            (self.store / SESS_ID / "record.json").read_text(encoding="utf-8")
        )
        sess_state = json.loads(
            (self.store / SESS_ID / "state.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(sess_rec["session"]["id"], sess_state["session"]["id"])
        self.assertTrue(sess_rec["session"]["id"])
        self.assertTrue(sess_state["session"]["id"])
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 4, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 4, data)
        self.assert_subject_mentions(data["events"], RUN_ID, "running")
        self.assert_subject_omits(data["events"], RUN_ID, "unknown")
        self.assert_subject_mentions(data["events"], TOKEN_ID, "unknown")
        self.assert_subject_omits(data["events"], TOKEN_ID, "running")
        self.assert_subject_mentions(data["events"], CWD_ID, "unknown")
        self.assert_subject_omits(data["events"], CWD_ID, "running")
        self.assert_subject_mentions(data["events"], SESS_ID, "unknown")
        self.assert_subject_omits(data["events"], SESS_ID, "running")

    def test_spend_comes_from_record_and_paths_are_config_relative(self):
        self.plant_job(
            SPEND_ID,
            changed_paths=["ops/a.py"],
            spend=self.spend_dict(total=18, input_n=11, cached=3, output=7),
        )
        self.plant_job(
            DECOY_ID,
            changed_paths=["ops/decoy.py"],
            store=self.cwd_store,
            spend=self.spend_dict(total=999, input_n=999, cached=1, output=1),
        )
        self.plant_config(
            self.dispatch_reg([SPEND_ID], field="total", op="gt", threshold=17)
        )
        before = (self.store / SPEND_ID / "record.json").read_bytes()
        decoy = (self.cwd_store / DECOY_ID / "record.json").read_bytes()
        sources = self.owned_source_bytes()
        planted = json.loads(before.decode("utf-8"))
        self.assertEqual(planted["session"]["spend"]["total"], 18)
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 1, counts)
        self.assertEqual(counts["events"], 1, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 1, data)
        blob = json.dumps(data)
        self.assertIn(SPEND_ID, blob)
        self.assertNotIn(DECOY_ID, blob)
        self.assertEqual(
            (self.store / SPEND_ID / "record.json").read_bytes(), before
        )
        self.assertEqual(
            (self.cwd_store / DECOY_ID / "record.json").read_bytes(), decoy
        )
        self.assert_owned_sources(sources)

        other = self.scratch / "state-spend-eq"
        other.mkdir()
        self.state = other
        self.plant_config(
            self.dispatch_reg([SPEND_ID], field="total", op="gt", threshold=18)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 1, counts)
        self.assertEqual(counts["events"], 0, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 0, data)

    def test_new_generation_rearms_changed_without_inheriting_cooldown(self):
        self.plant_config(
            self.dispatch_reg([GEN_ID], cooldown_s=300)
        )
        self.plant_job(
            GEN_ID,
            changed_paths=[],
            attempts=[{}],
            launched=LAUNCHED,
        )
        _, first = self.run_counts()
        self.assertEqual(first["events"], 1, first)
        self.plant_job(
            GEN_ID,
            changed_paths=[],
            attempts=[{}, {}],
            launched=LAUNCHED_2,
        )
        _, second = self.run_counts(now=NOW_30)
        self.assertEqual(second["events"], 1, second)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 2, data)

    def test_same_generation_changed_emits_on_field_transition(self):
        self.plant_config(self.dispatch_reg([GEN_ID]))
        self.plant_job(
            GEN_ID,
            changed_paths=[],
            attempts=[{}],
            launched=LAUNCHED,
        )
        _, first = self.run_counts()
        self.assertEqual(first["events"], 1, first)
        self.plant_job(
            GEN_ID,
            changed_paths=["ops/a.py"],
            attempts=[{}],
            launched=LAUNCHED,
        )
        landed = json.loads(
            (self.store / GEN_ID / "record.json").read_text(encoding="utf-8")
        )
        self.assertEqual(landed["attempts"], [{}])
        self.assertEqual(landed["at"]["launched"], LAUNCHED)
        self.assertEqual(landed["result"]["changed_paths"], ["ops/a.py"])
        _, second = self.run_counts(now=NOW_30)
        self.assertEqual(second["events"], 1, second)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 2, data)
        gen_events = self.events_for_subject(data["events"], GEN_ID)
        self.assertEqual(len(gen_events), 2, gen_events)
        self.assertIn("no-op", json.dumps(gen_events[0]))
        self.assertNotIn("completion", json.dumps(gen_events[0]))
        self.assertIn("completion", json.dumps(gen_events[1]))
        self.assertNotIn("no-op", json.dumps(gen_events[1]))


class CodexTailBounds(_MonitorFixture):
    """Codex adapter: last in-window count, bounds, unknown not zero."""

    def test_last_count_in_window_is_not_the_sum(self):
        first = self.token_row(
            self.token_info(input_n=100, cached=50, output=40, total=140),
            ts="2026-09-04T12:00:00.000Z",
        )
        last = self.token_row(
            self.token_info(input_n=400, cached=300, output=90, total=490),
            ts="2026-09-04T12:09:00.000Z",
        )
        self.plant_rollout([first, last])
        # last=490 does not cross 500; the sum 630 would.
        self.plant_config(self.codex_reg(field="total", op="gt", threshold=500))
        before = self.rollout.read_bytes()
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 1, counts)
        self.assertEqual(counts["events"], 0, counts)
        self.assertEqual(self.rollout.read_bytes(), before)
        self.plant_config(self.codex_reg(field="total", op="gt", threshold=400))
        other = self.scratch / "state-last"
        other.mkdir()
        self.state = other
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 1, counts)

    def test_tail_bound_does_not_fall_back_to_the_head_count(self):
        head = self.token_row(
            self.token_info(input_n=900, cached=0, output=0, total=900),
            ts="2026-09-04T11:00:00.000Z",
        )
        noise = {
            "timestamp": "2026-09-04T12:00:00.000Z",
            "type": "event_msg",
            "payload": {"type": "shell_command", "command": "x" * 80},
        }
        rows = [head] + [noise] * 40
        data = self.plant_rollout(rows)
        self.assertGreater(len(data), 1024)
        self.plant_config(
            self.codex_reg(max_bytes=1024, field="total", op="gt", threshold=0)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 1, counts)
        self.assertEqual(counts["events"], 0, counts)
        _, data_json = self.digest_data()
        self.assertEqual(len(data_json["events"]), 0, data_json)

    def test_partial_rows_bools_and_missing_currencies_are_unknown(self):
        good = self.token_row(
            self.token_info(input_n=400, cached=300, output=90, total=490),
        )
        cut = json.dumps(good)[10:].encode("utf-8") + b"\n"
        incomplete = b'{"timestamp":"2026-09-04T12:09:00.000Z","type":'
        bool_row = self.token_row(
            {
                "total_token_usage": {
                    "input_tokens": True,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": True,
                }
            }
        )
        neg = self.token_row(
            self.token_info(input_n=-1, cached=0, output=0, total=-1)
        )
        self.plant_rollout([bool_row], prefix=cut, suffix=incomplete)
        self.plant_config(self.codex_reg(field="total", op="gt", threshold=-0.5))
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 0, counts)
        other = self.scratch / "state-neg"
        other.mkdir()
        self.state = other
        self.plant_rollout([neg])
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 0, counts)
        other = self.scratch / "state-empty"
        other.mkdir()
        self.state = other
        self.plant_rollout([])
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 0, counts)
        self.assertEqual(counts["unavailable"], 0, counts)

    def test_cache_is_not_added_to_input_and_fractions_use_last_request(self):
        info = self.token_info(
            input_n=400,
            cached=300,
            output=90,
            total=490,
            last_input=80,
            last_cached=20,
            window=100,
        )
        self.plant_rollout([self.token_row(info)])
        # cache added to input would be 700 and fire gt 500; 400 does not.
        self.plant_config(self.codex_reg(field="input", op="gt", threshold=500))
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 0, counts)
        other = self.scratch / "state-cache"
        other.mkdir()
        self.state = other
        self.plant_config(
            self.codex_reg(field="cache_fraction", op="gt", threshold=0.5)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 1, counts)
        other = self.scratch / "state-ctx"
        other.mkdir()
        self.state = other
        # last_input/window = 0.8; input/window = 4.0; last/input = 0.2
        self.plant_config(
            self.codex_reg(field="context_fraction", op="gt", threshold=0.75)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 1, counts)
        other = self.scratch / "state-ctx2"
        other.mkdir()
        self.state = other
        self.plant_config(
            self.codex_reg(field="context_fraction", op="lt", threshold=0.5)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 0, counts)


class GithubRateAndQuota(_MonitorFixture):
    """Replayable gh mock: one argv, per-bucket readings, reset re-arm."""

    def test_one_code_built_call_and_valid_buckets(self):
        payload = self.rate_payload(
            core=self.bucket(5000, 1000, RESET_1),
            graphql=self.bucket(5000, 2500, RESET_1),
            search=self.bucket(30, 1, RESET_1),
        )
        self.plant_gh_rate(payload)
        self.plant_config(
            self.github_reg(
                extra_conditions={
                    "also": {
                        "enabled": True,
                        "monitor": "github",
                        "op": "lt",
                        "field": "remaining_fraction",
                        "threshold": 0.5,
                        "cooldown_s": 0,
                    }
                },
                extra_routes={
                    "also": {
                        "enabled": True,
                        "condition": "also",
                        "adapter": "queue",
                        "consumer": CONSUMER,
                    }
                },
            )
        )
        _, counts = self.run_counts()
        self.assertEqual(self.gh_argvs(), [["api", "rate_limit"]])
        self.assertEqual(counts["observations"], 3, counts)
        self.assertGreaterEqual(counts["events"], 1, counts)
        _, data = self.digest_data()
        blob = json.dumps(data)
        self.assertIn("search", blob)

    def test_malformed_bucket_is_unknown_failures_are_unavailable(self):
        payload = self.rate_payload(
            core=self.bucket(10, 99, RESET_1),
            graphql=self.bucket(5000, 2500, RESET_1),
            search=self.bucket(30, 1, RESET_1),
        )
        self.plant_gh_rate(payload)
        # 99/10 = 9.9 would cross gt 1 if accepted; unknown must not.
        self.plant_config(
            self.github_reg(op="gt", field="remaining_fraction", threshold=1)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], 3, counts)
        self.assertEqual(counts["unavailable"], 0, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 0, data)
        blob = json.dumps(data)
        self.assertNotIn(str(99 / 10), blob)
        self.assertNotIn("9.9", blob)

        other = self.scratch / "state-gh-fail"
        other.mkdir()
        self.state = other
        self.plant_gh_raw(
            "",
            exit_code=1,
            stderr=f"error: {GH_SECRET}\n",
        )
        proc, counts = self.run_counts()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertGreaterEqual(counts["unavailable"], 1, counts)
        self.assertEqual(counts["events"], 0, counts)
        self.assertNotIn(GH_SECRET, self.combined(proc))
        _, data = self.digest_data()
        self.assertNotIn(GH_SECRET, json.dumps(data))

        other = self.scratch / "state-gh-json"
        other.mkdir()
        self.state = other
        self.plant_gh_raw("not-json", exit_code=0)
        _, counts = self.run_counts()
        self.assertGreaterEqual(counts["unavailable"], 1, counts)

        other = self.scratch / "state-gh-miss"
        other.mkdir()
        self.state = other
        if self.gh.exists():
            self.gh.unlink()
        self.assertIsNone(shutil.which("gh", path=self.path_without_gh()))
        proc, counts = self.run_counts()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertGreaterEqual(counts["unavailable"], 1, counts)

    def test_new_quota_window_rearms_and_timeout_does_not_block_others(self):
        low = self.rate_payload(
            core=self.bucket(5000, 100, RESET_1),
            graphql=self.bucket(5000, 2500, RESET_1),
            search=self.bucket(30, 30, RESET_1),
        )
        low2 = self.rate_payload(
            core=self.bucket(5000, 100, RESET_2),
            graphql=self.bucket(5000, 2500, RESET_2),
            search=self.bucket(30, 30, RESET_2),
        )
        self.plant_gh_sequence([low, low, low2])
        self.plant_config(self.github_reg(cooldown_s=300, threshold=0.2))
        _, first = self.run_counts(now=NOW)
        self.assertGreaterEqual(first["events"], 1, first)
        _, persist = self.run_counts(now=NOW_30)
        self.assertEqual(persist["events"], 0, persist)
        _, reset = self.run_counts(now=NOW_60)
        self.assertGreaterEqual(reset["events"], 1, reset)

        other = self.scratch / "state-timeout"
        other.mkdir()
        self.state = other
        self.plant_job(NOOP_ID, changed_paths=[])
        self.plant_gh_sleep(
            5,
            self.rate_payload(core=self.bucket(5000, 5000, RESET_1)),
        )
        mixed = self.registry(
            monitors={
                "jobs": {
                    "enabled": True,
                    "adapter": "dispatch",
                    "schedule": "fast",
                    "params": {"store": "./jobs", "ids": [NOOP_ID]},
                },
                "github": {
                    "enabled": True,
                    "adapter": "github-rate",
                    "schedule": "fast",
                    "params": {"timeout_s": 0.2},
                },
            },
            conditions={
                "job_change": {
                    "enabled": True,
                    "monitor": "jobs",
                    "op": "changed",
                    "field": "status",
                    "threshold": None,
                    "cooldown_s": 0,
                }
            },
            routes={
                "jobs": {
                    "enabled": True,
                    "condition": "job_change",
                    "adapter": "queue",
                    "consumer": CONSUMER,
                }
            },
        )
        self.plant_config(mixed)
        t0 = time.monotonic()
        proc, counts = self.run_counts()
        elapsed = time.monotonic() - t0
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertGreaterEqual(counts["events"], 1, counts)
        self.assertGreaterEqual(counts["unavailable"], 1, counts)
        self.assertTrue(
            self.sleep_marker.is_file(),
            "gh sleep mock was not entered",
        )
        self.assertEqual(self.gh_call_count(), 1)
        self.assertIn(["api", "rate_limit"], self.gh_argvs())
        self.assertLess(elapsed, 4.0)
        _, data = self.digest_data()
        self.assertTrue(
            any(NOOP_ID in str(e.get("subject")) for e in data["events"]),
            data,
        )


class ConditionsCooldown(_MonitorFixture):
    """Entry, recovery, pending cooldown, cancel, unknown never crosses."""

    def test_numeric_emits_on_entry_not_while_matching(self):
        low = self.rate_payload(core=self.bucket(1000, 100, RESET_1))
        high = self.rate_payload(core=self.bucket(1000, 900, RESET_1))
        self.plant_gh_sequence([low, low, high, low])
        self.plant_config(self.github_reg(threshold=0.2, cooldown_s=0))
        _, a = self.run_counts(now=NOW)
        self.assertEqual(a["events"], 1, a)
        _, b = self.run_counts(now=NOW_30)
        self.assertEqual(b["events"], 0, b)
        _, c = self.run_counts(now=NOW_60)
        self.assertEqual(c["events"], 0, c)
        _, d = self.run_counts(now=NOW_90)
        self.assertEqual(d["events"], 1, d)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 2, data)

    def test_cooldown_pending_then_cancel_or_emit(self):
        low = self.rate_payload(core=self.bucket(1000, 100, RESET_1))
        high = self.rate_payload(core=self.bucket(1000, 900, RESET_1))
        self.plant_gh_sequence([low, high, low, high])
        self.plant_config(self.github_reg(threshold=0.2, cooldown_s=300))
        _, a = self.run_counts(now=NOW)
        self.assertEqual(a["events"], 1, a)
        _, b = self.run_counts(now=NOW_30)
        self.assertEqual(b["events"], 0, b)
        _, c = self.run_counts(now=NOW_60)
        self.assertEqual(c["events"], 0, c)
        _, d = self.run_counts(now=NOW_90)
        self.assertEqual(d["events"], 0, d)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 1, data)

        other = self.scratch / "state-cool-emit"
        other.mkdir()
        self.state = other
        if self.counter.exists():
            self.counter.unlink()
        self.plant_gh_sequence([low, high, low, low])
        _, a = self.run_counts(now=NOW)
        self.assertEqual(a["events"], 1, a)
        _, b = self.run_counts(now=NOW_30)
        self.assertEqual(b["events"], 0, b)
        _, c = self.run_counts(now=NOW_60)
        self.assertEqual(c["events"], 0, c)
        _, d = self.run_counts(now=NOW_400)
        self.assertEqual(d["events"], 1, d)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 2, data)

    def test_persistent_match_does_not_realert_when_cooldown_expires(self):
        low = self.rate_payload(core=self.bucket(1000, 100, RESET_1))
        self.plant_gh_sequence([low, low, low])
        self.plant_config(self.github_reg(threshold=0.2, cooldown_s=300))
        _, a = self.run_counts(now=NOW)
        self.assertEqual(a["events"], 1, a)
        _, b = self.run_counts(now=NOW_30)
        self.assertEqual(b["events"], 0, b)
        _, c = self.run_counts(now=NOW_400)
        self.assertEqual(c["events"], 0, c)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 1, data)

    def test_unknown_never_crosses_and_unavailable_matches_only_that_state(self):
        self.plant_config(
            self.dispatch_reg([MISS_ID], field="total", op="gt", threshold=-1)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 0, counts)
        other = self.scratch / "state-zero-total"
        other.mkdir()
        self.state = other
        self.plant_job(
            SPEND_ID,
            changed_paths=["ops/a.py"],
            spend=self.spend_dict(total=0, input_n=0, cached=0, output=0),
        )
        planted = json.loads(
            (self.store / SPEND_ID / "record.json").read_text(encoding="utf-8")
        )
        self.assertEqual(planted["session"]["spend"]["total"], 0)
        self.plant_config(
            self.dispatch_reg([SPEND_ID], field="total", op="gt", threshold=-1)
        )
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 1, counts)
        other = self.scratch / "state-unavail"
        other.mkdir()
        self.state = other
        self.plant_config(
            self.registry(
                monitors={
                    "jobs": {
                        "enabled": True,
                        "adapter": "dispatch",
                        "schedule": "fast",
                        "params": {"store": "./jobs", "ids": [UNREAD_ID]},
                    }
                },
                conditions={
                    "down": {
                        "enabled": True,
                        "monitor": "jobs",
                        "op": "unavailable",
                        "field": None,
                        "threshold": None,
                        "cooldown_s": 0,
                    }
                },
                routes={
                    "down": {
                        "enabled": True,
                        "condition": "down",
                        "adapter": "queue",
                        "consumer": CONSUMER,
                    }
                },
            )
        )
        self.plant_job(UNREAD_ID, record_as_dir=True)
        _, counts = self.run_counts()
        self.assertGreaterEqual(counts["unavailable"], 1, counts)
        self.assertEqual(counts["events"], 1, counts)


class DigestAckAndBounds(_MonitorFixture):
    """Per-consumer digest, explicit ack, limits, UTF-8 byte bound."""

    def _queue_many(self, n: int) -> None:
        ids = [f"rec{i:02d}-aa41" for i in range(n)]
        for rec_id in ids:
            self.plant_job(rec_id, changed_paths=[])
        self.plant_config(self.dispatch_reg(ids))
        _, counts = self.run_counts()
        self.assertEqual(counts["observations"], n, counts)
        self.assertEqual(counts["events"], n, counts)

    def test_default_limit_more_flag_and_repeatable_read(self):
        self._queue_many(12)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 10, data)
        self.assertIs(data["more"], True, data)
        self.assert_events_ordered(data["events"])
        first_ids = [e["id"] for e in data["events"]]
        _, again = self.digest_data()
        self.assertEqual([e["id"] for e in again["events"]], first_ids)
        _, limited = self.digest_data(limit=2)
        self.assertEqual(len(limited["events"]), 2, limited)
        self.assertIs(limited["more"], True, limited)
        _, rest = self.digest_data(limit=100)
        self.assertEqual(len(rest["events"]), 12, rest)
        self.assertIs(rest["more"], False, rest)

    def test_ack_advances_only_that_consumer(self):
        extra = {
            "other": {
                "enabled": True,
                "condition": "job_change",
                "adapter": "queue",
                "consumer": OTHER,
            }
        }
        self.plant_config(self.dispatch_reg([NOOP_ID, DONE_ID], extra_routes=extra))
        self.plant_job(NOOP_ID, changed_paths=[])
        self.plant_job(DONE_ID, changed_paths=["ops/a.py"])
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 4, counts)
        _, before = self.digest_data(consumer=CONSUMER, limit=10)
        self.assertEqual(len(before["events"]), 2, before)
        self.assertTrue(
            all(event["consumer"] == CONSUMER for event in before["events"]),
            before,
        )
        deliveries = [event["delivery"] for event in before["events"]]
        self.assertTrue(all(item is not None for item in deliveries), before)
        _, again = self.digest_data(consumer=CONSUMER, limit=10)
        self.assertEqual(
            [event["id"] for event in again["events"]],
            [event["id"] for event in before["events"]],
        )
        self.assertEqual(
            [event["delivery"] for event in again["events"]], deliveries
        )
        _, page = self.digest_data(consumer=CONSUMER, limit=1, ack=True)
        self.assertEqual(len(page["events"]), 1, page)
        self.assertEqual(page["events"][0]["consumer"], CONSUMER)
        self.assertEqual(page["events"][0]["delivery"], deliveries[0])
        ack_id = page["events"][0]["id"]
        _, next_page = self.digest_data(consumer=CONSUMER, limit=10)
        self.assertTrue(next_page["events"], next_page)
        self.assertNotEqual(next_page["events"][0]["id"], ack_id)
        self.assertTrue(
            all(event["consumer"] == CONSUMER for event in next_page["events"]),
            next_page,
        )
        self.assertEqual(
            [event["delivery"] for event in next_page["events"]],
            deliveries[1:],
        )
        _, other = self.digest_data(consumer=OTHER, limit=10)
        self.assertEqual(len(other["events"]), 2, other)
        self.assertTrue(
            all(e["consumer"] == OTHER for e in other["events"]), other
        )
        _, leaked = self.digest_data(consumer="nobody")
        self.assertEqual(len(leaked["events"]), 0, leaked)
        self.assertIs(leaked["more"], False, leaked)

    def test_max_bytes_is_a_cap_and_unsupported_stays_queued(self):
        self._queue_many(5)
        proc, data = self.digest_data(max_bytes=200)
        raw = (proc.stdout or "").encode("utf-8")
        self.assertLessEqual(len(raw), 200, raw)
        self.assertIsInstance(data["events"], list, data)
        if not data["events"]:
            self.assertIs(data["more"], True, data)
        for event in data["events"]:
            self.assert_event_shape(event)
            self.assertEqual(event["consumer"], CONSUMER)

        tiny_proc, tiny = self.digest_data(max_bytes=64)
        tiny_raw = (tiny_proc.stdout or "").encode("utf-8")
        self.assertLessEqual(len(tiny_raw), 64, tiny_raw)
        self.assertEqual(tiny["events"], [], tiny)
        self.assertIs(tiny["more"], True, tiny)

        proc_ok, data_ok = self.digest_data()
        raw_ok = (proc_ok.stdout or "").encode("utf-8")
        self.assertLessEqual(len(raw_ok), 4096, raw_ok)
        self.assertGreaterEqual(len(data_ok["events"]), 1, data_ok)
        self.assert_event_shape(data_ok["events"][0])
        self.assertEqual(data_ok["events"][0]["consumer"], CONSUMER)

        other = self.scratch / "state-unsup"
        other.mkdir()
        self.state = other
        body = self.dispatch_reg([NOOP_ID])
        body["routes"]["jobs"]["adapter"] = "unsupported"
        self.plant_config(body)
        self.plant_job(NOOP_ID, changed_paths=[])
        _, counts = self.run_counts()
        self.assertEqual(counts["events"], 1, counts)
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 1, data)
        self.assertEqual(data["events"][0]["delivery"], "unsupported")
        _, acked = self.digest_data(ack=True)
        self.assertEqual(acked["events"][0]["delivery"], "unsupported")
        self.assertEqual(acked["events"][0]["id"], data["events"][0]["id"])

    def test_stdout_route_is_valid_and_queue_delivery_persists(self):
        extra = {
            "announce": {
                "enabled": True,
                "condition": "job_change",
                "adapter": "stdout",
                "consumer": CONSUMER,
            }
        }
        self.plant_config(self.dispatch_reg([NOOP_ID], extra_routes=extra))
        data = self.check_ok()
        self.assertEqual(data["routes"], 2, data)
        self.plant_job(NOOP_ID, changed_paths=[])
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        lines = [
            line for line in (proc.stdout or "").splitlines() if line.strip()
        ]
        self.assertTrue(lines, self.combined(proc))
        parsed = []
        for line in lines:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError as exc:
                self.fail(f"stdout line is not JSON: {line!r} ({exc})")
        self.assertGreaterEqual(len(parsed), 2, parsed)
        counts = parsed[-1]
        self.assertIsInstance(counts, dict, counts)
        for key in ("observations", "events", "unavailable"):
            self.assertIn(key, counts, counts)
            self.assert_int_count(counts[key], key)
        self.assertGreaterEqual(counts["events"], 1, counts)
        stdout_events = parsed[:-1]
        announce = [
            event
            for event in stdout_events
            if isinstance(event, dict) and event.get("route") == "announce"
        ]
        self.assertTrue(announce, stdout_events)
        for event in announce:
            self.assertEqual(event.get("route"), "announce", event)
            self.assertEqual(event.get("consumer"), CONSUMER, event)
            self.assertEqual(event.get("delivery"), "stdout", event)
        _, page = self.digest_data()
        queued = [event for event in page["events"] if event.get("route") == "jobs"]
        self.assertTrue(queued, page)
        self.assertTrue(
            all(event["consumer"] == CONSUMER for event in queued), queued
        )
        deliveries = [event["delivery"] for event in queued]
        _, again = self.digest_data()
        again_queued = [
            event for event in again["events"] if event.get("route") == "jobs"
        ]
        self.assertEqual(
            [event["id"] for event in again_queued],
            [event["id"] for event in queued],
        )
        self.assertEqual(
            [event["delivery"] for event in again_queued], deliveries
        )
        _, acked = self.digest_data(ack=True)
        acked_queued = [
            event for event in acked["events"] if event.get("route") == "jobs"
        ]
        self.assertEqual(
            [event["delivery"] for event in acked_queued], deliveries
        )


class RestartConcurrentAndLoop(_MonitorFixture):
    """Restart and concurrent runs share one queue; invalid reload aborts."""

    def test_restart_does_not_duplicate_and_skips_missed_intervals(self):
        self.plant_gh_rate(
            self.rate_payload(core=self.bucket(5000, 5000, RESET_1))
        )
        body = self.dispatch_reg([NOOP_ID])
        body["monitors"]["github"] = {
            "enabled": True,
            "adapter": "github-rate",
            "schedule": "fast",
            "params": {"timeout_s": 10},
        }
        self.plant_config(body)
        self.plant_job(NOOP_ID, changed_paths=[])
        _, first = self.run_counts(now=NOW)
        self.assertEqual(first["events"], 1, first)
        self.assertEqual(self.gh_argvs(), [["api", "rate_limit"]])
        _, second = self.run_counts(now=NOW_400)
        self.assertEqual(second["events"], 0, second)
        self.assertEqual(
            self.gh_argvs(),
            [["api", "rate_limit"], ["api", "rate_limit"]],
        )
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 1, data)

    def test_concurrent_once_cannot_double_enqueue_the_same_transition(self):
        # External concurrent start only. A correct atomic due-claim may
        # admit one observer; this does not force both into enqueue.
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.plant_job(NOOP_ID, changed_paths=[])
        barrier = threading.Barrier(2)

        def once():
            barrier.wait(timeout=5)
            return self.run_once(new_session=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(once)
            fb = pool.submit(once)
            pa = fa.result(timeout=RUN_CAP)
            pb = fb.result(timeout=RUN_CAP)
        for proc in (pa, pb):
            data = self.parse_json(proc, expect_exit=0)
            self.assert_int_count(data["events"], "events")
        _, data = self.digest_data()
        self.assertEqual(len(data["events"]), 1, data)

    def test_invalid_reload_during_loop_exits_3_and_preserves_queue(self):
        self.plant_gh_rate(
            self.rate_payload(core=self.bucket(5000, 5000, RESET_1))
        )
        valid = self.dispatch_reg([NOOP_ID])
        valid["monitors"]["github"] = {
            "enabled": True,
            "adapter": "github-rate",
            "schedule": "fast",
            "params": {"timeout_s": 10},
        }
        self.plant_config(valid)
        valid_bytes = self.config.read_bytes()
        self.plant_job(NOOP_ID, changed_paths=[])
        evidence = (self.store / NOOP_ID / "record.json").read_bytes()
        _, counts = self.run_counts()
        self.assertGreaterEqual(counts["events"], 1, counts)
        calls_before = len(self.gh_argvs())
        self.assertGreaterEqual(calls_before, 1)
        _, before = self.digest_data()
        n = len(before["events"])
        self.assertGreaterEqual(n, 1)
        env = self.env(agent=AGENT)
        loop = subprocess.Popen(
            [sys.executable, str(self.staged), *self.cfg_args(), "run", "--loop"],
            cwd=str(self.cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
        self.world.own(loop)
        self._workers.append(loop)
        deadline = time.monotonic() + max(LOOP_CAP - 2, 2)
        while time.monotonic() < deadline:
            if len(self.gh_argvs()) > calls_before:
                break
            if loop.poll() is not None:
                break
            time.sleep(0.05)
        else:
            loop.kill()
            loop.communicate(timeout=2)
            self.fail("loop did not start an observation before invalid reload")
        self.assertIsNone(loop.poll(), "loop exited before invalid reload")
        self._plant_bytes(self.config, b"{")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(self.config.read_text(encoding="utf-8"))
        try:
            _stdout, stderr = loop.communicate(timeout=LOOP_CAP)
        except subprocess.TimeoutExpired:
            loop.kill()
            loop.communicate(timeout=2)
            self.fail("loop did not abort on invalid reload")
        self.assertEqual(loop.returncode, 3, stderr)
        self._plant_bytes(self.config, valid_bytes)
        self.assertEqual(self.config.read_bytes(), valid_bytes)
        _, after = self.digest_data()
        self.assertEqual(len(after["events"]), n, after)
        self.assertEqual(
            (self.store / NOOP_ID / "record.json").read_bytes(), evidence
        )

    def test_state_defaults_under_xdg_lane_name(self):
        self.plant_lane_conf(LANE_NAME="lanex")
        self.plant_config(self.dispatch_reg([NOOP_ID]))
        self.plant_job(NOOP_ID, changed_paths=[])
        proc = self.invoke(
            ["--config", str(self.config), "run", "--once", "--now", NOW],
            agent=AGENT,
        )
        data = self.parse_json(proc, expect_exit=0)
        self.assertGreaterEqual(data["events"], 1, data)
        lane_state = self.xdg / "lanex" / "monitor"
        default_state = self.xdg / "agentscient" / "monitor"
        self.assertTrue(lane_state.exists(), f"missing {lane_state}")
        self.assertFalse(
            default_state.exists(),
            "wrote the literal agentscient monitor state",
        )


if __name__ == "__main__":
    unittest.main()

