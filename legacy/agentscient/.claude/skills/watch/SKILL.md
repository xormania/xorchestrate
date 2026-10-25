---
name: watch
description: How a Lead or Coordinator session watches the lane without polling by hand — the watchdog for live conditions, the monitor instrument for durable events, and the harness Monitor tool that carries either into the chat. Use when you are running a lane and want to be told what happened instead of asking every few minutes.
---

# watch — be told, don't poll

A session that asks "anything happen?" every few minutes pays for the
question and still misses what fell between the asks. Arm a watch.
(`ops/conductor/tools/watch.py` is a different thing: one PR's checks.)

## The watchdog — live conditions, one line each

```sh
python3 ops/conductor/tools/watchdog.py --tick 60 --heartbeat-every 5
```

It reads the dispatch store, the ladders, the deliverable lock and, unless
`--no-forge`, the forge; it writes nothing. Each line prints once per
record or key for the life of the process, after the first line, `watchdog
armed: store <path>, <n> records ignored as backlog, tick <s>s`.

| flag | what it does |
|:--|:--|
| `--tick S` | seconds between ticks; default 300, or `WATCHDOG_TICK` |
| `--once` | one tick, exit 0 — the way to take a reading now |
| `--forge-every N` | ask the forge on tick 1 and every N ticks (default 1) |
| `--no-forge` | never ask the forge; the heartbeat says `not watched` |
| `--report-existing` | report the backlog once: closed at start, no record copy, unreadable inputs |
| `--heartbeat-every N` | the running/settling/launchers/PRs line; default 6 |
| `--warn-fraction F` | when a run is `long`; default two thirds of its wall |

What each line means, and what a session does with it:

- `stall <branch>: no dispatch running, <m> min since <id|the lock>` — a
  deliverable branch nobody is working: pick it up, or lift it from the
  lock with a reason. The id is the record the quiet is measured from,
  usually an old one, so never filter stall lines by the date in the id.
  `stall <b>: ladder <id> stopped <reason> at <stage>` is a ladder
  waiting on a decision, not a dead process.
- `deviation <kind> <id>` — a closed record disagrees with its brief:
  assumption (assumed, substituted or stubbed), bucket and size (more
  changed than the bucket line allows), lock (a lockfile nobody named),
  envelope (tripped or invalid yet carrying findings or changes), errors
  (an author-tests round that errored rather than failed), refused (a
  refusal in held output), retry (a ladder stage retried or stopped),
  minutes (far over the calibration row), unavailable (an input it could
  not read). Open the record first: a deviation is a question.
- `no record copy <id>: cannot judge` — a job directory with no
  record.json. Nothing can be said about that job; saying so is the
  finding, and its outcome is never inferred.
- `harness gone`, `exit written, not collected`, `unobservable`, `long` —
  the worker's process is the subject; the first goes to `dead-dispatch`.
- the forge lines: `ci fail|cancel #<pr> at <sha>: <check>` for every
  failing or cancelled check on an open PR into the trunk, and `drift PR
  #<n>: head <b> outside the deliverable` for a PR the lock does not name.
- `watchdog error: <type>: <message> (tick <t>; the loop continues)` — the
  tick failed, the loop did not (`watchdog-lever.md` §The lines: the loop
  never dies quietly). Read the record it names.

Backlog is silent: whatever the watchdog can already see when it starts —
a record closed, a directory with no record copy, one whose inputs cannot
be read — says nothing in any family unless `--report-existing`, which
reports all of it once (LD-70). The silence belongs to the condition, not
to the name: a muted record whose kind or fingerprint changes speaks
again, so a backlog directory that gains a record, an amended record and
inputs that turn unreadable are all live. Ask for backlog when you take
over a lane; leave the flag off for what happens from now on.

## Carrying it into a session — the harness Monitor tool

Every stdout line of a monitored command becomes a message in the chat,
so arm the watchdog and filter to the lines you would act on:

```
Monitor(description: "lane watchdog: stalls, deviations, no record copy, harness gone, watchdog errors, CI", timeout_ms: 1800000,
  command: "python3 ops/conductor/tools/watchdog.py --tick 120 2>&1 | grep -E --line-buffered 'stall |deviation |no record copy |harness gone |watchdog error|ci fail |ci cancel '")
```

The description names exactly what the filter passes, and the forge stays
on or no `ci` line can exist to match. `--line-buffered`, or the matches
sit in grep's buffer unseen. A monitor expires at `timeout_ms`; re-arm
it, and read an expiry with no events as too narrow a filter, not a quiet
lane. The surface itself — the call shape, one notification per stdout
line, a 200ms batch window, a five-minute default, a thirty-minute
ceiling, and the automatic stop when a source floods — was measured in
this session on 2026-09-16 and is a row in
`ops/conductor/harness-capabilities.md`; no probe re-checks it on a
cadence, which is a named gap.

On a checkout without the LD-70 fix the first tick replays every
historical condition — 328 lines at dev 46639344, measured by
agentscient-c8 on 2026-09-16. Pass only the conditions that cannot be a
replay:

```sh
... | grep -E --line-buffered '^(stall |ci fail |ci cancel |harness gone |exit written|worker unobservable |long |heartbeat|launchers |watchdog )'
```

Key on the line's own condition word, never on a record id it carries: a
stall line names the old record its quiet is measured from, and a date
filter would drop the line you most want. Key on the word the tool
prints, too: the line reads `worker unobservable`, and an earlier
version of this filter anchored `unobservable ` and dropped it in
silence (codex, round two of #244). Every line
`ops/conductor/watchdog-lever.md` §The lines names is a row in
`ops/conductor/tests/test_watch_skill.py`, passed or declared a replay,
so the next line added there cannot go missing here. The cost is that
live deviations, `closed` and `no record copy` go with the replay until
the checkout carries the fix; take those by hand with `--once`.

## The monitor instrument — events that outlive the session

```sh
python3 ops/conductor/tools/monitor.py check
python3 ops/conductor/tools/monitor.py run --once
python3 ops/conductor/tools/monitor.py digest --consumer lead --limit 20 [--ack]
```

- `check` validates the registry before any source or state is touched,
  prints `{"valid":true,"monitors":N,...}`, and creates no state.
- `run --once` observes what is due, enqueues events, persists dedup and
  cooldown, prints `observations,events,unavailable`, and needs an
  identity (`WF_AGENT`); `--loop` is silent between events.
- `digest --consumer ID` returns that consumer's events in increasing id
  order with a `more` flag, 10 events and 4096 bytes by default; reading
  without `--ack` is repeatable, and a foreign or omitted consumer never
  sees another's rows. `--ack` needs identity and advances only that
  consumer's cursor, through the last returned event.

**Consuming a digest is not delivering it.** An ack says the named
conductor read those rows — not that a session saw them, that anyone
acted, or that any external harness received anything (`monitor-lever.md`
§Boundary and command wire). Ack after you have written down what you
will do with them: an acked event nobody recorded is one nobody was told.

Which instrument: the watchdog for the lane's live state now, nothing kept;
the monitor when an event must outlive this session — one that fires while
you are away, a queue a second consumer reads, a cursor that remembers.

Record the use: `python3 ops/conductor/tools/skills.py log-use watch
"<what was watched>"` with `WF_AGENT` set.
