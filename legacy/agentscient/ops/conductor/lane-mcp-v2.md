# Lane MCP v2 — three evidence reads, one transport fix

Extension contract for `ops/conductor/lane-mcp-lever.md`, corrected
against the tree at `36fe0562e32dd5e8ae0b4378712e662a8fc28ff0` on
2026-09-05 from the prior draft (sha256
`42cb46bab5501a00bd90b5dbef391c5a0e98fe085675083cf739b7be391f0398`).
It is closed: what it does not name is not promised. Everything in
`lane-mcp-lever.md` stands unchanged unless a line here says otherwise.
Startup, homes, handoffs and actions are [`discovery-lever.md`](discovery-lever.md), not this extension.
This document inherits `ops/conductor/instrument-conventions.md` and the
lever's boundary paragraph (no launcher, monitor, watchdog or writer
imports; no subprocess; no network; no file or database origination;
bytecode writes disabled at the entry point; data never becomes
instructions). Standing: admitted for this owner-authorized pipeline on 2026-09-05.
Admission and independent ruling: `reports/mcp-pipeline-2026-09-05.md`.

## 1. Configuration

Two config versions are valid. The version selects the surface.

| version | exact keys | surface |
|---|---|---|
| 1 | `version, dispatch_store, job_ids, monitor_state, consumer, usage_rollout` | the four v1 tools; the three fixed v1 resources (`lane://brief`, `lane://alerts`, `lane://usage`) plus one `lane://record/ID` per configured id; `check` → `{"valid":true,"tools":4}`; every v1 payload byte-identical to today |
| 2 | the six above plus `job_registry` | the v1 surface plus `lane_findings`, `lane_changes`, `lane_jobs` and their resources (§6); `check` → `{"valid":true,"tools":7}` |

- `check.tools` equals the number served by `tools/list` for that
  config version: four for v1, seven for v2.
- `job_registry` is an explicit string path or null (disabled), resolved
  from the config file like the other sources, same 1024-byte and
  printable rules, refusal class `config`. It names one regular JSON
  file: the job registry (`ops/devlane/task/jobs.json` in this tree).
- `version` 1 with `job_registry` present, `version` 2 without it, any
  other version, and every existing v1 refusal keep class `config`, with
  the exact-keys refusal naming the key set for the declared version.
- No missing-source fallback, no environment lookup, no default registry
  path. Config is still loaded once per process.
- `ops/conductor/lane-mcp.example.json` stays version 1 as the
  compatibility witness. `ops/conductor/lane-mcp-v2.example.json` is
  added: version 2, all sources null, `job_ids` empty, `consumer`
  `conductor`, `job_registry` null.

## 2. Revision

`revision` is the first 16 lowercase hex characters of the SHA-256 of
the exact bytes read from a source file (record or registry) within the
1 MiB source bound. It identifies bytes, never meaning: a rewrite with
identical bytes is the same revision. It is computed per query and
never stored. Grammar wherever accepted as input: `^[0-9a-f]{16}$`.

## 3. `lane_findings`

Page the findings and note of one configured dispatch record.

Arguments, closed object: `{id: string, cursor?: string}`.

- `id` must be in configured `job_ids` (existing `arguments` refusal).
- `cursor` grammar `^[0-9a-f]{16}:[1-9][0-9]{0,8}$` (revision, colon,
  1-based index of the first entry wanted); malformed → `arguments`.
- Any other key or type → `arguments`.

Source: exactly `dispatch_store/ID/record.json`, opened and bounded as
`lane_record` opens it (no symlink following, regular file, ≤ 1 MiB).
Missing store or record → `unknown`; unreadable, oversized, malformed,
not valid UTF-8, or non-object → `unavailable`; both carry
`findings: []`, `findings_total: null`, `record: null`, `note: null`,
`note_bytes: null`, `page: null`, and the `reason` strings `lane_record`
uses.

Result, `state: ok`:

```
{schema, query:"lane_findings", state:"ok", reason:null,
 record:{id, job, stage, recorded_status, verdict, counts, revision, evidence},
 note: string|null, note_bytes: int|null,
 findings:[{n, severity, where, claim, reproduce, omitted:[...]}...],
 findings_total: int|null,
 page:{from:int|null, to:int|null, count:int, more:bool, next_cursor:string|null}}
```

- `record.job` and `record.stage` come from the record's top-level
  `job` and `stage` (256-byte text rule, else null); `recorded_status`,
  `verdict`, `counts`, `evidence` exactly as `lane_record` derives them.
  Counts stay recorded labels: implementation records carry command
  diaries as P3 entries; this tool never sums them into a defect count.
