---
name: dead-dispatch
description: Triage a dispatch that died, returned invalid, or has no envelope - the job's stderr first, then credential age, model name, sandbox against host, the cap, the derived command - classified as lane or content before anything is relaunched. Use before any re-dispatch or any claim that a worker died.
---

# dead-dispatch — read the job's own files before touching anything

Why it exists, measured: on 2026-09-10 a sandboxed `status` read DIED
for workers that were alive on the host, and duplicates were launched;
Codex network denials sat in retained stderr while the reviewer loop
kept running; nine of forty-one records that day ended invalid with no
envelope. On 2026-09-11 two Grok jobs died in five seconds on an aged
credential (LD-18, pitfall 20), a dispatch with a model name not in
the conf died in four (pitfall 4), a stage waited the default wall
because the driver read the wrong cap (LD-17), and two code attempts
were stopped by the driver's derived command, not by the code (LD-19).
Every one of these read as "the worker failed" until its files were
read.

## The steps

1. **The record copy** on the branch, `.dev/records/dispatches/<id>.json`:
   `status`, `result.envelope`, `caps`, `at`. Grok writes
   `envelope.json`, Codex `last-message.json`; the record copy carries
   `result.envelope` for both (pitfall 11).

2. **The job directory**, `~/.local/state/agentscient/dispatch/<id>/`:

   ```sh
   cat <job>/exit; wc -c <job>/raw.out; tail -20 <job>/stderr; tail -5 <job>/launcher.log
   ```

   These four lines decide the class. Nothing is relaunched before they
   are pasted.

3. **Classify** from what they say:

   | reading | class | do this |
   |---|---|---|
   | `Not signed in` in stderr, exit 1 in about 5 s, empty `raw.out` | credential (Grok; LD-18) | host `~/.grok/auth.json` is older than about 30 h even if jobs ran an hour ago; the owner runs `grok login --device-code`; the launcher now refuses this before the mint. |
   | harness exited in about 4 s, record invalid | model name (pitfall 4) | the name is not in `MODEL_*` of `ops/lane.conf`; dispatch reads the conf, never a remembered name. |
   | `Operation not permitted` on a network call | sandbox context | the harness needs the approved host context; the brief was not the problem. |
   | DIED from `launch.py status` inside a sandbox | not evidence | host `ps -o pid,etime,cmd -p <pid>`; a sandbox cannot see host pids. |
   | stopped as `wall` while `raw.out` still grows | the cap, not death (LD-17) | let it close; resume finds the closed record. The ladder now reads the cap from the job registry. |
   | discover exits 5, zero tests | the derived command (LD-19) | a support module under `tests/` was named as a test; only `test*.py` are commands. |
   | envelope invalid, code untouched | the frozen command or the tests' own assertion (LD-15, pitfall 19) | the tree and the command, not the coder; `resume <id> --retry <stage> --because '<text>'` records the extra attempt. |
   | exit 3, a last reading printed, partials on disk | an abort on the wire | read the partials; nothing is residue. |
   | none of the above, and the envelope carries findings or a verdict | content | the worker finished; the record is the answer. |

4. **Before any relaunch:** the host `ps` shows the pid gone and the
   record reads `closed` or `died`. A relaunch beside a live worker
   leaves two records and two commits, which is what 2026-09-10 spent
   its evening undoing.

5. **Route it:** a lane class is a row in
   the defect ledger (`python3 ops/conductor/tools/defects.py add`, one file under `ops/conductor/handoff/lane-defects/`) with the record id; a content
   class is the finding, imported into the ledger from the record.

Record the use: `python3 ops/conductor/tools/skills.py log-use dead-dispatch "<record id> <class>"`
with `WF_AGENT` set.
