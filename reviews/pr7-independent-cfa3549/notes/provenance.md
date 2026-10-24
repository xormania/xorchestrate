# Independent provenance and original hosted evidence review

Review subject: `cfa3549490ee9db1c3e6cad918523cd854902e51`, not a later branch head. This track read the full handoff and independent-verification assignment, the retained source/result manifests, native invocation records, label-review/preflight context, original workflow source and hosted evidence. It made no model calls, source repairs, branch updates or remote mutations.

## Recomputed identities and preservation

| Role | Commit | Tree |
| --- | --- | --- |
| Product subject and frozen v1 publication | `d2d2123bb8d4e866454c8e8e364f7749fa63a198` | `7bb88d5fd01a21478b720ad2d42b2b627b74bb92` |
| Main measured source | `9c6d7612e31bb78eb07868b5ac2a046629c5876c` | `5313e3a176515e7b160315a56da430fcde63b189` |
| Original hosted validation | `0d8d5187209c1fb2c92e8d2a4305c6f7b40da9aa` | `febf6a8676f5231ff585019b6374dbc190b75be4` |
| Post-hoc supplement source | `4577eb2c09a211218cfb822f195127d6a8a3f918` | `58930924d0d7fe0db7def67625cd09354d0b9c00` |
| Evidence-publication review candidate | `cfa3549490ee9db1c3e6cad918523cd854902e51` | `a5d9f1d77aca18c7a5540f22a0b628474fcc878a` |

Git object inspection verifies all 1,735 tracked base files remain byte-identical in the candidate. All 197 changed paths relative to the base are additions under the v2 experiment directory or its new workflow. Thus the candidate preserves product source, frozen v1, and base material; it does not incorporate PR #6 repairs. The parent review separately refreshed PR #6's remote state.

The hosted-only commit adds `fetch-depth: 0` in the two checkout steps, with no measured executable/data changes. The supplement adds exactly its plan, prepared-plan JSON, and script. The final publication adds reports and retained evidence. All 99 paths bound by the main manifest match both the measured-source Git blobs and candidate bytes. All 28 frozen-corpus inventory hashes match. Corpus version is `2.0.1`, policy plan version `policies-2.0.0`, requested/observed Jev model `jev-1.14.0`.

All five archive-index byte lengths and SHA-256 values match, and ZIP CRCs and member uniqueness/path safety pass. The main archive has 1,204 members including the seal; the complete 1,203-file seal matches every extracted byte and the exact file set. This establishes retained-byte integrity, not the validity of the experimental design.

## Original hosted validation independently cross-checked

Fresh read-only GitHub API results confirm [run 40071712425](https://github.com/xormania/xorchestrate/actions/runs/40071712425) succeeded at `0d8d518…`. `validate` succeeded; `hosted-live` was skipped. The offline job is not a second paid measurement.

Artifact `12551771552` is 845,583 bytes. Fresh GitHub metadata reports SHA-256 `237c592593cb76f1695f9824fbe160cf53dfcbb4db5b4ffb33aa3e7e048a18df`, matching the retained original ZIP. Its `actions-context.json` records Python `3.12.15`, hosted source/tree above, run attempt 1, and route `github_hosted_offline`.

Raw JUnit and freshly retrieved job logs agree:

| Suite | Tests passed | Subtests passed | Failures/errors/skips |
| --- | ---: | ---: | ---: |
| Experiment/v1 | 101 | 8 | 0 |
| Hexagonal | 598 | 164 | 0 |
| Total | 699 | 172 | 0 |

The report's 871 test/subtest total is correct. XML contains 699 `<testcase>` elements; subtests appear in suite totals and are confirmed by logs. These checks establish execution of those tests, not experimental validity or installed-vendor behavior. The earlier failed hosted attempt remains retained separately.

## Native invocation evidence and independence limits

All 22 native-evaluation directories contain a distinct request, response, invocation metadata and invocation prompt. There are 11 reasoning-only and 11 post-P3 records, 22 unique recorded invocation IDs (`/root/native_001` through `/root/native_022`), and 22 unique request joins. For every record, independent checks confirm:

- Native request bytes exactly match the exported request file.
- Request and raw-response hashes match invocation metadata.
- Request IDs join index, request, response, imported responses, combined responses and attempt ledger.
- Imported response content exactly equals raw response plus its invocation metadata.
- The invocation prompt limits reading to the single request packet and writing to its response file.

This is consistent with the reported separate native invocations and supports replay of their retained answers. It is not provider-signed attestation or a full session trace: the files do not independently prove every tool action, OS isolation or perfect blinding. The declared context is fresh `fork_turns=none`, shared system/developer context, and instruction-limited tool access. Exact observed model, reasoning effort, usage, elapsed time and cost remain null for all 22; requested model/effort are inherited session settings. Do not attribute these outcomes to a named pinned reasoning model or price them at zero.

The original preparatory reviewer openly shared implementation lineage/workspace and influenced premeasurement fixes and the supplement. It did not author implementation or model responses. Its initial packet judgments preceded opening the key according to the retained chronology; this review cannot independently attest that historical exposure boundary. The bound approval file's current hash is unchanged (`62c50cd0…`), and recorded approval `06:44:43.936813`, binding `06:45:55.671382`, and first smoke `06:45:55.680458` are correctly ordered on 2026-10-24 UTC. Those retained timestamps are not independently signed timing evidence.

The original manifest's `measurement_complete: false` is explicitly the frozen preparation state; the separate completion record describes later execution. It is not by itself evidence that invocations were absent. Conversely, the completion record's `execution_complete: true` does not settle E14 methodological completion.

## Deliverables and replay

`verify_provenance.py` is an independent stdlib-only verifier and does not import experiment modules. It rechecks Git identities, every base file, scoped additions, measured-source hashes, frozen-corpus inventory, archives, the main complete seal, native joins, chronology, and original hosted counts. It produces `provenance-results.json`; all assertions passed.

```sh
python3 verify_provenance.py --repo /path/to/pinned/repo --out /tmp/pr7-provenance
```

Fresh connector observations are retained as `github-hosted-0.json` (run), `github-hosted-1.json` (jobs), `github-hosted-2.json` (artifact), and `github-validate.log`. When those metadata files are in `--out`, the script additionally checks their run/source/job identities and GitHub artifact digest. Reading those observations can be repeated through the same public API endpoints; it does not alter repository state.

This track's findings support E01's preservation/provenance and the bounded retained-deliverable part of E13. E11's exact native attribution remains unverified. It found no byte/source/publication substitution; green hosted results and matching hashes cannot establish equal-evidence comparison, causal probe value, or economic usefulness.

The parent review separately owns fresh review-hosted execution and publication. Its workflow-only review commit `d5af81334d5742968a138665d738d3b20084b72a` (tree `c858073fd6512cfbb79be90aa19196ac9c3afc4d`) launches run `40131762088` and explicitly checks out the pinned candidate. This track did not inspect that run and assigns it no pass credit.
