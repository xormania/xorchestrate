# Executions, environments, and retained limits

Candidate: `cfa3549490ee9db1c3e6cad918523cd854902e51`. No paid model calls were made. Local work used a new clone, an isolated venv, and disposable output directories under `/workspace/work/pr7-review`; pre-existing repositories and worktrees were preserved.

| Check | Fresh execution result | Retained evidence |
| --- | --- | --- |
| Main keyless relocated replay | Pass; all 1,203 files unchanged before/after scoring | `evidence/verify-before.json`, `verify-after.json`; hosted ZIP also contains replay outputs |
| Supplemental relocated replay | Pass; frozen plan SHA `197bd8547da565db8284bccee821864cb3307e50f9a28350f92098302a53e6ca` | `evidence/supplement-replay.log` |
| Independent raw arithmetic | All original joins/actions/routes/audits/costs agree | `results/recomputed.json` |
| Independent supplement/economic arithmetic | Pass, including 96 synthetic interpreter controls | `results/economics-recomputed.json` |
| Independent methodology/operand checks | Pass; stage separation, grouping, packets, raw-only healthy/fault/order controls | `results/methodology-checks.json` |
| Independent provenance | Pass | `results/provenance-results.json` |
| Restricted local integrated suite | 25 failures from forbidden sockets; 666 pass, 8 tmux skips, 172 subtests pass | `evidence/local-tests.log`, `.xml` |
| Local integrated suite with permitted sockets | 691 pass, 8 tmux skips, 172 subtests pass | `evidence/local-sockets-tests.log`, `.xml` |
| Restricted behavioral controls | 36/40; four ordinary busy/replay cases explicitly not exercised | `results/blocked-sandbox-results.json` |
| Socket-capable behavioral controls | 40/40 + four actual action controls | `results/behavior-results.json`, `evidence/behavior-controls.zip` |
| Semantic tamper controls | Six rejected, six accepted by score; all twelve rejected by original complete seal | `results/tamper-results.json` |
| Independent checker against six scorer omissions | Untouched control accepted; all six inconsistencies rejected | `results/independent-tamper-crosscheck.json` |
| Fresh hosted pinned suite | **699 tests +172 subtests =871 passing records; zero failures/errors/skips** | Run `40131762088`; original ZIP retained |
| Fresh hosted corpus + main/supplement replay | All pass | Same run and ZIP |

Local Python was 3.12.14; hosted Python 3.12.15; pytest 9.1.1. Declared dependencies were installed (`pip install -e '.[test]'`); versions are retained in the hosted ZIP. Local tmux was absent. Network/socket sandbox restrictions initially blocked git fetch, pip installation and socket tests; authorized sandbox escalation enabled downloads/local sockets. No destination denial was bypassed. Eight local skips were specifically `tests/hexagonal/test_acceptance_lifecycle.py` tmux checks; fresh hosted execution installed tmux and exercised them all.

The dedicated review workflow was published as `d5af81334d5742968a138665d738d3b20084b72a`, tree `c858073fd6512cfbb79be90aa19196ac9c3afc4d`, on the review branch. **Workflow SHA is not tested-source SHA:** it explicitly checks out candidate `cfa3549490ee9db1c3e6cad918523cd854902e51`, tree `a5d9f1d77aca18c7a5540f22a0b628474fcc878a`. It has read-only repository permissions, no secret injection, and no live provider command. It does not modify the experiment workflow or branch.

- Run: https://github.com/xormania/xorchestrate/actions/runs/40131762088 (attempt 1).
- Job: `120984765941`, `offline`, all steps successful.
- Artifact: `12574744485`, `pr7-independent-40131762088-1`.
- Original ZIP: `evidence/actions-40131762088.zip`, 4,888,748 bytes.
- SHA-256: `fce0ef1e9ee1fcc3d23b6c006301d78427ad5ad3f998304b602bcb8ed54b2820`, independently matched to GitHub artifact metadata.
- ZIP CRC and raw JUnit/log/checked-out identity inspected; the 871 total includes 172 subtests, not 871 independent tests/incidents.

The ZIP retains complete rerun evidence after the hosted artifact expires, including the replayed main/supplement evidence, fresh corpus, JUnit, logs, identity and versions. The artifact/run/job metadata and full job log are also saved beside it. The original experiment's hosted run `40071712425` was independently inspected through fresh API reads, not merely trusted from its report; see `notes/provenance.md`.

One reviewer control aggregation attempt failed with JSON decoding after completing behavior. It is retained as `evidence/controls-final.log`. The reviewer script was corrected and re-aggregated the existing raw controls with explicit `--summarize-existing`; `controls-final-aggregation.log` records 40/40. This is not counted as a second fresh behavior execution. Earlier complete permitted-socket results independently recorded 40/40. No experiment code was changed.

Still unexercised/unverified: new paid/live model behavior; installed vendor harness behavior; exact native provider model/effort/billing/full-session attestation; new operational incident sampling; actual authority/effect downstream interventions; prospective economic routing, extraction cost and monetized learning. These are not failures of the completed offline tests and are not presented as passes.
