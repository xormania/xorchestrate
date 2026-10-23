#!/usr/bin/env python3
"""What has been dispatched, how long it has run, and what it is for.

A conductor loses track of background dispatches quickly: they live in
throwaway snapshots under a session scratchpad, they finish out of
order, and a run that died looks exactly like one still working. This
answers the question that actually gets asked -- "is anything still
going, and what was it doing?" -- from the snapshots themselves rather
than from memory.

Everything here is read from disk. Nothing is inferred from a previous
answer, so the output is as current as the moment it ran.

Four states, and the fourth is the one worth having:

  running    the process is alive
  finished   an exit file exists, with its code
  tripped    a supervision wire terminated it; the reason is shown
  DIED       the process is gone and no exit file was written

DIED is not a variant of finished. A dispatch whose process vanished
without recording an exit was never observed to complete, and reporting
it as done would turn "nobody looked" into "we checked and it was
fine" -- which is the failure this lane keeps paying for.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import tempfile
import time

# A dispatch snapshot is any directory holding both of these: the brief
# it was given, and the marker the launcher writes as it starts.
MARKERS = ("prompt.txt", "launched")

ROLE_RE = re.compile(r"You are the ([A-Z][A-Z \-]+?)[.\s]", re.MULTILINE)


def find_snapshots(roots):
    out = []
    for root in roots:
        for path in glob.glob(os.path.join(root, "*")):
            if not os.path.isdir(path):
                continue
            if all(os.path.isfile(os.path.join(path, m)) for m in MARKERS):
                out.append(path)
    return sorted(out)


def default_roots():
    """Session scratchpads, newest first.

    The scratchpad path contains a session id, so it cannot be hardcoded
    and is not worth asking the caller for.
    """
    # The base comes from the environment rather than a literal "/tmp":
    # TMPDIR is where this platform actually puts them, a hard-coded
    # path is wrong on any machine that moved it, and a literal made
    # ruff read a SEARCH pattern as a file this program creates (S108).
    # Nothing here writes; these are globs for stores someone else made.
    base = os.environ.get("TMPDIR") or tempfile.gettempdir()
    pats = [os.path.join(base, "claude-*", "*", "*", "scratchpad"),
            os.path.expanduser("~/.claude/scratchpad")]
    seen = []
    for p in pats:
        seen.extend(d for d in glob.glob(p) if os.path.isdir(d))
    return sorted(set(seen), key=os.path.getmtime, reverse=True)


def read(path, limit=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit) if limit else fh.read()
    except OSError:
        return ""


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


WRITE_RE = re.compile(r"^WRITE[^:]*:\s*(.*)$", re.MULTILINE)


def purpose(snap):
    """Role and deliverable, from the dispatch's own brief.

    What a run is FOR is better answered by what it must produce than by
    what it was told to read -- two dispatches often read the same files
    and produce entirely different things. Nothing is inferred: if the
    brief does not state a role, that is what gets printed.
    """
    text = read(os.path.join(snap, "prompt.txt"), 6000)
    match = ROLE_RE.search(text)
    role = match.group(1).strip().lower() if match else ""

    produces = ""
    hit = WRITE_RE.search(text)
    if hit:
        rest = hit.group(1).strip()
        if not rest:   # `WRITE, under \`x\`:` puts targets on following lines
            tail = text[hit.end():].splitlines()
            rest = " ".join(t.strip() for t in tail[:3] if t.strip())
        produces = re.sub(r"\s+", " ", rest)[:60].strip()

    if role and produces:
        return f"{role} -> {produces}"
    return role or produces or "(no role stated in the brief)"


def spend(snap):
    """Output tokens produced, from the harness's own trace.

    Output is the honest work signal. Totals are dominated by cache
    re-reads -- measured at 93.4% across twenty dispatches -- so a total
    tracks how many turns a run took, not what it produced.
    """
    stream = read(os.path.join(snap, "stream.path")).strip()
    if not stream or not os.path.isfile(stream):
        return None
    out = 0
    if "rollout-" in os.path.basename(stream):        # codex
        last = None
        for line in read(stream).splitlines():
            if '"total_token_usage"' not in line:
                continue
            try:
                blob = json.loads(line)
            except ValueError:
                continue

            def find(o):
                if isinstance(o, dict):
                    if "total_token_usage" in o:
                        return o["total_token_usage"]
                    for v in o.values():
                        got = find(v)
                        if got:
                            return got
                return None
            got = find(blob)
            if got:
                last = got
        return last.get("output_tokens") if last else None
    if os.path.basename(stream) == "updates.jsonl":   # grok
        # Grok reports a running total per update rather than a delta,
        # so the LAST one is the answer; summing would multiply it.
        last = 0
        for line in read(stream).splitlines():
            try:
                blob = json.loads(line)
            except ValueError:
                continue
            usage = (((blob.get("params") or {}).get("update") or {})
                     .get("usage") or {})
            if "outputTokens" in usage:
                last = usage["outputTokens"]
        return last or None
    for line in read(stream).splitlines():            # claude
        try:
            blob = json.loads(line)
        except ValueError:
            continue
        usage = (blob.get("message") or {}).get("usage") or {}
        out += usage.get("output_tokens", 0)
    return out or None


def state(snap):
    exit_file = os.path.join(snap, "reviewer.exit")
    tripped = os.path.join(snap, "TRIPPED.md")
    pid = read(os.path.join(snap, "reviewer.pid")).strip()
    if os.path.isfile(tripped):
        why = ""
        for line in read(tripped).splitlines():
            if line.startswith("TRIPWIRE"):
                why = line.replace("TRIPWIRE ", "").strip()
                break
        return "tripped", why
    if os.path.isfile(exit_file):
        code = read(exit_file).strip()
        return "finished", f"exit {code}"
    if pid and alive(pid):
        return "running", f"pid {pid}"
    if pid:
        # Gone, and nothing recorded why. Say so plainly.
        return "DIED", f"pid {pid} gone, no exit recorded"
    return "unlaunched", "no pid; the launcher may have refused"


def elapsed(snap, running):
    start = os.path.getmtime(os.path.join(snap, "launched"))
    end = time.time()
    if not running:
        # Latest write in the snapshot stands in for when it stopped.
        for name in ("reviewer.exit", "TRIPPED.md", "review.txt"):
            p = os.path.join(snap, name)
            if os.path.isfile(p):
                end = max(os.path.getmtime(p), start)
                break
    secs = int(end - start)
    return f"{secs // 60}m{secs % 60:02d}s"


def harness(snap):
    stream = read(os.path.join(snap, "stream.path")).strip()
    base = os.path.basename(stream)
    if base.startswith("rollout-"):
        return "codex"
    if base == "updates.jsonl":
        return "grok"
    if base.endswith(".jsonl"):
        return "claude"
    return "?"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", action="append", default=[],
                    help="scratchpad to scan; repeatable (default: this session's)")
    ap.add_argument("--all", action="store_true",
                    help="include finished dispatches (default: all of them anyway)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    roots = args.root or default_roots()
    snaps = find_snapshots(roots)
    if not snaps:
        print("no dispatch snapshots found under:")
        for r in roots:
            print(f"  {r}")
        return 1

    rows = []
    for snap in snaps:
        st, detail = state(snap)
        rows.append({
            "name": os.path.basename(snap),
            "harness": harness(snap),
            "state": st,
            "detail": detail,
            "elapsed": elapsed(snap, st == "running"),
            "output_tokens": spend(snap),
            "purpose": purpose(snap),
            "path": snap,
        })

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    order = {"running": 0, "DIED": 1, "tripped": 2, "unlaunched": 3, "finished": 4}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r["name"]))
    width = max(len(r["name"]) for r in rows)
    live = sum(1 for r in rows if r["state"] == "running")
    print(f"{live} running, {len(rows)} total\n")
    for r in rows:
        tok = f"{r['output_tokens']:,}" if r["output_tokens"] else "—"
        print(f"  {r['name']:{width}}  {r['harness']:6} {r['state']:10} "
              f"{r['elapsed']:>8}  {tok:>9} out  {r['detail']}")
        print(f"  {'':{width}}  {r['purpose'][:96]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
