# Requirement matrix — measured implementation candidate

This matrix preserves every H1–H8, B1–B5 and A1–A2 requirement. All listed implementation responsibilities are implemented and their mapped deterministic assertions passed on the frozen candidate. Authenticated native acceptance and fresh-session independent review remain pending.

Measured source: `xormania/xorchestrate`, candidate `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`, tree `0f4034870f40fb5a1d022c9f35ad0b866236455f`, clean working tree. Base: `30254fd0f184b7e31aa7e0896347429004933ed4` on `integration/hexagonal`. Content SHA-256: `ccba046c2b8caa0a4b94bcaa9be9e4229ce7c3e837db911720cf1ecf2e44aa09`. Exact commands, timestamps and all input identities are in the final manifest and evidence archive. The evidence publication commit on `evidence/behavioral-preservation-b0a00f1` adds only generated evidence/documentation after this measured code commit; PR #5 retains the measured source head.

The complete owner brief remains the requirements input. No subordinate task, fixture, historical role hierarchy, threshold, policy or cancelled contribution revises it. Changes to the owner basis must be explicit and trigger reassessment of affected evidence.

## State and evidence conventions

The durable package is `docs/implementation/evidence/verification-evidence.tar.xz`. Extract it outside the measured checkout; it creates `evidence-data/`. Artifact paths below are relative to that extracted directory. `final-manifest.json` and `artifact-index.json` identify every published artifact. Local collection root was `/workspace/scratch/13fc354c99cb/final-verification-3`.

| Key | Retained evidence and interpretation |
|---|---|
| APP | `commands/application/{command.json,stdout.log,stderr.log}` and `application.xml`: active application tests, with exact assertion identities and outcomes. The lead matched all 89 distinct selectors listed below to executed, passing final-candidate JUnit cases; parameter variants are included. |
| PROTOCOL | `protocol/{report.json,run.json,candidate.json,candidate-after.json,checker.json,bindings.json,criteria.json,configuration.json,fixture.json,cleanup.json}` plus raw application and lifecycle records. The run is the actual active composition and real native adapter implementations against deterministic subprocess protocol peers. It does not provide authenticated vendor evidence. |
| CAL-NATIVE | `native-calibration/<name>/{calibration.json,mutation.json,control.xml,mutant.xml,control-run/*,mutant-run/*}`. Each result must identify its measured candidate, passing control, intended target/diagnostic and unaffected controls. |
| CAL-WORK | `work-calibration/`: exact owner-outcome/acceptance calibration manifest, mutation, control/fault commands and assertion results. Preserve the directory's actual index and classifications. |
| NATIVE | Future local authenticated `--mode native` run with all required cases, installed versions, settings, raw joins and cleanup. **Pending local run.** |
| REVIEW | Future fresh-session section 11 review against the owner brief and exact candidate/checker/input identities. Internal implementation-agent reviews do not satisfy this dimension. **Pending.** |

Final local application result: **588 passed, 10 skipped, 164 subtests passed**. The ten environment-dependent lifecycle tests require a matching PID namespace or tmux; their local skips are explicit, and hosted CI outcomes are recorded separately. All 89 distinct named selectors in this matrix passed locally without skips. Protocol fixture: **46/46 requested cases passed**, cleanup passed, exact candidate unchanged. Seven native/delivery/lifecycle source faults and five acceptance source faults were detected by the intended assertions with passing controls. Static, boundary, compilation, security-lint coverage and optional toolchain checks passed.

Earlier failures are retained under `prior-runs/` and `development/`. The first frozen protocol run aborted before Grok submission because concurrent evidence invalidated a pre-effect authorization snapshot; the runner now reauthorizes only explicitly safe conflicts, bounded to eight attempts with unchanged arguments/key. The next suite caught the same race in a read-only Claude polling helper; it now retries safe reads within its original deadline and propagates unsafe errors. Their original assertions remain. Earlier dirty-snapshot protocol results marked `invalid_candidate` receive no final pass credit. Local historical toolkit failures involve unavailable Unix sockets/PID namespace and are separate from active application results; hosted CI is the broader environment check.

