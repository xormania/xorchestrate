"""Lane-MCP acceptance tests, written from the contracts.

Contracts (only): `ops/conductor/lane-mcp-lever.md` and the inherited
`ops/conductor/instrument-conventions.md`. Named counterparties:
`ops/conductor/monitor-lever.md` (event fields and codex-usage numeric
names/semantics) and the monitor.db schema at c0ce300. The tools
`ops/conductor/tools/lane_mcp.py` and `ops/conductor/tools/lane_read.py`
are the subject and are not read here.

Tests invoke the documented CLI as a subprocess with owned synthetic
fixtures: `python3 -B ops/conductor/tools/lane_mcp.py --config FILE
check|serve|query TOOL [JSON]`. No live dispatch store, network,
credentials, example config, or harness calls. MCP is exercised as
UTF-8 newline-delimited JSON-RPC on that same CLI's `serve` stdio.

World owns HOME, the XDG roots and TMPDIR; every subprocess goes
through run with a timeout. The implementation is absent. A missing
tool file is one red (`test_the_tool_file_is_present`). Every other
case stages a copy-tool-bytes stand-in so a missing file is not every
test's failure: when the tool exists its bytes are copied and run;
when it does not, a no-op stub (exit 0, no output) is planted so each
assertion fails on the contract promise rather than Python's
missing-file exit 2. Absent-entrypoint red is the presence test
alone. This run does not claim a later green.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import sys
import unittest
from pathlib import Path

from ops.testkit import Result, World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "lane_mcp.py"
READ = TOOL.with_name("lane_read.py")  # test_lane_mcp_review.py and test_lane_mcp_context.py import it
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("lane_read.py", "instrument.py", "mcp_transport.py", "dispatch_observation.py")

RUN_CAP = 20
FIFO_CAP = 5
JOB = "jobAa41mcp1"
JOB2 = "jobAa41mcp2"
JOB_UNLISTED = "jobAa41mcp9"
CONSUMER = "conductor"
OTHER = "other-conductor"
MODEL = "model-token-aa41mcp"
HEAD = "headtokenaa41mcp0000000000000000000001"
VERDICT = "approve"
COUNTS = {"p1": 7, "p2": 3, "p3": 1, "opinions": 5}
FINDING = "FINDING_MUST_NOT_LEAK_aa41mcp"
NOTE = "NOTE_MUST_NOT_LEAK_aa41mcp"
PROMPT = "PROMPT_MUST_NOT_LEAK_aa41mcp"
TRANSCRIPT = "TRANSCRIPT_MUST_NOT_LEAK_aa41mcp"
USAGE_INPUT = 400
USAGE_CACHED = 100
USAGE_OUTPUT = 50
USAGE_TOTAL = 450
USAGE_LAST_INPUT = 80
USAGE_LAST_CACHED = 20
USAGE_WINDOW = 200
USAGE_OLD_INPUT = 111111
SCHEMA = "agentscient.lane/v1"
TOOLS = ("lane_record", "lane_alerts", "lane_usage", "lane_brief")
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
OMISSIONS = (
    "no live PR/CI/review readiness",
    "no watchdog stdout history",
    "no automatic discovery of other jobs",
    "no idle-agent wakeup",
)
PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
# Counterparty schema at c0ce300; tests plant this shape, they do not
# import the monitor instrument.
MONITOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
  monitor TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, next_due REAL);
CREATE TABLE IF NOT EXISTS condition_state (
  condition_id TEXT NOT NULL, subject TEXT NOT NULL,
  fingerprint TEXT NOT NULL, generation TEXT NOT NULL, epoch INTEGER NOT NULL,
  baseline TEXT, matching INTEGER NOT NULL, pending INTEGER NOT NULL,
  last_emit REAL, sequence INTEGER NOT NULL,
  PRIMARY KEY(condition_id, subject));
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL,
  consumer TEXT NOT NULL, condition_id TEXT NOT NULL, monitor TEXT NOT NULL,
  subject TEXT NOT NULL, state TEXT NOT NULL, summary TEXT NOT NULL,
  evidence TEXT, observed_at TEXT NOT NULL, delivery TEXT NOT NULL,
  dedup_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS cursors (
  consumer TEXT PRIMARY KEY, event_id INTEGER NOT NULL);
"""


def _tree_files(root: Path) -> tuple[str, ...]:
    if not root.exists():
        return ()
    names: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() or path.is_symlink():
            names.append(str(path.relative_to(root)))
    return tuple(sorted(names))


def _json_object(text: object) -> dict | None:
    if not isinstance(text, str):
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _find_lane(value: object) -> list[dict]:
    # Envelope boundary only: brief embeds nested record/alerts objects.
    found: list[dict] = []
    if not isinstance(value, dict):
        return found
    envelope_keys = ("structuredContent", "content", "contents")
    has_envelope = any(key in value for key in envelope_keys)
    if has_envelope:
        structured = value.get("structuredContent")
        if isinstance(structured, dict) and structured.get("schema") == SCHEMA:
            found.append(structured)
        content = value.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                parsed = _json_object(item.get("text"))
                if parsed is not None and parsed.get("schema") == SCHEMA:
                    found.append(parsed)
        contents = value.get("contents")
        if isinstance(contents, list):
            for item in contents:
                if not isinstance(item, dict):
                    continue
                parsed = _json_object(item.get("text"))
                if parsed is not None and parsed.get("schema") == SCHEMA:
                    found.append(parsed)
        return found
    if value.get("schema") == SCHEMA:
        found.append(value)
    return found


def _dedupe(objs: list[dict]) -> list[dict]:
    uniq: list[dict] = []
    for obj in objs:
        if obj not in uniq:
            uniq.append(obj)
    return uniq


def rpc(method: str, rpc_id: object, params: object | None = None) -> str:
    msg: dict = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg, separators=(",", ":"))


def notify(method: str, params: object | None = None) -> str:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg, separators=(",", ":"))


