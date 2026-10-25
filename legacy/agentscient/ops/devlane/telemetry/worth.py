#!/usr/bin/env python3
"""worth — costs and results, joined (ops/process/worth.md).

`report` joins the window's per-harness spend (same stores and
accounting rules as usage.py) with what the repo's history says
landed; `waste` ranks the window's sessions by spend and names the
signals. Every figure is produced at run time and stamped with the
window and repo state it was measured against. Grok cost stays in
raw ticks: the scale is unverified, so no figure here is ever USD.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "conductor" / "tools")
)
import laneconf

CHURN_FACTOR = 20
MERGE_PR = re.compile(r"^Merge pull request #(\d+)\b")
_CLAUDE_CURRENCIES = frozenset((
    "input_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens", "output_tokens"))
_CODEX_CURRENCIES = frozenset((
    "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_output_tokens", "total_tokens"))
_GROK_CURRENCIES = frozenset((
    "inputTokens", "cachedReadTokens", "cacheCreationTokens",
    "outputTokens", "totalTokens", "reasoningTokens", "costUsdTicks"))


def parse_stamp(value):
    """Store-side: a malformed timestamp raises ValueError so the
    reader can treat the SESSION as unparseable — a gap, never a
    crash and never a silently dropped row."""
    if not isinstance(value, str):
        # ValueError on purpose: callers treat every malformed stamp
        # as one class of gap, whatever the malformation
        raise ValueError(f"non-string timestamp: {value!r}")
    trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value)
    stamp = datetime.fromisoformat(trimmed)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def parse_iso(value, label):
    try:
        return parse_stamp(value)
    except ValueError:
        raise SystemExit2(
            f"invalid {label}: {value!r} is not ISO 8601") from None


class SystemExit2(Exception):
    pass


# ---------------------------------------------------------------- stores

def read_jsonl(path):
    """Every line parses or the file is unparseable — a half-read
    store silently under-reports, which is worse than a gap."""
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except (OSError, ValueError):
        return None
    return rows


DENY_MARKER = "hook returned a deny decision"
# The refusal wire as a line: an optional [source] tag, the class token,
# then expected, found and needed in that order. Anchored at a line start
# so a result that quotes a wire line inside an assertion message is not
# a refusal (review of 75f6e9cf).
WIRE_LINE = re.compile(
    r"^\s*(?:\[[^\]\n]*\]\s*)?[a-z][a-z0-9-]*: expected .*; found .*; needed ",
    re.MULTILINE,
)


def _is_refusal_text(text):
    """The harness's deny marker, or the lane's refusal wire
    (class: expected; found; needed) as a hook prints it since the
    marker stopped prefixing it (measured 2026-09-15 on session
    44f9de58: 30 wire refusals, none with the marker)."""
    return DENY_MARKER in text or WIRE_LINE.search(text) is not None


def _in_repo(cwd, repo):
    """A row is the repo's when its cwd is the root or a directory under
    it; a sibling that shares the prefix without the separator is another
    repository (measured 2026-09-15: 30 usage rows under ops/ and
    ops/conductor/tools counted as no session's)."""
    return isinstance(cwd, str) and (cwd == repo or cwd.startswith(repo + os.sep))


def _tool_result_denied(message):
    content = message.get("content") or []
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == "tool_result"
        and item.get("is_error") is True
        and _is_refusal_text(str(item.get("content", "")))
        for item in content
    )


def _claude_gap(kind, repo="unrecorded", window="unrecorded"):
    return {
        "kind": "gap",
        "gap_kind": kind,
        "repo": repo,
        "window": window,
        "turns": "unrecorded",
        "avg_context": "unrecorded",
        "refusals": "unrecorded",
    }


def _validated_claude_usage(usage):
    values = {key: usage.get(key, 0) for key in _CLAUDE_CURRENCIES}
    if any(isinstance(value, bool)
           or not isinstance(value, (int, float))
           or value < 0
           or (isinstance(value, float) and not math.isfinite(value))
           for value in values.values()):
        raise ValueError(
            "Claude usage currencies must be numeric, nonnegative, and finite"
        )
    return values