| ID | Implementation | Deterministic verification | Native verification | Independent review |
|---|---|---|---|---|
| H1 | Implemented; source inspected | Passed: APP + PROTOCOL; calibrated relevant faults | Pending local run | Pending |
| H2 | Implemented; source inspected | Passed: APP + PROTOCOL; calibrated relevant faults | Pending local run | Pending |
| H3 | Implemented; source inspected | Passed: APP + PROTOCOL; calibrated relevant faults | Pending local run | Pending |
| H4 | Implemented; source inspected | Passed: APP + PROTOCOL; calibrated relevant faults | Pending local run | Pending |
| H5 | Implemented; source inspected | Passed: mapped APP assertions | Pending for native permissions/provider/lifecycle integration | Pending |
| H6 | Implemented; source inspected | Passed: APP + PROTOCOL; calibrated relevant faults | Pending for actual native dispatch composition | Pending |
| H7 | Implemented; source inspected | Passed: mapped APP assertions | Pending for applicable native recovery and lifecycle cases | Pending |
| H8 | Runner/checker/local package implemented; source inspected | Passed: 46/46 PROTOCOL + APP boundary checks | Pending local run | Pending |
| B1 | Implemented; source inspected | Passed: mapped APP assertions | Not separately required: deterministic requirement preservation/coverage semantics; H8 integration remains pending | Pending |
| B2 | Implemented; source inspected | Passed: mapped APP assertions | Pending where acceptance consumes native evidence; deterministic mechanical evidence is separately measured | Pending |
| B3 | Implemented; source inspected | Passed: mapped APP assertions | Pending; unknown observed fields must stay unknown | Pending |
| B4 | Isolation/classification/calibrations implemented | Passed: 12/12 assertion-specific source faults + classifier controls | No separate authenticated mutation campaign required by brief; native evidence negatives still covered deterministically | Pending |
| B5 | Exact-candidate/criteria/coverage/checker machinery implemented | Passed: mapped APP assertions | Native run binding and actual native coverage pending | Pending |
| A1 | Implemented; source inspected | Passed: mapped APP assertions | No separate vendor test for composability; applicable H5/H8 native integration pending | Pending |
| A2 | Implemented; source inspected | Passed: mapped APP assertions | Applicable native joins pending | Pending |

All selectors below are relative to `tests/hexagonal/`. Omitted parametrization suffixes mean every collected variant, not one selected harness or store. The final JUnit XML is the authority for what actually executed.

## H1 — persistent native contexts and identity

**Preserved source behavior:** Codex, Claude Code and Grok Build retain private startup markers and observed native identities across repeated exchanges. Marker values are absent from subsequent challenge and lifecycle probe prompts.

**Implementation:** `xorchestrate/adapters/sessions.py`, `claude.py`, `grok.py`, `native_gateway.py`; trusted callback composition; raw native identity observations; the composed verification runner and lifecycle probes.

**Named assertions:**

- `test_native_sessions.py::test_private_native_marker_retained_across_exchanges_and_resume`
- `test_native_sessions.py::test_native_callback_persists_trusted_evidence_through_active_composition`
- `test_claude_sessions.py::test_claude_retained_context_callbacks_progress_without_poll_and_native_identity`
- `test_claude_sessions.py::test_claude_application_callback_through_active_composition_and_sqlite`
- `test_verification.py::test_complete_exchange_control_has_correlated_native_and_durable_evidence`

**Evidence:** APP; PROTOCOL/NATIVE `startup-{codex,claude,grok}`, all six `direct-<source>-<target>` cases, retained-context lifecycle cases and `native-tool-audit`.

**Limit:** Deterministic protocol peers establish adapter handling and private-marker mechanics, not native model context retention. Vendor evidence remains local and pending.

## H2 — outstanding-tool delivery, unfinished work and concurrent progress

**Preserved source behavior:** Codex `turn/start.toolOutput`, Claude `notifications/claude/channel`, Grok idle `session/prompt` and active `_x.ai/interject`; submission, activity, native completion and control remain separate. Incoming callbacks progress independently of a controller poll; an outstanding callback does not block unrelated sessions.