class _LaneMcpFixture(unittest.TestCase):
    """Scratch HOME, staged CLI copy, owned config and sources."""

    def setUp(self):
        self.world = World(prefix="lane-mcp-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.scratch = self.world.path("scratch")
        self.work = self.world.path("work")
        self.cwd = self.world.cwd
        self.scratch.mkdir()
        self.work.mkdir()
        self._stage_tool_bytes()
        self.store = self.work / "store"
        self.state_dir = self.work / "monitor"
        self.db = self.state_dir / "monitor.db"
        self.rollout = self.work / "rollout.jsonl"
        self.config_path = self.work / "config.json"
        self.plant_config()
        self.assertFalse(
            (self.home / ".local" / "state" / "agentscient" / "dispatch").exists(),
            "plant: isolated store must start absent",
        )

    def _stage_tool_bytes(self) -> None:
        self.staged = self.world.stage(TOOL, "lane_mcp.py", siblings=SIBLINGS)
        if TOOL.is_file():
            self.assertEqual(
                self.staged.read_bytes(),
                TOOL.read_bytes(),
                "copy-tool-bytes did not land intact",
            )
            for name in SIBLINGS:
                sibling = self.staged.with_name(name)
                self.assertEqual(
                    sibling.read_bytes(),
                    TOOL.with_name(name).read_bytes(),
                )
        self.assertTrue(self.staged.is_file())

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def env(self, extra: dict | None = None) -> dict:
        extra_env: dict[str, str] = {}
        drop: list[str] = []
        if extra:
            for key, value in extra.items():
                if value is None:
                    drop.append(key)
                else:
                    extra_env[key] = value
        env = self.world.env(extra=extra_env or None)
        for key in drop:
            env.pop(key, None)
        return env

    def plant_config(
        self,
        payload: dict | str | bytes | None = None,
        *,
        path: Path | None = None,
        job_ids: list[str] | None = None,
    ) -> Path:
        dest = path if path is not None else self.config_path
        if payload is None:
            payload = {
                "version": 1,
                "dispatch_store": "store",
                "job_ids": list(job_ids if job_ids is not None else [JOB]),
                "monitor_state": "monitor",
                "consumer": CONSUMER,
                "usage_rollout": "rollout.jsonl",
            }
        if isinstance(payload, bytes):
            raw = payload
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        self._plant_bytes(dest, raw)
        return dest

    def plant_record(
        self,
        rec_id: str = JOB,
        *,
        status: str = "closed",
        model: object = MODEL,
        extra: dict | None = None,
        raw: bytes | None = None,
    ) -> Path:
        rec_path = self.store / rec_id / "record.json"
        if raw is not None:
            self._plant_bytes(rec_path, raw)
            return rec_path
        rec = {
            "id": rec_id,
            "status": status,
            "model": model,
            "result": {
                "head": HEAD,
                "envelope": {
                    "verdict": VERDICT,
                    "counts": dict(COUNTS),
                    "findings": [{"claim": FINDING}],
                    "note": NOTE,
                },
                "changed_paths": ["alpha.py", "beta.py"],
            },
            "attempts": [{}, {}],
            "session": {
                "spend": {
                    "harness": "grok",
                    "total": 99,
                    "out": 11,
                    "runs": 2,
                    "secret": TRANSCRIPT,
                }
            },
            "prompt": PROMPT,
        }
        if extra:
            rec.update(extra)
        body = json.dumps(rec, indent=2) + "\n"
        self._plant_bytes(rec_path, body.encode("utf-8"))
        landed = json.loads(rec_path.read_text(encoding="utf-8"))
        self.assertEqual(landed["id"], rec_id)
        self.assertEqual(landed["status"], status)
        return rec_path

    def plant_db(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.db.exists():
            self.db.unlink()
        con = sqlite3.connect(str(self.db))
        try:
            con.executescript(MONITOR_SCHEMA)
            con.commit()
        finally:
            con.close()
        self.assertTrue(self.db.is_file())
        self.assertGreater(self.db.stat().st_size, 0)
        return self.db

    def plant_events(self, rows: list[dict], *, cursor: dict | None = None) -> None:
        self.plant_db()
        con = sqlite3.connect(str(self.db))
        try:
            for row in rows:
                con.execute(
                    "INSERT INTO events(id, route, consumer, condition_id, "
                    "monitor, subject, state, summary, evidence, observed_at, "
                    "delivery, dedup_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        row.get("route", "jobs"),
                        row["consumer"],
                        row.get("condition", "job_change"),
                        row.get("monitor", "jobs"),
                        row.get("subject", "subject-" + str(row["id"])),
                        row.get("state", "ok"),
                        row.get("summary", "summary-" + str(row["id"])),
                        json.dumps(row.get("evidence", "evidence-" + str(row["id"]))),
                        row.get("observed_at", "2026-09-04T12:00:00Z"),
                        row.get("delivery", "queued"),
                        row.get("dedup_key", "dedup-" + str(row["id"])),
                    ),
                )
            if cursor:
                for consumer, event_id in cursor.items():
                    con.execute(
                        "INSERT INTO cursors(consumer, event_id) VALUES (?,?)",
                        (consumer, event_id),
                    )
            con.commit()
            counted = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(counted, len(rows))
        finally:
            con.close()

    def token_row(
        self,
        *,
        ts: str = "2026-09-04T12:00:00.000Z",
        input_n: int | bool | None = USAGE_INPUT,
        cached: int | None = USAGE_CACHED,
        output: int | None = USAGE_OUTPUT,
        total: int | None = USAGE_TOTAL,
        last_input: int | None = USAGE_LAST_INPUT,
        last_cached: int | None = USAGE_LAST_CACHED,
        window: int | None = USAGE_WINDOW,
    ) -> dict:
        usage: dict = {}
        if input_n is not None:
            usage["input_tokens"] = input_n
        if cached is not None:
            usage["cached_input_tokens"] = cached
        if output is not None:
            usage["output_tokens"] = output
        if total is not None:
            usage["total_tokens"] = total
        info: dict = {"total_token_usage": usage}
        last: dict = {}
        if last_input is not None:
            last["input_tokens"] = last_input
        if last_cached is not None:
            last["cached_input_tokens"] = last_cached
        if last:
            info["last_token_usage"] = last
        if window is not None:
            info["model_context_window"] = window
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
        return data

    def invoke(
        self,
        args: list[str],
        *,
        config: Path | None = None,
        stdin: str | None = None,
        extra_env: dict | None = None,
        cap: int = RUN_CAP,
        minus_b: bool = True,
    ) -> Result:
        cfg = self.config_path if config is None else config
        py = [sys.executable]
        if minus_b:
            py.append("-B")
        argv = [*py, str(self.staged), "--config", str(cfg), *args]
        return run(
            argv,
            cwd=str(self.cwd),
            env=self.env(extra_env),
            stdin=stdin,
            timeout=cap,
        )

    def query(self, tool: str, arguments: dict | None = None, **kwargs) -> Result:
        args = ["query", tool]
        if arguments is not None:
            args.append(json.dumps(arguments, separators=(",", ":")))
        return self.invoke(args, **kwargs)

    def serve(self, lines: list[str], **kwargs) -> Result:
        payload = "".join(
            line if line.endswith("\n") else line + "\n" for line in lines
        )
        return self.invoke(["serve"], stdin=payload, **kwargs)

    def rpc_objects(self, proc: Result) -> list[dict]:
        self.assertTrue(proc.stdout.strip(), "stdout empty")
        objs: list[dict] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                objs.append(json.loads(line))
            except ValueError:
                self.fail(f"non-JSON stdout line: {line!r}")
        self.assertTrue(objs, proc.stdout)
        return objs

    def load_query(self, proc: Result) -> dict:
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip(), "stdout empty")
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"stdout is not JSON: {proc.stdout!r}")
        self.assertIsInstance(payload, dict, payload)
        return payload

    def assert_refusal(self, proc: Result, class_token: str | None = None) -> str:
        refusal = proc.refusal()
        if class_token is not None:
            self.assertEqual(refusal.cls, class_token, refusal.line)
        return refusal.line

    def assert_lane(self, payload: dict, tool: str) -> dict:
        self.assertIsInstance(payload, dict, payload)
        self.assertEqual(payload.get("schema"), SCHEMA, payload)
        self.assertEqual(payload.get("query"), tool, payload)
        self.assertIn(payload.get("state"), ("ok", "unknown", "unavailable"), payload)
        dumped = json.dumps(payload)
        for leak in (FINDING, NOTE, PROMPT, TRANSCRIPT):
            self.assertNotIn(leak, dumped)
        return payload

    def lane_from_rpc(self, obj: dict) -> dict:
        found = _dedupe(_find_lane(obj.get("result")))
        self.assertEqual(len(found), 1, obj)
        return found[0]

    def reset_sources(self) -> None:
        for path in (self.store, self.state_dir):
            if path.exists():
                shutil.rmtree(path)
        if self.rollout.exists() or self.rollout.is_symlink():
            self.rollout.unlink()
        self.plant_config()

    def rpc_by_id(self, objs: list[dict], rpc_id: object) -> dict:
        for obj in objs:
            if obj.get("id") == rpc_id:
                return obj
        self.fail(f"missing rpc id {rpc_id}: {objs}")

    def source_bytes(self) -> dict[str, bytes | None]:
        paths = {
            "config": self.config_path,
            "record": self.store / JOB / "record.json",
            "db": self.db,
            "rollout": self.rollout,
        }
        snapped: dict[str, bytes | None] = {}
        for name, path in paths.items():
            if path.exists() and path.is_file() and not path.is_symlink():
                snapped[name] = path.read_bytes()
            elif path.is_symlink() or path.exists():
                snapped[name] = b"*"
            else:
                snapped[name] = None
        return snapped

    def handshake(
        self,
        requests: list[str],
        *,
        version: str = "2025-06-18",
        initialized: bool = True,
    ) -> list[str]:
        lines = [
            rpc(
                "initialize",
                1,
                {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "lane-mcp-oracle", "version": "0"},
                },
            )
        ]
        if initialized:
            lines.append(notify("notifications/initialized"))
        lines.extend(requests)
        return lines




