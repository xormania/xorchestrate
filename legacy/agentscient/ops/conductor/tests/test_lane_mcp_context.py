"""Lane-MCP v2 context-read acceptance tests, written from the contracts.

Contracts (only): `ops/conductor/lane-mcp-v2.md` extending
`ops/conductor/lane-mcp-lever.md`, plus inherited
`ops/conductor/instrument-conventions.md`. Named counterparties are
the record envelope fields and the job-registry shape quoted in
lane-mcp-v2.md §9. `ops/conductor/tools/lane_mcp.py` and
`ops/conductor/tools/lane_read.py` are the subject and are not read
here.

Tests reuse the v1 fixture in test_lane_mcp.py, plant synthetic
records and registries, and invoke the documented CLI as a subprocess:
`python3 -B ops/conductor/tools/lane_mcp.py --config FILE
check|serve|query TOOL [JSON]`. No live dispatch store, tracked
record, real job registry, network, or harness call.

World owns HOME, the XDG roots and TMPDIR; every subprocess goes
through run with a timeout. A missing entrypoint fails the endpoint integration checks; this file stages the same
copy-tool-bytes stand-in so each assertion fails on the v2 promise
when the subject is absent.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_lane_mcp import (
    CONSUMER,
    COUNTS,
    FINDING,
    HEAD,
    JOB,
    JOB2,
    JOB_UNLISTED,
    MODEL,
    NOTE,
    OMISSIONS,
    PROMPT,
    READ,
    RUN_CAP,
    SCHEMA,
    TOOL,
    TOOLS,
    TRANSCRIPT,
    VERDICT,
    _LaneMcpFixture,
    _tree_files,
    notify,
    rpc,
)

from ops.testkit import Timeout, World, run

# World.stage copies the subject (and siblings) into the world; an
# absent file still plants the exit-0 stand-in used to prove red.
_SUBJECT = TOOL

REPO = Path(__file__).resolve().parents[3]
V1_EXAMPLE = REPO / "ops" / "conductor" / "lane-mcp.example.json"
V2_EXAMPLE = REPO / "ops" / "conductor" / "lane-mcp-v2.example.json"
V2_TOOLS = TOOLS + ("lane_findings", "lane_changes", "lane_jobs")
CURSOR_GRAMMAR = re.compile(r"^[0-9a-f]{16}:[1-9][0-9]{0,8}$")
REVISION_GRAMMAR = re.compile(r"^[0-9a-f]{16}$")
JOB_NAME_GRAMMAR = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
JOB3 = "jobAa41mcp3"
FINDINGS_PAGE_CAP = 8192
QUERY_CAP = 16384
SOURCE_CAP = 1024 * 1024
FIT_FIELDS = ("reproduce", "claim", "where")
PLACEHOLDERS = ("ref", "base", "diff", "into", "out", "inputs", "scope")
LEAKS = (PROMPT, TRANSCRIPT, "raw.out")
DISPATCHABLE = {
    "adapter": "harness",
    "role": "write",
    "snapshot": "whole",
    "prompt": "Write tests for {scope} at {ref} into {out}.",
    "deliverable": "a red test file",
    "constraints": ["do not edit the implementation", "no network"],
    "caps": {"timeout": 1800},
}


def revision_of(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:16]


def canonical_bytes(obj: object) -> bytes:
    """Measure a reply with the stdlib ensure_ascii encoder.

    lane-mcp-v2.md §3 names ensure_ascii canonical JSON. Compact
    separators match the suite's RPC encoder. No production encoder is
    read; CLI tests also bound raw stdout.
    """
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def utf8_fill(n: int, *, mark: str = "", ch: str = "é") -> str:
    rest = n - len(mark.encode("utf-8"))
    unit = len(ch.encode("utf-8"))
    body = ch * (rest // unit)
    pad = "x" * (rest - len(body.encode("utf-8")))
    text = mark + body + pad
    if len(text.encode("utf-8")) != n:
        raise AssertionError(len(text.encode("utf-8")))
    return text


def ascii_fill(n: int, mark: str) -> str:
    text = mark + ("X" * (n - len(mark)))
    if len(text.encode("utf-8")) != n:
        raise AssertionError(len(text.encode("utf-8")))
    return text


def fat_finding(n: int) -> dict:
    return {
        "severity": "p2",
        "where": ascii_fill(256, f"w{n}-"),
        "claim": ascii_fill(1024, f"claim-{n}-"),
        "reproduce": ascii_fill(1024, f"repro-{n}-"),
    }


def whole_projected_entry(n: int, source: dict) -> dict:
    entry = {
        "n": n,
        "severity": source.get("severity"),
        "where": source.get("where"),
        "claim": source.get("claim"),
        "reproduce": source.get("reproduce"),
        "omitted": [],
    }
    if entry["severity"] not in ("p1", "p2", "p3"):
        entry["severity"] = None
    return entry


def fit_omission_forms(n: int, source: dict) -> list[dict]:
    """Whole entry, then each §3 drop: reproduce, claim, where."""
    forms = [whole_projected_entry(n, source)]
    omitted: list[str] = []
    current = dict(forms[0])
    for field in FIT_FIELDS:
        omitted = [*omitted, field]
        current = dict(current)
        current[field] = None
        current["omitted"] = list(omitted)
        forms.append(current)
    return forms


class _V2Fixture(_LaneMcpFixture):
    """v1 fixture plus version-2 config, records and registries.

    World owns the temporary roots; invoke goes through run.
    """

    def setUp(self):
        self.world = World(prefix="lane-mcp-context-")
        self.home = self.world.home
        self.scratch = self.world.path("stage")
        siblings = tuple(
            path.name
            for path in (
                READ,
                READ.with_name("instrument.py"),
                READ.with_name("mcp_transport.py"),
                READ.with_name("dispatch_observation.py"),
                READ.with_name("laneconf.py"),
            )
            if path.is_file()
        )
        self.staged = self.world.stage(
            _SUBJECT,
            "lane_mcp.py",
            siblings=siblings if _SUBJECT.is_file() else (),
        )
        self.work = self.world.path("work")
        self.work.mkdir()
        self.cwd = self.world.cwd
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

    def tearDown(self):
        self.world.close()

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def env(self, extra=None):
        env = self.world.env()
        env.pop("WF_AGENT", None)
        env.pop("CLAUDE_SESSION_ID", None)
        if extra:
            for key, value in extra.items():
                if value is None:
                    env.pop(key, None)
                else:
                    env[key] = value
        return env

    def invoke(
        self,
        args,
        *,
        config=None,
        stdin=None,
        extra_env=None,
        cap=RUN_CAP,
        minus_b=True,
    ):
        cfg = self.config_path if config is None else config
        py = [sys.executable]
        if minus_b:
            py.append("-B")
        argv = [*py, str(self.staged), "--config", str(cfg), *args]
        try:
            return run(
                    argv,
                    cwd=self.cwd,
                    env=self.env(extra_env),
                    stdin=stdin,
                    timeout=cap,
                )
        except Timeout as exc:
            self.fail(f"timed out: {argv}: {exc}")

    def load_query(self, proc):
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip(), "stdout empty")
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"stdout is not JSON: {proc.stdout!r}")
        self.assertIsInstance(payload, dict, payload)
        return payload

    def assert_refusal(self, proc, class_token=None):
        refusal = proc.refusal()
        if class_token is not None:
            self.assertEqual(refusal.cls, class_token, refusal.line)
        return refusal.line

    def plant_v2_config(
        self,
        *,
        job_ids: list[str] | None = None,
        job_registry: str | None = "jobs.json",
        **overrides,
    ) -> Path:
        payload = {
            "version": 2,
            "dispatch_store": "store",
            "job_ids": list(job_ids if job_ids is not None else [JOB]),
            "monitor_state": "monitor",
            "consumer": CONSUMER,
            "usage_rollout": "rollout.jsonl",
            "job_registry": job_registry,
        }
        payload.update(overrides)
        return self.plant_config(payload)

    def plant_context_record(
        self,
        rec_id: str = JOB,
        *,
        findings: list | None = None,
        note: object = NOTE,
        job: object = "author-tests",
        stage: object = "code",
        counts: dict | None = None,
        verdict: object = VERDICT,
        extra: dict | None = None,
        raw: bytes | None = None,
    ) -> Path:
        if raw is not None:
            return self.plant_record(rec_id, raw=raw)
        rows = findings
        if rows is None:
            rows = [
                {
                    "severity": "p2",
                    "where": "ops/conductor/lane-mcp-v2.md",
                    "claim": FINDING,
                    "reproduce": "python3 -m unittest ...test_lane_findings",
                }
            ]
        rec = {
            "id": rec_id,
            "job": job,
            "stage": stage,
            "status": "closed",
            "model": MODEL,
            "result": {
                "head": HEAD,
                "envelope": {
                    "verdict": verdict,
                    "counts": dict(counts if counts is not None else COUNTS),
                    "findings": rows,
                    "note": note,
                },
                "changed_paths": ["alpha.py"],
            },
            "attempts": [{}],
            "session": {
                "spend": {
                    "harness": "grok",
                    "total": 1,
                    "out": 0,
                    "runs": 1,
                    "secret": TRANSCRIPT,
                }
            },
            "prompt": PROMPT,
            "artifacts": {"log": "must-not-leak"},
            "commit": {"subject": "must-not-leak"},
        }
        if extra:
            rec.update(extra)
        return self.plant_record(rec_id, raw=(json.dumps(rec, indent=2) + "\n").encode())

    def plant_registry(
        self,
        jobs: dict | None = None,
        *,
        path: Path | None = None,
        raw: bytes | None = None,
    ) -> Path:
        dest = path if path is not None else self.work / "jobs.json"
        if raw is not None:
            self._plant_bytes(dest, raw)
            return dest
        body = json.dumps(jobs if jobs is not None else {}, indent=2) + "\n"
        self._plant_bytes(dest, body.encode("utf-8"))
        return dest

    def assert_v2(self, payload: dict, tool: str) -> dict:
        self.assertIsInstance(payload, dict, payload)
        self.assertEqual(payload.get("schema"), SCHEMA, payload)
        self.assertEqual(payload.get("query"), tool, payload)
        self.assertIn(payload.get("state"), ("ok", "unknown", "unavailable"), payload)
        dumped = json.dumps(payload)
        for leak in LEAKS:
            self.assertNotIn(leak, dumped)
        for banned in ("artifacts", "commit", "spend", "prompt"):
            self.assertNotIn(banned, payload)
        return payload

    def empty_findings_fields(self, payload: dict) -> None:
        self.assertEqual(payload.get("findings"), [])
        self.assertIsNone(payload.get("findings_total"))
        self.assertIsNone(payload.get("record"))
        self.assertIsNone(payload.get("note"))
        self.assertIsNone(payload.get("note_bytes"))
        self.assertIsNone(payload.get("page"))

    def rpc_error_line(self, obj: dict) -> str:
        result = obj.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            content = result.get("content") or []
            texts = [
                item.get("text")
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            self.assertTrue(texts, obj)
            return str(texts[0])
        err = obj.get("error")
        self.fail(f"expected domain isError line, got {obj}; error={err}")

    def notifications(self, objs: list[dict]) -> list[dict]:
        return [obj for obj in objs if "method" in obj and "id" not in obj]

    def assert_page_cap(self, payload: dict, raw: str | None = None) -> bytes:
        encoded = canonical_bytes(payload)
        self.assertLessEqual(len(encoded), FINDINGS_PAGE_CAP, len(encoded))
        if raw is not None:
            self.assertLessEqual(len(raw.encode("utf-8")), FINDINGS_PAGE_CAP)
        return encoded

    def assert_fit_omission_order(self, entry: dict) -> list[str]:
        omitted = list(entry.get("omitted") or [])
        fit = [name for name in FIT_FIELDS if name in omitted]
        self.assertEqual(fit, list(FIT_FIELDS[: len(fit)]), omitted)
        for name in fit:
            self.assertIsNone(entry.get(name), name)
        return fit

    def with_next_whole_entry(
        self,
        payload: dict,
        extra: dict,
        revision: str,
        *,
        remaining_after: bool,
    ) -> dict:
        cand = copy.deepcopy(payload)
        cand["findings"] = list(payload["findings"]) + [extra]
        page = dict(payload["page"])
        page["count"] = len(cand["findings"])
        page["from"] = cand["findings"][0]["n"]
        page["to"] = cand["findings"][-1]["n"]
        page["more"] = remaining_after
        nxt = extra["n"] + 1
        page["next_cursor"] = f"{revision}:{nxt}" if remaining_after else None
        cand["page"] = page
        return cand

    def assert_page_filled(
        self,
        payload: dict,
        sources: list[dict],
        revision: str,
    ) -> None:
        page = payload["page"]
        if not page["more"]:
            return
        nxt = page["to"] + 1
        extra = whole_projected_entry(nxt, sources[nxt - 1])
        remaining_after = nxt < len(sources)
        cand = self.with_next_whole_entry(
            payload, extra, revision, remaining_after=remaining_after
        )
        self.assertGreater(
            len(canonical_bytes(cand)),
            FINDINGS_PAGE_CAP,
            "page under-filled: next whole entry still fits",
        )

    def owned_surface(self) -> dict[str, object]:
        records: dict[str, bytes | None] = {}
        if self.store.exists():
            for rec in sorted(self.store.glob("*/record.json")):
                rel = str(rec.relative_to(self.work))
                if rec.is_file() and not rec.is_symlink():
                    records[rel] = rec.read_bytes()
                else:
                    records[rel] = b"*"
        registry = self.work / "jobs.json"
        if registry.is_file() and not registry.is_symlink():
            registry_bytes: bytes | None = registry.read_bytes()
        elif registry.exists() or registry.is_symlink():
            registry_bytes = b"*"
        else:
            registry_bytes = None
        config: bytes | None
        if self.config_path.is_file() and not self.config_path.is_symlink():
            config = self.config_path.read_bytes()
        elif self.config_path.exists() or self.config_path.is_symlink():
            config = b"*"
        else:
            config = None
        return {
            "config": config,
            "registry": registry_bytes,
            "records": records,
            "source_bytes": self.source_bytes(),
            "work_files": _tree_files(self.work),
            "home_files": _tree_files(self.home),
            "scratch_files": _tree_files(self.scratch),
            "cwd_files": _tree_files(self.cwd),
        }

    def assert_domain_error_matches_cli(self, rpc_obj: dict, cli_line: str) -> None:
        self.assertNotIn("error", rpc_obj, rpc_obj)
        result = rpc_obj.get("result") or {}
        self.assertIs(result.get("isError"), True, rpc_obj)
        self.assertEqual(self.rpc_error_line(rpc_obj), cli_line)

    def assert_reading_matches_cli(self, rpc_obj: dict, cli_payload: dict) -> None:
        err = rpc_obj.get("error")
        self.assertIsNone(err, f"reading became transport error {err}")
        result = rpc_obj.get("result") or {}
        self.assertIsNot(result.get("isError"), True, rpc_obj)
        self.assertEqual(self.lane_from_rpc(rpc_obj), cli_payload)


class TestLaneMcpContextConfig(_V2Fixture):
    def test_v1_check_and_example_stay_four_tools(self):
        proc = self.invoke(["check"])
        self.assertEqual(self.load_query(proc), {"valid": True, "tools": 4})
        self.assertTrue(V1_EXAMPLE.is_file(), V1_EXAMPLE)
        example = json.loads(V1_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(example["version"], 1)
        self.assertNotIn("job_registry", example)
        via_example = self.invoke(["check"], config=V1_EXAMPLE)
        self.assertEqual(self.load_query(via_example), {"valid": True, "tools": 4})

    def test_v1_with_job_registry_and_v2_without_it_refuse_config(self):
        cases = [
            (
                "v1-with-registry",
                {
                    "version": 1,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                    "job_registry": None,
                },
            ),
            (
                "v2-without-registry",
                {
                    "version": 2,
                    "dispatch_store": "store",
                    "job_ids": [JOB],
                    "monitor_state": "monitor",
                    "consumer": CONSUMER,
                    "usage_rollout": "rollout.jsonl",
                },
            ),
        ]
        for name, payload in cases:
            with self.subTest(name=name):
                self.plant_config(payload)
                line = self.assert_refusal(self.invoke(["check"]), "config")
                self.assertIn("job_registry", line)

    def test_v2_check_is_seven_tools_and_example_matches_the_declared_shape(self):
        self.plant_v2_config(job_registry=None)
        proc = self.invoke(["check"])
        self.assertEqual(self.load_query(proc), {"valid": True, "tools": 7})

        self.assertTrue(V2_EXAMPLE.is_file(), V2_EXAMPLE)
        example = json.loads(V2_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(example["version"], 2)
        self.assertEqual(
            set(example),
            {
                "version",
                "dispatch_store",
                "job_ids",
                "monitor_state",
                "consumer",
                "usage_rollout",
                "job_registry",
            },
        )
        self.assertIsNone(example["dispatch_store"])
        self.assertEqual(example["job_ids"], [])
        self.assertIsNone(example["monitor_state"])
        self.assertEqual(example["consumer"], "conductor")
        self.assertIsNone(example["usage_rollout"])
        self.assertIsNone(example["job_registry"])
        via_example = self.invoke(["check"], config=V2_EXAMPLE)
        self.assertEqual(self.load_query(via_example), {"valid": True, "tools": 7})

    def test_v1_query_refuses_the_three_v2_tool_names(self):
        self.plant_config()
        for tool, arguments in (
            ("lane_findings", {"id": JOB}),
            ("lane_changes", {}),
            ("lane_jobs", {}),
        ):
            with self.subTest(tool=tool):
                proc = self.query(tool, arguments)
                self.assert_refusal(proc, "arguments")


class TestLaneMcpContextFindings(_V2Fixture):
    def test_lane_findings_projects_note_findings_and_recorded_counts(self):
        counts = {"p1": 0, "p2": 9, "p3": 0, "opinions": 0}
        rec = self.plant_context_record(counts=counts)
        self.plant_v2_config()
        proc = self.query("lane_findings", {"id": JOB})
        payload = self.assert_v2(self.load_query(proc), "lane_findings")
        self.assertEqual(payload["state"], "ok")
        self.assertIsNone(payload["reason"])
        record = payload["record"]
        self.assertEqual(record["id"], JOB)
        self.assertEqual(record["job"], "author-tests")
        self.assertEqual(record["stage"], "code")
        self.assertEqual(record["recorded_status"], "closed")
        self.assertEqual(record["verdict"], VERDICT)
        self.assertEqual(record["counts"], counts)
        self.assertNotEqual(record["counts"]["p2"], 1)
        self.assertEqual(record["revision"], revision_of(rec))
        self.assertRegex(record["revision"], REVISION_GRAMMAR)
        self.assertIn("record.json", str(record["evidence"]))
        self.assertEqual(payload["note"], NOTE)
        self.assertEqual(payload["note_bytes"], len(NOTE.encode("utf-8")))
        self.assertEqual(payload["findings_total"], 1)
        self.assertEqual(len(payload["findings"]), 1)
        entry = payload["findings"][0]
        self.assertEqual(entry["n"], 1)
        self.assertEqual(entry["severity"], "p2")
        self.assertEqual(entry["claim"], FINDING)
        self.assertEqual(entry["omitted"], [])
        page = payload["page"]
        self.assertEqual(page["from"], 1)
        self.assertEqual(page["to"], 1)
        self.assertEqual(page["count"], 1)
        self.assertFalse(page["more"])
        self.assertIsNone(page["next_cursor"])
        dumped = json.dumps(payload)
        self.assertIn(FINDING, dumped)
        self.assertIn(NOTE, dumped)
        self.assertNotIn("must-not-leak", dumped)
        self.assertNotIn("secret", dumped)

    def test_lane_findings_missing_unavailable_and_cursor_not_judged(self):
        self.plant_v2_config()
        bogus = "aaaaaaaaaaaaaaaa:1"

        missing_store = self.query("lane_findings", {"id": JOB})
        payload = self.assert_v2(self.load_query(missing_store), "lane_findings")
        self.assertEqual(payload["state"], "unknown")
        self.assertEqual(payload["reason"], "dispatch store is missing")
        self.empty_findings_fields(payload)

        self.store.mkdir()
        missing_record = self.query("lane_findings", {"id": JOB, "cursor": bogus})
        payload = self.assert_v2(self.load_query(missing_record), "lane_findings")
        self.assertEqual(payload["state"], "unknown")
        self.assertEqual(payload["reason"], "record is missing")
        self.empty_findings_fields(payload)
        self.assertNotIn("cursor", payload.get("reason") or "")

        self.plant_context_record(raw=b"{not-json\n")
        malformed = self.query("lane_findings", {"id": JOB, "cursor": bogus})
        payload = self.assert_v2(self.load_query(malformed), "lane_findings")
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["reason"], "record is unreadable or malformed")
        self.empty_findings_fields(payload)

        self.reset_sources()
        self.plant_v2_config()
        self.plant_context_record()
        unconfigured = self.query("lane_findings", {"id": JOB_UNLISTED})
        self.assert_refusal(unconfigured, "arguments")
        extra = self.query("lane_findings", {"id": JOB, "extra": 1})
        self.assert_refusal(extra, "arguments")

    def test_lane_findings_omitted_fields_note_rules_and_non_object_entries(self):
        long_where = "W" * 257
        long_claim = "C" * 1025
        long_repro = "R" * 1025
        long_note = "N" * 2049
        long_job = "j" * 300
        findings = [
            {
                "severity": "p1",
                "where": "here",
                "claim": "ok-claim",
                "reproduce": "ok-repro",
            },
            {
                "severity": "fatal",
                "where": long_where,
                "claim": long_claim,
                "reproduce": long_repro,
            },
            {
                "severity": "p3",
                "where": 12,
                "claim": None,
            },
            ["not", "an", "object"],
        ]
        rec = {
            "id": JOB,
            "job": long_job,
            "stage": "code",
            "status": "closed",
            "result": {
                "envelope": {
                    "verdict": VERDICT,
                    "counts": dict(COUNTS),
                    "findings": findings,
                    "note": long_note,
                }
            },
        }
        self.plant_context_record(raw=(json.dumps(rec) + "\n").encode("utf-8"))
        self.plant_v2_config()
        proc = self.query("lane_findings", {"id": JOB})
        payload = self.assert_v2(self.load_query(proc), "lane_findings")
        self.assertEqual(payload["state"], "ok")
        self.assertIsNone(payload["note"])
        self.assertEqual(payload["note_bytes"], len(long_note.encode("utf-8")))
        self.assertIsNone(payload["record"]["job"])
        self.assertEqual(payload["record"]["stage"], "code")
        self.assertEqual(payload["findings_total"], 4)
        self.assertEqual(len(payload["findings"]), 4)
        ok, bad, partial, broken = payload["findings"]
        self.assertEqual(ok["n"], 1)
        self.assertEqual(ok["omitted"], [])
        self.assertEqual(bad["n"], 2)
        self.assertIsNone(bad["severity"])
        self.assertIsNone(bad["where"])
        self.assertIsNone(bad["claim"])
        self.assertIsNone(bad["reproduce"])
        for field in ("severity", "where", "claim", "reproduce"):
            self.assertIn(field, bad["omitted"])
        self.assertEqual(partial["n"], 3)
        self.assertEqual(partial["severity"], "p3")
        self.assertIsNone(partial["where"])
        self.assertIsNone(partial["claim"])
        self.assertIsNone(partial["reproduce"])
        self.assertIn("where", partial["omitted"])
        self.assertNotIn("claim", partial["omitted"])
        self.assertNotIn("reproduce", partial["omitted"])
        self.assertEqual(broken["n"], 4)
        self.assertEqual(broken["omitted"], ["entry"])
        self.assertIsNone(broken["severity"])

        rec["result"]["envelope"]["note"] = 11
        rec["result"]["envelope"]["findings"] = []
        self.plant_context_record(raw=(json.dumps(rec) + "\n").encode("utf-8"))
        not_string = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertEqual(not_string["state"], "ok")
        self.assertIsNone(not_string["note"])
        self.assertIsNone(not_string["note_bytes"])
        self.assertEqual(not_string["reason"], "note is not a string")

        rec["result"]["envelope"]["note"] = NOTE
        rec["result"]["envelope"]["findings"] = {"n": 1}
        self.plant_context_record(raw=(json.dumps(rec) + "\n").encode("utf-8"))
        not_list = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertEqual(not_list["state"], "unavailable")
        self.assertEqual(not_list["reason"], "envelope findings is not a list")
        self.assertIsNone(not_list["findings_total"])
        self.assertEqual(not_list["findings"], [])
        self.assertIsInstance(not_list["record"], dict)
        self.assertEqual(not_list["record"]["id"], JOB)

        rec["result"]["envelope"]["findings"] = []
        rec["result"]["envelope"]["note"] = "ok"
        raw = (
            json.dumps(rec).replace('"note": "ok"', r'"note": "\uD800"') + "\n"
        ).encode("ascii")
        self.plant_context_record(raw=raw)
        bad_unicode = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertEqual(bad_unicode["state"], "ok")
        self.assertIsNone(bad_unicode["note"])
        self.assertIsNone(bad_unicode["note_bytes"])
        self.assertEqual(bad_unicode["reason"], "note is not valid Unicode")

    def test_lane_findings_cursor_pages_revision_and_malformed_cursors(self):
        rows = [
            {
                "severity": "p2",
                "where": f"w{n}",
                "claim": f"claim-{n}-" + ("X" * 900),
                "reproduce": f"repro-{n}",
            }
            for n in range(1, 9)
        ]
        rec = self.plant_context_record(findings=rows, note="first-page-note")
        rev = revision_of(rec)
        self.plant_v2_config()
        first = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertEqual(first["state"], "ok")
        self.assertEqual(first["findings_total"], 8)
        self.assertEqual(first["note"], "first-page-note")
        page = first["page"]
        self.assertGreaterEqual(page["count"], 1)
        self.assertEqual(len(first["findings"]), page["count"])
        self.assertEqual(first["findings"][0]["n"], page["from"])
        self.assertEqual(first["findings"][-1]["n"], page["to"])
        self.assertTrue(page["more"])
        self.assertRegex(page["next_cursor"], CURSOR_GRAMMAR)
        self.assertEqual(page["next_cursor"], f"{rev}:{page['to'] + 1}")

        second = self.assert_v2(
            self.load_query(
                self.query(
                    "lane_findings",
                    {"id": JOB, "cursor": page["next_cursor"]},
                )
            ),
            "lane_findings",
        )
        self.assertEqual(second["state"], "ok")
        self.assertIsNone(second["note"])
        self.assertEqual(second["note_bytes"], first["note_bytes"])
        self.assertEqual(second["findings"][0]["n"], page["to"] + 1)
        seen = [row["n"] for row in first["findings"] + second["findings"]]
        self.assertEqual(seen, list(range(1, seen[-1] + 1)))
        self.assertNotIn(1, [row["n"] for row in second["findings"]])

        stale = f"{'b' * 16}:1"
        self.assertNotEqual(stale.split(":")[0], rev)
        refused = self.query("lane_findings", {"id": JOB, "cursor": stale})
        line = self.assert_refusal(refused, "cursor")
        self.assertIn(f"cursor for revision {rev}", line)
        self.assertIn(stale.split(":")[0], line)
        self.assertIn("restart from the first page", line)

        past = self.query("lane_findings", {"id": JOB, "cursor": f"{rev}:9"})
        line = self.assert_refusal(past, "cursor")
        self.assertIn("supply an index within findings_total", line)

        for cursor in (f"{rev}:0", f"{rev}:01", f"{rev.upper()}:1", "nope"):
            with self.subTest(cursor=cursor):
                proc = self.query("lane_findings", {"id": JOB, "cursor": cursor})
                self.assert_refusal(proc, "arguments")

        rewritten = rec.read_bytes()
        rec.write_bytes(rewritten)
        self.assertEqual(revision_of(rec), rev)
        again = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertEqual(again["record"]["revision"], rev)


class TestLaneMcpContextChanges(_V2Fixture):
    def test_lane_changes_checkpoint_identity_unknown_and_unavailable(self):
        first = self.plant_context_record(JOB)
        second = self.plant_context_record(JOB2, extra={"id": JOB2})
        rev1 = revision_of(first)
        rev2 = revision_of(second)
        self.plant_v2_config(job_ids=[JOB, JOB2])

        empty = self.assert_v2(
            self.load_query(self.query("lane_changes", {})),
            "lane_changes",
        )
        held_nothing = self.assert_v2(
            self.load_query(self.query("lane_changes", {"checkpoint": {}})),
            "lane_changes",
        )
        self.assertEqual(empty, held_nothing)
        self.assertEqual(empty["state"], "ok")
        self.assertEqual(empty["configured_ids"], [JOB, JOB2])
        self.assertEqual([row["id"] for row in empty["changed"]], [JOB, JOB2])
        self.assertEqual(empty["unchanged"], [])
        self.assertEqual(empty["unknown"], [])
        self.assertEqual(empty["unavailable"], [])
        self.assertEqual(empty["checkpoint"], {JOB: rev1, JOB2: rev2})
        for row in empty["changed"]:
            self.assertEqual(
                set(row),
                {"id", "revision", "recorded_status", "verdict", "evidence"},
            )
            self.assertNotIn("findings", row)
        self.assertNotIn(FINDING, json.dumps(empty))

        mixed = self.assert_v2(
            self.load_query(
                self.query("lane_changes", {"checkpoint": {JOB: rev1}})
            ),
            "lane_changes",
        )
        self.assertEqual(mixed["unchanged"], [JOB])
        self.assertEqual([row["id"] for row in mixed["changed"]], [JOB2])
        self.assertEqual(mixed["changed"][0]["revision"], rev2)

        first.write_bytes(first.read_bytes())
        still = self.assert_v2(
            self.load_query(
                self.query(
                    "lane_changes",
                    {"checkpoint": {JOB: rev1, JOB2: rev2}},
                )
            ),
            "lane_changes",
        )
        self.assertEqual(still["unchanged"], [JOB, JOB2])
        self.assertEqual(still["changed"], [])

        mutated = json.loads(second.read_text(encoding="utf-8"))
        mutated["status"] = "failed"
        second.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")
        self.assertNotEqual(revision_of(second), rev2)
        after = self.assert_v2(
            self.load_query(
                self.query(
                    "lane_changes",
                    {"checkpoint": {JOB: rev1, JOB2: rev2}},
                )
            ),
            "lane_changes",
        )
        self.assertEqual(after["unchanged"], [JOB])
        self.assertEqual([row["id"] for row in after["changed"]], [JOB2])
        self.assertEqual(after["checkpoint"][JOB2], revision_of(second))

        self.assert_refusal(
            self.query("lane_changes", {"checkpoint": {JOB_UNLISTED: rev1}}),
            "arguments",
        )
        self.assert_refusal(
            self.query("lane_changes", {"checkpoint": {JOB: "not-a-revision"}}),
            "arguments",
        )

        self.reset_sources()
        self.plant_v2_config(job_ids=[JOB, JOB2])
        missing = self.assert_v2(
            self.load_query(self.query("lane_changes", {})),
            "lane_changes",
        )
        self.assertEqual(missing["state"], "unknown")
        self.assertEqual(missing["reason"], "dispatch store is missing")
        self.assertEqual(missing["changed"], [])
        self.assertEqual(missing["unchanged"], [])
        self.assertEqual(missing["unknown"], [JOB, JOB2])
        self.assertEqual(missing["unavailable"], [])
        self.assertEqual(missing["checkpoint"], {})

        self._plant_bytes(self.store, b"not-a-directory\n")
        blocked = self.assert_v2(
            self.load_query(self.query("lane_changes", {})),
            "lane_changes",
        )
        self.assertEqual(blocked["state"], "unavailable")
        self.assertEqual(blocked["reason"], "dispatch store is unavailable")
        self.assertEqual(blocked["unavailable"], [JOB, JOB2])
        self.assertEqual(blocked["unknown"], [])
        self.assertEqual(blocked["checkpoint"], {})


class TestLaneMcpContextJobs(_V2Fixture):
    def test_lane_jobs_cards_dispatchable_pairs_and_undeclared_name(self):
        jobs = {
            "author-tests": dict(DISPATCHABLE),
            "held-job": {**DISPATCHABLE, "withheld": True, "role": "read"},
            "zero-held": {**DISPATCHABLE, "withheld": 0, "role": "read"},
            "bad-slot": {
                **DISPATCHABLE,
                "prompt": "Use {scope} and {foo}",
            },
            "1nvalid": {"adapter": "harness"},
            "ok-object": "not-an-object",
        }
        self.plant_registry(jobs)
        self.plant_v2_config()
        listing = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(listing["state"], "ok")
        self.assertIsNone(listing["job"])
        self.assertEqual(listing["evidence"]["path"], str(self.work / "jobs.json"))
        self.assertEqual(
            listing["evidence"]["revision"],
            revision_of(self.work / "jobs.json"),
        )
        names = [row["name"] for row in listing["jobs"]]
        self.assertEqual(len(listing["jobs"]), 6)
        self.assertEqual(names[0], "author-tests")
        self.assertEqual(names[1], "held-job")
        self.assertEqual(names[2], "zero-held")
        self.assertEqual(names[3], "bad-slot")
        self.assertIsNone(names[4])
        self.assertIsNone(names[5])
        self.assertNotIn("1nvalid", json.dumps(listing))
        by_name = {row["name"]: row for row in listing["jobs"] if row["name"]}
        self.assertTrue(by_name["author-tests"]["dispatchable"])
        self.assertFalse(by_name["held-job"]["dispatchable"])
        self.assertTrue(by_name["zero-held"]["dispatchable"])
        self.assertFalse(by_name["bad-slot"]["dispatchable"])
        self.assertEqual(by_name["author-tests"]["slots"], ["out", "ref", "scope"])
        for row in listing["jobs"]:
            self.assertEqual(
                set(row),
                {"name", "adapter", "role", "dispatchable", "slots"},
            )
            self.assertFalse(row["dispatchable"] is True and row["name"] is None)
        dumped = json.dumps(listing)
        self.assertNotIn(DISPATCHABLE["prompt"], dumped)
        self.assertNotIn("{scope}", dumped)

        card = self.assert_v2(
            self.load_query(self.query("lane_jobs", {"name": "author-tests"})),
            "lane_jobs",
        )
        self.assertEqual(card["jobs"], [])
        job = card["job"]
        self.assertEqual(job["name"], "author-tests")
        self.assertEqual(job["adapter"], "harness")
        self.assertEqual(job["role"], "write")
        self.assertEqual(job["snapshot"], "whole")
        self.assertTrue(job["dispatchable"])
        self.assertEqual(job["deliverable"], "a red test file")
        self.assertEqual(
            job["constraints"],
            ["do not edit the implementation", "no network"],
        )
        self.assertEqual(job["timeout"], 1800)
        self.assertEqual(
            job["prompt_bytes"],
            len(DISPATCHABLE["prompt"].encode("utf-8")),
        )
        self.assertNotIn("prompt", job)
        self.assertNotIn(DISPATCHABLE["prompt"], json.dumps(card))

        missing = self.assert_v2(
            self.load_query(self.query("lane_jobs", {"name": "no-such-job"})),
            "lane_jobs",
        )
        self.assertEqual(missing["state"], "ok")
        self.assertIsNone(missing["job"])
        self.assertEqual(missing["jobs"], [])
        self.assertEqual(missing["reason"], "job is not declared in the registry")

        self.assert_refusal(
            self.query("lane_jobs", {"name": "Author-tests"}),
            "arguments",
        )
        self.assert_refusal(
            self.query("lane_jobs", {"name": "bad_name"}),
            "arguments",
        )
        self.assertTrue(JOB_NAME_GRAMMAR.match("author-tests"))
        self.assertFalse(JOB_NAME_GRAMMAR.match("Author-tests"))

    def test_lane_jobs_source_boundaries_caps_and_no_default_registry(self):
        self.plant_v2_config(job_registry=None)
        decoy = {
            "author-tests": dict(DISPATCHABLE),
            "adversarial-review": dict(DISPATCHABLE),
        }
        self.plant_registry(decoy, path=self.cwd / "jobs.json")
        tracked = REPO / "ops" / "devlane" / "task" / "jobs.json"
        self.assertTrue(tracked.is_file(), "tracked registry exists in the tree")
        env = {
            "JOB_REGISTRY": str(tracked),
            "LANE_JOB_REGISTRY": str(self.cwd / "jobs.json"),
        }
        disabled = self.assert_v2(
            self.load_query(self.query("lane_jobs", {}, extra_env=env)),
            "lane_jobs",
        )
        self.assertEqual(disabled["state"], "unknown")
        self.assertEqual(disabled["jobs"], [])
        self.assertIsNone(disabled["job"])
        self.assertIsNone(disabled["evidence"]["path"])
        self.assertIsNone(disabled["evidence"]["revision"])
        blob = json.dumps(disabled)
        self.assertNotIn("author-tests", blob)
        self.assertNotIn("adversarial-review", blob)
        self.assertNotIn("implement", blob)

        nested = self.work / "nested" / "jobs.json"
        self.plant_registry({"rel-job": dict(DISPATCHABLE)}, path=nested)
        self.plant_v2_config(job_registry="nested/jobs.json")
        relative = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(relative["state"], "ok")
        self.assertEqual(relative["jobs"][0]["name"], "rel-job")
        self.assertEqual(relative["evidence"]["path"], str(nested))

        many = {f"job-{n:02d}": dict(DISPATCHABLE) for n in range(65)}
        self.assertEqual(len(many), 65)
        self.plant_registry(many)
        self.plant_v2_config()
        overflow = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(overflow["state"], "unavailable")
        self.assertEqual(overflow["reason"], "registry lists more than 64 jobs")
        self.assertEqual(overflow["jobs"], [])

        target = self.scratch / "elsewhere.json"
        self.plant_registry({"sym-job": dict(DISPATCHABLE)}, path=target)
        link = self.work / "jobs.json"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        self.assertTrue(link.is_symlink())
        self.plant_v2_config()
        linked = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(linked["state"], "unavailable")
        self.assertEqual(linked["jobs"], [])
        self.assertNotIn("sym-job", json.dumps(linked))

        self.plant_registry(raw=b"{not-json\n")
        bad = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(bad["state"], "unavailable")
        self.assertEqual(bad["jobs"], [])

        long_path = "p" * 1025
        self.plant_v2_config(job_registry=long_path)
        self.assert_refusal(self.invoke(["check"]), "config")

    def test_lane_jobs_named_card_at_sixty_five_entries(self):
        filler = {
            "adapter": "harness",
            "role": "read",
            "snapshot": "whole",
            "prompt": "{scope}",
        }
        jobs = {"author-tests": dict(DISPATCHABLE)}
        for n in range(64):
            jobs[f"job-{n:02d}"] = dict(filler)
        self.assertEqual(len(jobs), 65)
        expected = (json.dumps(jobs, indent=2) + "\n").encode("utf-8")
        self.assertLess(len(expected), SOURCE_CAP)
        self.assertLess(len(expected), QUERY_CAP)
        planted = self.plant_registry(jobs)
        landed = planted.read_bytes()
        self.assertEqual(landed, expected)
        self.assertEqual(len(landed), len(expected))
        self.assertIn(b'"author-tests"', landed)
        self.assertEqual(len(json.loads(landed)), 65)
        self.plant_v2_config()

        card = self.assert_v2(
            self.load_query(self.query("lane_jobs", {"name": "author-tests"})),
            "lane_jobs",
        )
        self.assertEqual(card["state"], "ok")
        self.assertIsNone(card["reason"])
        self.assertEqual(card["jobs"], [])
        self.assertEqual(
            card["job"],
            {
                "name": "author-tests",
                "adapter": "harness",
                "role": "write",
                "snapshot": "whole",
                "dispatchable": True,
                "slots": ["out", "ref", "scope"],
                "deliverable": "a red test file",
                "constraints": [
                    "do not edit the implementation",
                    "no network",
                ],
                "timeout": 1800,
                "prompt_bytes": len(DISPATCHABLE["prompt"].encode("utf-8")),
            },
        )
        self.assertNotIn("prompt", card["job"])
        self.assertNotIn(DISPATCHABLE["prompt"], json.dumps(card))
        self.assertEqual(card["evidence"]["path"], str(self.work / "jobs.json"))
        self.assertEqual(card["evidence"]["revision"], revision_of(planted))
        self.assertLessEqual(len(canonical_bytes(card)), QUERY_CAP)

        proc = self.serve(
            self.handshake(
                [
                    rpc(
                        "tools/call",
                        2,
                        {
                            "name": "lane_jobs",
                            "arguments": {"name": "author-tests"},
                        },
                    ),
                    rpc(
                        "resources/read",
                        3,
                        {"uri": "lane://jobs/author-tests"},
                    ),
                ]
            )
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        tool_payload = self.lane_from_rpc(self.rpc_by_id(objs, 2))
        resource_payload = self.lane_from_rpc(self.rpc_by_id(objs, 3))
        self.assertEqual(tool_payload, card)
        self.assertEqual(resource_payload, card)

        listing = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(listing["state"], "unavailable")
        self.assertEqual(listing["reason"], "registry lists more than 64 jobs")
        self.assertEqual(listing["jobs"], [])
        self.assertIsNone(listing["job"])
        self.assertLessEqual(len(canonical_bytes(listing)), QUERY_CAP)

        missing = self.assert_v2(
            self.load_query(self.query("lane_jobs", {"name": "no-such-job"})),
            "lane_jobs",
        )
        self.assertEqual(missing["state"], "ok")
        self.assertIsNone(missing["job"])
        self.assertEqual(missing["jobs"], [])
        self.assertEqual(missing["reason"], "job is not declared in the registry")
        self.assertLessEqual(len(canonical_bytes(missing)), QUERY_CAP)


class TestLaneMcpContextMeta(_V2Fixture):
    def test_request_meta_object_is_ignored_non_object_refused_no_progress(self):
        self.plant_record()
        self.plant_config()
        token = {"progressToken": "tok-aa41-context"}
        cli = self.assert_lane(
            self.load_query(self.query("lane_record", {"id": JOB})),
            "lane_record",
        )
        requests = [
            rpc("ping", 2, {"_meta": token}),
            rpc("tools/list", 3, {"_meta": token}),
            rpc("resources/list", 4, {"_meta": token}),
            rpc(
                "tools/call",
                5,
                {
                    "name": "lane_record",
                    "arguments": {"id": JOB},
                    "_meta": token,
                },
            ),
            rpc(
                "tools/call",
                6,
                {"name": "lane_record", "arguments": {"id": JOB}},
            ),
            rpc(
                "resources/read",
                7,
                {"uri": f"lane://record/{JOB}", "_meta": token},
            ),
            rpc("resources/read", 8, {"uri": f"lane://record/{JOB}"}),
            rpc(
                "tools/call",
                9,
                {
                    "name": "lane_record",
                    "arguments": {"id": JOB, "extra": 1},
                    "_meta": token,
                },
            ),
            rpc("ping", 10, {"_meta": "not-an-object"}),
            rpc("tools/list", 11, {"_meta": ["x"]}),
            rpc("resources/read", 12, {"uri": f"lane://record/{JOB}", "_meta": 1}),
            rpc(
                "initialize",
                13,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "lane-mcp-oracle", "version": "0"},
                    "_meta": "not-an-object",
                },
            ),
        ]
        init = rpc(
            "initialize",
            1,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "lane-mcp-oracle", "version": "0"},
                "_meta": token,
            },
        )
        proc = self.serve([init, notify("notifications/initialized"), *requests])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("tok-aa41-context", proc.stdout)
        objs = self.rpc_objects(proc)
        self.assertEqual(self.notifications(objs), [])
        self.assertIn("result", self.rpc_by_id(objs, 1))
        self.assertEqual(self.rpc_by_id(objs, 2).get("result"), {})
        listed = [tool["name"] for tool in self.rpc_by_id(objs, 3)["result"]["tools"]]
        self.assertEqual(sorted(listed), sorted(TOOLS))
        with self.subTest("tools-call-with-meta"):
            self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 5)), cli)
        with self.subTest("tools-call-without-meta"):
            self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 6)), cli)
        with self.subTest("resource-read-with-meta"):
            self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 7)), cli)
        with self.subTest("resource-read-without-meta"):
            self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 8)), cli)
        with self.subTest("unknown-argument-still-refuses"):
            extra = self.rpc_by_id(objs, 9)
            self.assertEqual((extra.get("result") or {}).get("isError"), True)
            self.assertIn("expected", self.rpc_error_line(extra))
        for rpc_id, label in (
            (10, "ping-meta-not-object"),
            (11, "tools-list-meta-not-object"),
            (12, "resource-read-meta-not-object"),
            (13, "initialize-meta-not-object"),
        ):
            with self.subTest(label=label):
                obj = self.rpc_by_id(objs, rpc_id)
                self.assertIn("error", obj)
                self.assertEqual(obj["error"]["code"], -32602)


class TestLaneMcpContextSurface(_V2Fixture):
    def test_v2_lists_tools_resources_and_the_jobs_template(self):
        self.plant_context_record()
        self.plant_registry({"author-tests": dict(DISPATCHABLE)})
        self.plant_v2_config(job_ids=[JOB, JOB2])
        proc = self.serve(
            self.handshake(
                [
                    rpc("tools/list", 2),
                    rpc("resources/list", 3),
                    rpc("resources/templates/list", 4),
                    rpc("resources/read", 5, {"uri": "lane://jobs/author-tests"}),
                    rpc("resources/read", 6, {"uri": f"lane://findings/{JOB_UNLISTED}"}),
                    rpc("resources/read", 7, {"uri": f"lane://findings/{JOB}?cursor=1"}),
                ]
            )
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        tools = self.rpc_by_id(objs, 2)["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(sorted(names), sorted(V2_TOOLS))
        self.assertEqual(len(tools), 7)
        by_name = {tool["name"]: tool for tool in tools}
        for name in V2_TOOLS:
            ann = by_name[name]["annotations"]
            self.assertEqual(ann["readOnlyHint"], True)
            self.assertEqual(ann["destructiveHint"], False)
            schema = by_name[name]["inputSchema"]
            self.assertEqual(schema.get("additionalProperties"), False)
        findings_schema = by_name["lane_findings"]["inputSchema"]
        self.assertIn("id", findings_schema.get("required") or [])
        self.assertIn("id", (findings_schema.get("properties") or {}))
        self.assertNotIn(
            "checkpoint",
            by_name["lane_changes"]["inputSchema"].get("required") or [],
        )
        self.assertNotIn(
            "name",
            by_name["lane_jobs"]["inputSchema"].get("required") or [],
        )

        uris = [item["uri"] for item in self.rpc_by_id(objs, 3)["result"]["resources"]]
        self.assertEqual(
            set(uris),
            {
                "lane://brief",
                "lane://alerts",
                "lane://usage",
                f"lane://record/{JOB}",
                f"lane://record/{JOB2}",
                f"lane://findings/{JOB}",
                f"lane://findings/{JOB2}",
                "lane://changes",
                "lane://jobs",
            },
        )
        self.assertNotIn("lane://jobs/author-tests", uris)
        self.assertNotIn(f"lane://findings/{JOB_UNLISTED}", uris)

        templates = self.rpc_by_id(objs, 4)["result"]["resourceTemplates"]
        self.assertEqual(len(templates), 1)
        template = templates[0]
        self.assertEqual(template["uriTemplate"], "lane://jobs/{name}")
        self.assertEqual(template["name"], "Lane job card")
        self.assertEqual(template["mimeType"], "application/json")

        card = self.lane_from_rpc(self.rpc_by_id(objs, 5))
        self.assertEqual(card["query"], "lane_jobs")
        self.assertEqual(card["job"]["name"], "author-tests")
        for rpc_id in (6, 7):
            err = self.rpc_by_id(objs, rpc_id)
            self.assertEqual(err["error"]["code"], -32602)

    def test_v1_listings_omit_v2_surface_and_templates_stay_not_found(self):
        self.plant_record()
        self.plant_config(job_ids=[JOB])
        proc = self.serve(
            self.handshake(
                [
                    rpc("tools/list", 2),
                    rpc("resources/list", 3),
                    rpc("resources/templates/list", 4),
                    rpc("resources/read", 5, {"uri": f"lane://findings/{JOB}"}),
                    rpc("resources/read", 6, {"uri": "lane://changes"}),
                    rpc("resources/read", 7, {"uri": "lane://jobs"}),
                ]
            )
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        names = [tool["name"] for tool in self.rpc_by_id(objs, 2)["result"]["tools"]]
        self.assertEqual(sorted(names), sorted(TOOLS))
        self.assertEqual(len(names), 4)
        uris = [item["uri"] for item in self.rpc_by_id(objs, 3)["result"]["resources"]]
        self.assertEqual(len(uris), 4)
        for uri in (
            f"lane://findings/{JOB}",
            "lane://changes",
            "lane://jobs",
        ):
            self.assertNotIn(uri, uris)
        self.assertEqual(self.rpc_by_id(objs, 4)["error"]["code"], -32601)
        for rpc_id in (5, 6, 7):
            self.assertEqual(self.rpc_by_id(objs, rpc_id)["error"]["code"], -32602)

    def test_cli_tool_resource_parity_on_the_three_v2_reads(self):
        rec = self.plant_context_record()
        self.plant_context_record(JOB2, extra={"id": JOB2})
        self.plant_registry({"author-tests": dict(DISPATCHABLE)})
        self.plant_v2_config(job_ids=[JOB, JOB2])
        before = rec.read_bytes()
        pairs = [
            ("lane_findings", {"id": JOB}, f"lane://findings/{JOB}"),
            ("lane_changes", {}, "lane://changes"),
            ("lane_jobs", {}, "lane://jobs"),
            ("lane_jobs", {"name": "author-tests"}, "lane://jobs/author-tests"),
            ("lane_record", {"id": JOB}, f"lane://record/{JOB}"),
        ]
        cli = {}
        for tool, arguments, _uri in pairs:
            first = self.load_query(self.query(tool, arguments))
            second = self.load_query(self.query(tool, arguments))
            self.assertEqual(first, second, tool)
            self.assert_v2(first, tool)
            self.assertLessEqual(len(json.dumps(first).encode("utf-8")), 16384, tool)
            cli[(tool, json.dumps(arguments, sort_keys=True))] = first
        requests = []
        rpc_id = 20
        for tool, arguments, uri in pairs:
            requests.append(
                rpc("tools/call", rpc_id, {"name": tool, "arguments": arguments})
            )
            rpc_id += 1
            requests.append(rpc("resources/read", rpc_id, {"uri": uri}))
            rpc_id += 1
        proc = self.serve(self.handshake(requests))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        replies = [obj for obj in objs if obj.get("id") not in (None, 1)]
        self.assertEqual(len(replies), 10, objs)
        idx = 0
        for tool, arguments, _uri in pairs:
            call_payload = self.lane_from_rpc(replies[idx])
            read_payload = self.lane_from_rpc(replies[idx + 1])
            idx += 2
            expected = cli[(tool, json.dumps(arguments, sort_keys=True))]
            self.assertEqual(call_payload, expected, tool)
            self.assertEqual(read_payload, expected, tool)
        self.assertEqual(rec.read_bytes(), before)
        self.plant_config(job_ids=[JOB, JOB2])
        v1_payload = self.load_query(self.query("lane_record", {"id": JOB}))
        self.plant_v2_config(job_ids=[JOB, JOB2])
        v2_payload = self.load_query(self.query("lane_record", {"id": JOB}))
        self.assertEqual(v1_payload, v2_payload)

    def test_findings_resource_is_first_page_only_and_reads_need_no_identity(self):
        rows = [
            {
                "severity": "p3",
                "where": f"w{n}",
                "claim": f"page-claim-{n}-" + ("Y" * 900),
                "reproduce": None,
            }
            for n in range(1, 9)
        ]
        self.plant_context_record(findings=rows, note="resource-note")
        self.plant_v2_config()
        env = self.env()
        self.assertNotIn("WF_AGENT", env)
        cli_first = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertTrue(cli_first["page"]["more"])
        proc = self.serve(
            self.handshake(
                [rpc("resources/read", 2, {"uri": f"lane://findings/{JOB}"})]
            )
        )
        resource = self.lane_from_rpc(self.rpc_by_id(self.rpc_objects(proc), 2))
        self.assertEqual(resource, cli_first)
        self.assertEqual(resource["note"], "resource-note")
        self.assertEqual(resource["page"]["next_cursor"], cli_first["page"]["next_cursor"])
        continued = self.assert_v2(
            self.load_query(
                self.query(
                    "lane_findings",
                    {"id": JOB, "cursor": cli_first["page"]["next_cursor"]},
                )
            ),
            "lane_findings",
        )
        self.assertNotEqual(continued["findings"], resource["findings"])
        self.assertIsNone(continued["note"])


class TestLaneMcpContextOracleGaps(_V2Fixture):
    def test_oracle_rejects_named_wrong_behaviors(self):
        """The seven upheld gaps, against crafted wrong payloads not the CLI."""

        over = {"schema": SCHEMA, "pad": "Y" * (FINDINGS_PAGE_CAP + 1)}
        with self.assertRaises(AssertionError):
            self.assertLessEqual(len(canonical_bytes(over)), FINDINGS_PAGE_CAP)

        tiny = {"severity": "p2", "where": "w", "claim": "c", "reproduce": "r"}
        sources = [dict(tiny) for _ in range(8)]
        four = {
            "schema": SCHEMA,
            "query": "lane_findings",
            "state": "ok",
            "reason": None,
            "record": {"id": JOB, "revision": "a" * 16},
            "note": "n",
            "note_bytes": 1,
            "findings": [whole_projected_entry(n, sources[n - 1]) for n in range(1, 5)],
            "findings_total": 8,
            "page": {
                "from": 1,
                "to": 4,
                "count": 4,
                "more": True,
                "next_cursor": f"{'a' * 16}:5",
            },
        }
        self.assertLessEqual(len(canonical_bytes(four)), FINDINGS_PAGE_CAP)
        with self.assertRaises(AssertionError):
            self.assert_page_filled(four, sources, "a" * 16)

        skipped = {
            "n": 1,
            "severity": "p1",
            "where": "here",
            "claim": None,
            "reproduce": "r",
            "omitted": ["claim"],
        }
        with self.assertRaises(AssertionError):
            self.assert_fit_omission_order(skipped)

        before = {"work_files": ("store/x/record.json",), "home_files": ()}
        after = {
            "work_files": ("checkpoint.json", "store/x/record.json"),
            "home_files": (),
        }
        self.assertNotEqual(before, after)
        self.assertIn("checkpoint.json", after["work_files"])

        transport = {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32602, "message": "x"},
        }
        with self.assertRaises(AssertionError):
            self.assert_domain_error_matches_cli(
                transport, "arguments: expected a; found b; needed c"
            )

        collapsed = {
            "state": "unknown",
            "changed": [],
            "unchanged": [],
            "unknown": [JOB, JOB2, JOB3],
            "unavailable": [],
            "checkpoint": {},
        }
        mixed_ok = collapsed["state"] == "ok" and JOB3 in collapsed["unavailable"]
        self.assertFalse(mixed_ok)

        wrong_direct = {
            "name": "author-tests",
            "adapter": "direct",
            "role": "write",
            "dispatchable": True,
            "slots": ["scope"],
        }
        self.assertEqual(wrong_direct["adapter"], "direct")
        self.assertTrue(wrong_direct["dispatchable"])
        with self.assertRaises(AssertionError):
            self.assertFalse(wrong_direct["dispatchable"])

        v1_only_record = {"lane_record": {"ok": 1}}
        self.assertNotIn("lane_brief", v1_only_record)
        self.assertNotIn("lane_alerts", v1_only_record)
        self.assertNotIn("lane_usage", v1_only_record)


class TestLaneMcpContextFindingsGaps(_V2Fixture):
    def test_lane_findings_byte_fit_omission_sequence_and_full_walk(self):
        rows = [fat_finding(n) for n in range(1, 9)]
        rec = self.plant_context_record(findings=rows, note="page-note")
        rev = revision_of(rec)
        self.plant_v2_config()

        seen: list[int] = []
        cursor = None
        first_note = None
        pages = 0
        while True:
            arguments = {"id": JOB}
            if cursor is not None:
                arguments["cursor"] = cursor
            proc = self.query("lane_findings", arguments)
            payload = self.assert_v2(self.load_query(proc), "lane_findings")
            self.assertEqual(payload["state"], "ok")
            self.assertEqual(payload["findings_total"], 8)
            page = payload["page"]
            self.assertGreaterEqual(page["count"], 1)
            self.assertEqual(len(payload["findings"]), page["count"])
            self.assert_page_cap(payload, proc.stdout)
            if pages == 0:
                first_note = payload["note"]
                self.assertEqual(payload["note"], "page-note")
            else:
                self.assertIsNone(payload["note"])
                self.assertEqual(payload["note_bytes"], len("page-note".encode("utf-8")))
            seen.extend(row["n"] for row in payload["findings"])
            self.assert_page_filled(payload, rows, rev)
            pages += 1
            if not page["more"]:
                self.assertIsNone(page["next_cursor"])
                break
            cursor = page["next_cursor"]
            self.assertRegex(cursor, CURSOR_GRAMMAR)
            if pages > 20:
                self.fail("findings walk did not terminate")
        self.assertEqual(seen, list(range(1, 9)))
        self.assertGreaterEqual(pages, 2)
        self.assertEqual(first_note, "page-note")

        uni = {
            "severity": "p1",
            "where": utf8_fill(256, mark="W"),
            "claim": utf8_fill(1024, mark="C"),
            "reproduce": utf8_fill(1024, mark="R"),
        }
        uni_note = utf8_fill(2048, mark="N")
        self.plant_context_record(findings=[uni], note=uni_note)
        proc = self.query("lane_findings", {"id": JOB})
        payload = self.assert_v2(self.load_query(proc), "lane_findings")
        self.assertEqual(payload["state"], "ok")
        self.assert_page_cap(payload, proc.stdout)
        self.assertEqual(payload["findings_total"], 1)
        self.assertEqual(payload["page"]["count"], 1)
        self.assertFalse(payload["page"]["more"])
        entry = payload["findings"][0]
        self.assertEqual(entry["n"], 1)
        fit = self.assert_fit_omission_order(entry)
        self.assertGreaterEqual(len(fit), 1)
        self.assertIn("reproduce", fit)
        if payload["note"] is not None:
            self.assertEqual(payload["note"], uni_note)
            self.assertNotEqual(payload["reason"], "note exceeds page")

    def test_lane_findings_note_removal_after_whole_and_intermediate_forms(self):
        job = utf8_fill(256, mark="J")
        stage = utf8_fill(256, mark="S")
        store_rel = "s" * 200
        source = {
            "severity": "p1",
            "where": ascii_fill(256, "w-"),
            "claim": ascii_fill(1024, "c-"),
            "reproduce": ascii_fill(1024, "r-"),
        }
        self.assertEqual(len(source["where"].encode("utf-8")), 256)
        self.assertEqual(len(source["claim"].encode("utf-8")), 1024)
        self.assertEqual(len(source["reproduce"].encode("utf-8")), 1024)
        rec = self.plant_context_record(
            findings=[source],
            note="placeholder",
            job=job,
            stage=stage,
        )
        # Re-plant under a long store path after config points there.
        long_store = self.work / store_rel
        self.plant_v2_config(dispatch_store=store_rel)
        moved = long_store / JOB / "record.json"
        raw = rec.read_bytes()
        self._plant_bytes(moved, raw)
        planted = moved.read_bytes()
        self.assertEqual(planted, raw)
        self.assertEqual(len(planted), len(raw))
        rev = revision_of(moved)
        record_obj = {
            "id": JOB,
            "job": job,
            "stage": stage,
            "recorded_status": "closed",
            "verdict": VERDICT,
            "counts": dict(COUNTS),
            "revision": rev,
            "evidence": str(moved),
        }
        forms = fit_omission_forms(1, source)
        whole, reduced = forms[0], forms[-1]
        self.assertEqual(reduced["omitted"], list(FIT_FIELDS))
        page_one = {"from": 1, "to": 1, "count": 1, "more": False, "next_cursor": None}

        def reply(
            entry: dict,
            note_text: str | None,
            note_bytes: int,
            reason: str | None,
        ) -> dict:
            return {
                "schema": SCHEMA,
                "query": "lane_findings",
                "state": "ok",
                "reason": reason,
                "record": record_obj,
                "note": note_text,
                "note_bytes": note_bytes,
                "findings": [entry],
                "findings_total": 1,
                "page": page_one,
            }

        chosen = None
        chosen_n = None
        chosen_with_reduced = None
        chosen_without = None
        chosen_frame = None
        for n in range(2, 2048, 2):
            note_text = utf8_fill(n)
            with_reduced = reply(reduced, note_text, n, None)
            without = reply(reduced, None, n, "note exceeds page")
            if (
                len(canonical_bytes(with_reduced)) > FINDINGS_PAGE_CAP
                and len(canonical_bytes(without)) <= FINDINGS_PAGE_CAP
            ):
                frame_plus = dict(with_reduced)
                frame_plus["findings"] = []
                frame_plus["page"] = {
                    "from": None,
                    "to": None,
                    "count": 0,
                    "more": True,
                    "next_cursor": f"{rev}:1",
                }
                if len(canonical_bytes(frame_plus)) > FINDINGS_PAGE_CAP:
                    continue
                form_sizes = [
                    len(canonical_bytes(reply(entry, note_text, n, None)))
                    for entry in forms
                ]
                if all(size > FINDINGS_PAGE_CAP for size in form_sizes):
                    chosen = note_text
                    chosen_n = n
                    chosen_with_reduced = with_reduced
                    chosen_without = without
                    chosen_frame = frame_plus
                    break
        self.assertIsNotNone(
            chosen,
            "standard encoder could not place note-removal after whole and intermediates",
        )
        assert chosen is not None
        assert chosen_n is not None
        assert chosen_with_reduced is not None
        assert chosen_without is not None
        assert chosen_frame is not None
        with_sizes = [
            len(canonical_bytes(reply(entry, chosen, chosen_n, None)))
            for entry in forms
        ]
        # Fixture inequalities: stdlib encoder only; do not need v2 present.
        self.assertGreater(with_sizes[0], FINDINGS_PAGE_CAP, with_sizes)
        for size, entry in zip(with_sizes, forms, strict=True):
            self.assertGreater(size, FINDINGS_PAGE_CAP, entry["omitted"])
        self.assertGreater(
            with_sizes[0],
            with_sizes[-1],
            "whole entry must be larger than the reduced entry (needs reduction)",
        )
        for i in range(1, len(with_sizes)):
            self.assertGreater(with_sizes[i - 1], with_sizes[i], i)
        self.assertGreater(len(canonical_bytes(chosen_with_reduced)), FINDINGS_PAGE_CAP)
        self.assertLessEqual(len(canonical_bytes(chosen_without)), FINDINGS_PAGE_CAP)
        self.assertLessEqual(len(canonical_bytes(chosen_frame)), FINDINGS_PAGE_CAP)
        self.assertGreater(
            len(canonical_bytes(reply(whole, chosen, chosen_n, None))),
            FINDINGS_PAGE_CAP,
        )
        rec_body = json.loads(moved.read_text(encoding="utf-8"))
        rec_body["result"]["envelope"]["note"] = chosen
        rec_body["job"] = job
        rec_body["stage"] = stage
        new_raw = (json.dumps(rec_body, indent=2) + "\n").encode("utf-8")
        self._plant_bytes(moved, new_raw)
        self.assertEqual(moved.read_bytes(), new_raw)
        planted_env = json.loads(moved.read_text(encoding="utf-8"))["result"]["envelope"]
        self.assertEqual(planted_env["findings"], [source])
        self.assertEqual(planted_env["note"], chosen)
        self.assertIn(source["where"].encode("utf-8"), new_raw)
        self.assertIn(source["claim"].encode("utf-8"), new_raw)
        self.assertIn(source["reproduce"].encode("utf-8"), new_raw)
        proc = self.query("lane_findings", {"id": JOB})
        payload = self.assert_v2(self.load_query(proc), "lane_findings")
        self.assertEqual(payload["state"], "ok")
        self.assert_page_cap(payload, proc.stdout)
        self.assertIsNone(payload["note"])
        self.assertEqual(payload["reason"], "note exceeds page")
        self.assertEqual(payload["note_bytes"], len(chosen.encode("utf-8")))
        self.assertEqual(payload["page"]["count"], 1)
        self.assertEqual(payload["findings"][0]["n"], 1)


class TestLaneMcpContextChangesGaps(_V2Fixture):
    def test_lane_changes_partitions_readable_missing_and_malformed_together(self):
        readable = self.plant_context_record(JOB)
        rev = revision_of(readable)
        malformed = self.store / JOB3 / "record.json"
        self._plant_bytes(malformed, b"{not-json\n")
        self.assertEqual(malformed.read_bytes(), b"{not-json\n")
        self.assertFalse((self.store / JOB2 / "record.json").exists())
        self.plant_v2_config(job_ids=[JOB, JOB2, JOB3])

        payload = self.assert_v2(
            self.load_query(self.query("lane_changes", {})),
            "lane_changes",
        )
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["configured_ids"], [JOB, JOB2, JOB3])
        self.assertEqual([row["id"] for row in payload["changed"]], [JOB])
        self.assertEqual(payload["unchanged"], [])
        self.assertEqual(payload["unknown"], [JOB2])
        self.assertEqual(payload["unavailable"], [JOB3])
        self.assertEqual(payload["checkpoint"], {JOB: rev})
        self.assertNotIn(JOB2, payload["checkpoint"])
        self.assertNotIn(JOB3, payload["checkpoint"])
        self.assertEqual(payload["checkpoint"][JOB], rev)

        held = self.assert_v2(
            self.load_query(
                self.query("lane_changes", {"checkpoint": {JOB: rev}})
            ),
            "lane_changes",
        )
        self.assertEqual(held["state"], "ok")
        self.assertEqual(held["unchanged"], [JOB])
        self.assertEqual(held["changed"], [])
        self.assertEqual(held["unknown"], [JOB2])
        self.assertEqual(held["unavailable"], [JOB3])
        self.assertEqual(held["checkpoint"], {JOB: rev})


class TestLaneMcpContextJobsGaps(_V2Fixture):
    def test_lane_jobs_dispatchable_predicates_and_bounded_cards(self):
        long_deliverable = "D" * 513
        long_constraint = "K" * 257
        constraints = [f"c{n}" for n in range(17)]
        constraints[3] = long_constraint
        constraints[5] = 12
        jobs = {
            "author-tests": dict(DISPATCHABLE),
            "read-role": {**DISPATCHABLE, "role": "read"},
            "not-harness": {**DISPATCHABLE, "adapter": "direct"},
            "bad-role": {**DISPATCHABLE, "role": "plan"},
            "bad-snap": {**DISPATCHABLE, "snapshot": "diff"},
            "empty-prompt": {**DISPATCHABLE, "prompt": ""},
            "no-prompt": {k: v for k, v in DISPATCHABLE.items() if k != "prompt"},
            "all-slots": {
                **DISPATCHABLE,
                "prompt": "Use {ref} {base} {diff} {into} {out} {inputs} {scope}.",
            },
            "card-bounds": {
                **DISPATCHABLE,
                "deliverable": long_deliverable,
                "constraints": constraints,
                "caps": {"timeout": -1},
            },
            "timeout-str": {**DISPATCHABLE, "caps": {"timeout": "fast"}},
            "constraints-obj": {**DISPATCHABLE, "constraints": {"a": 1}},
            "1nvalid": {"adapter": "harness", "role": "write", "snapshot": "whole"},
        }
        planted = self.plant_registry(jobs)
        self.assertIn(b"not-harness", planted.read_bytes())
        self.plant_v2_config()
        listing = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(listing["state"], "ok")
        rows = listing["jobs"]
        self.assertEqual(len(rows), 12)
        by_name = {row["name"]: row for row in rows if row["name"]}
        self.assertTrue(by_name["author-tests"]["dispatchable"])
        self.assertTrue(by_name["read-role"]["dispatchable"])
        self.assertTrue(by_name["all-slots"]["dispatchable"])
        self.assertEqual(
            by_name["all-slots"]["slots"],
            sorted(PLACEHOLDERS),
        )
        for name in (
            "not-harness",
            "bad-role",
            "bad-snap",
            "empty-prompt",
            "no-prompt",
        ):
            with self.subTest(name=name):
                self.assertFalse(by_name[name]["dispatchable"], name)
        invalid = [row for row in rows if row["name"] is None]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(
            invalid[0],
            {
                "name": None,
                "adapter": None,
                "role": None,
                "dispatchable": False,
                "slots": [],
            },
        )
        dumped = json.dumps(listing)
        self.assertNotIn("1nvalid", dumped)

        card = self.assert_v2(
            self.load_query(self.query("lane_jobs", {"name": "card-bounds"})),
            "lane_jobs",
        )
        job = card["job"]
        self.assertIsNone(job["deliverable"])
        self.assertEqual(len(job["constraints"]), 16)
        self.assertEqual(job["constraints"][0], "c0")
        self.assertIsNone(job["constraints"][3])
        self.assertIsNone(job["constraints"][5])
        self.assertNotIn("c16", job["constraints"])
        self.assertIsNone(job["timeout"])
        self.assertEqual(
            job["prompt_bytes"],
            len(DISPATCHABLE["prompt"].encode("utf-8")),
        )
        self.assertNotIn("prompt", job)

        timeout_str = self.assert_v2(
            self.load_query(self.query("lane_jobs", {"name": "timeout-str"})),
            "lane_jobs",
        )
        self.assertIsNone(timeout_str["job"]["timeout"])
        obj_constraints = self.assert_v2(
            self.load_query(self.query("lane_jobs", {"name": "constraints-obj"})),
            "lane_jobs",
        )
        self.assertEqual(obj_constraints["job"]["constraints"], [])


class TestLaneMcpContextMetaGaps(_V2Fixture):
    def test_request_meta_on_templates_list_and_v2_reads(self):
        self.plant_context_record()
        self.plant_registry({"author-tests": dict(DISPATCHABLE)})
        self.plant_v2_config()
        token = {"progressToken": "tok-v2-meta-aa41"}
        cli = {
            "findings": self.assert_v2(
                self.load_query(self.query("lane_findings", {"id": JOB})),
                "lane_findings",
            ),
            "changes": self.assert_v2(
                self.load_query(self.query("lane_changes", {})),
                "lane_changes",
            ),
            "jobs": self.assert_v2(
                self.load_query(self.query("lane_jobs", {})),
                "lane_jobs",
            ),
        }
        requests = [
            rpc("resources/templates/list", 2, {"_meta": token}),
            rpc("resources/templates/list", 3),
            rpc(
                "tools/call",
                4,
                {
                    "name": "lane_findings",
                    "arguments": {"id": JOB},
                    "_meta": token,
                },
            ),
            rpc(
                "tools/call",
                5,
                {"name": "lane_findings", "arguments": {"id": JOB}},
            ),
            rpc(
                "tools/call",
                6,
                {"name": "lane_changes", "arguments": {}, "_meta": token},
            ),
            rpc("tools/call", 7, {"name": "lane_changes", "arguments": {}}),
            rpc(
                "tools/call",
                8,
                {"name": "lane_jobs", "arguments": {}, "_meta": token},
            ),
            rpc("tools/call", 9, {"name": "lane_jobs", "arguments": {}}),
            rpc("resources/templates/list", 10, {"_meta": "not-an-object"}),
            rpc(
                "tools/call",
                11,
                {
                    "name": "lane_findings",
                    "arguments": {"id": JOB},
                    "_meta": ["x"],
                },
            ),
            rpc(
                "tools/call",
                12,
                {"name": "lane_changes", "arguments": {}, "_meta": 1},
            ),
            rpc(
                "tools/call",
                13,
                {"name": "lane_jobs", "arguments": {}, "_meta": True},
            ),
        ]
        proc = self.serve(self.handshake(requests))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("tok-v2-meta-aa41", proc.stdout)
        objs = self.rpc_objects(proc)
        self.assertEqual(self.notifications(objs), [])
        listed = self.rpc_by_id(objs, 2)["result"]["resourceTemplates"]
        self.assertEqual(listed, self.rpc_by_id(objs, 3)["result"]["resourceTemplates"])
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["uriTemplate"], "lane://jobs/{name}")
        self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 4)), cli["findings"])
        self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 5)), cli["findings"])
        self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 6)), cli["changes"])
        self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 7)), cli["changes"])
        self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 8)), cli["jobs"])
        self.assertEqual(self.lane_from_rpc(self.rpc_by_id(objs, 9)), cli["jobs"])
        for rpc_id in (10, 11, 12, 13):
            with self.subTest(rpc_id=rpc_id):
                obj = self.rpc_by_id(objs, rpc_id)
                self.assertIn("error", obj)
                self.assertEqual(obj["error"]["code"], -32602)


class TestLaneMcpContextSurfaceGaps(_V2Fixture):
    def test_v2_reads_leave_owned_bytes_and_names_unchanged(self):
        rec = self.plant_context_record()
        rec2 = self.plant_context_record(JOB2, extra={"id": JOB2})
        registry = self.plant_registry({"author-tests": dict(DISPATCHABLE)})
        config = self.plant_v2_config(job_ids=[JOB, JOB2])
        before = self.owned_surface()
        self.assertEqual(before["config"], config.read_bytes())
        self.assertEqual(before["registry"], registry.read_bytes())
        self.assertEqual(
            before["records"][f"store/{JOB}/record.json"], rec.read_bytes()
        )
        self.assertEqual(
            before["records"][f"store/{JOB2}/record.json"], rec2.read_bytes()
        )
        for tool, arguments in (
            ("lane_findings", {"id": JOB}),
            ("lane_changes", {}),
            ("lane_jobs", {}),
        ):
            first = self.load_query(self.query(tool, arguments))
            second = self.load_query(self.query(tool, arguments))
            self.assertEqual(first, second, tool)
            self.assert_v2(first, tool)
        proc = self.serve(
            self.handshake(
                [
                    rpc(
                        "tools/call",
                        2,
                        {"name": "lane_findings", "arguments": {"id": JOB}},
                    ),
                    rpc(
                        "tools/call",
                        3,
                        {"name": "lane_changes", "arguments": {}},
                    ),
                    rpc(
                        "tools/call",
                        4,
                        {"name": "lane_jobs", "arguments": {}},
                    ),
                ]
            )
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        after = self.owned_surface()
        self.assertEqual(after, before)
        self.assertNotIn("checkpoint.json", after["work_files"])

    def test_v2_refusals_and_readings_match_across_cli_tool_resource(self):
        rec = self.plant_context_record()
        rev = revision_of(rec)
        self.plant_registry({"author-tests": dict(DISPATCHABLE)})
        self.plant_v2_config()
        refusals = [
            ("unlisted-id", "lane_findings", {"id": JOB_UNLISTED}, "arguments"),
            ("extra-key", "lane_findings", {"id": JOB, "extra": 1}, "arguments"),
            (
                "stale-cursor",
                "lane_findings",
                {"id": JOB, "cursor": f"{'b' * 16}:1"},
                "cursor",
            ),
            (
                "past-cursor",
                "lane_findings",
                {"id": JOB, "cursor": f"{rev}:9"},
                "cursor",
            ),
            (
                "unlisted-checkpoint",
                "lane_changes",
                {"checkpoint": {JOB_UNLISTED: rev}},
                "arguments",
            ),
            ("bad-job-name", "lane_jobs", {"name": "Author-tests"}, "arguments"),
        ]
        cli_lines = {}
        for name, tool, arguments, class_token in refusals:
            with self.subTest(cli=name):
                proc = self.query(tool, arguments)
                cli_lines[name] = self.assert_refusal(proc, class_token)
        requests = []
        rpc_id = 20
        ids = {}
        for name, tool, arguments, _class_token in refusals:
            ids[name] = rpc_id
            requests.append(
                rpc("tools/call", rpc_id, {"name": tool, "arguments": arguments})
            )
            rpc_id += 1
        proc = self.serve(self.handshake(requests))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        objs = self.rpc_objects(proc)
        for name, _tool, _arguments, _class_token in refusals:
            with self.subTest(mcp=name):
                self.assert_domain_error_matches_cli(
                    self.rpc_by_id(objs, ids[name]), cli_lines[name]
                )

        self.reset_sources()
        self.plant_v2_config()
        self.store.mkdir()
        missing_cli = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertEqual(missing_cli["state"], "unknown")
        proc = self.serve(
            self.handshake(
                [
                    rpc(
                        "tools/call",
                        2,
                        {"name": "lane_findings", "arguments": {"id": JOB}},
                    ),
                    rpc("resources/read", 3, {"uri": f"lane://findings/{JOB}"}),
                ]
            )
        )
        objs = self.rpc_objects(proc)
        self.assert_reading_matches_cli(self.rpc_by_id(objs, 2), missing_cli)
        self.assert_reading_matches_cli(self.rpc_by_id(objs, 3), missing_cli)

        self.plant_context_record(raw=b"{not-json\n")
        bad_cli = self.assert_v2(
            self.load_query(self.query("lane_findings", {"id": JOB})),
            "lane_findings",
        )
        self.assertEqual(bad_cli["state"], "unavailable")
        proc = self.serve(
            self.handshake(
                [
                    rpc(
                        "tools/call",
                        2,
                        {"name": "lane_findings", "arguments": {"id": JOB}},
                    ),
                    rpc("resources/read", 3, {"uri": f"lane://findings/{JOB}"}),
                ]
            )
        )
        objs = self.rpc_objects(proc)
        self.assert_reading_matches_cli(self.rpc_by_id(objs, 2), bad_cli)
        self.assert_reading_matches_cli(self.rpc_by_id(objs, 3), bad_cli)

        self.reset_sources()
        self.plant_v2_config(job_ids=[JOB, JOB2])
        missing_changes_cli = self.assert_v2(
            self.load_query(self.query("lane_changes", {})),
            "lane_changes",
        )
        self.assertEqual(missing_changes_cli["state"], "unknown")
        proc = self.serve(
            self.handshake(
                [
                    rpc("tools/call", 2, {"name": "lane_changes", "arguments": {}}),
                    rpc("resources/read", 3, {"uri": "lane://changes"}),
                ]
            )
        )
        objs = self.rpc_objects(proc)
        self.assert_reading_matches_cli(
            self.rpc_by_id(objs, 2), missing_changes_cli
        )
        self.assert_reading_matches_cli(
            self.rpc_by_id(objs, 3), missing_changes_cli
        )

        self.reset_sources()
        self.plant_v2_config(job_registry=None)
        disabled_cli = self.assert_v2(
            self.load_query(self.query("lane_jobs", {})),
            "lane_jobs",
        )
        self.assertEqual(disabled_cli["state"], "unknown")
        proc = self.serve(
            self.handshake(
                [
                    rpc("tools/call", 2, {"name": "lane_jobs", "arguments": {}}),
                    rpc("resources/read", 3, {"uri": "lane://jobs"}),
                ]
            )
        )
        objs = self.rpc_objects(proc)
        self.assert_reading_matches_cli(self.rpc_by_id(objs, 2), disabled_cli)
        self.assert_reading_matches_cli(self.rpc_by_id(objs, 3), disabled_cli)

    def test_v1_brief_alerts_usage_payloads_stay_byte_identical_under_v2(self):
        self.plant_context_record()
        self.plant_events(
            [
                {
                    "id": 4,
                    "consumer": CONSUMER,
                    "delivery": "queued",
                    "summary": "v2-compat-event",
                }
            ]
        )
        self.plant_rollout([self.token_row()])
        self.plant_registry({"author-tests": dict(DISPATCHABLE)})
        self.plant_config(job_ids=[JOB])
        tools = (
            ("lane_record", {"id": JOB}),
            ("lane_brief", {}),
            ("lane_alerts", {}),
            ("lane_usage", {}),
        )
        v1_raw = {}
        v1 = {}
        for tool, arguments in tools:
            proc = self.query(tool, arguments)
            v1[tool] = self.load_query(proc)
            v1_raw[tool] = proc.stdout
        dumped = json.dumps(v1["lane_brief"])
        for phrase in OMISSIONS:
            self.assertIn(phrase, dumped)
        self.assertNotIn("lane_findings", dumped)
        self.assertEqual(v1["lane_brief"]["configured_ids"], [JOB])

        self.plant_v2_config(job_ids=[JOB])
        v2_brief = None
        for tool, arguments in tools:
            proc = self.query(tool, arguments)
            with self.subTest(tool=tool):
                payload = self.load_query(proc)
                self.assertEqual(proc.stdout, v1_raw[tool])
                self.assertEqual(payload, v1[tool])
                self.assertLessEqual(len(proc.stdout.encode("utf-8")), QUERY_CAP)
                if tool == "lane_brief":
                    v2_brief = payload
        self.assertIsNotNone(v2_brief)
        dumped_v2 = json.dumps(v2_brief)
        for phrase in OMISSIONS:
            self.assertIn(phrase, dumped_v2)


class DiscoveryImplementationIdentity(unittest.TestCase):
    def test_new_shared_dependencies_participate_in_the_implementation_fingerprint(self):
        with World(prefix="discovery-identity-") as world:
            staged = world.stage(READ.with_name("lane_refs.py"), siblings=(
                "lane_read.py", "lane_mcp.py", "instrument.py", "mcp_transport.py", "dispatch_observation.py",
            ))
            with patch.object(sys, "path", [str(staged.parent), *sys.path]), patch.dict(sys.modules):
                sys.modules.pop("lane_read", None)
                spec = importlib.util.spec_from_file_location("refs_under_test", staged)
                refs = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(refs)

                def fingerprint():
                    acquisition = refs.Acquisition({"discovery": {"anchors": {}}},
                                                   contract_bytes=b"synthetic fixed contract")
                    try:
                        return acquisition.definitions["adapter"]["implementation"]
                    finally:
                        acquisition.close()

                original = fingerprint()
                for name in ("mcp_transport.py", "dispatch_observation.py"):
                    with self.subTest(dependency=name):
                        path = staged.parent / name
                        data = path.read_text()
                        world.plant_text(path, data + "\n# planted implementation change\n")
                        self.assertNotEqual(fingerprint(), original)
                        world.plant_text(path, data)
                        self.assertEqual(fingerprint(), original)


if __name__ == "__main__":
    unittest.main()
