# Replay the IV01–IV04 repair evidence

Use the exact product commit in `input-identities.json`; the evidence publication
commit is a documentation child, not the product candidate. No vendor credentials
are needed for the deterministic checks below.

## Verify and restore

Download `Xorchestrate-PR6-IV-Repair-Evidence.zip`, `ARCHIVE.sha256`,
`restore_evidence.py` and `SHA256SUMS` from the same immutable GitHub publication
listed in its README. Verify the archive before using its contents:

```sh
sha256sum -c ARCHIVE.sha256
python3 restore_evidence.py Xorchestrate-PR6-IV-Repair-Evidence.zip --verify-only
python3 restore_evidence.py Xorchestrate-PR6-IV-Repair-Evidence.zip --out restored
```

The ZIP uses content deduplication: `package-manifest.json` maps every original
path, size, mode and SHA-256 to its payload. The helper verifies each payload and
restores those paths. Ordinary ZIP extraction alone does not reconstruct the
evidence tree. Exclusions are recorded; they are caches, Git administration and
runtime endpoints/aliases, not raw test failures or observations.

`inputs/retained-inputs.json` binds unchanged copies of all four original witness
scripts and the prior raw protocol inputs needed by IV03. The full prior review
remains unchanged at commit `3ac2342b6655f1a30652380c85ceb55262b7ad0c`, path
`verification/pr6-independent-ef75675`. Its archive is
`Xorchestrate-PR6-Independent-Verification-Evidence.zip`, SHA-256
`d161f2318727b8b52aa5dd78fa91d6d5ebc1aa4c12103ec8e385b328715833dc`.
The new `evidence/provenance/input-verification.json` records the revalidation of
that publication, its archive and all 57,328 restored files. Downloading that
older archive is optional for the minimal replays below and required to inspect
the entire earlier history. Follow its own README and `restore_review.py`.

## Recover exact source

Use absolute paths and choose a new output root. The source bundle includes
reviewed baseline, component and final product refs. Fetch its explicit refs;
plain `git clone` alone does not select these custom refs.

```sh
export REPAIR_EVIDENCE=/absolute/path/to/restored
export REPAIR_REPLAY=/absolute/path/to/new-replay
mkdir -p "$REPAIR_REPLAY"
git init "$REPAIR_REPLAY/repository"
git -C "$REPAIR_REPLAY/repository" fetch \
  "$REPAIR_EVIDENCE/evidence/root/repair-source.bundle" \
  'refs/repair-evidence/*:refs/repair-evidence/*'
git -C "$REPAIR_REPLAY/repository" worktree add --detach \
  "$REPAIR_REPLAY/candidate" refs/repair-evidence/final
git -C "$REPAIR_REPLAY/repository" worktree add --detach \
  "$REPAIR_REPLAY/baseline" refs/repair-evidence/reviewed
git -C "$REPAIR_REPLAY/candidate" show -s --format='%H%n%T%n%P'
git -C "$REPAIR_REPLAY/candidate" status --porcelain
python3 -m venv "$REPAIR_REPLAY/venv"
export REPAIR_PYTHON="$REPAIR_REPLAY/venv/bin/python"
"$REPAIR_PYTHON" -m pip install -r "$REPAIR_EVIDENCE/inputs/requirements-replay.txt"
"$REPAIR_PYTHON" -m pip install --no-deps -e "$REPAIR_REPLAY/candidate"
export PYTHONDONTWRITEBYTECODE=1
```

The recorded local runtime is Python 3.12.14. Hosted CI separately measures
Python 3.11 and 3.12. Dependency versions are retained; the replay requirements
omit only the original machine's editable-install URL, replaced above with the
selected source. Supply a functioning tmux executable and local Unix/TCP sockets.
The original runner's isolated tmux binary is retained under execution preparation;
its portability is limited to compatible Linux x86-64 hosts.

## Replay decisive witnesses

Run each command into a new directory. Use the selected candidate as `PYTHONPATH`;
the IV02 script imports the product from its working directory.

```sh
cd "$REPAIR_REPLAY/candidate"
export PYTHONPATH="$REPAIR_REPLAY/candidate"
"$REPAIR_PYTHON" "$REPAIR_EVIDENCE/inputs/original-witnesses/iv01.py" \
  "$REPAIR_REPLAY/candidate" "$REPAIR_REPLAY/iv01-original.json"
"$REPAIR_PYTHON" "$REPAIR_EVIDENCE/evidence/iv01/closed-witness.py" \
  "$REPAIR_REPLAY/candidate" "$REPAIR_REPLAY/iv01-closed.json"
"$REPAIR_PYTHON" "$REPAIR_EVIDENCE/inputs/original-witnesses/iv02.py" \
  --peers codex claude grok --out "$REPAIR_REPLAY/iv02"
"$REPAIR_PYTHON" "$REPAIR_EVIDENCE/inputs/original-witnesses/iv03.py" \
  --candidate "$REPAIR_REPLAY/candidate" \
  --baseline "$REPAIR_EVIDENCE/inputs/retained-ef-protocol" --out "$REPAIR_REPLAY/iv03"
"$REPAIR_PYTHON" "$REPAIR_EVIDENCE/inputs/original-witnesses/iv04.py" \
  --candidate "$REPAIR_REPLAY/candidate" --out "$REPAIR_REPLAY/iv04"
```