**Implementation:** concurrent bounded JSON-RPC/MCP dispatch; adapter-specific delivery paths; application session/control state; the hold provider and gated work fixture in `scripts/verification/`.

**Named assertions:**

- `test_transport_progress.py::test_callback_after_submission_ack_progresses_without_poll`
- `test_transport_progress.py::test_held_callback_allows_native_control_and_nested_request`
- `test_native_sessions.py::test_submission_then_callback_busy_delivery_and_unrelated_progress`
- `test_claude_sessions.py::test_claude_channel_delivery_during_outstanding_callback_and_unrelated_session`
- `test_native_sessions.py::test_unknown_prompt_completion_is_not_idle_and_interjections_are_bounded`
- `test_verification.py::test_successful_transport_does_not_hide_wrong_work_missing_completion_or_unsupported_interjection`

**Evidence:** APP; PROTOCOL/NATIVE `busy-claude-codex`, `busy-grok-claude`, `busy-codex-grok`; `work-codex-claude-{0,1}`, `work-claude-grok-{0,1}`, `work-grok-codex-{0,1}`; separate work batches and completion cases. Native progress calibration belongs in CAL-NATIVE.

**Limit:** Busy delivery means submission during an outstanding hold followed by a verified exchange. It does not establish interruption during token generation. Preserve observed reply-before-work-completion timing as a separate measured fact. Unsupported interjection must fail explicitly.

## H3 — complete durable exchange, distinct claims and adapter observations

**Preserved source behavior:** A complete exchange joins the native challenge callback, durable message/recipient/delivery attempts, session incarnation, submission, private-memory reply, reverse submission and native sender receipt. Queueing, RPC return, receipt, response, turn completion and work acceptance are separate facts.

**Implementation:** `delivery.py`, `exchange.py`, `adapters/delivery.py`, storage records/events and trusted requested/completed callback observations in `composition.py`. The production receipt and separate checker consume durable records. Caller-reported proof cannot create adapter-native observations.

**Named assertions:**

- `test_delivery.py::test_routing_queues_durable_attempt_and_submission_is_not_receipt`
- `test_delivery.py::test_observation_provenance_duplicate_conflict_and_stale_incarnation`
- `test_verification.py::test_production_receipt_requires_durable_native_callback_chain`
- `test_verification.py::test_production_receipt_faults_have_specific_diagnostics`
- `test_verification.py::test_exchange_faults_fail_the_intended_assertion`
- `test_delivery_adversarial.py::test_conflicting_adapter_submission_identity_cannot_become_submitted`

**Evidence:** APP; PROTOCOL/NATIVE every direct/busy/work exchange and `native-tool-audit`; APP receipt negatives and CAL-NATIVE delivery uncertainty faults. Named negative variants cover queue/RPC-only success, wrong/missing memory, stale nonce, wrong identity/attempt/incarnation, absent native hook, wrong dispatch, missing receipt, conflicting call ID and a later unknown retry.

**Limit:** Trusted observations mean observations collected by the configured adapter boundary. This is not cryptographic proof against a malicious configured adapter/host. Communication correctness does not establish work correctness or acceptance.

## H4 — explicit ownership, attachment, detach, resume and recovery

**Preserved source behavior:** Native conversation identity, live connection incarnation and process ownership differ. Operator-owned external hosts survive client detach. Resume requires adapter-issued receipts, source-control and destination-create authorization, and rejects caller-manufactured native bindings.

**Implementation:** application attach/detach/reconnect/resume actions; `adapters/bridge.py` and `xorchestrate/bridge.py` operator host; durable host journal and attachment leases; adapter-specific resume and lifecycle runner.

**Named assertions:**

