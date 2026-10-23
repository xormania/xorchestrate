#!/usr/bin/env python3
"""Token and session usage across the three harnesses, from their own stores.

    usage.py sessions [--json] [--repo PATH] [--claude-dir D] [--codex-dir D] [--grok-dir D]
    usage.py report   [same flags]

Nothing here instruments anything: every harness already writes a session
store, and this reads them. Measured shapes (2026-08-21):

- Claude: ``<dir>/<project-slug>/<session>.jsonl`` — per-message
  ``message.usage`` (input, cache_creation, cache_read, output). Summed.
- Codex: ``<dir>/sessions/Y/M/D/rollout-*.jsonl`` — a ``session_meta`` head
  carrying ``cwd``, then cumulative ``token_count`` events. The LAST one is
  the session's usage; summing them would multiply it.
- Grok: ``<dir>/sessions/<urlencoded-cwd>/<id>/`` — summary.json holds
  messages/model/timestamps; spend lives in updates.jsonl
  ``turn_completed`` events (cumulative within a run; runs split when
  totalTokens shrinks; last report per currency wins) including raw
  costUsdTicks. Pre-upgrade sessions have no usage events and stay an
  explicit gap; nothing is ever estimated.

The report is aggregates only. The stores hold prompts and transcripts;
none of that content leaves them through this tool.

Two principles taken from loopstrap's telemetry design (TELEMETRY.md,
xormania/loopstrap): the capture rule — an unknown value stays an explicit
gap, never a zero — and the accounting rule — a session's spend is input +
cache_creation + cache_read + output, cached reads counted as the cheap
class they are, not ignored.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

GROK_GAP = "grok usage not yet parsed: updates.jsonl turn_completed"


def agy_read(path: Path, offset=0):
    """Read captured native stdout, without inventing transcript fields.

    20260908T071244Z-code-grok-220fb8: result.usage is cumulative across
    explicit-ID resumes; thinking/cache are separate, overlapping figures.
    Only complete NDJSON lines count while a process is still writing.
    A decreasing counter is unresolved, never a guessed new billing run.
    """
    raw = path.read_bytes()[offset:]
    rows, issues, identity, cwd, model = [], [], None, None, None
    tokens = None
    prior = None
    messages = None
    terminal = False
    steps = {}
    live_incomplete = False
    live_output = None
    for line in raw.splitlines(keepends=True):
        if not line.strip():
            continue
        if not line.endswith(b'\n'):
            issues.append("truncated native frame")
            break
        try:
            frame = json.loads(line)
            if not isinstance(frame, dict):
                raise ValueError("frame is not an object")
            event = frame.get("event")
            body = frame.get(event) if event in {"init", "result"} else None
            if event not in {"init", "result", "step_update"}:
                raise ValueError("unknown native frame")
            if event in {"init", "result"}:
                if not isinstance(body, dict):
                    raise ValueError("native carrier is not an object")
                sid = frame.get("conversation_id") if event == "init" else body.get("conversation_id")
                if not isinstance(sid, str) or not sid or Path(sid).name != sid or sid in {".", ".."}:
                    raise ValueError("invalid conversation identity")
                if identity is not None and identity != sid:
                    raise ValueError("contradictory conversation identity")
                identity = sid
            if event == "init":
                terminal = False
                tokens = None
                steps = {}
                live_incomplete = False
                live_output = None
                found_cwd = body.get("cwd")
                if not isinstance(found_cwd, str) or not Path(found_cwd).is_absolute():
                    raise ValueError("missing absolute init.cwd")
                # Captured attempts can relocate together with their job.
                # Launch checks each attempt against its rebound snapshot.
                cwd = found_cwd
                observed = body.get("model")
                if observed is not None:
                    if not isinstance(observed, str) or not observed.strip():
                        raise ValueError("malformed init.model")
                    if model and observed != model:
                        raise ValueError("contradictory init.model")
                    model = observed
            elif event == "step_update":
                step = frame.get("step_update")
                if not isinstance(step, dict):
                    raise ValueError("native step_update is not an object")
                if step.get("state") == "DONE" and step.get("step_type") == "agent_response":
                    # Canonical stdout: 1314 + 5136 == result output 6450.
                    # Only this output currency is established by the steps.
                    if step.get("conversation_id") != identity or identity is None:
                        raise ValueError("contradictory step conversation identity")
                    index = step.get("step_index")
                    native = step.get("usage")
                    output = native.get("output_tokens") if isinstance(native, dict) else None
                    if (type(index) is not int or index < 0
                            or type(output) is not int or output < 0):
                        # An unknown step cannot license a partial live sum,
                        # nor contradict independently measured terminal usage.
                        live_incomplete = True
                        if not terminal:
                            live_output = None
                    else:
                        if index in steps and steps[index] != output:
                            raise ValueError("contradictory step output usage")
                        steps[index] = output
                        if not terminal and not live_incomplete:
                            live_output = sum(steps.values())
            elif event == "result":
                if terminal:
                    raise ValueError("duplicate terminal result")
                terminal = True
                live_output = None
                native = body.get("usage")
                if not isinstance(native, dict):
                    tokens = None
                    raise ValueError("missing terminal usage")
                tokens = {}
                for key, source in (("input", "input_tokens"), ("cached", "cache_read_tokens"),
                                    ("output", "output_tokens"), ("total", "total_tokens")):
                    value = native.get(source)
                    tokens[key] = value if type(value) is int and value >= 0 else None
                if any(value is None for value in tokens.values()):
                    issues.append("incomplete terminal usage")
                if prior and any(tokens[k] is not None and prior[k] is not None
                                 and tokens[k] < prior[k] for k in tokens):
                    issues.append("cumulative usage decreased; reset semantics unresolved")
                prior = tokens.copy()
                # Native result counters include resumed history: never add
                # the step sum or the preceding result to this counter.
                live_output = tokens["output"]
                messages = body.get("num_turns")
                if type(messages) is not int or messages < 0:
                    messages = None
            rows.append(frame)
        except (ValueError, TypeError, UnicodeError) as exc:
            issues.append(str(exc))
    if not terminal:
        issues.append("terminal usage unavailable")
    if not identity or not cwd:
        issues.append("session/cwd binding unavailable")
    if issues and tokens:
        tokens["total"] = None
    return {"harness": "agy", "session": identity, "model": model, "cwd": cwd,
            "started": None, "ended": None, "messages": messages, "tokens": tokens,
            "incomplete": bool(issues), "issues": issues, "frames": rows,
            "live_output": live_output,
            "source": str(path),
            "note": "; ".join(issues + ["native timestamps and provider unavailable"]
                              + ([] if model else ["native model unavailable"]))}


def under(cwd: str | None, repo: str | None) -> bool:
    """True when `cwd` is `repo` or a directory inside it.

    Every reader here compared the two by string equality, so a session
    that worked from a subdirectory counted for no repository at all.
    The test is on path components, never on characters: `<repo>-other`
    is a different checkout that merely shares a spelling, and a prefix
    compare would absorb its spend into this one.

    No repo asked for means every session answers. A repo asked for and
    no cwd to compare is not an answer: it is a session that cannot be
    placed, and it is not counted for this repository.
    """
    if not repo:
        return True
    if not cwd:
        return False
    here, root = Path(cwd), Path(repo)
    return here == root or root in here.parents


UNREACHABLE = "UNREACHABLE"


class Placement:
    """Which sessions are this repository's work, and which could not be
    placed at all.

    The path test below answers most of the question and costs nothing.
    It cannot answer one case: a worktree lives outside the checkout it
    was cut from, so its cwd is under no path of this repository, and
    every reader dropped it. What a worktree does share is the
    repository's `.git`, which `git rev-parse --git-common-dir` names
    for the checkout, for its subdirectories and for every worktree
    alike. That is the question asked here, and only of a cwd the path
    test has already refused, so the common case spawns nothing.

    Three answers, not two. A cwd that is gone was not looked at, and
    `unplaced` collects those rather than letting them read as elsewhere
    — the distinction docs/wire.md calls looked and found nothing
    against could not look. Each distinct cwd is asked once.
    """

    def __init__(self, repo: str | None):
        self.repo = repo
        self.unplaced: set[str] = set()
        self._seen: dict[str, str | None] = {}
        self.repo_common = self.common_dir(repo) if repo else None
        if self.repo_common is UNREACHABLE:
            # The repository asked about is not on disk. Nothing can be
            # resolved against it, so the path test is the whole answer
            # and no cwd is reported unplaced for a question never asked.
            self.repo_common = None

    @staticmethod
    def common_dir(cwd: str) -> str | None:
        """The shared `.git` of the checkout at `cwd`, `None` when the
        path was read and is no checkout, `UNREACHABLE` when it could
        not be read at all."""
        if not os.path.isdir(cwd):
            return UNREACHABLE
        try:
            proc = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
                capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return UNREACHABLE
        found = proc.stdout.strip()
        if proc.returncode != 0 or not found:
            return None
        # git answers relative to the directory it was run in.
        return os.path.realpath(os.path.join(cwd, found))

    def holds(self, cwd: str | None) -> bool:
        if not self.repo:
            return True
        if not cwd:
            return False
        if under(cwd, self.repo):
            return True
        if self.repo_common is None:
            return False
        if cwd not in self._seen:
            self._seen[cwd] = self.common_dir(cwd)
        found = self._seen[cwd]
        if found is UNREACHABLE:
            self.unplaced.add(cwd)
            return False
        return found is not None and found == self.repo_common


def placed(place, reader: str) -> "Placement":
    """Refuse a reader's second argument that is not a Placement.

    cd1083a8 gave the four readers a Placement where they took a repo path
    and left ten callers handing over the old shape. Each failed deep in
    the reader on the first attribute it touched, and one of them failed
    inside a worker thread in test_launch_watch, where the traceback went
    to a log nobody was reading and the test waited on a result that was
    never coming. The wire's words, raised where the call is made: a
    caller that cannot be read is told what it needed.
    """
    if not isinstance(place, Placement):
        raise TypeError(
            f"placement: expected a Placement for {reader}; "
            f"found {type(place).__name__}; "
            "needed usage.Placement(<repo path or None>)")
    return place

def agy_sessions(root: Path, place: "Placement"):
    """Captured job roots (raw.out), or their isolated home/agy directory.

    Native brain transcripts do not carry usage/model/provider. The existing
    launch capture supplies those observations; no sidecar transcript format.
    Callers select a root explicitly; there is no personal-store default.
    """
    place = placed(place, "agy_sessions")
    if root.name == "agy" and root.parent.name == "home":
        root = root.parent.parent
    paths = [root / "raw.out"] if (root / "raw.out").is_file() else sorted(root.rglob("raw.out"))
    readings = [agy_read(path) for path in paths]
    for row in readings:
        if not place.holds(row["cwd"]):
            continue
        key = row["session"]
        if sum(other["session"] == key for other in readings) > 1:
            row.update(tokens=None, incomplete=True,
                       note="duplicate conversation across captures; usage unresolved")
        yield {k: v for k, v in row.items()
               if k not in {"frames", "issues", "cwd", "live_output"}}


def _read_jsonl(path: Path):
    for raw in path.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except ValueError:
            continue


def claude_sessions(root: Path, place: "Placement"):
    place = placed(place, "claude_sessions")
    if not root.is_dir():
        return
    repo = place.repo
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        # Claude encodes the project path with '-' separators. The name
        # is an index, not a filter: it can settle that a directory is
        # this repository's, and it can settle nothing else. The
        # encoding flattens '/' and '-' alike, so a subdirectory and a
        # sibling checkout whose name extends this one spell a single
        # directory name; and a worktree of this repository is a
        # different path altogether, so its own name resembles this one
        # not at all. Every directory the name does not settle is left
        # to the cwd in the entries, which is the only exact truth here.
        named = bool(repo) and project.name == "-" + "-".join(repo.strip("/").split("/"))
        for f in sorted(project.glob("*.jsonl")):
            per_msg = {}
            model = None
            first = last = None
            cwds = set()
            for line_no, entry in enumerate(_read_jsonl(f)):
                if entry.get("cwd"):
                    cwds.add(entry["cwd"])
                stamp = entry.get("timestamp")
                if stamp:
                    first = first or stamp
                    last = stamp
                message = entry.get("message") or {}
                usage = message.get("usage")
                if not usage:
                    continue
                model = message.get("model") or model
                # Keyed by message id, LAST wins: live streams are the
                # snapshot-rewrite shape (measured 2026-08-21: 2086
                # usage lines over 1052 unique ids in one session), and
                # summing every line nearly doubles the spend.
                key = message.get("id") or f"line-{line_no}"
                per_msg[key] = {
                    "input": usage.get("input_tokens", 0),
                    "cached": (usage.get("cache_creation_input_tokens", 0)
                               + usage.get("cache_read_input_tokens", 0)),
                    "output": usage.get("output_tokens", 0),
                }
            if not per_msg:
                continue
            inside = bool(repo) and any(place.holds(cwd) for cwd in cwds)
            if repo and cwds and not inside:
                # Two paths can flatten to one directory slug; the cwd
                # stored in the entries is the truth.
                continue
            if repo and not named and not inside:
                # The directory name did not settle it and the entries
                # carry no cwd this repository holds, so nothing here
                # places the session. Could not look is not an answer,
                # and an unplaced session is not this repository's spend.
                continue
            messages = len(per_msg)
            tokens = {key: sum(m[key] for m in per_msg.values())
                      for key in ("input", "cached", "output")}
            tokens["total"] = sum(tokens.values())
            yield {"harness": "claude", "session": f.stem, "model": model,
                   "started": first, "ended": last, "messages": messages,
                   "tokens": tokens}


def codex_sessions(root: Path, place: "Placement"):
    place = placed(place, "codex_sessions")
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    for f in sorted(sessions.glob("*/*/*/rollout-*.jsonl")):
        meta, last_count, events = {}, None, 0
        first = last = None
        for entry in _read_jsonl(f):
            stamp = entry.get("timestamp")
            if stamp:
                first = first or stamp
                last = stamp
            payload = entry.get("payload") or {}
            if "cwd" in payload:
                # Measured split: session_meta carries id/cwd, a later
                # turn_context carries model/effort. Merge, never replace —
                # replacing loses the id to whichever payload came last.
                meta = {**meta, **payload}
            if payload.get("type") == "token_count":
                info = payload.get("info") or {}
                last_count = info.get("total_token_usage") or last_count
            events += 1
        if not place.holds(meta.get("cwd")):
            continue
        tokens = None
        if last_count:
            tokens = {"input": last_count.get("input_tokens", 0),
                      "cached": last_count.get("cached_input_tokens", 0),
                      "output": last_count.get("output_tokens", 0),
                      "total": last_count.get("total_tokens", 0)}
        yield {"harness": "codex", "session": meta.get("id", f.stem),
               "model": meta.get("model"), "started": first, "ended": last,
               "messages": events, "tokens": tokens}


GROK_USAGE_CURRENCIES = (
    "inputTokens", "outputTokens", "totalTokens", "cachedReadTokens",
    "cacheCreationTokens", "reasoningTokens", "costUsdTicks",
)


def _grok_usage_totals(events):
    """Session spend from turn_completed events (measured 2026-08-21):
    usage is cumulative WITHIN a run and a run ends when the cumulative
    totalTokens SHRINKS (the only observable reset signal — turns can
    rise or repeat across a reset). The session figure is each
    currency's last-reported value per run, summed across runs.
    Last-wins-across-the-stream undercounts, summing every event
    overcounts, and max-merge overcounts when a reset arrives without
    a turns drop (measured live, skeptic finding). An event omitting a
    currency does not erase the run's earlier report, and a RUN that
    never reports costUsdTicks makes the session's cost a gap (None) —
    never zero, never a partial sum."""
    runs, current, prev_total = [], {}, None
    incomplete = False
    for usage in events:
        if "totalTokens" not in usage:
            # An event that does not report totals cannot signal a
            # reset; coercing absence to zero invents a run boundary.
            if usage.get("usageIsIncomplete"):
                incomplete = True
            for key in GROK_USAGE_CURRENCIES:
                if key in usage:
                    current[key] = usage[key]
            continue
        total = usage["totalTokens"]
        if prev_total is not None and total < prev_total:
            # A cumulative snapshot that SHRINKS is a new run — the only
            # observable reset signal. numTurns is NOT it: a live stream
            # (019fb283…) reset totals 7.5M→1.9M while turns ROSE 12→15,
            # and turns repeat freely within a run.
            runs.append(current)
            current = {}
        prev_total = total
        if usage.get("usageIsIncomplete"):
            incomplete = True
        for key in GROK_USAGE_CURRENCIES:
            if key in usage:
                # Cumulative within the run: the LAST event to report a
                # currency wins; an event omitting one does not erase
                # the run's earlier report.
                current[key] = usage[key]
    runs.append(current)
    totals = {key: sum(run.get(key, 0) for run in runs)
              for key in GROK_USAGE_CURRENCIES}
    cost = (None if any("costUsdTicks" not in run for run in runs)
            else totals["costUsdTicks"])
    tokens = {"input": totals["inputTokens"],
              "cached": (totals["cachedReadTokens"]
                         + totals["cacheCreationTokens"]),
              "output": totals["outputTokens"],
              "total": totals["totalTokens"]}
    return tokens, totals["reasoningTokens"], cost, incomplete


def _grok_usage_events(updates_path):
    if not updates_path.is_file():
        return []
    events = []
    for entry in _read_jsonl(updates_path):
        update = (entry.get("params") or {}).get("update") or {}
        # Only turn_completed carries spend; usage keys on other update
        # kinds are not accounting records (measured decoy: a tool
        # update carrying a usage dict).
        if update.get("sessionUpdate") == "turn_completed" and isinstance(
            update.get("usage"), dict
        ):
            events.append(update["usage"])
    return events


def grok_sessions(root: Path, place: "Placement"):
    place = placed(place, "grok_sessions")
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    for cwd_dir in sorted(p for p in sessions.iterdir() if p.is_dir()):
        # The directory name is the url-encoded cwd, so it is decoded
        # before it is compared: encoding the repo and matching strings
        # cannot see that a subdirectory is inside it.
        if not place.holds(unquote(cwd_dir.name)):
            continue
        for sdir in sorted(p for p in cwd_dir.iterdir() if p.is_dir()):
            summary = sdir / "summary.json"
            if not summary.exists():
                continue
            try:
                s = json.loads(summary.read_text())
            except ValueError:
                continue
            row = {"harness": "grok", "incomplete": False,
                   "session": (s.get("info") or {}).get("id", sdir.name),
                   "model": s.get("current_model_id"),
                   "started": s.get("created_at"),
                   "ended": s.get("updated_at"),
                   "messages": s.get("num_messages"),
                   "tokens": None, "reasoning": None,
                   "cost_usd_ticks": None, "note": GROK_GAP}
            events = _grok_usage_events(sdir / "updates.jsonl")
            if events:
                tokens, reasoning, cost, incomplete = _grok_usage_totals(events)
                row["tokens"] = tokens
                row["reasoning"] = reasoning
                row["cost_usd_ticks"] = cost
                row["incomplete"] = incomplete
                row["note"] = ("grok usage parsed from updates.jsonl"
                               " (usageIsIncomplete: figures incomplete)"
                               if incomplete
                               else "grok usage parsed from updates.jsonl")
            yield row


def collect(args):
    """The rows, and the placement that decided which rows they are.

    The placement is returned with them because what it could not look
    at is part of the reading: a caller that printed only the rows would
    be reporting a total it cannot vouch for.
    """
    place = Placement(args.repo)
    rows = []
    rows += list(claude_sessions(Path(args.claude_dir), place))
    rows += list(codex_sessions(Path(args.codex_dir), place))
    rows += list(grok_sessions(Path(args.grok_dir), place))
    if getattr(args, "agy_dir", None):
        rows += list(agy_sessions(Path(args.agy_dir), place))
    rows.sort(key=lambda r: (r.get("started") or "", r["harness"]))
    return rows, place


def unplaced_line(place) -> str:
    """One line naming the cwds that could not be read, or nothing."""
    if not place.unplaced:
        return ""
    return ("unplaced: " + str(len(place.unplaced))
            + " cwd(s) UNREACHABLE, not counted for this repo: "
            + ", ".join(sorted(place.unplaced)))


def cmd_sessions(args) -> int:
    rows, place = collect(args)
    if args.json:
        # The key is always present: an empty list is the answer that
        # every cwd was read, which is not the same as not saying.
        print(json.dumps({"sessions": rows,
                          "unplaced": sorted(place.unplaced)},
                         indent=1, sort_keys=True))
        return 0
    for r in rows:
        t = r.get("tokens")
        spent = (f"in={t['input']} cached={t['cached']} out={t['output']}"
                 f" total={t['total']}" if t else r.get("note", "-"))
        if t and r["harness"] == "grok":
            ticks = r.get("cost_usd_ticks")
            spent += (f" cost_usd_ticks={ticks}" if ticks is not None
                      else " cost_usd_ticks=unrecorded")
            if "incomplete" in (r.get("note") or "").lower():
                spent += " (incomplete)"
        print(f"{r['harness']:<7} {str(r['session'])[:12]:<13}"
              f" {str(r.get('model'))[:18]:<19} msgs={r.get('messages')}"
              f"  {spent}")
    return 0


def cmd_report(args) -> int:
    rows, place = collect(args)
    by = {}
    for r in rows:
        agg = by.setdefault(r["harness"], {
            "sessions": 0, "messages": 0,
            "tokens": {"input": 0, "cached": 0, "output": 0, "total": 0},
            "counted": 0})
        agg["sessions"] += 1
        agg["messages"] += r.get("messages") or 0
        if r.get("tokens"):
            agg["counted"] += 1
            for k in agg["tokens"]:
                value = r["tokens"].get(k, 0)
                if value is None or agg["tokens"][k] is None:
                    agg["tokens"][k] = None
                else:
                    agg["tokens"][k] += value
            if r["harness"] == "grok":
                if r.get("incomplete"):
                    agg["incomplete_sessions"] = agg.get(
                        "incomplete_sessions", 0) + 1
                # Cost aggregates gap-honestly: one counted session
                # with unknown cost makes the total unknown, never a
                # partial sum passed off as complete.
                if r.get("cost_usd_ticks") is None:
                    agg["cost_usd_ticks"] = None
                elif agg.get("cost_usd_ticks", 0) is not None:
                    agg["cost_usd_ticks"] = (agg.get("cost_usd_ticks") or 0
                                             ) + r["cost_usd_ticks"]
    if args.json:
        print(json.dumps({"by_harness": by,
                          "unplaced": sorted(place.unplaced)},
                         indent=1, sort_keys=True))
        return 0
    for harness in sorted(by):
        agg = by[harness]
        t = agg["tokens"]
        line = (f"{harness:<7} sessions={agg['sessions']}"
                f" messages={agg['messages']}")
        if agg["counted"]:
            line += (f"  in={t['input']} cached={t['cached']}"
                     f" out={t['output']} total={t['total']}")
            if agg["counted"] < agg["sessions"]:
                # The figures cover a subset; saying so is the line's
                # licence to print them at all.
                line += f" counted={agg['counted']}/{agg['sessions']}"
            if agg.get("cost_usd_ticks") is not None:
                line += f" cost_usd_ticks={agg['cost_usd_ticks']}"
            elif harness == "grok":
                line += " cost_usd_ticks=unrecorded"
            if agg.get("incomplete_sessions"):
                # An explicitly incomplete measurement must never read
                # as a verified total.
                line += f" incomplete={agg['incomplete_sessions']}"
        else:
            # The capture rule (loopstrap): an unavailable value is an
            # explicit gap. Zeros here would read as "measured: nothing
            # spent", which is the one thing this line must never say.
            line += f"  tokens=unrecorded ({GROK_GAP})" if harness == "grok"                     else "  tokens=unrecorded"
        print(line)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=["sessions", "report"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repo", default=None,
                        help="only sessions whose cwd is this path")
    home = Path.home()
    parser.add_argument("--claude-dir", default=str(home / ".claude" / "projects"))
    parser.add_argument("--codex-dir", default=str(home / ".codex"))
    parser.add_argument("--grok-dir", default=str(home / ".grok"))
    parser.add_argument("--agy-dir", help="explicit dispatch capture root")
    args = parser.parse_args(argv)
    if args.repo is not None:
        # The readers compare this against the transcripts' absolute cwd,
        # so a relative form (`.` from the checkout) matched nothing and
        # the report read an empty store as an answer. Resolve once here,
        # the boundary worth.py resolves at (LD-44).
        args.repo = os.path.abspath(str(args.repo))
    return cmd_sessions(args) if args.verb == "sessions" else cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