def claude_sessions(root, repo, window, include_unreadable=False):
    """Per-message accounting, last write per message id wins,
    cwd-filtered to the repo."""
    since, until = window
    sessions = {}
    for path in sorted(Path(root).glob("*/*.jsonl")):
        rows = read_jsonl(path)
        if rows is None:
            slug = repo.replace("/", "-").replace(".", "-")
            if include_unreadable and path.parent.name == slug:
                sessions[path.stem] = _claude_gap("unreadable-transcript")
            continue
        in_window = {}
        refusals = 0
        try:
            for row in rows:
                if not _in_repo(row.get("cwd"), repo):
                    continue
                message = row.get("message") or {}
                usage = message.get("usage")
                mid = message.get("id")
                stamp = row.get("timestamp")
                if not stamp:
                    continue
                at = parse_stamp(stamp)
                if not since <= at < until:
                    continue
                if _tool_result_denied(message):
                    refusals += 1
                if (row.get("type") == "assistant"
                        and isinstance(usage, dict)
                        and set(usage) & _CLAUDE_CURRENCIES
                        and mid):
                    # last-wins among IN-WINDOW emissions: a re-emission
                    # after the window must not erase history from it
                    in_window[mid] = (at, stamp, usage)
        except ValueError:
            if include_unreadable:
                sessions[path.stem] = _claude_gap(
                    "malformed-timestamp", repo=repo)
            continue
        if not in_window:
            continue
        sid = path.stem
        try:
            in_window = {
                mid: (at, raw, _validated_claude_usage(usage))
                for mid, (at, raw, usage) in in_window.items()
            }
        except ValueError:
            if include_unreadable:
                window_name = f"[{since.isoformat()},{until.isoformat()})"
                sessions[sid] = _claude_gap(
                    "malformed-usage", repo=repo, window=window_name)
            continue
        contexts = [
            usage["input_tokens"]
            + usage["cache_creation_input_tokens"]
            + usage["cache_read_input_tokens"]
            for _, _, usage in in_window.values()
        ]
        totals = {"in": 0, "cached": 0, "out": 0}
        heavy = None
        for mid, (_at, _raw, usage) in sorted(in_window.items()):
            out = usage.get("output_tokens", 0)
            totals["in"] += usage.get("input_tokens", 0)
            totals["cached"] += (usage.get("cache_creation_input_tokens", 0)
                                 + usage.get("cache_read_input_tokens", 0))
            totals["out"] += out
            if heavy is None or out > heavy["out"]:
                heavy = {"message": mid, "out": out}
        cached_read = sum(
            usage.get("cache_read_input_tokens", 0)
            for _, _, usage in in_window.values()
        )
        sessions[sid] = {
            "messages": len(in_window),
            "turns": len(contexts),
            "avg_context": sum(contexts) / len(contexts),
            "refusals": refusals,
            "in": totals["in"],
            "cached": totals["cached"],
            "out": totals["out"],
            "total": totals["in"] + totals["cached"] + totals["out"],
            "cached_read": cached_read,
            "heavy": heavy,
        }
    return sessions


def codex_sessions(root, repo, window):
    """token_count events are cumulative; the last in-window count IS
    the spend (summing them is the measured double-count mistake)."""
    since, until = window
    sessions = {}
    for path in sorted(Path(root).glob("sessions/*/*/*/rollout-*.jsonl")):
        rows = read_jsonl(path)
        if rows is None:
            continue
        meta = next((row for row in rows
                     if row.get("type") == "session_meta"), None)
        if not meta or (meta.get("payload") or {}).get("cwd") != repo:
            continue
        sid = (meta.get("payload") or {}).get("id") or path.stem
        counts = []
        try:
            for row in rows:
                payload = row.get("payload") or {}
                if (row.get("type") == "event_msg"
                        and payload.get("type") == "token_count"):
                    usage = (payload.get("info") or {}).get(
                        "total_token_usage")
                    stamp = row.get("timestamp")
                    if (isinstance(usage, dict) and stamp
                            and set(usage) & _CODEX_CURRENCIES):
                        counts.append((parse_stamp(stamp), stamp, usage))
        except ValueError:
            continue
        in_window = [(at, raw, usage) for at, raw, usage in counts
                     if since <= at < until]
        if not in_window:
            continue
        last = in_window[-1][2]
        # counts are cumulative for the SESSION: the last pre-window
        # count is the baseline, or a straddling session charges its
        # pre-window spend to this window
        baseline = {}
        for at, _raw, usage in counts:
            if at < since:
                baseline = usage

        def net(key, last=last, baseline=baseline):
            return last.get(key, 0) - baseline.get(key, 0)

        # the heaviest MESSAGE is the largest step between
        # consecutive counts, session-wide so the first in-window
        # message is not credited with pre-window spend
        deltas = {}
        prev_out = 0
        for _at, raw, usage in counts:
            out_here = usage.get("output_tokens", 0)
            deltas[raw] = out_here - prev_out
            prev_out = out_here
        _, heavy_raw, heavy_usage = max(
            in_window, key=lambda item: deltas.get(item[1], 0))
        sessions[sid] = {
            "messages": len(in_window),
            "in": net("input_tokens"),
            "cached": net("cached_input_tokens"),
            "out": net("output_tokens"),
            "total": net("input_tokens") + net("output_tokens"),
            "cached_read": net("cached_input_tokens"),
            "heavy": {"at": heavy_raw,
                      "out": heavy_usage.get("output_tokens", 0),
                      "out_delta": deltas.get(heavy_raw, 0)},
        }
    return sessions