- `test_external_bridge.py::test_external_detach_does_not_terminate_host_and_reconnect_retains_context`
- `test_external_bridge.py::test_replaced_connection_cannot_send_or_apply_late_completion`
- `test_external_bridge.py::test_bridge_rejects_manufactured_receipts_and_changed_configuration`
- `test_external_bridge.py::test_actual_host_crash_preserves_unknown_applied_effect_without_replay`
- `test_application.py::test_resume_requires_source_control_and_destination_creation`
- `test_application.py::test_resume_requires_verified_adapter_receipt`
- `test_application.py::test_resume_replay_reauthorizes_source_and_destination`
- `test_lifecycle_runner.py::test_all_lifecycle_cases_use_real_application_and_external_processes`

**Evidence:** APP; PROTOCOL/NATIVE all `lifecycle-<peer>-<operation>` where peers are codex/claude/grok and operations are resume/reconnect/attach/detach/replace (15 distinct cases); CAL-NATIVE external-detach fault.

**Limit:** Attachment is through a suitable independently owned bridge endpoint, not arbitrary takeover of an unrelated running TUI or PID. This is the implemented attachment mechanism, not a scope exemption. Actual local harness compatibility and retained context still need native measurement. FIFO is used where this environment denies UNIX sockets; local UNIX transport and FIFO are explicit choices.

## H5 — independently configurable providers, permissions and lifecycle

**Preserved source behavior:** MCP/CLI providers, native tool permissions, application-mediated authorization, lifecycle ownership and scheduling are independently replaceable choices. Trusted configuration precedence is intentional and visible.

**Implementation:** capability providers and contract discovery; session/native permission handlers for each harness; independent driver, provider, ownership and delivery policy composition.

**Named assertions:**

- `test_transports.py::TransportTests::test_external_capability_execution_and_replay_work_across_cli_and_mcp`
- `test_capabilities.py::CapabilityTests::test_mcp_negotiation_pagination_and_dynamic_new_tool`
- `test_capabilities.py::CapabilityTests::test_cli_manifest_and_dynamic_discovery_share_call_contract`
- `test_native_sessions.py::test_codex_native_permission_response_is_explicit_and_independent`
- `test_native_sessions.py::test_grok_native_permissions_separate_from_application_callback`
- `test_claude_sessions.py::test_claude_native_permissions_independent_of_application_tools`
- `test_delivery.py::test_policy_substitution_scheduling_selection_and_authorization`

**Evidence:** APP; actual native local selected permissions/configuration and host ownership in NATIVE; configured provider and permission positive/negative tests in APP.

**Limit:** The suite's chosen allowlist is fixture configuration, not a mandatory deployment policy. No claim is made that every vendor permission or model combination was measured.

## H6 — routing to durable delivery and replaceable boundaries

**Preserved source behavior:** Routing produces durable native work; routing, session selection, scheduling, retry, receipt/acceptance and storage remain ports. Routing decisions and their actor/relation inputs are retained as a resolved-at-enqueue snapshot; later delivery follows its fixed recipients. Native effects and local commits have an explicit uncertainty boundary.

**Implementation:** application message transaction, delivery records/attempts/events, selection validation and adapter submission; replaceable policies/storage in composition; current-context checks before effects and receipt commits.

**Named assertions:**

- `test_delivery.py::test_policy_substitution_scheduling_selection_and_authorization`
- `test_delivery.py::test_latest_session_policy_is_independent_of_store_key_order`
- `test_delivery_adversarial.py::test_delivery_selection_is_revalidated_before_native_effect`
- `test_delivery_adversarial.py::test_receipt_commit_rechecks_authorization_context_after_policy_evaluation`
- `test_storage.py::MemoryStoreTests::test_exception_rolls_back_record_and_event_bundle`
- `test_storage.py::SQLiteStoreTests::test_exception_rolls_back_record_and_event_bundle`
- `test_storage.py::SQLiteStoreTests::test_multiple_processes_commit_atomic_counter_and_event_updates`
- `test_boundaries.py::DependencyBoundaryTests::test_application_and_its_helpers_do_not_depend_on_infrastructure`

**Evidence:** APP; PROTOCOL/NATIVE actual active routing → queued recipient → attempt → adapter → receipt paths for all 15 exchanges. Storage substitutions and real SQLite process checks are distinct from synthetic session tests.