The unchanged IV01 script intentionally exits **1** on the repair: its old
assertion requires the disclosure to exist. The separate two-line expectation
adaptation must exit **0**, keep the authorized projection and preserve direct
denial controls on both stores. The exact adaptation diff is retained.
IV02 must exit 0 with all six schedules valid. IV03 retains original input
bindings (it is rechecking historical raw data, not fresh native execution): the
unmodified control passes, each contradictory callback is rejected, and product
receipts agree. IV04 must classify both unrelated raises as `unrelated_failure`
and the real assertion as `detected`, in both calibrators.

Repeat against the baseline worktree to reproduce the original defects. IV01's
original exits 0 there; IV02 exposes Codex BA; IV03 wrongly grants the three
mutations passing credit; IV04 wrongly credits the same-line unrelated raise.
Do not combine these commands under `set -e` without accounting for intentional
nonzero witness and planted-fault exits.

The normal suite includes sustained known-bad subjects, valid controls and
unrelated-error controls for each repair. Extra adversarial scripts and their
arguments are retained under `evidence/adversarial/`; component command records
identify every replay adaptation. The final witness ledger identifies replays
on the frozen combined product, separately from earlier component runs.

## Full execution and raw recomputation

Run from the exact candidate with outputs outside source:

```sh
"$REPAIR_PYTHON" -m pytest tests/hexagonal -q -rs \
  --junitxml="$REPAIR_REPLAY/application.xml"
"$REPAIR_PYTHON" -m scripts.verification.calibrate_suite \
  --candidate "$REPAIR_REPLAY/candidate" --out "$REPAIR_REPLAY/native-calibration"
"$REPAIR_PYTHON" tests/hexagonal/calibrate_work_acceptance.py \
  --output "$REPAIR_REPLAY/work-calibration"
git -C "$REPAIR_REPLAY/repository" worktree add --detach \
  "$REPAIR_REPLAY/checker" refs/repair-evidence/final
cd "$REPAIR_REPLAY/checker"
export PYTHONPATH="$REPAIR_REPLAY/checker"
cp docs/implementation/owner-brief.md "$REPAIR_REPLAY/owner-brief.md"
"$REPAIR_PYTHON" -m scripts.verification criteria \
  --brief "$REPAIR_REPLAY/owner-brief.md" --out "$REPAIR_REPLAY/criteria.json"
"$REPAIR_PYTHON" -m scripts.verification.multi_harness \
  --candidate "$REPAIR_REPLAY/candidate" --mode protocol-fixture \
  --brief "$REPAIR_REPLAY/owner-brief.md" --criteria "$REPAIR_REPLAY/criteria.json" \
  --out "$REPAIR_REPLAY/protocol" --timeout 60
"$REPAIR_PYTHON" -m scripts.verification check \
  --run "$REPAIR_REPLAY/protocol/run.json" \
  --criteria "$REPAIR_REPLAY/protocol/criteria.json" \
  --bindings "$REPAIR_REPLAY/protocol/bindings.json" \
  --fixture "$REPAIR_REPLAY/protocol/fixture.json" \
  --out "$REPAIR_REPLAY/recomputed.json"
```

Compare parsed report objects, not just exit status or totals. The original
46-case inventory must remain intact. `evidence/provenance/` contains independent
raw calibration, protocol and selector audits, including source changes beneath
preserved selector names. `evidence/execution-prep/run_final.py --help` documents
the guarded full recorder used for local execution. It refuses a dirty candidate
or a different commit and records actual module origins. Its command ledger also
includes static checks, optional helpers and live tmux execution.

Hosted ZIPs and decoded job logs are retained under `evidence/ci/`. Verify both
Python `identity.txt` files: hosted PR execution may use an integration commit;
its tree must equal the candidate. A shallow Actions checkout can leave the
`%P` parent line empty. Fetch that exact integration commit object (or inspect
the GitHub Git-commit API) and independently verify its parents contain the
measured head and expected base. The retained integration proof does this;
an empty shallow parent field is not itself proof of ancestry.
Do not substitute a successful older workflow run. Failed, component and retained
historical results are evidence of their own sources only.

## Limits

The subprocess protocol peers are deterministic, not authenticated vendors.
The user's local authenticated run remains planned work. This package contains
implementation verification and an adversarial review within the same session;
the next session must independently challenge it using
`Independent-Verification-Handoff.md`. No merge, release or Jev change was made.