def grok_runs(rows):
    """usage.py's run accounting: cumulative within a run, a run ends
    when a REPORTED totalTokens shrinks, absent totals merge, the last
    report per currency wins."""
    runs = []
    current = {}
    current_at = None
    prev_total = None
    run_incomplete = False
    for row in rows:
        update = (row.get("params") or {}).get("update")
        if not (isinstance(update, dict)
                and update.get("sessionUpdate") == "turn_completed"):
            continue
        usage = update.get("usage")
        if not isinstance(usage, dict) or not set(usage) & _GROK_CURRENCIES:
            continue
        total = usage.get("totalTokens")
        if (total is not None and prev_total is not None
                and total < prev_total):
            if current:
                runs.append((current, current_at, run_incomplete))
            current = {}
            run_incomplete = False
        if total is not None:
            prev_total = total
        current.update(usage)
        if usage.get("usageIsIncomplete"):
            run_incomplete = True
        current_at = row.get("timestamp")
    if current:
        runs.append((current, current_at, run_incomplete))
    return runs


def _summary_overlaps(summary, since, until):
    try:
        created = parse_stamp(summary.get("created_at"))
        updated = parse_stamp(summary.get("updated_at"))
    except ValueError:
        return False
    return created < until and updated >= since


def grok_sessions(root, repo, window):
    since, until = window
    sessions = {}
    base = Path(root) / "sessions" / quote(repo, safe="")
    for session_dir in sorted(base.iterdir()) if base.is_dir() else []:
        try:
            summary = json.loads(
                (session_dir / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # not even attributable to this repo
        if (summary.get("info") or {}).get("cwd") != repo:
            continue
        sid = session_dir.name
        updates = read_jsonl(session_dir / "updates.jsonl")
        if updates is None:
            # attributable session whose spend is unreadable: it MUST
            # hold a place in the counted=N/M denominator, or a store
            # with holes reports itself complete (Codex audit, PR #31)
            if _summary_overlaps(summary, since, until):
                sessions[sid] = {"runs": 0, "incomplete": False}
            continue
        runs = grok_runs(updates)
        if any(at is not None and not isinstance(at, (int, float))
               for _, at, _ in runs):
            # a non-numeric run timestamp is a malformed store shape
            # (events.jsonl uses ISO; updates.jsonl is epoch) — the
            # session's spend is unreadable, not zero and not a crash
            if _summary_overlaps(summary, since, until):
                sessions[sid] = {"runs": 0, "incomplete": False}
            continue
        in_window = []
        incomplete = False
        for usage, at_epoch, run_incomplete in runs:
            if at_epoch is None:
                continue
            at = datetime.fromtimestamp(at_epoch, timezone.utc)
            if since <= at < until:
                in_window.append(usage)
                # incompleteness rides the runs INSIDE the window: an
                # incomplete run elsewhere says nothing about these
                if run_incomplete:
                    incomplete = True
        if runs and not in_window:
            continue  # usage exists, none of it in this window
        if not runs:
            present = any(
                since <= datetime.fromtimestamp(row.get("timestamp", 0),
                                                timezone.utc) < until
                for row in updates
                if isinstance(row.get("timestamp"), (int, float))
            )
            if not present:
                continue
        record = {"runs": len(in_window), "incomplete": incomplete}
        if in_window:
            record["in"] = sum(u.get("inputTokens", 0) for u in in_window)
            record["cached"] = sum(u.get("cachedReadTokens", 0)
                                   + u.get("cacheCreationTokens", 0)
                                   for u in in_window)
            record["out"] = sum(u.get("outputTokens", 0) for u in in_window)
            record["total"] = sum(u.get("totalTokens", 0) for u in in_window)
            record["cached_read"] = sum(u.get("cachedReadTokens", 0)
                                        for u in in_window)
            if all("costUsdTicks" in u for u in in_window):
                record["ticks"] = sum(u["costUsdTicks"] for u in in_window)
            heaviest = max(in_window,
                           key=lambda u: u.get("outputTokens", 0))
            record["heavy"] = {"out": heaviest.get("outputTokens", 0)}
        sessions[sid] = record
    return sessions


# ---------------------------------------------------------------- git

def run_git(repo, *args):
    proc = subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def repo_results(repo, window):
    since, until = window
    stdout = run_git(repo, "log", "--first-parent",
                     "--format=%H%x00%cI%x00%s%x00%P", "HEAD")
    line_log = []
    for line in (stdout or "").splitlines():
        parts = line.split("\x00")
        if len(parts) == 4:
            sha, cdate, subject, parents = parts
            line_log.append((sha, parse_iso(cdate, "commit date"),
                             subject, parents.split()))
    merges, prs, commits = [], [], 0
    for sha, at, subject, parents in line_log:
        if not since <= at < until:
            continue
        if len(parents) > 1:
            entry = {"sha": sha[:7], "subject": subject}
            numbered = MERGE_PR.match(subject)
            if numbered:
                entry["pr"] = int(numbered.group(1))
                prs.append(int(numbered.group(1)))
            merges.append(entry)
        else:
            commits += 1

    def edge_sha(predicate):
        for sha, at, _, _ in line_log:
            if predicate(at):
                return sha
        return None

    def count_tests(sha):
        # git grep -I skips binary blobs, so an image in the tree is
        # not a decode crash; exit 1 just means zero definitions
        proc = subprocess.run(
            ["git", "-C", repo, "grep", "-I", "-c", "-E",
             r"^[[:space:]]*(#\[test\][[:space:]]*$|def test_)", sha],
            capture_output=True, text=True, check=False)
        if proc.returncode == 1:
            return 0
        if proc.returncode != 0:
            return None
        return sum(int(line.rsplit(":", 1)[1])
                   for line in proc.stdout.splitlines() if ":" in line)

    since_sha = edge_sha(lambda at: at <= since)
    until_sha = edge_sha(lambda at: at < until)
    tests = {}
    since_count = count_tests(since_sha) if since_sha else None
    until_count = count_tests(until_sha) if until_sha else None
    tests["since"] = "unrecorded" if since_count is None else since_count
    tests["until"] = "unrecorded" if until_count is None else until_count
    if since_count is None or until_count is None:
        tests["delta"] = "unrecorded"
    else:
        tests["delta"] = until_count - since_count
    merges.reverse()  # oldest first, the order they landed
    return {"commits": commits, "prs": prs, "merges": merges,
            "tests": tests}


# ---------------------------------------------------------------- output

def cost_record(harness, sessions):
    if not sessions:
        return {"sessions": 0, "tokens": "unrecorded"}
    record = {"sessions": len(sessions)}
    if harness == "grok":
        counted = {sid: s for sid, s in sessions.items() if s["runs"]}
        record["runs"] = sum(s["runs"] for s in counted.values())
        # counted=N/M always accompanies a non-empty session list: its
        # absence is how a store with holes passes as complete
        record["counted"] = f"{len(counted)}/{len(sessions)}"
        if counted:
            for key in ("in", "cached", "out", "total"):
                record[key] = sum(s[key] for s in counted.values())
            if all("ticks" in s for s in counted.values()):
                record["cost_usd_ticks"] = sum(
                    s["ticks"] for s in counted.values())
            else:
                record["cost_usd_ticks"] = "unrecorded"
            record["incomplete"] = any(
                s["incomplete"] for s in counted.values())
        else:
            record["tokens"] = "unrecorded"
    else:
        record["messages"] = sum(s["messages"] for s in sessions.values())
        for key in ("in", "cached", "out", "total"):
            record[key] = sum(s[key] for s in sessions.values())
    return record


def plain_cost_line(harness, record):
    parts = [f"{harness:7s}"]
    for key in ("sessions", "messages", "runs", "in", "cached", "out",
                "total", "counted", "cost_usd_ticks", "tokens"):
        if key in record:
            parts.append(f"{key}={record[key]}")
    if record.get("incomplete"):
        parts.append("(incomplete)")
    return " ".join(parts)


def stamp_block(repo, window, now, harness_sessions=None):
    since, until = window
    head = (run_git(repo, "rev-parse", "--short", "HEAD") or "").strip()
    branch = (run_git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    stamp = {"head": head or "unrecorded", "branch": branch or "unrecorded",
             "since": since, "until": until, "now": now}
    if harness_sessions is not None:
        stamp["sessions"] = harness_sessions
    return stamp


def plain_stamp(stamp):
    line = (f"stamp head={stamp['head']} branch={stamp['branch']} "
            f"window=[{stamp['since']}, {stamp['until']}) "
            f"now={stamp['now']}")
    return line


def gather(args, window, include_unreadable=False):
    repo = str(args.repo)
    return {
        "claude": claude_sessions(
            args.claude_dir, repo, window, include_unreadable),
        "codex": codex_sessions(args.codex_dir, repo, window),
        "grok": grok_sessions(args.grok_dir, repo, window),
    }


def _same_repo(record, repo):
    """A dispatch is another repository's only when both signs say so.

    A snapshot rooted in this checkout is this repository's, and a root
    that cannot be looked at is counted, not dropped. A launcher snapshot
    is a bare fetch of one commit with no remote, so its top level is
    never this checkout; the repository identity it carries is
    ``snapshot.ref_sha``. A snapshot elsewhere whose base commit this
    checkout does not hold was dispatched for another repository.
    """
    snapshot = record.get("snapshot") or {}
    root = snapshot.get("root")
    found = run_git(root, "rev-parse", "--show-toplevel") if root else None
    if found is None:
        return True
    if Path(found.strip()).resolve() == Path(repo).resolve():
        return True
    sha = snapshot.get("ref_sha")
    if not sha:
        return True
    return run_git(repo, "cat-file", "-e", f"{sha}^{{commit}}") is not None


def dispatch_rows(root, repo, window):
    """Read launcher records, preserving unreadable jobs as denominator gaps."""
    since, until = window
    tracked = Path(repo) / ".dev" / "records" / "dispatches"
    rows = []
    for job_dir in sorted(Path(root).iterdir()) if Path(root).is_dir() else []:
        if not job_dir.is_dir():
            continue
        path = job_dir / "record.json"
        if not path.is_file():
            path = tracked / f"{job_dir.name}.json"
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            try:
                launched = datetime.strptime(
                    job_dir.name[:16], "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                launched = None
            if launched is not None and not since <= launched < until:
                continue
            rows.append({"id": job_dir.name, "harness": "grok",
                         "tokens": "unrecorded",
                         "reason": "record unavailable"})
            continue
        if not _same_repo(rec, repo):
            continue
        base = {
            "id": rec.get("id", job_dir.name), "job": rec.get("job"),
            "stage": rec.get("stage"),
            "harness": (rec.get("harness") or {}).get("name"),
            "unit": rec.get("unit"), "status": rec.get("status"),
            "lineage": {"branch": (rec.get("lineage") or {}).get("branch")},
            "model": {"ran": (rec.get("model") or {}).get("ran")},
        }
        try:
            launched = parse_stamp((rec.get("at") or {}).get("launched"))
        except ValueError:
            base.update(tokens="unrecorded", reason="launched is not ISO 8601")
            rows.append(base)
            continue
        if not since <= launched < until:
            continue
        spend = (rec.get("session") or {}).get("spend")
        if not isinstance(spend, dict) or "total" not in spend:
            reason = (spend or {}).get("unresolved", "spend unavailable") \
                if isinstance(spend, dict) else "spend unavailable"
            base.update(tokens="unrecorded", reason=reason)
        else:
            for key in ("input", "cached", "output", "total",
                        "cost_usd", "cost_usd_ticks", "messages"):
                if key in spend:
                    base[key] = spend[key]
            if "cost_usd" not in spend and "cost_usd_ticks" not in spend:
                base["cost"] = "unrecorded"
        rows.append(base)
    return rows


def dispatch_block(rows):
    block = {"rows": rows}
    for harness in ("claude", "codex", "grok"):
        own = [row for row in rows if row.get("harness") == harness]
        counted = [row for row in own if "total" in row]
        coverage = f"counted={len(counted)}/{len(own)}"
        item = {"dispatches": len(own), "counted": f"{len(counted)}/{len(own)}",
                "coverage": coverage}
        if counted:
            for key in ("input", "cached", "output", "total"):
                item[key] = sum(row.get(key, 0) for row in counted)
            cost_key = "cost_usd_ticks" if harness == "grok" else "cost_usd"
            item[cost_key] = (sum(row[cost_key] for row in counted)
                              if all(cost_key in row for row in counted)
                              else "unrecorded")
        else:
            item["tokens"] = "unrecorded"
        block[harness] = item
    return block


def plain_dispatch_line(harness, item):
    parts = [harness, f"dispatches={item['dispatches']}", item["coverage"]]
    for key in ("input", "cached", "output", "total", "cost_usd",
                "cost_usd_ticks", "tokens"):
        if key in item:
            parts.append(f"{key}={item[key]}")
    return " ".join(parts)


def cmd_report(args, window, window_iso):
    per_harness = gather(args, window)
    cost = {harness: cost_record(harness, sessions)
            for harness, sessions in per_harness.items()}
    results = repo_results(str(args.repo), window)
    stamp = stamp_block(str(args.repo), window_iso, args.now)
    dispatches = dispatch_block(dispatch_rows(args.dispatch_dir,
                                               str(args.repo), window))
    data = {"stamp": stamp, "cost": cost, "results": results}
    if dispatches["rows"]:
        data["dispatches"] = dispatches
    if args.format == "json":
        print(json.dumps(data, sort_keys=True))
        return
    lines = [plain_stamp(stamp), ""]
    if dispatches["rows"]:
        for harness in ("claude", "codex", "grok"):
            lines.append(plain_dispatch_line(harness, dispatches[harness]))
        for row in dispatches["rows"]:
            parts = [f"record={row['id']}", f"job={row.get('job', 'unrecorded')}"]
            for key in ("harness", "stage", "unit", "status", "input", "cached", "output", "total",
                        "cost_usd", "cost_usd_ticks", "cost", "tokens", "reason"):
                if key in row:
                    parts.append(f"{key}={row[key]}")
            if "lineage" in row:
                parts.extend((f"branch={row['lineage']['branch']}",
                              f"ran={row['model']['ran']}"))
            lines.append(" ".join(parts))
        lines.append("")
    for harness in ("claude", "codex", "grok"):
        lines.append(plain_cost_line(harness, cost[harness]))
    lines.append("")
    prs = ",".join(str(n) for n in results["prs"]) or "0"
    lines.append(f"results commits={results['commits']} prs={prs}")
    for merge in results["merges"]:
        label = f"#{merge['pr']} " if "pr" in merge else ""
        lines.append(f"merge {label}{merge['sha']} {merge['subject']}")
    tests = results["tests"]
    lines.append(f"tests delta={tests['delta']} since={tests['since']}"
                 f" until={tests['until']}")
    print("\n".join(lines))


def cmd_waste(args, window, window_iso):
    per_harness = gather(args, window, include_unreadable=True)
    ranked = []
    gaps = []
    for harness, sessions in per_harness.items():
        for sid, s in sessions.items():
            if s.get("kind") == "gap":
                gaps.append((s, {
                    "id": sid, "harness": harness, "session": sid, **s,
                }))
                continue
            if "total" not in s:
                continue
            entry = {"kind": "session", "id": sid,
                     "harness": harness, "session": sid,
                     "total": s["total"], "out": s["out"],
                     "cached": s["cached"]}
            if harness == "grok":
                entry["runs"] = s["runs"]
            else:
                entry["messages"] = s["messages"]
                if harness == "claude":
                    for key in ("turns", "avg_context", "refusals"):
                        entry[key] = s[key]
            ranked.append((s, entry))
    dispatches = dispatch_rows(args.dispatch_dir, str(args.repo), window)
    for row in dispatches:
        if "total" not in row:
            continue
        entry = dict(row)
        entry.update(kind="dispatch", heavy={
            "declared_absent": "record carries no per-turn usage"})
        ranked.append(({"out": row.get("output", 0),
                        "cached_read": row.get("cached", 0)}, entry))
    ranked.sort(key=lambda item: (
        "total" not in item[1],
        -item[1].get("total", 0),
        item[1].get("session", item[1].get("id")),
    ))
    ranked = ranked[:args.top]
    gaps.sort(key=lambda item: (
        item[1]["harness"], item[1]["session"], item[1]["gap_kind"]))
    listed = ranked + gaps

    signals = []
    for s, entry in ranked:
        if (s.get("out", 0) > 0
                and s.get("cached_read", 0) > CHURN_FACTOR * s["out"]):
            signal = {"kind": "cache-churn",
                            "harness": entry["harness"],
                            "cached": s["cached_read"],
                            "out": s["out"]}
            signal["record" if entry.get("kind") == "dispatch" else "session"] = (
                entry.get("id", entry.get("session")))
            signals.append(signal)
    for s, entry in ranked:
        if entry.get("kind") == "dispatch":
            continue
        heavy = s.get("heavy")
        if heavy:
            signal = {"kind": "heavy-turn", "harness": entry["harness"],
                      "session": entry["session"]}
            signal.update(heavy)
            signals.append(signal)

    counts = {harness: sum(s.get("kind") != "gap"
                           for s in sessions.values())
              for harness, sessions in per_harness.items()}
    # The census counts the window; the sessions array is the top-N
    # ranking, so the two differ whenever --top truncates or a session
    # carried no usage. State what was listed rather than leaving the
    # reader to infer the difference (LD-45).
    shown = {harness: sum(entry.get("kind") == "session"
                          and entry.get("harness") == harness
                          for _, entry in listed)
             for harness in counts}
    stamp = stamp_block(str(args.repo), window_iso, args.now,
                        harness_sessions=counts)
    data = {"stamp": stamp, "listed": shown,
            "sessions": [entry for _, entry in listed],
            "signals": signals}
    if dispatches:
        data["dispatches"] = {
            harness: sum(row.get("harness") == harness for row in dispatches)
            for harness in ("claude", "codex", "grok")
        }
    if args.format == "json":
        print(json.dumps(data, sort_keys=True))
        return
    lines = [plain_stamp(stamp), ""]
    for harness in ("claude", "codex", "grok"):
        n_dispatches = sum(row.get("harness") == harness
                           for row in dispatches)
        suffix = f" dispatches={n_dispatches}" if n_dispatches else ""
        lines.append(f"{harness:7s} sessions={counts[harness]}"
                     f" listed={shown[harness]}{suffix}")
    lines.append("")
    for _, entry in listed:
        if entry.get("kind") == "dispatch":
            parts = [f"{entry['harness']:7s} record={entry['id']}",
                     f"job={entry.get('job', 'unrecorded')}"]
        elif entry.get("kind") == "gap":
            parts = [f"{entry['harness']:7s} kind=gap",
                     f"session={entry['session']}",
                     f"gap_kind={entry['gap_kind']}",
                     f"repo={entry['repo']}",
                     f"window={entry['window']}"]
        else:
            parts = [f"{entry['harness']:7s} session={entry['session']}"]
        for key in ("stage", "unit", "status", "input", "total", "out", "output",
                    "cached", "messages", "runs", "turns", "avg_context",
                    "refusals", "cost_usd",
                    "cost_usd_ticks", "cost", "tokens"):
            if key in entry:
                parts.append(f"{key}={entry[key]}")
        if entry.get("kind") == "dispatch":
            parts.extend((f"branch={entry['lineage']['branch']}",
                          f"ran={entry['model']['ran']}",
                          entry["heavy"]["declared_absent"]))
        lines.append(" ".join(parts))
    for signal in signals:
        identity = (f"record={signal['record']}" if "record" in signal
                    else f"session={signal['session']}")
        parts = [(f"signal {signal['kind']} harness={signal['harness']}"
                  f" {identity}")]
        for key, value in signal.items():
            if key not in ("kind", "harness", "session", "record"):
                parts.append(f"{key}={value}")
        lines.append(" ".join(parts))
    print("\n".join(lines))


# --------------------------------------------------------------------
# compare: two windows, the levers as rows (ops/process/worth.md
# "worth compare" and Behavior contract item 9). The levers are data:
# adding one is a row here, never a branch below.
# --------------------------------------------------------------------

LEVERS = (
    {"lever": "context", "threshold": ("under", 300_000),
     "figure": "mean context tokens per Claude session"},
    {"lever": "refusals", "threshold": ("falling", None),
     "figure": "refusals per Claude session"},
    {"lever": "landed", "threshold": ("falling", None),
     "figure": "dispatches per landed PR"},
    {"lever": "dispatch-cached", "threshold": ("under", 3_000_000),
     "figure": "mean cached tokens per author-tests dispatch"},
)


def _mean(values):
    return sum(values) / len(values) if values else None


def _claude_figures(sessions, key):
    """One number per Claude session, skipping the rows that could not
    be read: an unrecorded session is not a zero."""
    return [row[key] for row in (sessions.get("claude") or {}).values()
            if isinstance(row.get(key), (int, float))
            and not isinstance(row.get(key), bool)]


def _lever_readings(args, window):
    """Every lever's figure and its population, for one window."""
    sessions = gather(args, window)
    rows = dispatch_rows(args.dispatch_dir, args.repo, window)
    results = repo_results(args.repo, window)
    contexts = _claude_figures(sessions, "avg_context")
    refusals = _claude_figures(sessions, "refusals")
    landed = len(results.get("prs") or [])
    tests = [row for row in rows
             if row.get("job") == "author-tests"
             and isinstance(row.get("cached"), (int, float))]
    return {
        "context": {"figure": _mean(contexts), "n": len(contexts)},
        "refusals": {"figure": _mean(refusals), "n": len(refusals)},
        # A rate has two populations, and either can be absent. No merge
        # in the window is nothing to divide by; no dispatch is nothing
        # to divide, and a rate of zero from an empty numerator is not an
        # improvement (LD-54).
        "landed": {"figure": (len(rows) / landed) if landed else None,
                   "n": landed, "dispatches": len(rows)},
        "dispatch-cached": {"figure": _mean([row["cached"] for row in tests]),
                            "n": len(tests)},
    }


def _usable(reading):
    """A figure stands on its populations: every count the row declares
    must be above zero, or the row has no result to report."""
    if reading.get("figure") is None:
        return False
    counts = [reading.get("n")] + [
        value for key, value in reading.items()
        if key not in ("figure", "n") and isinstance(value, int)
    ]
    return all(counts)


def _verdict(kind, limit, base, win):
    """Mechanical, never a judgment. An arm that could not look has no
    result; a fall measured against nothing is not a fall."""
    if not _usable(win):
        return "UNREACHABLE"
    if kind == "falling":
        if not _usable(base):
            return "UNREACHABLE"
        return "met" if win["figure"] < base["figure"] else "short"
    return "met" if win["figure"] < limit else "short"


def _side(reading):
    """Every count the reading declares, and the figure only where the
    counts hold it up."""
    out = dict(reading)
    out["figure"] = reading["figure"] if _usable(reading) else "unrecorded"
    return out


def cmd_compare(args, baseline_iso, window_iso):
    baseline = (parse_iso(baseline_iso[0], "baseline since"),
                parse_iso(baseline_iso[1], "baseline until"))
    window = (parse_iso(window_iso[0], "window since"),
              parse_iso(window_iso[1], "window until"))
    base_readings = _lever_readings(args, baseline)
    win_readings = _lever_readings(args, window)
    levers = []
    for spec in LEVERS:
        name = spec["lever"]
        kind, limit = spec["threshold"]
        base = base_readings[name]
        win = win_readings[name]
        levers.append({
            "lever": name,
            "figure": spec["figure"],
            "threshold": (f"under {limit}" if kind == "under"
                          else "falling against the baseline"),
            "baseline": _side(base),
            "window": _side(win),
            "verdict": _verdict(kind, limit, base, win),
        })
    stamp = stamp_block(args.repo, window_iso, args.now)
    payload = {
        "stamp": stamp,
        "baseline": {"since": baseline_iso[0], "until": baseline_iso[1]},
        "window": {"since": window_iso[0], "until": window_iso[1]},
        "levers": levers,
    }
    if args.format == "json":
        print(json.dumps(payload, indent=1, sort_keys=True))
        return
    print(plain_stamp(stamp))
    print(f"baseline=[{baseline_iso[0]}, {baseline_iso[1]}) "
          f"window=[{window_iso[0]}, {window_iso[1]})")
    print()
    for row in levers:
        print(f"{row['lever']:16s} {row['verdict']:12s} "
              f"baseline={_plain_side(row['baseline'])} "
              f"window={_plain_side(row['window'])} "
              f"({row['threshold']})")


def _plain_side(side):
    """figure, then every count the row declares, so a rate never shows
    a figure without both of its populations."""
    counts = " ".join(f"{key}={value}" for key, value in side.items()
                      if key != "figure")
    return f"{side['figure']} {counts}"


def _range(raw, label):
    """SINCE..UNTIL, both halves present, upper exclusive and after."""
    parts = str(raw).split("..")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise SystemExit2(
            f"{label}: expected SINCE..UNTIL, two ISO 8601 stamps"
            f" separated by '..'; found {raw!r}; needed both halves")
    since = parse_iso(parts[0], f"{label} since")
    until = parse_iso(parts[1], f"{label} until")
    if until <= since:
        raise SystemExit2(
            f"{label}: expected until after since; found"
            f" {parts[1]} at or before {parts[0]}; needed a window with width")
    return (parts[0], parts[1])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="worth.py", description=__doc__)
    parser.add_argument("verb", choices=("report", "waste", "compare"))
    parser.add_argument("--baseline")
    parser.add_argument("--window")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--now")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--format", choices=("plain", "json"),
                        default="plain")
    parser.add_argument("--claude-dir")
    parser.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    parser.add_argument("--grok-dir", default=str(Path.home() / ".grok"))
    default_state = Path(os.environ.get(
        "XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    parser.add_argument("--dispatch-dir", default=os.environ.get(
        "DISPATCH_JOBS",
        str(default_state / laneconf.get("LANE_NAME") / "dispatch"),
    ))
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if args.verb == "compare" and any(
            item == "--top" or item.startswith("--top=") for item in raw):
        print("compare: expected no --top; found --top; needed drop it —"
              " compare ranks nothing and declares every lever",
              file=sys.stderr)
        return 2
    # One resolution for every reader: a transcript carries an absolute
    # cwd, and a relative --repo (".", the usual shape from the checkout)
    # matched nothing and read sessions=0 (T-56). Resolved here so the
    # session, dispatch and stamp halves see one path (review of 75f6e9cf).
    args.repo = os.path.abspath(str(args.repo))

    try:
        if args.claude_dir is None:
            try:
                configured = Path(laneconf.get("CLAUDE_DIR"))
            except laneconf.ConfigError as exc:
                kind = "conf-key" if exc.kind == "key" else exc.kind
                raise SystemExit2(exc.line(kind)) from None
            if not configured.is_absolute():
                configured = Path.home() / configured
            args.claude_dir = str(configured)
        if args.now is None:
            now = datetime.now(timezone.utc)
            args.now = now.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z")
        else:
            now = parse_iso(args.now, "now")
        until = parse_iso(args.until, "until") if args.until else now
        since = (parse_iso(args.since, "since") if args.since
                 else now - timedelta(hours=24))
        if until <= since:
            raise SystemExit2(
                f"until ({args.until or args.now}) must be after"
                f" since ({args.since})")
    except SystemExit2 as exc:
        print(str(exc), file=sys.stderr)
        return 2

    since_iso = args.since or since.isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    until_iso = args.until or args.now
    window = (since, until)
    window_iso = (since_iso, until_iso)
    if args.verb == "compare":
        try:
            if not args.baseline or not args.window:
                raise SystemExit2(
                    "compare: expected --baseline SINCE..UNTIL and --window"
                    " SINCE..UNTIL; found"
                    f" baseline={args.baseline!r} window={args.window!r};"
                    " needed both, neither implied")
            baseline_range = _range(args.baseline, "baseline")
            window_range = _range(args.window, "window")
        except SystemExit2 as exc:
            print(str(exc), file=sys.stderr)
            return 2
        cmd_compare(args, baseline_range, window_range)
    elif args.verb == "report":
        cmd_report(args, window, window_iso)
    else:
        cmd_waste(args, window, window_iso)
    return 0


if __name__ == "__main__":
    sys.exit(main())