**Limit:** Each local enqueue or attempt-reservation transaction commits its records and events atomically. It cannot make a native remote effect atomic with a local commit; H7 preserves that uncertain boundary.

## H7 — uncertainty, replay and stale fencing

**Preserved source behavior:** Lost responses, crashes, duplicate calls and replaced connections never imply an unqualified fresh effect. Unknown outcomes survive reconstruction. Observation/control remain available. Stale native observations and completions cannot reactivate current state or become current proof.

**Implementation:** durable operation reservations, delivery attempt/incarnation fencing, native-call replay records, bounded transport admission, explicit retained-result reconciliation, bridge journal/recovery, current authorization on replay. Only explicitly pre-effect retry-safe conflicts may reauthorize; committed/unknown effects are never blindly retried.

**Named assertions:**

- `test_delivery.py::test_lost_submission_stays_unknown_across_reconstructed_application`
- `test_delivery.py::test_observation_and_control_available_without_resolving_unknown_effect`
- `test_delivery.py::test_concurrent_dispatch_cannot_duplicate_native_effect`
- `test_delivery.py::test_reconnect_preserves_unknown_source_effect_while_enabling_observation`
- `test_delivery_adversarial.py::test_native_call_replay_cannot_repeat_non_idempotent_application_action`
- `test_delivery_adversarial.py::test_native_retry_reauthorizes_changed_guardrail_and_stays_denied`
- `test_delivery_adversarial.py::test_post_enqueue_dispatch_conflict_never_repeats_committed_message`
- `test_delivery_adversarial.py::test_native_resolution_cannot_take_a_caller_supplied_result`
- `test_transport_progress.py::test_conflicting_call_or_unmatched_response_closes_with_unknown_outcome`
- `test_external_bridge.py::test_actual_host_crash_preserves_unknown_applied_effect_without_replay`

**Evidence:** APP; lifecycle stale-handle cases in PROTOCOL/NATIVE; delivery uncertainty and progress controls/faults in CAL-NATIVE.

**Limit:** Duplicate rejection and durable replay guards are not a general end-to-end exactly-once claim. Preserved unknown outcomes require explicit resolution. Runner regressions `test_composed_runner_reauthorizes_after_callback_without_duplicating_submission` and `test_composed_runner_preserves_unqualified_conflict_and_bounds_safe_retries` passed, including one actual application submission and bounded/unsafe controls.

## H8 — active composed deterministic and native acceptance

**Preserved source behavior:** The PoC exchange, work, timing and audit predicates are adapted to the actual active application, routing, SQLite storage, native adapters and callback evidence. The standalone PoC and imported runtime cannot satisfy this requirement.

**Implementation:** `scripts/verification/{multi_harness,checker,normalize,fixture,lifecycle,capture,calibration}.py`, original build-log fixture, native config example and `docs/behavioral-verification.md`. Product imports are selected from the measured candidate before loading; verifier code may come from a separately reviewed checkout.

**Named assertions:**

- `test_verification.py::test_native_inventory_preserves_complete_requested_counts`
- `test_verification.py::test_checker_loads_selected_candidate_instead_of_its_own_checkout`
- `test_verification.py::test_lifecycle_summary_without_raw_records_cannot_manufacture_a_pass`
- `test_verification.py::test_successful_transport_does_not_hide_wrong_work_missing_completion_or_unsupported_interjection`
- `test_boundaries.py::DependencyBoundaryTests::test_active_package_does_not_import_the_historical_runtime`

**Evidence:** APP and the complete PROTOCOL/NATIVE inventory: 3 private-memory startup + 6 directed + 3 busy + 6 unfinished-work exchanges + 9 work batches + 3 native completions + 15 lifecycle + 1 all-actual-call audit = **46 requested cases**. Requested, discovered and executed coverage stay separate.

**Limit:** `fixture` is a synthetic session-port composition; `protocol-fixture` uses real adapters with deterministic subprocess peers; `native` alone provides vendor evidence. No fixed historical 69-call requirement is imposed. Missing coverage remains incomplete. Failed composed attempts remain retained; the final exact-candidate run passed all required deterministic cases.