- Text rule for every string this tool returns: the value must be a
  string that UTF-8 encodes (a lone surrogate from a `\uD800`-style
  escape does not) and whose UTF-8 length is within the field's cap;
  otherwise the field is null. Caps: `note` 2048 bytes, `where` 256,
  `claim` 1024, `reproduce` 1024. Caps are UTF-8 bytes of the value,
  not bytes of its JSON encoding.
- `note` is `result.envelope.note`. A string within cap is returned
  verbatim; `note_bytes` is its UTF-8 length. Over cap → `note: null`,
  `note_bytes` its measured length. Absent or null → both null. Not a
  string → both null and reason `"note is not a string"`. A string that
  does not UTF-8 encode → both null and reason `"note is not valid
  Unicode"`. State stays `ok` in every case.
- `findings` is `result.envelope.findings`. If it is not a list, state
  is `unavailable` with reason `"envelope findings is not a list"`,
  `findings_total: null`, `record` still filled. If it is a list,
  `findings_total` is its length (0 is an answer).
- Every entry keeps its 1-based `n`. `severity` is one of `p1 p2 p3`
  else null; `where`, `claim`, `reproduce` follow the text rule;
  `reproduce` is null when absent or null. `omitted` lists each of
  `severity where claim reproduce` that became null because it was of
  the wrong type, over cap, not valid Unicode, or dropped for page fit
  (below); a field that was absent or null in the source is null and
  not listed. An entry that is not an object is `{n, severity:null,
  where:null, claim:null, reproduce:null, omitted:["entry"]}`. No entry
  is ever skipped.
