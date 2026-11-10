# Measuring Jev architecture diagnostics

The evaluator compares the existing deterministic architecture checker with Jev
on the same small, frozen source corpus. It measures whether a diagnostic identifies
an independently specified source fault that the deterministic checker missed.
A completed request or a concern label alone earns no confirmed finding.

Run the keyless reporting fixture from a checkout:

```bash
python -m scripts.architecture.effectiveness --output .xorchestrate/jev-effectiveness-fixture.json
```

This mode executes the real architecture checker and source-context builder, then
supplies explicitly scripted answers. It includes a deliberate false alarm and
an abstention to exercise reporting. Its `scripted_fixture_only` result does not
measure Jev quality, availability, latency or usage; it requires no credentials
and makes no network requests.

For an optional live measurement, provide the existing configured credential in
the environment and run:

```bash
python -m scripts.architecture.effectiveness --mode live \
  --jev-config contracts/hexagonal-jev.json \
  --max-seconds 120 \
  --output .xorchestrate/jev-effectiveness-live.json
```

`JEV_ENDPOINT` and `JEV_MODEL` override the deployment configuration as in ordinary
diagnostics. Transport limits and source/candidate budgets come from the supplied
configuration. Corpus source selections replace deployment-specific paths and Git
references. No live provider job is added to required CI. Unavailable credentials,
provider errors and incomplete evidence remain separate outcomes; the command's
successful exit means the report was produced, not that Jev met an adoption bar.
The default corpus asks one check about one function per case, for six calls.
The overall budget defaults to 120 seconds. Each case's transport timeout is
capped by the remaining budget divided by its maximum task count; no further
provider work starts after expiry, and remaining cases report `budget_exhausted`.
This is an application deadline rather than a hard operating-system process limit.

The report retains the exact corpus hash, source bytes and hashes, deterministic
reports, bounded provider input packets, questions and returned assessments,
configured/observed provider identity where exposed, usage where supplied, wall
clock duration and the working candidate identity. Credentials are read only by
the existing adapter and are not written to the report. Reviewer minutes and
monetary costs remain `null` because this command does not measure them.

## Corpus and interpretation

The versioned [corpus](../tests/fixtures/architecture-effectiveness.json) has six
cases in three paired dependency groups:

| Group | Healthy control | Fault | Deterministic expectation |
| --- | --- | --- | --- |
| Concrete import | Injected storage operation | Core imports SQLite | Existing external-dependency rule detects the fault |
| Storage payload | Neutral domain record passed to port | Core constructs SQL and table/column encoding | Import/call/literal rules pass; explicit semantic responsibility is violated |
| Transport envelope | Injected method names `open` and `execute` | Core constructs a JSON-RPC wire envelope | Import/call/literal rules pass; explicit semantic responsibility is violated |

Semantic requirements are supplied in the architecture profile to both arms.
No model receives the oracle, healthy/fault label, group ID or scripted answer.
The baseline is the complete existing checker with its configured rules, not a
lexical comparison designed to lose. Its semantic misses identify the boundary
of those rules; they are not defects in structural enforcement.

Confirmation requires a `concern` bound to a client-generated candidate, source
span, question and contract, and a match against the independent fault oracle's
source span and check. Source grounding is necessary but does not establish
general diagnostic truth. Repeated labels on the same case count as one confirmed
case. The report also retains unsupported allegations, including extra concerns
on faulty cases. Incremental confirmation requires the deterministic arm to have
no confirmed finding for that case.

Both arms must have the same complete source evidence for a comparison to qualify.
The runner verifies exact source text and line coverage rather than trusting a
declared file count. A budget that omits or truncates source makes that case
ineligible and its Jev comparison unknown. It cannot create an apparent win.

An abstention is unknown, not a false clear or decisive miss. `no_concern` on a
fault is a false clear; an unsupported concern on a fault is a miss with an
unsupported allegation. A concern on a healthy control is a false alarm. Counts
retain the full healthy/fault population, unknowns and comparison eligibility;
there is no pass-rate denominator that silently drops unavailable cases.

These are synthetic examples from three mechanisms, not six independent incidents
or a production benchmark. All cases are visible and revisable, so they are not
a blind holdout. Fixture results test the runner; a live run measures one pinned
provider/configuration on this corpus. Neither proves reduced engineering effort,
new operational defect discovery or production benefit. A useful next sample can
add independently labeled real incidents with healthy controls and record actual
review effort without changing these distinctions.