## B1 — full and revisable owner outcomes

**Preserved source behavior:** The full owner objective and required A+B outcomes survive narrower child plans and cancelled B contributions. Explicit owner revisions change current applicability while preserving history; changes during/after assessment invalidate stale applicability.

**Implementation:** durable requirement revision records, outcome definitions and explicit owner revisions in `acceptance.py`/application actions, assessment input snapshots and current decision basis checks.

**Named assertions:**

- `test_work_acceptance.py::test_full_owner_objective_survives_narrowing_and_cancellation`
- `test_work_acceptance.py::test_explicit_owner_revision_changes_current_basis_without_erasing_history`
- `test_work_acceptance.py::test_requirements_changed_during_assessment_make_it_historical`
- `test_work_acceptance.py::test_changed_basis_during_policy_evaluation_cannot_commit_acceptance`
- `test_work_acceptance.py::test_scope_mismatch_and_stale_requirement_revisions_are_rejected`

**Evidence:** APP; targeted owner-outcome/stale-basis faults in CAL-WORK.

**Limit:** Preserving the full assessment input does not prove a model recognizes arbitrary missing free-text objectives. Explicit deterministic outcome/check coverage supplies the mechanical guarantee; no model-understanding claim is made.

## B2 — sufficient, current and correctly bound acceptance evidence

**Preserved source behavior:** Claims, status changes, favorable assessment and unrelated passes cannot hide missing outcomes, failed required checks, running/unknown measurements or stale/misbound evidence. Measurement, assessment, authorization and acceptance remain different records and responsibilities.

**Implementation:** `acceptance.py`, `adapters/acceptance.py`, trusted provider measurements and artifact identity observations, criterion/outcome/candidate/configuration bindings, currentness checks on assessment/decision reads and replay.

**Named assertions:**

- `test_work_acceptance.py::test_claim_and_done_status_do_not_create_acceptance`
- `test_work_acceptance.py::test_insufficient_or_misbound_measurement_cannot_pass`
- `test_work_acceptance.py::test_required_failure_cannot_be_erased_by_unrelated_success_or_high_assessment`
- `test_work_acceptance.py::test_running_measurement_blocks_earlier_pass_and_observation_progresses`
- `test_work_acceptance.py::test_uncertain_measurement_cannot_be_masked_by_new_pass`
- `test_work_acceptance.py::test_candidate_change_invalidates_existing_acceptance_and_old_evidence`
- `test_work_acceptance.py::test_composed_cli_measurement_observes_file_identity_instead_of_echoing_request`
- `test_work_acceptance.py::test_native_replay_cannot_present_historical_acceptance_as_current`
- `test_work_acceptance.py::test_tool_evaluator_binds_its_provider_configuration_across_composed_rebuild`

**Evidence:** APP; CAL-WORK per-fault control/mutant evidence; delivery-native evidence applicability in PROTOCOL/NATIVE where acceptance relies on it.

**Limit:** An evidence-backed policy can only establish the configured explicit outcomes/checks. A transport success or score is not universal task correctness. Historical decisions remain inspectable but must not be presented as current acceptance.

## B3 — requested, resolved and observed configuration

**Preserved source behavior:** Nondefault selection persists across applicable lifecycle transitions. Material same-name changes require explicit handling. Configured fallback is visible and authorized; absent/unavailable selections never silently substitute. Trusted configuration precedence is retained; unobserved native values remain unknown.

**Implementation:** native settings snapshots/configuration identities, requested/resolved/native-observed records, adapter resume policies, effective-driver authorization and explicit fallback records; acceptance evaluator/provider dependency fingerprints.

**Named assertions:**