class TestLaneMcpCliWire(_LaneMcpFixture):
    def test_check_validates_without_reading_sources(self):
        fifo = self.store / "blocked"
        fifo.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(fifo)
        self.assertTrue(stat.S_ISFIFO(fifo.stat().st_mode))
        self.plant_config(
            {
                "version": 1,
                "dispatch_store": "store/blocked",
                "job_ids": [JOB],
                "monitor_state": "monitor",
                "consumer": CONSUMER,
                "usage_rollout": "rollout.jsonl",
            }
        )
        proc = self.invoke(["check"], cap=FIFO_CAP)
        payload = self.load_query(proc)
        self.assertEqual(payload, {"valid": True, "tools": 4})
        self.assertFalse(self.db.exists())
        self.assertFalse(self.rollout.exists())

    def test_hermetic_config_refusal_matches_the_wire(self):
        proc = self.invoke(["check"], config=Path("/dev/null"))
        self.assert_refusal(proc, "config")

    def test_config_and_argument_refusals_use_the_wire(self):
        long_path = "p" * 1025
        long_id = "A" + ("b" * 128)
        long_consumer = "c" * 129
        extra = {
            "version": 1,
            "dispatch_store": "store",
            "job_ids": [JOB],
            "monitor_state": "monitor",
            "consumer": CONSUMER,
            "usage_rollout": "rollout.jsonl",
            "extra": 1,
        }
        dup = (
            '{"version":1,"version":2,"dispatch_store":"store","job_ids":["'
            + JOB
            + '"],"monitor_state":"monitor","consumer":"conductor",'
            '"usage_rollout":"rollout.jsonl"}\n'
        )
        fat = json.dumps(
            {
                "version": 1,
                "dispatch_store": "store",
                "job_ids": [JOB],
                "monitor_state": "monitor",
                "consumer": CONSUMER,
                "usage_rollout": "x" * (64 * 1024),
            }
        )
        cases = [
            (
                "unknown-key",
                extra,
                ["check"],
                "config",
            ),
            (
                "duplicate-keys",
                dup,
                ["check"],
                "config",
            ),
            (
                "version-2",
                {
                    "version": 2,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "bool-version",
                {
                    "version": True,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "missing-key",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                },
                ["check"],
                "config",
            ),
            (
                "bad-id",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": ["-bad"],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "dup-ids",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [JOB, JOB],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "too-many-ids",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [f"id{n}" for n in range(21)],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "long-id",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [long_id],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "slash-consumer",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": "a/b",
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "backslash-consumer",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": "a\\b",
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "control-consumer",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": "bad\nname",
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "long-consumer",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": long_consumer,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            (
                "long-path",
                {
                    "version": 1,
                    "dispatch_store": long_path,
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
                ["check"],
                "config",
            ),
            ("fat-config", fat, ["check"], "config"),
            ("unknown-verb", None, ["frobnicate"], "arguments"),
            (
                "record-without-id",
                None,
                ["query", "lane_record", "{}"],
                "arguments",
            ),
            (
                "unknown-field",
                None,
                ["query", "lane_usage", '{"extra":1}'],
                "arguments",
            ),
            (
                "bool-after",
                None,
                ["query", "lane_alerts", '{"after":true}'],
                "arguments",
            ),
            (
                "malformed-json",
                None,
                ["query", "lane_brief", "{"],
                "arguments",
            ),
        ]
        self.assertEqual(len(cases), 20)
        for name, payload, args, class_token in cases:
            with self.subTest(name=name):
                if payload is not None:
                    self.plant_config(payload)
                else:
                    self.plant_config()
                proc = self.invoke(list(args))
                self.assert_refusal(proc, class_token)

    def test_reads_need_no_identity(self):
        self.plant_record()
        env = self.env()
        self.assertNotIn("WF_AGENT", env)
        proc = self.query("lane_record", {"id": JOB})
        payload = self.assert_lane(self.load_query(proc), "lane_record")
        self.assertEqual(payload["state"], "ok")

    def test_relative_paths_resolve_from_the_config_file(self):
        self.plant_record()
        proc = self.query("lane_record", {"id": JOB})
        payload = self.assert_lane(self.load_query(proc), "lane_record")
        self.assertEqual(payload["state"], "ok")
        record = payload["record"]
        self.assertEqual(record["id"], JOB)
        self.assertIn("record.json", str(record["evidence"]))


class TestLaneMcpRecord(_LaneMcpFixture):
    def test_lane_record_projects_allowlisted_summary(self):
        rec = self.plant_record()
        state_path = self.store / JOB / "state.json"
        self._plant_bytes(
            state_path,
            json.dumps({"pid": 1, "session": {"id": "live"}}).encode("utf-8"),
        )
        proc = self.query("lane_record", {"id": JOB})
        payload = self.assert_lane(self.load_query(proc), "lane_record")
        self.assertEqual(payload["state"], "ok")
        record = payload["record"]
        for key in (
            "id",
            "recorded_status",
            "model",
            "head",
            "verdict",
            "counts",
            "changed_count",
            "attempt_count",
            "spend",
            "evidence",
        ):
            self.assertIn(key, record, record)
        self.assertEqual(record["id"], JOB)
        self.assertEqual(record["recorded_status"], "closed")
        self.assertEqual(record["model"], MODEL)
        self.assertEqual(record["head"], HEAD)
        self.assertEqual(record["verdict"], VERDICT)
        self.assertEqual(record["counts"], COUNTS)
        self.assertEqual(record["changed_count"], 2)
        self.assertEqual(record["attempt_count"], 2)
        self.assertIsInstance(record["spend"], dict)
        self.assertEqual(record["spend"].get("total"), 99)
        self.assertNotIn("secret", record["spend"])
        evidence = Path(str(record["evidence"]))
        self.assertEqual(evidence.name, rec.name)
        self.assertEqual(evidence.parent.name, JOB)

    def test_lane_record_missing_hostile_and_unconfigured(self):
        cases = []

        def add(name, setup, arguments, expect_state=None, refuse=False):
            cases.append((name, setup, arguments, expect_state, refuse))

        add("missing", lambda: None, {"id": JOB}, "unknown")

        def _malformed():
            self.plant_record(raw=b"{not-json\n")

        add("malformed", _malformed, {"id": JOB}, "unavailable")

        def _oversize():
            self.plant_record(raw=b"{" + (b"x" * (1048576 + 1)) + b"}")

        add("oversize", _oversize, {"id": JOB}, "unavailable")

        def _fifo():
            path = self.store / JOB / "record.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            os.mkfifo(path)
            self.assertTrue(stat.S_ISFIFO(path.stat().st_mode))

        add("fifo", _fifo, {"id": JOB}, "unavailable")

        def _symlink_record():
            real_dir = self.scratch / "elsewhere" / JOB
            real_dir.mkdir(parents=True)
            target = real_dir / "record.json"
            self._plant_bytes(
                target,
                json.dumps({"id": JOB, "status": "closed", "model": MODEL}).encode(),
            )
            job_dir = self.store / JOB
            job_dir.mkdir(parents=True)
            (job_dir / "record.json").symlink_to(target)
            self.assertTrue((job_dir / "record.json").is_symlink())

        add("symlink-record", _symlink_record, {"id": JOB}, "unavailable")

        def _symlink_job():
            real_dir = self.scratch / "job-target" / JOB
            real_dir.mkdir(parents=True)
            self._plant_bytes(
                real_dir / "record.json",
                json.dumps({"id": JOB, "status": "closed", "model": MODEL}).encode(),
            )
            self.store.mkdir(parents=True, exist_ok=True)
            (self.store / JOB).symlink_to(real_dir)
            self.assertTrue((self.store / JOB).is_symlink())

        add("symlink-job", _symlink_job, {"id": JOB}, "unavailable")
        add("unconfigured", self.plant_record, {"id": JOB_UNLISTED}, None, True)
        add("path-id", self.plant_record, {"id": "../secret"}, None, True)
        self.assertEqual(len(cases), 8)
        for name, setup, arguments, expect_state, refuse in cases:
            with self.subTest(name=name):
                self.reset_sources()
                setup()
                cap = FIFO_CAP if name == "fifo" else RUN_CAP
                proc = self.query("lane_record", arguments, cap=cap)
                if refuse:
                    self.assert_refusal(proc)
                    continue
                payload = self.assert_lane(self.load_query(proc), "lane_record")
                self.assertEqual(payload["state"], expect_state, payload)
                self.assertNotIn(MODEL, proc.stdout)
                if payload["state"] != "ok":
                    self.assertTrue(payload.get("reason"))

    def test_text_fields_capped_and_nested_malformed_do_not_crash(self):
        long_model = "m" * 300
        rec = {
            "id": JOB,
            "status": "closed",
            "model": long_model,
            "result": {
                "head": HEAD,
                "envelope": {"verdict": VERDICT, "counts": "not-an-object"},
                "changed_paths": "not-a-list",
            },
            "attempts": "not-a-list",
            "session": {"spend": [1, 2, 3]},
        }
        self.plant_record(raw=(json.dumps(rec) + "\n").encode("utf-8"))
        proc = self.query("lane_record", {"id": JOB})
        payload = self.assert_lane(self.load_query(proc), "lane_record")
        self.assertIn(payload["state"], ("ok", "unavailable"), payload)
        if payload["state"] == "ok":
            model = payload["record"]["model"]
            self.assertIsNone(model)
            counts = payload["record"]["counts"]
            self.assertEqual(counts, {})
            spend = payload["record"]["spend"]
            self.assertEqual(spend, {})
            self.assertIsNone(payload["record"]["changed_count"])
            self.assertIsNone(payload["record"]["attempt_count"])


class TestLaneMcpAlerts(_LaneMcpFixture):
    def test_lane_alerts_empty_unknown_unavailable(self):
        missing = self.query("lane_alerts", {})
        payload = self.assert_lane(self.load_query(missing), "lane_alerts")
        self.assertEqual(payload["state"], "unknown")

        self.plant_db()
        empty = self.query("lane_alerts", {})
        payload = self.assert_lane(self.load_query(empty), "lane_alerts")
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["events"], [])
        self.assertIn("more", payload)
        self.assertIn("next_after", payload)

        self._plant_bytes(self.db, b"this is not a database\n")
        bad = self.query("lane_alerts", {})
        payload = self.assert_lane(self.load_query(bad), "lane_alerts")
        self.assertEqual(payload["state"], "unavailable")
        self.assertTrue(payload.get("reason"))

        fifo_dir = self.work / "fifo-state"
        fifo_dir.mkdir()
        os.mkfifo(fifo_dir / "monitor.db")
        self.plant_config(
            {
                "version": 1,
                "dispatch_store": "store",
                "job_ids": [JOB],
                "monitor_state": "fifo-state",
                "consumer": CONSUMER,
                "usage_rollout": "rollout.jsonl",
            }
        )
        fifo = self.query("lane_alerts", {}, cap=FIFO_CAP)
        payload = self.assert_lane(self.load_query(fifo), "lane_alerts")
        self.assertEqual(payload["state"], "unavailable")

    def test_lane_alerts_pagination_consumer_and_cursors(self):
        rows = []
        for event_id, consumer, delivery in (
            (10, CONSUMER, "queued"),
            (11, OTHER, "queued"),
            (12, CONSUMER, "unsupported"),
            (13, CONSUMER, "delivered"),
            (14, CONSUMER, "queued"),
            (15, CONSUMER, "queued"),
            (16, CONSUMER, "queued"),
        ):
            rows.append(
                {
                    "id": event_id,
                    "consumer": consumer,
                    "delivery": delivery,
                    "observed_at": f"2026-09-04T12:00:{event_id:02d}Z",
                    "evidence": f"ev-{event_id}",
                    "summary": f"sum-{event_id}",
                }
            )
        self.plant_events(rows, cursor={CONSUMER: 100, OTHER: 0})
        before = self.db.read_bytes()
        proc = self.query("lane_alerts", {"after": 0, "limit": 2})
        payload = self.assert_lane(self.load_query(proc), "lane_alerts")
        self.assertEqual(payload["state"], "ok")
        events = payload["events"]
        self.assertEqual(len(events), 2, payload)
        ids = [event["id"] for event in events]
        self.assertEqual(ids, [10, 12])
        for event in events:
            for key in EVENT_KEYS:
                self.assertIn(key, event, event)
            self.assertEqual(event["consumer"], CONSUMER)
            self.assertIn(event["delivery"], ("queued", "unsupported"))
            self.assertTrue(event["observed_at"])
            self.assertTrue(event["evidence"])
        self.assertEqual(payload["more"], True)
        self.assertEqual(payload["next_after"], 12)
        page = self.query("lane_alerts", {"after": 12, "limit": 2})
        nxt = self.assert_lane(self.load_query(page), "lane_alerts")
        self.assertEqual([event["id"] for event in nxt["events"]], [14, 15])
        self.assertEqual(nxt["more"], True)
        self.assertEqual(nxt["next_after"], 15)
        last = self.query("lane_alerts", {"after": 15, "limit": 5})
        tail = self.assert_lane(self.load_query(last), "lane_alerts")
        self.assertEqual([event["id"] for event in tail["events"]], [16])
        self.assertEqual(tail["more"], False)
        self.assertEqual(self.db.read_bytes(), before)

    def test_lane_alerts_unadvanceable_oversize_event_is_unavailable(self):
        self.plant_events(
            [
                {
                    "id": 1,
                    "consumer": CONSUMER,
                    "summary": "S" * 5000,
                    "delivery": "queued",
                }
            ]
        )
        proc = self.query("lane_alerts", {"after": 0, "limit": 5})
        payload = self.assert_lane(self.load_query(proc), "lane_alerts")
        self.assertEqual(payload["state"], "unavailable")
        self.assertTrue(payload.get("reason"))
        self.assertFalse(payload.get("state") == "ok" and payload.get("events") == [])


class TestLaneMcpUsage(_LaneMcpFixture):
    def test_lane_usage_tail_numeric_timestamp_and_unknown(self):
        missing = self.query("lane_usage", {})
        payload = self.assert_lane(self.load_query(missing), "lane_usage")
        self.assertEqual(payload["state"], "unknown")
        metrics = payload.get("metrics") or {}
        self.assertNotEqual(metrics.get("input"), 0)
        self.assertNotEqual(metrics.get("total"), 0)

        old = self.token_row(input_n=USAGE_OLD_INPUT, ts="2026-09-04T10:00:00.000Z")
        new = self.token_row(ts="2026-09-04T16:00:00-04:00")
        chatter = {
            "timestamp": "2026-09-04T11:00:00.000Z",
            "type": "event_msg",
            "payload": {"type": "agent_message", "text": TRANSCRIPT},
        }
        prefix = (json.dumps(old) + "\n").encode("utf-8") + (b"Y" * 1048576)
        self.plant_rollout(
            [chatter, new], prefix=prefix, suffix=b'{"type":"event_msg","pay'
        )
        proc = self.query("lane_usage", {})
        payload = self.assert_lane(self.load_query(proc), "lane_usage")
        self.assertEqual(payload["state"], "ok")
        metrics = payload["metrics"]
        for key in (
            "input",
            "cached",
            "output",
            "total",
            "last_input",
            "last_cached",
            "context_window",
            "cache_fraction",
            "context_fraction",
        ):
            self.assertIn(key, metrics, metrics)
        self.assertEqual(metrics["input"], USAGE_INPUT)
        self.assertEqual(metrics["cached"], USAGE_CACHED)
        self.assertEqual(metrics["output"], USAGE_OUTPUT)
        self.assertEqual(metrics["total"], USAGE_TOTAL)
        self.assertNotEqual(metrics["total"], USAGE_INPUT + USAGE_CACHED + USAGE_OUTPUT)
        self.assertEqual(metrics["last_input"], USAGE_LAST_INPUT)
        self.assertEqual(metrics["last_cached"], USAGE_LAST_CACHED)
        self.assertEqual(metrics["context_window"], USAGE_WINDOW)
        self.assertEqual(metrics["cache_fraction"], 0.25)
        self.assertEqual(metrics["context_fraction"], 0.4)
        evidence = payload["evidence"]
        self.assertEqual(Path(str(evidence["path"])).name, self.rollout.name)
        event_at = evidence["event_at"]
        self.assertTrue(event_at.endswith("Z") or event_at.endswith("+00:00"))
        self.assertEqual(
            event_at.replace("+00:00", "Z"),
            "2026-09-04T20:00:00Z",
            event_at,
        )
        self.assertNotIn(str(USAGE_OLD_INPUT), proc.stdout)
        self.assertNotIn(TRANSCRIPT, proc.stdout)

        bad_ts = self.token_row(ts="not-a-timestamp")
        self.plant_rollout([bad_ts])
        proc = self.query("lane_usage", {})
        payload = self.assert_lane(self.load_query(proc), "lane_usage")
        self.assertEqual(payload["metrics"]["input"], USAGE_INPUT)
        self.assertIsNone(payload["evidence"]["event_at"])
        self.assertTrue(payload.get("reason"))
        self.assertNotIn("not-a-timestamp", json.dumps(payload["evidence"]))

        bool_row = self.token_row(input_n=True)
        self.plant_rollout([bool_row])
        proc = self.query("lane_usage", {})
        payload = self.assert_lane(self.load_query(proc), "lane_usage")
        metrics = payload.get("metrics") or {}
        self.assertNotEqual(metrics.get("input"), 0)
        self.assertNotEqual(metrics.get("input"), 1)

        zero_den = self.token_row(input_n=0, cached=10, last_input=10, window=0)
        self.plant_rollout([zero_den])
        proc = self.query("lane_usage", {})
        payload = self.assert_lane(self.load_query(proc), "lane_usage")
        metrics = payload["metrics"]
        self.assertEqual(metrics["input"], 0)
        self.assertNotEqual(metrics.get("cache_fraction"), 0)
        self.assertNotEqual(metrics.get("context_fraction"), 0)

        self.plant_rollout(
            [{"timestamp": "2026-09-04T12:00:00.000Z", "type": "session_meta"}]
        )
        proc = self.query("lane_usage", {})
        payload = self.assert_lane(self.load_query(proc), "lane_usage")
        self.assertEqual(payload["state"], "unknown")
        metrics = payload.get("metrics") or {}
        self.assertNotEqual(metrics.get("input"), 0)

    def test_usage_rollout_symlink_to_regular_is_allowed(self):
        real = self.scratch / "real-rollout.jsonl"
        self.plant_rollout([self.token_row()], path=real)
        self.rollout.symlink_to(real)
        self.assertTrue(self.rollout.is_symlink())
        proc = self.query("lane_usage", {})
        payload = self.assert_lane(self.load_query(proc), "lane_usage")
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["metrics"]["input"], USAGE_INPUT)