- Page fit is measured on the complete canonical reply bytes (`ensure_ascii`
  JSON, the lever's encoder), capped at 8192. UTF-8 field caps do not
  guarantee that encoded text fits. A scratch reproduction on 2026-09-05
  constructed a valid 8191-byte frame plus note, then measured 8297 bytes
  after adding a fully reduced first entry. The rule is therefore an
  explicit omission sequence:
  1. The frame is the reply with `findings: []` and `note: null`. If
     the frame alone exceeds 8192 bytes, the reply is `unavailable`
     with reason `"page frame exceeds 8192 bytes"`, `findings: []`,
     `findings_total` filled, `page: null`.
  2. On the first page (no cursor) the note is included when the frame
     plus the note fits; otherwise `note: null`, `note_bytes` still
     measured, reason `"note exceeds page"`, state `ok`. Every later
     page carries `note: null` and `note_bytes` unchanged, so the note
     is sent at most once.
  3. Entries are appended in order while the page fits. The first
     entry of a page that does not fit whole is reduced in the fixed
     order `reproduce`, then `claim`, then `where`, each dropped field
     set null and named in `omitted`, until it fits; the reduced entry
     ends the page. If the first entry still cannot fit after all three
     text fields have been nulled, remove the note when present (keep
     `note_bytes`, set reason `"note exceeds page"`) and measure again.
     If the reduced entry still cannot fit, return `unavailable`, reason
     `"page frame exceeds 8192 bytes"`, `findings: []`, the measured
     `findings_total`, and `page: null`; never advance a cursor past an
     entry that was not returned. An entry that is not the first on
     its page is never reduced; it starts the next page whole.
  4. A successful page has `count >= 1` whenever entries remain at
     the requested start index. Every fit measurement includes the final
     page metadata, reason and omission markers, not just entry text.
- `page.from`/`page.to` are the first and last `n` on the page or null
  when `count` is 0; `more` is true when entries remain;
  `next_cursor` is `"<revision>:<to+1>"` when `more`, else null.
- A cursor whose revision differs from the record's current revision is
  refused, class `cursor`: `expected cursor for revision <current>;
  found <given>; needed restart from the first page`. A cursor whose
  index exceeds `findings_total` is refused, class `cursor`, needed
  `supply an index within findings_total`. When the record is missing or
  unavailable the cursor is not judged; the `unknown`/`unavailable`
  reading is returned.
- Never returned: prompt text, raw.out, transcripts, `artifacts`,
  `commit`, spend, the launcher's `brief`, or any field not named here.
- Byte-identical across CLI, tool and resource while the record bytes
  are stationary; no per-call timestamps.

## 4. `lane_changes`

Compare a caller-held checkpoint with the configured records and return
only what changed.

Arguments, closed object: `{checkpoint?: {ID: revision, ...}}`.

- Every key must be a configured id; every value must match the revision
  grammar; anything else → `arguments`. An empty object is allowed. An
  absent checkpoint means the caller holds nothing.

Result:

```
{schema, query:"lane_changes", state:"ok", reason:null,
 configured_ids:[...],
 changed:[{id, revision, recorded_status, verdict, evidence}...],
 unchanged:[id...], unknown:[id...], unavailable:[id...],
 checkpoint:{ID: revision, ...}}
```

- For each configured id, in `job_ids` order, the record is opened as
  `lane_record` opens it. Missing → `unknown`; unreadable, oversized or
  malformed → `unavailable`; readable → its revision is computed.
- A readable record whose revision equals the caller's value for that id
  is `unchanged` (id only). Every other readable record is `changed`,
  including records the caller did not name. `changed` entries carry
  exactly the five fields shown (`id`, `revision`, `recorded_status`,
  `verdict`, `evidence`); `lane_record` and `lane_findings` are the
  detail reads.
- `checkpoint` in the result is the complete map of every readable
  record's current revision, the value the caller holds next time.
  Missing and unavailable ids are absent from it and named in their
  lists; silence is never a checkpoint.
- `state` is `ok` whenever the store is configured and openable, even
  if every id is unknown; store missing → `unknown`, store unavailable
  → `unavailable`, both with all four lists empty except `unknown` or
  `unavailable` holding every configured id and `checkpoint: {}`.
- Stateless: nothing is written, no cursor is stored, no wakeup or
  subscription is implied, and the server does not poll. "Changed" is
  byte identity, not recorded status.

## 5. `lane_jobs`

Read the configured job registry as compact cards.

Arguments, closed object: `{name?: string}`; `name` grammar
`^[a-z][a-z0-9-]{0,63}$` (this query's bounded name shape), else `arguments`.

Source: the configured `job_registry` file, bounded like a record (≤ 1
MiB, regular file, no symlink following). Not configured or missing →
`unknown`; unreadable, oversized, malformed JSON, not valid UTF-8, or a
non-object root → `unavailable`. Both carry `jobs: []`, `job: null`,
`evidence: {path, revision:null}` (path null when not configured).

Result without `name` (the list):

```
{schema, query:"lane_jobs", state:"ok", reason:null,
 evidence:{path, revision},
 jobs:[{name, adapter, role, dispatchable, slots:[...]}...], job:null}
```

Result with `name` (one card): the same, `jobs: []`, and

```
job:{name, adapter, role, snapshot, dispatchable, slots:[...],
     deliverable, constraints:[...], timeout, prompt_bytes}
```

- Entries are the registry object's keys in file order. A key not
  matching the name grammar, not UTF-8 encodable, or whose value is not
  an object produces `{name:null, adapter:null, role:null,
  dispatchable:false, slots:[]}`. Null name and false dispatchable mark
  an invalid row; the row is never dropped and the malformed key is
  never echoed. At most 64 entries are listed; more → `unavailable`
  with reason `"registry lists more than 64 jobs"`.
- `adapter`, `role`, `snapshot`: the entry's strings under the 256-byte
  text rule (string, UTF-8 encodable, ≤ 256 bytes), else null.
- `dispatchable` is a limited structural assessment of a supported
  registry row, not a dry run or a claim that all launch checks pass.
  It is true exactly when the name matches the query grammar, the entry
  is an object, `adapter` is `"harness"`, `role` is `"read"` or `"write"`,
  `snapshot` is `"whole"`, `withheld` is absent or falsey under Python
  truth testing, and `prompt` is a non-empty UTF-8-encodable string whose
  placeholders are all among `ref base diff into out inputs scope`.
  This deliberately requires a declared role and non-empty prompt even
  though the launcher's pre-mint code does not itself enforce both.
  The adapter, snapshot, truthy-withheld and placeholder checks were
  measured in `launch.py` at the stamped ref; this service never imports
  or runs that writer. Harness support, scope, inputs, refs, identity,
  environment, caps and other launch preconditions remain unmeasured.
  The wrapper always passes `--scope`; the launcher refuses it when the
  prompt has no `{scope}` slot. A structurally dispatchable card without
  `scope` therefore does not imply it can run through the wrapper.
- `slots` is the sorted, de-duplicated list of placeholder names in the
  prompt matched by `\{([A-Za-z_][A-Za-z0-9_]*)\}`, the pattern shared
  by the renderer (`ops/devlane/task/run.py:197`) and the launcher
  (`launch.py:360`); `[]` when the prompt is null or not a string.
- `deliverable`: string ≤ 512 bytes under the text rule else null.
  `constraints`: the first 16 string items each ≤ 256 bytes (longer,
  non-Unicode and non-string items become null in place); not a list →
  `[]`. `timeout`: `caps.timeout` when it is a finite non-negative
  number, else null. `prompt_bytes`: UTF-8 length of the prompt string,
  null when not a string or not UTF-8 encodable. The prompt text itself
  is never returned.
- `name` naming no key of a readable registry is an answer:
  `state: ok`, `job: null`, reason `"job is not declared in the
  registry"`.
- A card is evidence about registry bytes; it promises nothing about a
  launch.

## 6. Resources and templates

| URI | query | listed in `resources/list` |
|---|---|---|
| `lane://findings/ID` | `lane_findings {id}` first page | yes, one per configured id (v2 only) |
| `lane://changes` | `lane_changes {}` | yes (v2 only) |
| `lane://jobs` | `lane_jobs {}` | yes (v2 only) |
| `lane://jobs/NAME` | `lane_jobs {name}` | no; discoverable by template |

- Continuation pages are tool-only; a resource URI never carries a
  cursor. Unlisted or malformed URIs keep today's `-32602`.
- Under a version 2 config the server answers `resources/templates/list`
  with exactly one template: `{uriTemplate:"lane://jobs/{name}",
  name:"Lane job card", mimeType:"application/json"}`. Under version 1
  the method returns `-32601` as today. This is the only optional
  feature admitted, because job cards cannot be listed without reading
  the registry at list time, and `resources/list` reads no source.
- `tools/list` under v2 lists the three new tools beside the four v1
  tools with the same annotations; `lane_findings` requires `id`;
  `lane_changes` and `lane_jobs` have optional arguments; all
  `additionalProperties:false`.
- `lane_brief` is unchanged under both versions; it reads neither the
  registry nor findings and its omissions list is unchanged.

## 7. Request `_meta` (both versions, own slice)

Every JSON-RPC request may carry `params._meta`. When present it must be
a JSON object; any other type → `-32602`. It is accepted on
`initialize`, `ping`, `tools/list`, `resources/list`,
`resources/templates/list`, `tools/call` and `resources/read`, ignored,
never echoed, and never changes a payload, so CLI, tool and resource
parity holds with or without it. `tools/call` params are then exactly
`{name, arguments?, _meta?}` and `resources/read` exactly `{uri, _meta?}`;
`arguments` stays a closed domain object and unknown argument fields
still refuse. A `progressToken` inside `_meta` produces no progress
notification: the server emits no notifications at all, as today.

## 8. Bounds, refusals, wire

- Whole-query cap stays 16384 bytes with the existing `unavailable`
  overflow reading; `lane_findings` pages are capped at 8192 bytes by
  the omission sequence in §3.
- Refusal classes: `config`, `arguments`, `cursor`. On the CLI: exit 2,
  one stderr line, empty stdout. Over MCP: `isError: true` with the same
  line. Missing or malformed sources are readings, never refusals.
- Readings and their reasons are fixed strings named in this document;
  no source text is ever copied into a reason.
- Reads need no identity. Control bytes are JSON-escaped. New fields
  returned by the three v2 reads contain valid Unicode scalar sequences:
  source strings that do not UTF-8 encode are nulled under their declared
  text rules. This does not change existing v1 payload handling. Deeply nested or oversized source
  JSON follows the lever's existing unavailable handling and never
  terminates the server.
- The literal guard exemptions stay exactly the two existing lines
  (`ops/conductor/tools/laneconf.py:366–375`); the new code introduces
  no new lane literal.
- This extension adds no numeric-hardening policy. It retains the current
  dev JSON parsing guards and the surviving limits documented in the lever
  (usage-reader huge integers, out-of-range alert cursors, non-finite evidence).

## 9. Coupling proof

Counterparties: the record shape written by
`ops/devlane/dispatch/record.py` (`result.envelope.findings`,
`result.envelope.note`, top-level `job`, `stage`) and the envelope
finding fields in `ops/devlane/task/envelope.py:38`
(`severity where claim reproduce`); the registry shape read by
`ops/devlane/dispatch/launch.py:1081–1187` and `ops/devlane/task/run.py`
(`adapter role snapshot withheld caps prompt deliverable constraints`,
placeholder regex). A change to any re-demands the fixture rows of the
slice that reads it, not every suite. Independent tests plant synthetic
records and registries; no tracked record, the live store, or the real
registry is read by a test.

## 10. Evidence bytes

The PR body reports, per read, the canonical response bytes against the
raw source bytes for one fixed task, produced by commands at the PR's
head: a `lane_findings` first page against `wc -c record.json`; an
all-unchanged `lane_changes` call against the summed record bytes; a
`lane_jobs` card against `wc -c jobs.json`. Bytes, not tokens; what each
response excludes is stated beside the number.
