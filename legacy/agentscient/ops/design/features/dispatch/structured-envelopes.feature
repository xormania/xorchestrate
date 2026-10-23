Feature: each harness emits the envelope by construction

  The launcher asks every harness for the nine-key envelope up front
  (D-ENV-1). Claude: --output-format json --json-schema. Codex: --json -o.
  Grok: --output-format plain and no --json-schema — measured 2026-08-29,
  record 20260829T234838Z-review-grok-66ccb2 (grok 1.0.5): --json-schema
  (which implies --output-format json) returned a schema-valid empty
  envelope after one 213-token turn and ended. Replay fixture:
  ops/devlane/fixtures/envelopes/grok-json-schema-short-circuit-66ccb2.raw.out.
  Per-harness first source, then the last-JSON-object scan as fallback so
  today's raw.out files still parse. On grok's plain stdout (D-ENV-2):
  full validation against ENVELOPE_SCHEMA_JSON (nested types included)
  decides what counts as an envelope. The envelope is the LAST fully-valid
  object. A trailing object that is nine-key-shaped but fails nested
  validation must lose to an earlier valid envelope. Two fully-valid
  objects: the last wins, because the job prompt makes the envelope the
  agent's final output. Fenced ```json envelopes on grok's path are a
  covered scenario (D-ENV-3). Wrapper usage of {} or all zeros never
  replaces a U5 spend value and never yields {unresolved} when U5
  resolved; when both wrapper and U5 exist the merge is total arithmetic
  (input, cached, output, total all pinned). Scenarios are U10 of
  ops/docs/scratch/harness-research.md §4 / §2 feature ending / §6,
  amended by reviews 86dd91 and f3ca84 and skeptic 2fd473.

  Background: a worktree of a lineage branch, WF_AGENT set, fake CLIs on PATH

  Scenario: claude fresh argv asks for json output and the envelope schema
    Given a claude launch
    When launch.py runs
    Then the child argv carries --output-format json and --json-schema
    And the schema value is the exported ENVELOPE_SCHEMA

  Scenario: claude resume argv keeps json output and the envelope schema
    Given a closed claude job
    When resume runs
    Then the child argv still carries --output-format json and --json-schema

  Scenario: grok keeps plain output — `--json-schema` short-circuits the loop
    Given a grok launch
    When launch.py runs
    Then the child argv carries --output-format plain
    And the child argv does not carry --json-schema

  Scenario: grok resume argv keeps plain output and no schema
    Given a closed grok job
    When resume runs
    Then the child argv still carries --output-format plain
    And the child argv does not carry --json-schema
    And the child argv still carries --model
    And the child argv still carries --prompt-file

  Scenario: the launcher's scan accepts the captured bytes — and that is the problem the argv change answers
    Given stdout is the verbatim raw.out of 20260829T234838Z-review-grok-66ccb2
    When the reader scans it
    Then the nested structuredOutput is a schema-valid envelope with empty findings
    And that is why grok must not be launched with --json-schema

  Scenario: codex fresh argv asks for json events and a last-message file
    Given a codex launch
    When launch.py runs
    Then the child argv carries --json and -o <job>/out/last-message.json

  Scenario: codex resume argv keeps json events and the last-message file
    Given a closed codex job
    When resume runs
    Then the child argv still carries --json and -o <job>/out/last-message.json

  Scenario: the child reads the --json-schema value from its own argv
    Given a grok or claude launch
    When the child starts
    Then it opens --json-schema from its argv under the snapshot or job dir
    And the bytes match ENVELOPE_SCHEMA

  Scenario: claude argv schema is the exported envelope schema
    Given a claude launch
    Then --json-schema is envelope.ENVELOPE_SCHEMA, not a grok-only copy

  Scenario: a claude wrapper with structured_output is unwrapped as the envelope
    Given stdout is a type=result wrapper whose structured_output is the nine keys
    When the job closes
    Then result.envelope is that structured_output, status ok

  Scenario: a claude wrapper with the envelope only in result text is unwrapped
    Given stdout is a type=result wrapper with no structured_output
    And result is a JSON string of the nine keys
    When the job closes
    Then result.envelope is the parsed result string, status ok

  Scenario: a claude wrapper with no envelope is claude-result subtype
    Given stdout is a type=result wrapper with is_error true, subtype error_max_turns
    And neither structured_output nor a parseable result envelope is present
    When the job closes
    Then envelope.status is invalid
    And result.cause.reason is claude-result:error_max_turns

  Scenario: a claude wrapper with is_error true is claude-result even when structured_output is present
    Given stdout is a type=result wrapper with is_error true and subtype S
    And structured_output is a nine-key envelope the model wrote before the wall
    When the job closes
    Then envelope.status is invalid
    And result.cause.reason is claude-result:S

  Scenario: a grok trailing nested-invalid object loses to an earlier valid envelope
    Given grok stdout is a fully-valid nine-key envelope
    And a later nine-key object that fails nested ENVELOPE_SCHEMA types trails it
    When the job closes
    Then result.envelope is the earlier valid object, status ok
    # D-ENV-2: full validation against ENVELOPE_SCHEMA_JSON (nested types
    # included) decides what counts as an envelope. On grok's plain stdout
    # the envelope is the LAST fully-valid object. A trailing object that
    # is nine-key-shaped but fails nested validation must lose to an
    # earlier valid envelope.

  Scenario: two fully-valid grok stdout objects - the last wins
    Given grok stdout is two fully-valid nine-key envelopes
    When the job closes
    Then result.envelope is the later object, status ok
    # D-ENV-2 stated limit: two fully-valid objects, the last wins,
    # because the job prompt makes the envelope the agent's final output.

  Scenario: a grok fenced json envelope on plain stdout is the envelope
    Given grok stdout is narration then a ```json fence holding a
      pretty-printed allowed envelope
    When the job closes
    Then the fenced object is the envelope
    And the close is not envelope-parse
    # D-ENV-3: fenced ```json envelopes on grok's path are a covered
    # scenario. A line- or NDJSON-oriented scan must not drop it.

  Scenario: a legal envelope with a null verdict is not schema-invalid
    Given grok stdout is an allowed envelope with status invalid and verdict null
    And commit is absent
    When the job closes
    Then result.cause.reason is not schema-invalid

  Scenario: a legal envelope with optional commit is not schema-invalid
    Given grok stdout is an allowed nine-key envelope plus commit
    When the job closes
    Then result.envelope keeps commit and status is ok

  Scenario: a grok stdout object that fails the schema is schema-invalid
    Given grok stdout is one JSON object with an extra top-level key
    When the job closes
    Then result.cause.reason is schema-invalid
    And envelope.status is invalid

  Scenario: a nested wrong type closes schema-invalid
    Given grok stdout is a nine-key object with a nested value that fails ENVELOPE_SCHEMA
    When the job closes
    Then envelope.status is invalid
    And result.cause.reason is schema-invalid

  Scenario: status invalid with verdict approve is not an ok envelope
    Given grok stdout is a nine-key object with status invalid and verdict approve
    When the job closes
    Then envelope.status is not ok
    And envelope.verdict is not approve

  Scenario: a grok narration-then-object raw.out still parses via the fallback scan
    Given stdout is a constructed grok shape: narration immediately followed by an allowed object
    When the job closes
    Then the object is the envelope and the close is not envelope-parse

  Scenario: a grok fallback-scan object that fails the schema is schema-invalid
    Given stdout is narration then an object with an extra top-level key
    When the job closes
    Then result.cause.reason is schema-invalid

  Scenario: a captured grok S2 raw.out recovers the object instead of envelope-parse
    Given stdout is the verbatim raw.out of 20260828T222028Z-tests-grok-13f5f2
    When the job closes
    Then the object is the envelope
    And the close is not envelope-parse

  Scenario: a codex last-message file is the first envelope source
    Given --json -o names <job>/out/last-message.json holding the nine keys
    And stdout is NDJSON events with no envelope object
    When the job closes
    Then result.envelope is the file's object, status ok

  Scenario: a codex last-message file wins over a disagreeing agent_message
    Given the -o file holds envelope A
    And stdout NDJSON ends with an agent_message holding envelope B
    When the job closes
    Then result.envelope is A

  Scenario: a codex last agent_message is used when the file is absent
    Given the -o file is absent
    And stdout NDJSON ends with an agent_message whose text is the nine keys
    When the job closes
    Then result.envelope is that message, status ok

  Scenario: a missing codex last-message file with nothing else is no-last-message
    Given the -o file is absent
    And stdout NDJSON has no agent_message envelope and no fallback object
    When the job closes
    Then result.cause.reason is no-last-message

  Scenario: a codex stdout last-JSON-object is used when -o and agent_message both fail
    Given the -o file is absent
    And stdout NDJSON has no agent_message envelope
    And stdout carries a bare nine-key object
    When the job closes
    Then result.envelope is that object, status ok
    And result.cause.reason is not no-last-message

  Scenario: a resumed codex attempt clears the previous last-message file
    Given attempt 1 wrote out/last-message.json
    When resume starts attempt 2
    Then the file is absent before the child runs
    And result.envelope is attempt 2's

  Scenario: a codex last-message file is written on a non-zero exit
    Given WRAPPER=codex writes the -o file and TASK_LAUNCH_EXIT=1
    When the job closes
    Then the file exists
    And result.cause.reason starts with harness-cli:
    And the file is still the envelope source

  Scenario: envelope-parse remains only when nothing at all parsed
    Given stdout is prose with no JSON object
    When the job closes
    Then result.cause.reason names envelope-parse
    And envelope.status is invalid

  Scenario: the launcher overwrites stamp.ref on an unwrapped envelope
    Given a claude structured_output whose stamp.ref is harness-placeholder
    When the job closes
    Then result.envelope.stamp.ref is the snapshot ref_sha

  Scenario: claude wrapper usage is session.spend
    Given a claude type=result wrapper with usage and total_cost_usd
    And no session stream
    When the job closes
    Then session.spend is {input, output, cost_usd, source: result.usage}

  Scenario: claude-result still records session.spend from the wrapper
    Given a claude wrapper with is_error true and no envelope
    When the job closes
    Then cause is claude-result:<subtype>
    And session.spend is still read from result.usage

  Scenario: empty wrapper usage does not overwrite session.spend with zeros
    Given a claude type=result wrapper whose usage is {} or all-zero
    And session.spend already holds a U5 value
    When the job closes
    Then session.spend is still the U5 value
    And session.spend is not {unresolved}
    And session.spend is not {source: result.usage} with no tokens

  Scenario: wrapper usage that names cache tokens keeps cached and total
    Given a claude type=result wrapper with cache_read_input_tokens and cache_creation_input_tokens
    When the job closes
    Then session.spend carries cached and total, not only input and output

  Scenario: incomplete wrapper usage does not drop the store cached tally
    Given a claude type=result wrapper whose usage names input and output only
    And session.spend already holds a U5 cached tally
    When the job closes
    Then session.spend.cached is still the U5 value
    And session.spend.input is the wrapper input
    And session.spend.output is the wrapper output
    And session.spend.total equals input plus cached plus output
    # D-ENV-3: when both wrapper and U5 exist the merge is total
    # arithmetic (input, cached, output, total all pinned), not one key.

  Scenario: harness.argv is persisted in the launched record
    Given a grok launch whose child is still running
    Then the on-disk record has status launched
    And harness.argv is the child argv, not []

  Scenario: a non-zero exit keeps the harness note and names the exit
    Given a harness that wrote a legal envelope then exited non-zero
    When the job closes
    Then envelope.note still contains the harness note
    And envelope.note names the exit

  Scenario: a committed record does not carry private envelope-scan keys
    Given a grok stdout object that fails the schema and a non-zero exit
    When the job closes
    Then the on-disk record has no _envelope_cause key

  Scenario: ENVELOPE_SCHEMA is exported from envelope.py with the nine keys
    Given the task envelope module
    Then ENVELOPE_SCHEMA is a JSON Schema object
    And required is the nine keys job status verdict counts findings artifacts spend stamp note
    And commit is optional
    And verdict is nullable

  Scenario: ENVELOPE_SCHEMA pins the allowed property types
    Given ENVELOPE_SCHEMA
    Then counts findings artifacts spend stamp status verdict job note and commit have types
    And stamp has ref
    And status is ok|invalid|tripped
    And verdict is approve|changes|null

  Scenario: a complete envelope is a positive schema example
    Given a nine-key envelope including optional commit
    Then ENVELOPE_SCHEMA accepts it
    And a null-verdict invalid envelope without commit is also accepted

  Scenario: an extra key is a negative schema example
    Given a nine-key envelope plus an unknown top-level key
    Then ENVELOPE_SCHEMA rejects it

  Scenario: a missing key is a negative schema example
    Given a nine-key envelope with note removed
    Then ENVELOPE_SCHEMA rejects it