class TestLaneMcpBrief(_LaneMcpFixture):
    def test_lane_brief_aggregates_omissions_and_skips_rollout(self):
        self.plant_record(JOB)
        self.plant_record(JOB2, extra={"id": JOB2})
        unlisted = self.store / JOB_UNLISTED / "record.json"
        self._plant_bytes(
            unlisted,
            json.dumps({"id": JOB_UNLISTED, "status": "closed", "model": MODEL}).encode(),
        )
        self.plant_config(job_ids=[JOB, JOB2])
        self.plant_events(
            [
                {
                    "id": 1,
                    "consumer": CONSUMER,
                    "delivery": "queued",
                    "summary": "brief-event-aa41",
                }
            ]
        )
        os.mkfifo(self.rollout)
        self.assertTrue(stat.S_ISFIFO(self.rollout.stat().st_mode))
        proc = self.query("lane_brief", {}, cap=FIFO_CAP)
        payload = self.assert_lane(self.load_query(proc), "lane_brief")
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["configured_ids"], [JOB, JOB2])
        self.assertNotIn(JOB_UNLISTED, json.dumps(payload))
        dumped = json.dumps(payload)
        for phrase in OMISSIONS:
            self.assertIn(phrase, dumped)
        self.assertIn(JOB, dumped)
        self.assertIn("brief-event-aa41", dumped)
        self.assertNotIn(str(USAGE_INPUT), dumped)
        usage = self.query("lane_usage", {}, cap=FIFO_CAP)
        usage_payload = self.assert_lane(self.load_query(usage), "lane_usage")
        self.assertEqual(usage_payload["state"], "unavailable")