- `test_native_sessions.py::test_settings_preserve_requested_resolved_observed_and_require_changed_config_policy`
- `test_claude_sessions.py::test_claude_requested_resolved_and_observed_settings_and_resume`
- `test_claude_sessions.py::test_claude_changed_configuration_requires_explicit_policy`
- `test_delivery.py::test_explicit_fallback_records_requested_and_resolved_driver`
- `test_delivery_adversarial.py::test_effective_fallback_driver_must_be_authorized_before_launch`
- `test_sessions.py::SessionTests::test_native_parameter_delegation_is_bounded_and_configuration_wins`
- `test_native_sessions.py::test_operational_limits_do_not_change_material_selection_identity`
- `test_work_acceptance.py::test_same_name_provider_configuration_change_invalidates_measurements_and_decisions`
- `test_work_acceptance.py::test_persisted_assessment_rejects_same_name_evaluator_reconfiguration_after_rebuild`

**Evidence:** APP; selection/configuration fault records; PROTOCOL/NATIVE actual requested/resolved/observed snapshots, version commands and lifecycle observations.

**Limit:** A requested model/effort is not an independently observed setting. Grok command arguments are configurable; arbitrary ACP session fields are not assumed to configure the vendor. Unknown Claude effort or vendor setting mismatches must remain explicit; successful communication alone cannot establish configuration fidelity.

## B4 — assertion-specific critical fault calibration

**Preserved source behavior:** A passing baseline plus the intended failed assertion/diagnostic and passing unaffected controls is required for detection. An arbitrary nonzero exit earns no credit.

**Implementation:** isolated exact-candidate control/mutant snapshots; one-match source mutation; retained raw/JUnit commands; explicit `detected`, `escaped`, `setup_error`, `unrelated_failure`, `skipped`, `not_exercised` and invalid-control classifications (use the exact retained schema labels in the final index).

**Named assertions:**

- `test_verification.py::test_calibrator_distinguishes_target_detection_from_environment_failure`
- `test_verification.py::test_calibration_runs_isolated_real_control_and_mutation`
- `test_work_calibration.py::test_calibrator_requires_exact_assertion_and_passing_controls`
- `test_work_calibration.py::test_missing_dependency_is_setup_error_and_never_fault_detection`
- `test_work_calibration.py::test_absent_requested_assertion_is_missing_coverage`

**Evidence:** APP and complete CAL-NATIVE/CAL-WORK index, not just the reported count. Critical targets include autonomous callback progress, native observation/hook requirement, persistent selection/identity, receipt completeness, unknown delivery preservation, external detach ownership, owner-outcome retention and stale/unsupported acceptance.

**Limit:** Faults calibrate named assertions, not all possible failures. Missing dependencies, unsupported environment operations, timeouts, skipped coverage, wrong diagnostic and unrelated failure remain distinct. Rebind or rerun affected calibrations after source/checker/fixture changes.

## B5 — independently checkable exact candidate and requested coverage

**Preserved source behavior:** A separately selected checker and owner basis assess the candidate actually measured, including dirty/untracked inputs. Required coverage is not inferred from discovered cases. Fabricated summaries, missing cases and another candidate's evidence cannot pass. Owner revisions remain permitted.

**Implementation:** exact content capture, recoverable snapshot/patch, checker/criteria/fixture/configuration/owner-basis hashes, before/after candidate identity, product import provenance, independent raw-record normalization and explicit required/discovered/executed inventories.

**Named assertions:**

- `test_verification.py::test_candidate_capture_includes_dirty_and_untracked_inputs`
- `test_verification.py::test_requested_coverage_and_exact_bindings_override_fabricated_summary`
- `test_verification.py::test_checker_loads_selected_candidate_instead_of_its_own_checkout`
- `test_verification.py::test_lifecycle_summary_without_raw_records_cannot_manufacture_a_pass`
- `test_work_calibration.py::test_absent_requested_assertion_is_missing_coverage`
- `test_work_acceptance.py::test_explicit_owner_revision_changes_current_basis_without_erasing_history`

**Evidence:** APP; PROTOCOL/NATIVE candidate/checker/criteria/configuration/fixture manifests and run records; independently retained full owner brief; candidate recoverable snapshot; subsequent REVIEW.

**Limit:** Hashes prove identity/mismatch, not checker correctness, truthful collection or independence. The generated default criteria and candidate-authored checker require review against the owner brief. Separate checkout selection enables independent checking; it does not turn the implementation author's own run into the fresh review.