class TestLaneMcpParity(_LaneMcpFixture):
    def test_cli_tool_resource_parity_and_stationary_sources(self):
        rec = self.plant_record()
        self.plant_events(
            [{"id": 4, "consumer": CONSUMER, "delivery": "queued", "summary": "parity"}]
        )
        self.plant_rollout([self.token_row()])
        before = self.source_bytes()
        files_before = _tree_files(self.work)
        pairs = [
            ("lane_record", {"id": JOB}, f"lane://record/{JOB}"),
            ("lane_alerts", {}, "lane://alerts"),
            ("lane_usage", {}, "lane://usage"),
            ("lane_brief", {}, "lane://brief"),
        ]
        self.assertEqual(len(pairs), 4)
        cli_payloads = {}
        for tool, arguments, _uri in pairs:
            first = self.load_query(self.query(tool, arguments))
            second = self.load_query(self.query(tool, arguments))
            self.assertEqual(first, second, tool)
            self.assert_lane(first, tool)
            raw1 = self.query(tool, arguments).stdout
            raw2 = self.query(tool, arguments).stdout
            self.assertEqual(raw1, raw2, tool)
            self.assertLessEqual(len(raw1.encode("utf-8")), 16384, tool)
            cli_payloads[tool] = first
        requests = []
        rpc_id = 10
        for tool, arguments, uri in pairs:
            requests.append(
                rpc("tools/call", rpc_id, {"name": tool, "arguments": arguments})
            )
            rpc_id += 1
            requests.append(rpc("resources/read", rpc_id, {"uri": uri}))
            rpc_id += 1
        proc = self.serve(self.handshake(requests))
        self.assertEqual(proc.exit, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        replies = [obj for obj in objs if "id" in obj and obj.get("id") != 1]
        self.assertEqual(len(replies), 8, objs)
        idx = 0
        for tool, _arguments, _uri in pairs:
            call_obj = replies[idx]
            read_obj = replies[idx + 1]
            idx += 2
            call_payload = self.lane_from_rpc(call_obj)
            read_payload = self.lane_from_rpc(read_obj)
            self.assertEqual(call_payload, cli_payloads[tool], tool)
            self.assertEqual(read_payload, cli_payloads[tool], tool)
        self.assertEqual(self.source_bytes(), before)
        self.assertEqual(_tree_files(self.work), files_before)
        self.assertEqual((self.store / JOB / "record.json").read_bytes(), rec.read_bytes())

    def test_no_decoy_fallback_from_home_or_cwd(self):
        decoy = {
            "id": JOB,
            "status": "closed",
            "model": MODEL,
            "result": {"head": HEAD, "envelope": {"verdict": VERDICT, "counts": COUNTS}},
        }
        home_job = (
            self.home
            / ".local"
            / "state"
            / "agentscient"
            / "dispatch"
            / JOB
            / "record.json"
        )
        cwd_job = self.cwd / "store" / JOB / "record.json"
        self._plant_bytes(home_job, json.dumps(decoy).encode("utf-8"))
        self._plant_bytes(cwd_job, json.dumps(decoy).encode("utf-8"))
        proc = self.query("lane_record", {"id": JOB})
        payload = self.assert_lane(self.load_query(proc), "lane_record")
        self.assertEqual(payload["state"], "unknown")
        self.assertNotIn(MODEL, proc.stdout)


class TestLaneMcpTransport(_LaneMcpFixture):
    def test_mcp_initialize_lists_hints_and_version_negotiation(self):
        self.plant_record()
        self.plant_config(job_ids=[JOB])
        requests = [
            rpc("ping", 2),
            rpc("tools/list", 3),
            rpc("resources/list", 4),
        ]
        proc = self.serve(self.handshake(requests))
        self.assertEqual(proc.exit, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        result = self.rpc_by_id(objs, 1)["result"]
        caps = result["capabilities"]
        self.assertIn("tools", caps)
        self.assertIn("resources", caps)
        for banned in ("sampling", "elicitation", "logging"):
            self.assertNotIn(banned, caps)
        instructions = result["instructions"]
        self.assertIsInstance(instructions, str, instructions)
        text = instructions.lower()
        self.assertIn("read-only", text)
        self.assertRegex(text, r"evidence(?: is)? not authority", instructions)
        self.assertRegex(text, r"brief (?:before|then) detail", instructions)
        self.assertIn("no live readiness", text)
        ping = self.rpc_by_id(objs, 2)
        self.assertEqual(ping.get("result"), {})
        tools = self.rpc_by_id(objs, 3)["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(sorted(names), sorted(TOOLS))
        self.assertEqual(len(tools), 4)
        for tool in tools:
            ann = tool["annotations"]
            self.assertEqual(ann["readOnlyHint"], True)
            self.assertEqual(ann["destructiveHint"], False)
        resources = self.rpc_by_id(objs, 4)["result"]["resources"]
        uris = [item["uri"] for item in resources]
        self.assertEqual(len(uris), 4)
        self.assertEqual(
            set(uris),
            {
                "lane://brief",
                "lane://alerts",
                "lane://usage",
                f"lane://record/{JOB}",
            },
        )
        self.assertNotIn(f"lane://record/{JOB_UNLISTED}", uris)

    def test_malformed_then_healthy_notification_silence_and_discovery(self):
        self.plant_record()
        huge = "x" * (64 * 1024 + 1)
        lines = self.handshake(
            [
                "{",
                huge,
                rpc("no/such", 8),
                rpc("tools/call", 9, {"name": "lane_record", "arguments": {"id": JOB}}),
                notify("notifications/cancelled"),
                rpc("ping", 10),
                rpc(
                    "tools/call",
                    11,
                    {"name": "lane_record", "arguments": {"id": JOB, "extra": 1}},
                ),
                rpc(
                    "resources/read",
                    12,
                    {"uri": f"lane://record/{JOB_UNLISTED}"},
                ),
                rpc("tools/list", 13),
            ]
        )
        proc = self.serve(lines)
        self.assertEqual(proc.exit, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        ids = [obj.get("id") for obj in objs if "id" in obj]
        self.assertIn(1, ids)
        self.assertIn(9, ids)
        self.assertIn(10, ids)
        echoed = [
            obj
            for obj in objs
            if obj.get("method")
            in ("notifications/initialized", "notifications/cancelled")
        ]
        self.assertEqual(echoed, [])
        ping = self.rpc_by_id(objs, 10)
        self.assertEqual(ping.get("result"), {})
        unknown = self.rpc_by_id(objs, 8)
        self.assertIn("error", unknown)
        self.assertEqual(unknown["error"]["code"], -32601)
        healthy = self.rpc_by_id(objs, 9)
        self.assert_lane(self.lane_from_rpc(healthy), "lane_record")
        domain = self.rpc_by_id(objs, 11)
        result = domain.get("result") or {}
        self.assertEqual(result.get("isError"), True)
        blob = json.dumps(result)
        self.assertIn("expected", blob)
        self.assertIn("found", blob)
        self.assertIn("needed", blob)
        listed = self.rpc_by_id(objs, 13)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(sorted(names), sorted(TOOLS))

    def test_fresh_process_sees_updated_config(self):
        self.plant_record(JOB)
        self.plant_record(JOB2, extra={"id": JOB2})
        self.plant_config(job_ids=[JOB])
        first = self.assert_lane(
            self.load_query(self.query("lane_record", {"id": JOB})), "lane_record"
        )
        self.assertEqual(first["state"], "ok")
        self.plant_config(job_ids=[JOB2])
        restarted = self.assert_lane(
            self.load_query(self.query("lane_record", {"id": JOB2})), "lane_record"
        )
        self.assertEqual(restarted["state"], "ok")
        stale = self.query("lane_record", {"id": JOB})
        self.assert_refusal(stale)

    def test_control_bytes_escaped_overflow_and_no_bytecode(self):
        rec = {
            "id": JOB,
            "status": "closed",
            "model": "pre\x01post",
            "result": {"head": HEAD, "envelope": {"verdict": VERDICT, "counts": COUNTS}},
            "attempts": [{}],
            "session": {"spend": {"total": 1}},
        }
        self.plant_record(raw=(json.dumps(rec) + "\n").encode("utf-8"))
        proc = self.query("lane_record", {"id": JOB})
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertNotIn("\x01", proc.stdout)
        payload = self.assert_lane(self.load_query(proc), "lane_record")
        if payload["state"] == "ok":
            self.assertIn("pre", payload["record"]["model"])
            self.assertIn("post", payload["record"]["model"])

        ids = [f"J{n:02d}aa41mcp" for n in range(20)]
        self.plant_config(job_ids=ids)
        fat = "N" * 256
        for rec_id in ids:
            body = {
                "id": rec_id,
                "status": "closed",
                "model": fat,
                "result": {
                    "head": fat,
                    "envelope": {"verdict": fat, "counts": COUNTS},
                    "changed_paths": ["a.py"],
                },
                "attempts": [{}],
                "session": {"spend": {"total": 1}},
            }
            path = self.store / rec_id / "record.json"
            self._plant_bytes(path, (json.dumps(body) + "\n").encode("utf-8"))
        proc = self.query("lane_brief", {})
        payload = self.assert_lane(self.load_query(proc), "lane_brief")
        raw = proc.stdout.encode("utf-8")
        if payload["state"] == "ok":
            self.assertLessEqual(len(raw), 16384)
        else:
            self.assertEqual(payload["state"], "unavailable")
            self.assertTrue(payload.get("reason"))

        staged_dir = self.staged.parent
        cache_before = list(staged_dir.glob("**/__pycache__")) + list(
            staged_dir.glob("**/*.pyc")
        )
        proc = self.invoke(
            ["check"], extra_env={"PYTHONDONTWRITEBYTECODE": None}, minus_b=False
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        cache_after = list(staged_dir.glob("**/__pycache__")) + list(
            staged_dir.glob("**/*.pyc")
        )
        self.assertEqual(cache_after, cache_before)


if __name__ == "__main__":
    unittest.main()