## A1 — composable acceptance and policy

**Preserved interpretation:** Acceptance capability is implemented without forcing a fixed role hierarchy, Jev threshold or universal approval workflow. Deployments select acceptance, evaluation, authorization and coordination independently. Simple messaging needs no unrelated organizational model.

**Implementation:** acceptance/evaluation/authorization/delivery policy ports and composition factories; explicit optional work-requirement/assessment policy.

**Named assertions:**

- `test_work_acceptance.py::test_acceptance_policy_substitutes_without_weakening_required_proof`
- `test_work_acceptance.py::test_required_assessment_policy_and_authorization_remain_independent`
- `test_work_acceptance.py::test_no_requirements_or_acceptance_policy_required_for_simple_messaging`
- `test_composition.py::CompositionTests::test_installed_factory_extends_capabilities_without_source_changes`
- `test_delivery.py::test_policy_substitution_scheduling_selection_and_authorization`

**Evidence:** APP; active boundary assertions and configuration example. Historical policies remain source material unless capability or subsequent owner instruction calls for them.

**Limit:** Composability is not permission to omit acceptance capability or weaken the selected policy's required proof. Configuring a missing native mechanism cannot satisfy H1–H8.

## A2 — evidence bound at the applicable boundary

**Preserved interpretation:** Delivery binds message/recipient/attempt/incarnation/native callbacks. Acceptance binds applicable owner requirements, outcomes, measured candidate/criteria/configuration and supporting observations. Durable links connect those facts only when the consuming decision requires the relation.

**Implementation:** durable delivery snapshots and native-call IDs; requirement/measurement/assessment/acceptance dependencies; no universal every-identity-on-every-record envelope. Independent verifier reconstructs the links rather than trusting summaries.

**Named assertions:**

- `test_verification.py::test_production_receipt_faults_have_specific_diagnostics`
- `test_verification.py::test_lifecycle_summary_without_raw_records_cannot_manufacture_a_pass`
- `test_delivery_adversarial.py::test_native_resolution_rejects_misbound_completion`
- `test_work_acceptance.py::test_insufficient_or_misbound_measurement_cannot_pass`
- `test_work_acceptance.py::test_unrelated_provider_change_does_not_invalidate_current_measurements`
- `test_work_acceptance.py::test_unrelated_evaluator_change_does_not_invalidate_selected_assessment`

**Evidence:** APP; PROTOCOL/NATIVE raw durable joins; CAL-NATIVE/CAL-WORK misbinding faults.

**Limit:** Applicability is dependency-specific. Unrelated configuration must not invalidate an independent decision, while a consumed provider/evaluator/configuration change must. Missing applicable binding cannot be excused by avoiding a universal schema.

## Reproduction

Use commands from the retained `command.json` records as the exact executed commands, including their working directory/environment/start/end/status. The following are the intended entry points, not a substitute for those records:

```sh
python -m pip install -e '.[test]'
python -m pytest -q tests/hexagonal --junitxml=/absolute/evidence/application.xml
python -m ruff check xorchestrate tests devtools .github scripts
python -m scripts.verification criteria --brief /outside/owner-brief.md --out /outside/criteria.json
python -m scripts.verification.multi_harness --mode protocol-fixture --brief /outside/owner-brief.md --criteria /outside/criteria.json --out /outside/new-protocol-run --timeout 60
```

Native and independent reproduction, exact prerequisites/version inputs, ownership-aware cleanup and redaction provenance are documented in `docs/behavioral-verification.md`; `scripts/verification/native-config.example.json` is the configurable starting point. The native command uses `--mode native --config /outside/native-config.json` and a new private output directory. The independent runner additionally uses `--candidate /absolute/selected-candidate` from a separately reviewed checker checkout.

Exact candidate, original owner basis, checksum inventory, complete commands/logs/JUnit, redaction provenance, failed attempts and calibrated mutations are retained in the evidence package. All applicable native scenarios remain pending. Full behavioral acceptance is not claimed until required native coverage exists and independent findings are resolved or explicitly disposed of by the owner.
