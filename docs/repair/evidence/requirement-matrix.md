# H/B/A evidence map

Candidate `ef7567516520543970af55b6f19534cb66eda357`; tree `a0813aae760e6e5bb6245693bd92b7e7c321348b`. All 89 original selectors remain mapped. Deterministic evidence, authenticated native coverage and fresh-session review are separate states.

| Requirement | Repair findings | Implementation | Deterministic | Native |
|---|---|---|---|---|
| H1 | F04, F06 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| H2 | F06, F10 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| H3 | F02, F06, F08 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| H4 | F02, F03, F04 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| H5 | F01, F02, F04 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| H6 | F03 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| H7 | F02, F03, F04, F06, F09 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| H8 | F06, F07, F08, F09, F10 | Implemented | Mapped assertions passed | Pending authenticated native execution of applicable required cases. |
| B1 | F04 | Implemented | Mapped assertions passed | No separate vendor test for deterministic owner-outcome preservation; H8 native integration remains pending. |
| B2 | F04, F05 | Implemented | Mapped assertions passed | Pending where acceptance consumes native evidence; deterministic mechanical/assessment currentness separately measured. |
| B3 |  | Implemented | Mapped assertions passed | Pending actual settings fidelity; unobserved fields and differences remain explicit. |
| B4 | F01, F02, F03, F04, F05, F06, F07, F08, F09, F10 | Implemented | Mapped assertions passed | No separate authenticated mutation campaign required; all retained source/behavior/evidence calibrations require frozen deterministic measurement. |
| B5 | F06, F07, F08, F09, F10 | Implemented | Mapped assertions passed | Native run identity/coverage binding pending actual native evidence. |
| A1 | F05 | Implemented | Mapped assertions passed | No independent vendor test for policy composability; applicable H5/H8 integration remains pending. |
| A2 | F01, F02, F03, F04, F05, F06, F08, F09 | Implemented | Mapped assertions passed | Pending applicable native call/effect/receipt joins. |

## H1

Preserved assertions:

- `tests/hexagonal/test_native_sessions.py::test_private_native_marker_retained_across_exchanges_and_resume`
- `tests/hexagonal/test_native_sessions.py::test_native_callback_persists_trusted_evidence_through_active_composition`
- `tests/hexagonal/test_claude_sessions.py::test_claude_retained_context_callbacks_progress_without_poll_and_native_identity`
- `tests/hexagonal/test_claude_sessions.py::test_claude_application_callback_through_active_composition_and_sqlite`
- `tests/hexagonal/test_verification.py::test_complete_exchange_control_has_correlated_native_and_durable_evidence`

Added repair assertions:

- `tests/hexagonal/test_claude_overlap.py::test_anonymous_overlap_never_claims_unqualified_idle_from_duplicate_stop`
- `tests/hexagonal/test_claude_overlap.py::test_duplicate_or_unmatched_stop_and_sessionstart_cannot_clear_known_turns`
- `tests/hexagonal/test_claude_overlap.py::test_failed_callback_retires_running_tool_and_retains_uncertainty`
- `tests/hexagonal/test_claude_overlap.py::test_known_bad_global_idle_stop_is_detected`
- `tests/hexagonal/test_claude_overlap.py::test_real_claude_hooks_keep_second_callback_active`
- `tests/hexagonal/test_claude_overlap.py::test_unknown_state_or_no_outstanding_work_is_not_target_detection`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`

## H2

Preserved assertions:

- `tests/hexagonal/test_transport_progress.py::test_callback_after_submission_ack_progresses_without_poll`
- `tests/hexagonal/test_transport_progress.py::test_held_callback_allows_native_control_and_nested_request`
- `tests/hexagonal/test_native_sessions.py::test_submission_then_callback_busy_delivery_and_unrelated_progress`
- `tests/hexagonal/test_claude_sessions.py::test_claude_channel_delivery_during_outstanding_callback_and_unrelated_session`
- `tests/hexagonal/test_native_sessions.py::test_unknown_prompt_completion_is_not_idle_and_interjections_are_bounded`
- `tests/hexagonal/test_verification.py::test_successful_transport_does_not_hide_wrong_work_missing_completion_or_unsupported_interjection`

Added repair assertions:

- `tests/hexagonal/test_claude_overlap.py::test_anonymous_overlap_never_claims_unqualified_idle_from_duplicate_stop`
- `tests/hexagonal/test_claude_overlap.py::test_duplicate_or_unmatched_stop_and_sessionstart_cannot_clear_known_turns`
- `tests/hexagonal/test_claude_overlap.py::test_failed_callback_retires_running_tool_and_retains_uncertainty`
- `tests/hexagonal/test_claude_overlap.py::test_known_bad_global_idle_stop_is_detected`
- `tests/hexagonal/test_claude_overlap.py::test_real_claude_hooks_keep_second_callback_active`
- `tests/hexagonal/test_claude_overlap.py::test_unknown_state_or_no_outstanding_work_is_not_target_detection`
- `tests/hexagonal/test_completion_evidence.py::test_explicit_aggregate_completion_and_later_state_are_adjudicated`
- `tests/hexagonal/test_completion_evidence.py::test_one_claude_stop_with_another_held_callback_cannot_pass`
- `tests/hexagonal/test_completion_evidence.py::test_retired_state_does_not_override_current_native_idle`
- `tests/hexagonal/test_completion_evidence.py::test_sustained_fault_calibrates_the_completion_assertion`
- `tests/hexagonal/test_completion_evidence.py::test_turn_result_without_aggregate_state_is_unknown_not_idle`
- `tests/hexagonal/test_completion_evidence.py::test_unrelated_normalization_failure_is_not_completion_detection`
- `tests/hexagonal/test_native_completion_state.py::test_actual_acp_completion_observation_follows_pending_transition`
- `tests/hexagonal/test_native_completion_state.py::test_actual_completion_snapshot_keeps_held_callback_visible_to_checker`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_busy_tool_requires_submission_during_hold_but_can_reply_after_release`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_equal_work_reply_and_completion_timestamps_do_not_prove_before`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_observed_timestamp_order_uses_timezone_offsets`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_unfinished_work_requires_reply_before_completion`

## H3

Preserved assertions:

- `tests/hexagonal/test_delivery.py::test_routing_queues_durable_attempt_and_submission_is_not_receipt`
- `tests/hexagonal/test_delivery.py::test_observation_provenance_duplicate_conflict_and_stale_incarnation`
- `tests/hexagonal/test_verification.py::test_production_receipt_requires_durable_native_callback_chain`
- `tests/hexagonal/test_verification.py::test_production_receipt_faults_have_specific_diagnostics`
- `tests/hexagonal/test_verification.py::test_exchange_faults_fail_the_intended_assertion`
- `tests/hexagonal/test_delivery_adversarial.py::test_conflicting_adapter_submission_identity_cannot_become_submitted`

Added repair assertions:

- `tests/hexagonal/test_bridge_repair_calibration.py::test_f02_known_bad_callback_reassignment_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_observed_callback_cannot_acquire_replacement_authority`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_unbound_missing_and_stale_native_calls_cannot_gain_new_authority`
- `tests/hexagonal/test_claude_overlap.py::test_anonymous_overlap_never_claims_unqualified_idle_from_duplicate_stop`
- `tests/hexagonal/test_claude_overlap.py::test_duplicate_or_unmatched_stop_and_sessionstart_cannot_clear_known_turns`
- `tests/hexagonal/test_claude_overlap.py::test_failed_callback_retires_running_tool_and_retains_uncertainty`
- `tests/hexagonal/test_claude_overlap.py::test_known_bad_global_idle_stop_is_detected`
- `tests/hexagonal/test_claude_overlap.py::test_real_claude_hooks_keep_second_callback_active`
- `tests/hexagonal/test_claude_overlap.py::test_unknown_state_or_no_outstanding_work_is_not_target_detection`
- `tests/hexagonal/test_completion_evidence.py::test_explicit_aggregate_completion_and_later_state_are_adjudicated`
- `tests/hexagonal/test_completion_evidence.py::test_one_claude_stop_with_another_held_callback_cannot_pass`
- `tests/hexagonal/test_completion_evidence.py::test_retired_state_does_not_override_current_native_idle`
- `tests/hexagonal/test_completion_evidence.py::test_sustained_fault_calibrates_the_completion_assertion`
- `tests/hexagonal/test_completion_evidence.py::test_turn_result_without_aggregate_state_is_unknown_not_idle`
- `tests/hexagonal/test_completion_evidence.py::test_unrelated_normalization_failure_is_not_completion_detection`
- `tests/hexagonal/test_native_completion_state.py::test_actual_acp_completion_observation_follows_pending_transition`
- `tests/hexagonal/test_native_completion_state.py::test_actual_completion_snapshot_keeps_held_callback_visible_to_checker`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_argument_disagreement_survives_normalization_and_is_rejected`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_conflicting_native_work_observation_is_not_hidden_by_good_one`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_callback_work_redirect_cannot_override_retained_session`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_wrong_callback_cannot_substitute_for_the_persisted_effect`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_duplicate_raw_record_identity_cannot_overwrite_counterevidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_native_work_scope_is_preserved_without_forcing_session_default`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_null_native_argument_cannot_be_erased`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_injected_work_id_cannot_redirect_executed_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_latest_success_is_selected_without_erasing_unsuccessful_history`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_malformed_raw_evidence_has_no_pass`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_native_argument_comparison_preserves_json_types`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_persisted_receipt_actor_cannot_disagree_with_native_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_record_result_disagreement_is_not_hidden`

## H4

Preserved assertions:

- `tests/hexagonal/test_external_bridge.py::test_external_detach_does_not_terminate_host_and_reconnect_retains_context`
- `tests/hexagonal/test_external_bridge.py::test_replaced_connection_cannot_send_or_apply_late_completion`
- `tests/hexagonal/test_external_bridge.py::test_bridge_rejects_manufactured_receipts_and_changed_configuration`
- `tests/hexagonal/test_external_bridge.py::test_actual_host_crash_preserves_unknown_applied_effect_without_replay`
- `tests/hexagonal/test_application.py::test_resume_requires_source_control_and_destination_creation`
- `tests/hexagonal/test_application.py::test_resume_requires_verified_adapter_receipt`
- `tests/hexagonal/test_application.py::test_resume_replay_reauthorizes_source_and_destination`
- `tests/hexagonal/test_lifecycle_runner.py::test_all_lifecycle_cases_use_real_application_and_external_processes`

Added repair assertions:

- `tests/hexagonal/test_bridge_repair_calibration.py::test_callback_failure_does_not_classify_enclosing_native_effect_as_not_started`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f02_known_bad_callback_reassignment_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_known_bad_not_started_classification_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_no_replacement_control_completes_and_allows_later_effect`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_observed_callback_cannot_acquire_replacement_authority`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_post_effect_reconnect_preserves_fence_observation_and_recovery`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_unbound_missing_and_stale_native_calls_cannot_gain_new_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`

## H5

Preserved assertions:

- `tests/hexagonal/test_transports.py::TransportTests::test_external_capability_execution_and_replay_work_across_cli_and_mcp`
- `tests/hexagonal/test_capabilities.py::CapabilityTests::test_mcp_negotiation_pagination_and_dynamic_new_tool`
- `tests/hexagonal/test_capabilities.py::CapabilityTests::test_cli_manifest_and_dynamic_discovery_share_call_contract`
- `tests/hexagonal/test_native_sessions.py::test_codex_native_permission_response_is_explicit_and_independent`
- `tests/hexagonal/test_native_sessions.py::test_grok_native_permissions_separate_from_application_callback`
- `tests/hexagonal/test_claude_sessions.py::test_claude_native_permissions_independent_of_application_tools`
- `tests/hexagonal/test_delivery.py::test_policy_substitution_scheduling_selection_and_authorization`

Added repair assertions:

- `tests/hexagonal/test_bridge_repair_calibration.py::test_f02_known_bad_callback_reassignment_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_observed_callback_cannot_acquire_replacement_authority`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_unbound_missing_and_stale_native_calls_cannot_gain_new_authority`
- `tests/hexagonal/test_delivery_scope.py::test_delivery_inspection_authorizes_all_record_sections`
- `tests/hexagonal/test_delivery_scope.py::test_f01_sustained_global_snapshot_fault_is_detected`
- `tests/hexagonal/test_delivery_scope.py::test_f01_unrelated_inspection_error_receives_no_detection_credit`
- `tests/hexagonal/test_delivery_scope.py::test_native_inspection_projects_historical_aggregate_results`
- `tests/hexagonal/test_delivery_scope.py::test_native_observation_list_authorizes_each_session`
- `tests/hexagonal/test_delivery_scope.py::test_native_source_cannot_lend_scope_to_historical_target`
- `tests/hexagonal/test_delivery_scope.py::test_receipt_policy_keeps_trusted_cross_work_join`
- `tests/hexagonal/test_delivery_scope.py::test_same_work_message_restriction_is_not_bypassed`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`

## H6

Preserved assertions:

- `tests/hexagonal/test_delivery.py::test_policy_substitution_scheduling_selection_and_authorization`
- `tests/hexagonal/test_delivery.py::test_latest_session_policy_is_independent_of_store_key_order`
- `tests/hexagonal/test_delivery_adversarial.py::test_delivery_selection_is_revalidated_before_native_effect`
- `tests/hexagonal/test_delivery_adversarial.py::test_receipt_commit_rechecks_authorization_context_after_policy_evaluation`
- `tests/hexagonal/test_storage.py::MemoryStoreTests::test_exception_rolls_back_record_and_event_bundle`
- `tests/hexagonal/test_storage.py::SQLiteStoreTests::test_exception_rolls_back_record_and_event_bundle`
- `tests/hexagonal/test_storage.py::SQLiteStoreTests::test_multiple_processes_commit_atomic_counter_and_event_updates`
- `tests/hexagonal/test_boundaries.py::DependencyBoundaryTests::test_application_and_its_helpers_do_not_depend_on_infrastructure`

Added repair assertions:

- `tests/hexagonal/test_bridge_repair_calibration.py::test_callback_failure_does_not_classify_enclosing_native_effect_as_not_started`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_known_bad_not_started_classification_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_no_replacement_control_completes_and_allows_later_effect`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_post_effect_reconnect_preserves_fence_observation_and_recovery`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection`

## H7

Preserved assertions:

- `tests/hexagonal/test_delivery.py::test_lost_submission_stays_unknown_across_reconstructed_application`
- `tests/hexagonal/test_delivery.py::test_observation_and_control_available_without_resolving_unknown_effect`
- `tests/hexagonal/test_delivery.py::test_concurrent_dispatch_cannot_duplicate_native_effect`
- `tests/hexagonal/test_delivery.py::test_reconnect_preserves_unknown_source_effect_while_enabling_observation`
- `tests/hexagonal/test_delivery_adversarial.py::test_native_call_replay_cannot_repeat_non_idempotent_application_action`
- `tests/hexagonal/test_delivery_adversarial.py::test_native_retry_reauthorizes_changed_guardrail_and_stays_denied`
- `tests/hexagonal/test_delivery_adversarial.py::test_post_enqueue_dispatch_conflict_never_repeats_committed_message`
- `tests/hexagonal/test_delivery_adversarial.py::test_native_resolution_cannot_take_a_caller_supplied_result`
- `tests/hexagonal/test_transport_progress.py::test_conflicting_call_or_unmatched_response_closes_with_unknown_outcome`
- `tests/hexagonal/test_external_bridge.py::test_actual_host_crash_preserves_unknown_applied_effect_without_replay`

Added repair assertions:

- `tests/hexagonal/test_bridge_repair_calibration.py::test_callback_failure_does_not_classify_enclosing_native_effect_as_not_started`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f02_known_bad_callback_reassignment_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_known_bad_not_started_classification_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_no_replacement_control_completes_and_allows_later_effect`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_observed_callback_cannot_acquire_replacement_authority`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_post_effect_reconnect_preserves_fence_observation_and_recovery`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_unbound_missing_and_stale_native_calls_cannot_gain_new_authority`
- `tests/hexagonal/test_claude_overlap.py::test_anonymous_overlap_never_claims_unqualified_idle_from_duplicate_stop`
- `tests/hexagonal/test_claude_overlap.py::test_duplicate_or_unmatched_stop_and_sessionstart_cannot_clear_known_turns`
- `tests/hexagonal/test_claude_overlap.py::test_failed_callback_retires_running_tool_and_retains_uncertainty`
- `tests/hexagonal/test_claude_overlap.py::test_known_bad_global_idle_stop_is_detected`
- `tests/hexagonal/test_claude_overlap.py::test_real_claude_hooks_keep_second_callback_active`
- `tests/hexagonal/test_claude_overlap.py::test_unknown_state_or_no_outstanding_work_is_not_target_detection`
- `tests/hexagonal/test_completion_evidence.py::test_explicit_aggregate_completion_and_later_state_are_adjudicated`
- `tests/hexagonal/test_completion_evidence.py::test_one_claude_stop_with_another_held_callback_cannot_pass`
- `tests/hexagonal/test_completion_evidence.py::test_retired_state_does_not_override_current_native_idle`
- `tests/hexagonal/test_completion_evidence.py::test_sustained_fault_calibrates_the_completion_assertion`
- `tests/hexagonal/test_completion_evidence.py::test_turn_result_without_aggregate_state_is_unknown_not_idle`
- `tests/hexagonal/test_completion_evidence.py::test_unrelated_normalization_failure_is_not_completion_detection`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`
- `tests/hexagonal/test_native_completion_state.py::test_actual_acp_completion_observation_follows_pending_transition`
- `tests/hexagonal/test_native_completion_state.py::test_actual_completion_snapshot_keeps_held_callback_visible_to_checker`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_ambiguous_attempt_order_is_invalid_evidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_attempt_with_invalid_recipient_cannot_disappear`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_unsuccessful_attempt_cannot_be_masked_by_old_success`

## H8

Preserved assertions:

- `tests/hexagonal/test_verification.py::test_native_inventory_preserves_complete_requested_counts`
- `tests/hexagonal/test_verification.py::test_checker_loads_selected_candidate_instead_of_its_own_checkout`
- `tests/hexagonal/test_verification.py::test_lifecycle_summary_without_raw_records_cannot_manufacture_a_pass`
- `tests/hexagonal/test_verification.py::test_successful_transport_does_not_hide_wrong_work_missing_completion_or_unsupported_interjection`
- `tests/hexagonal/test_boundaries.py::DependencyBoundaryTests::test_active_package_does_not_import_the_historical_runtime`

Added repair assertions:

- `tests/hexagonal/test_calibration_attribution.py::test_malformed_junit_cannot_earn_detection`
- `tests/hexagonal/test_calibration_attribution.py::test_marker_in_source_is_not_executed_assertion`
- `tests/hexagonal/test_calibration_attribution.py::test_missing_or_ambiguous_observer_attribution_cannot_earn_detection`
- `tests/hexagonal/test_calibration_attribution.py::test_target_name_prefix_does_not_substitute_for_required_assertion`
- `tests/hexagonal/test_calibration_attribution.py::test_traceback_marker_cannot_substitute_for_actual_assertion_message`
- `tests/hexagonal/test_calibration_attribution.py::test_unapplied_fault_and_process_timeout_cannot_earn_detection`
- `tests/hexagonal/test_completion_evidence.py::test_explicit_aggregate_completion_and_later_state_are_adjudicated`
- `tests/hexagonal/test_completion_evidence.py::test_one_claude_stop_with_another_held_callback_cannot_pass`
- `tests/hexagonal/test_completion_evidence.py::test_retired_state_does_not_override_current_native_idle`
- `tests/hexagonal/test_completion_evidence.py::test_sustained_fault_calibrates_the_completion_assertion`
- `tests/hexagonal/test_completion_evidence.py::test_turn_result_without_aggregate_state_is_unknown_not_idle`
- `tests/hexagonal/test_completion_evidence.py::test_unrelated_normalization_failure_is_not_completion_detection`
- `tests/hexagonal/test_native_completion_state.py::test_actual_acp_completion_observation_follows_pending_transition`
- `tests/hexagonal/test_native_completion_state.py::test_actual_completion_snapshot_keeps_held_callback_visible_to_checker`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_ambiguous_attempt_order_is_invalid_evidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_argument_disagreement_survives_normalization_and_is_rejected`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_busy_tool_requires_submission_during_hold_but_can_reply_after_release`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_conflicting_native_work_observation_is_not_hidden_by_good_one`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_callback_work_redirect_cannot_override_retained_session`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_wrong_callback_cannot_substitute_for_the_persisted_effect`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_duplicate_raw_record_identity_cannot_overwrite_counterevidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_equal_work_reply_and_completion_timestamps_do_not_prove_before`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_native_work_scope_is_preserved_without_forcing_session_default`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_null_native_argument_cannot_be_erased`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_injected_work_id_cannot_redirect_executed_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_attempt_with_invalid_recipient_cannot_disappear`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_unsuccessful_attempt_cannot_be_masked_by_old_success`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_latest_success_is_selected_without_erasing_unsuccessful_history`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_malformed_raw_evidence_has_no_pass`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_native_argument_comparison_preserves_json_types`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_observed_timestamp_order_uses_timezone_offsets`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_persisted_receipt_actor_cannot_disagree_with_native_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_record_result_disagreement_is_not_hidden`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_unfinished_work_requires_reply_before_completion`

## B1

Preserved assertions:

- `tests/hexagonal/test_work_acceptance.py::test_full_owner_objective_survives_narrowing_and_cancellation`
- `tests/hexagonal/test_work_acceptance.py::test_explicit_owner_revision_changes_current_basis_without_erasing_history`
- `tests/hexagonal/test_work_acceptance.py::test_requirements_changed_during_assessment_make_it_historical`
- `tests/hexagonal/test_work_acceptance.py::test_changed_basis_during_policy_evaluation_cannot_commit_acceptance`
- `tests/hexagonal/test_work_acceptance.py::test_scope_mismatch_and_stale_requirement_revisions_are_rejected`

Added repair assertions:

- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`

## B2

Preserved assertions:

- `tests/hexagonal/test_work_acceptance.py::test_claim_and_done_status_do_not_create_acceptance`
- `tests/hexagonal/test_work_acceptance.py::test_insufficient_or_misbound_measurement_cannot_pass`
- `tests/hexagonal/test_work_acceptance.py::test_required_failure_cannot_be_erased_by_unrelated_success_or_high_assessment`
- `tests/hexagonal/test_work_acceptance.py::test_running_measurement_blocks_earlier_pass_and_observation_progresses`
- `tests/hexagonal/test_work_acceptance.py::test_uncertain_measurement_cannot_be_masked_by_new_pass`
- `tests/hexagonal/test_work_acceptance.py::test_candidate_change_invalidates_existing_acceptance_and_old_evidence`
- `tests/hexagonal/test_work_acceptance.py::test_composed_cli_measurement_observes_file_identity_instead_of_echoing_request`
- `tests/hexagonal/test_work_acceptance.py::test_native_replay_cannot_present_historical_acceptance_as_current`
- `tests/hexagonal/test_work_acceptance.py::test_tool_evaluator_binds_its_provider_configuration_across_composed_rebuild`

Added repair assertions:

- `tests/hexagonal/test_assessment_inputs.py::test_actual_cli_tool_evaluator_binds_context_and_provider_evidence`
- `tests/hexagonal/test_assessment_inputs.py::test_contribution_input_and_actor_context_remain_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_every_mutable_work_input_supplied_to_evaluator_is_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_explicit_work_projection_can_consume_and_bind_title`
- `tests/hexagonal/test_assessment_inputs.py::test_known_bad_stale_input_fault_is_detected_by_intended_assertion`
- `tests/hexagonal/test_assessment_inputs.py::test_material_change_during_acceptance_cannot_commit`
- `tests/hexagonal/test_assessment_inputs.py::test_material_inputs_invalidate_assessment_and_persisted_acceptance`
- `tests/hexagonal/test_assessment_inputs.py::test_material_json_type_changes_do_not_compare_as_equal`
- `tests/hexagonal/test_assessment_inputs.py::test_mechanical_only_acceptance_does_not_depend_on_unused_evaluator_inputs`
- `tests/hexagonal/test_assessment_inputs.py::test_mutable_evaluator_dependencies_changed_during_assessment_are_historical`
- `tests/hexagonal/test_assessment_inputs.py::test_title_and_unrelated_work_changes_preserve_assessment`
- `tests/hexagonal/test_assessment_inputs.py::test_unrelated_evaluator_failure_is_not_target_detection`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`

## B3

Preserved assertions:

- `tests/hexagonal/test_native_sessions.py::test_settings_preserve_requested_resolved_observed_and_require_changed_config_policy`
- `tests/hexagonal/test_claude_sessions.py::test_claude_requested_resolved_and_observed_settings_and_resume`
- `tests/hexagonal/test_claude_sessions.py::test_claude_changed_configuration_requires_explicit_policy`
- `tests/hexagonal/test_delivery.py::test_explicit_fallback_records_requested_and_resolved_driver`
- `tests/hexagonal/test_delivery_adversarial.py::test_effective_fallback_driver_must_be_authorized_before_launch`
- `tests/hexagonal/test_sessions.py::SessionTests::test_native_parameter_delegation_is_bounded_and_configuration_wins`
- `tests/hexagonal/test_native_sessions.py::test_operational_limits_do_not_change_material_selection_identity`
- `tests/hexagonal/test_work_acceptance.py::test_same_name_provider_configuration_change_invalidates_measurements_and_decisions`
- `tests/hexagonal/test_work_acceptance.py::test_persisted_assessment_rejects_same_name_evaluator_reconfiguration_after_rebuild`

Added repair assertions:


## B4

Preserved assertions:

- `tests/hexagonal/test_verification.py::test_calibrator_distinguishes_target_detection_from_environment_failure`
- `tests/hexagonal/test_verification.py::test_calibration_runs_isolated_real_control_and_mutation`
- `tests/hexagonal/test_work_calibration.py::test_calibrator_requires_exact_assertion_and_passing_controls`
- `tests/hexagonal/test_work_calibration.py::test_missing_dependency_is_setup_error_and_never_fault_detection`
- `tests/hexagonal/test_work_calibration.py::test_absent_requested_assertion_is_missing_coverage`

Added repair assertions:

- `tests/hexagonal/test_assessment_inputs.py::test_actual_cli_tool_evaluator_binds_context_and_provider_evidence`
- `tests/hexagonal/test_assessment_inputs.py::test_contribution_input_and_actor_context_remain_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_every_mutable_work_input_supplied_to_evaluator_is_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_explicit_work_projection_can_consume_and_bind_title`
- `tests/hexagonal/test_assessment_inputs.py::test_known_bad_stale_input_fault_is_detected_by_intended_assertion`
- `tests/hexagonal/test_assessment_inputs.py::test_material_change_during_acceptance_cannot_commit`
- `tests/hexagonal/test_assessment_inputs.py::test_material_inputs_invalidate_assessment_and_persisted_acceptance`
- `tests/hexagonal/test_assessment_inputs.py::test_material_json_type_changes_do_not_compare_as_equal`
- `tests/hexagonal/test_assessment_inputs.py::test_mechanical_only_acceptance_does_not_depend_on_unused_evaluator_inputs`
- `tests/hexagonal/test_assessment_inputs.py::test_mutable_evaluator_dependencies_changed_during_assessment_are_historical`
- `tests/hexagonal/test_assessment_inputs.py::test_title_and_unrelated_work_changes_preserve_assessment`
- `tests/hexagonal/test_assessment_inputs.py::test_unrelated_evaluator_failure_is_not_target_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_callback_failure_does_not_classify_enclosing_native_effect_as_not_started`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f02_known_bad_callback_reassignment_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_known_bad_not_started_classification_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_no_replacement_control_completes_and_allows_later_effect`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_observed_callback_cannot_acquire_replacement_authority`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_post_effect_reconnect_preserves_fence_observation_and_recovery`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_unbound_missing_and_stale_native_calls_cannot_gain_new_authority`
- `tests/hexagonal/test_calibration_attribution.py::test_malformed_junit_cannot_earn_detection`
- `tests/hexagonal/test_calibration_attribution.py::test_marker_in_source_is_not_executed_assertion`
- `tests/hexagonal/test_calibration_attribution.py::test_missing_or_ambiguous_observer_attribution_cannot_earn_detection`
- `tests/hexagonal/test_calibration_attribution.py::test_target_name_prefix_does_not_substitute_for_required_assertion`
- `tests/hexagonal/test_calibration_attribution.py::test_traceback_marker_cannot_substitute_for_actual_assertion_message`
- `tests/hexagonal/test_calibration_attribution.py::test_unapplied_fault_and_process_timeout_cannot_earn_detection`
- `tests/hexagonal/test_claude_overlap.py::test_anonymous_overlap_never_claims_unqualified_idle_from_duplicate_stop`
- `tests/hexagonal/test_claude_overlap.py::test_duplicate_or_unmatched_stop_and_sessionstart_cannot_clear_known_turns`
- `tests/hexagonal/test_claude_overlap.py::test_failed_callback_retires_running_tool_and_retains_uncertainty`
- `tests/hexagonal/test_claude_overlap.py::test_known_bad_global_idle_stop_is_detected`
- `tests/hexagonal/test_claude_overlap.py::test_real_claude_hooks_keep_second_callback_active`
- `tests/hexagonal/test_claude_overlap.py::test_unknown_state_or_no_outstanding_work_is_not_target_detection`
- `tests/hexagonal/test_delivery_scope.py::test_delivery_inspection_authorizes_all_record_sections`
- `tests/hexagonal/test_delivery_scope.py::test_f01_sustained_global_snapshot_fault_is_detected`
- `tests/hexagonal/test_delivery_scope.py::test_f01_unrelated_inspection_error_receives_no_detection_credit`
- `tests/hexagonal/test_delivery_scope.py::test_native_inspection_projects_historical_aggregate_results`
- `tests/hexagonal/test_delivery_scope.py::test_native_observation_list_authorizes_each_session`
- `tests/hexagonal/test_delivery_scope.py::test_native_source_cannot_lend_scope_to_historical_target`
- `tests/hexagonal/test_delivery_scope.py::test_receipt_policy_keeps_trusted_cross_work_join`
- `tests/hexagonal/test_delivery_scope.py::test_same_work_message_restriction_is_not_bypassed`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_ambiguous_attempt_order_is_invalid_evidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_argument_disagreement_survives_normalization_and_is_rejected`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_busy_tool_requires_submission_during_hold_but_can_reply_after_release`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_conflicting_native_work_observation_is_not_hidden_by_good_one`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_callback_work_redirect_cannot_override_retained_session`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_wrong_callback_cannot_substitute_for_the_persisted_effect`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_duplicate_raw_record_identity_cannot_overwrite_counterevidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_equal_work_reply_and_completion_timestamps_do_not_prove_before`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_native_work_scope_is_preserved_without_forcing_session_default`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_null_native_argument_cannot_be_erased`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_injected_work_id_cannot_redirect_executed_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_attempt_with_invalid_recipient_cannot_disappear`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_unsuccessful_attempt_cannot_be_masked_by_old_success`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_latest_success_is_selected_without_erasing_unsuccessful_history`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_malformed_raw_evidence_has_no_pass`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_native_argument_comparison_preserves_json_types`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_observed_timestamp_order_uses_timezone_offsets`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_persisted_receipt_actor_cannot_disagree_with_native_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_record_result_disagreement_is_not_hidden`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_unfinished_work_requires_reply_before_completion`

## B5

Preserved assertions:

- `tests/hexagonal/test_verification.py::test_candidate_capture_includes_dirty_and_untracked_inputs`
- `tests/hexagonal/test_verification.py::test_requested_coverage_and_exact_bindings_override_fabricated_summary`
- `tests/hexagonal/test_verification.py::test_checker_loads_selected_candidate_instead_of_its_own_checkout`
- `tests/hexagonal/test_verification.py::test_lifecycle_summary_without_raw_records_cannot_manufacture_a_pass`
- `tests/hexagonal/test_work_calibration.py::test_absent_requested_assertion_is_missing_coverage`
- `tests/hexagonal/test_work_acceptance.py::test_explicit_owner_revision_changes_current_basis_without_erasing_history`

Added repair assertions:

- `tests/hexagonal/test_calibration_attribution.py::test_malformed_junit_cannot_earn_detection`
- `tests/hexagonal/test_calibration_attribution.py::test_marker_in_source_is_not_executed_assertion`
- `tests/hexagonal/test_calibration_attribution.py::test_missing_or_ambiguous_observer_attribution_cannot_earn_detection`
- `tests/hexagonal/test_calibration_attribution.py::test_target_name_prefix_does_not_substitute_for_required_assertion`
- `tests/hexagonal/test_calibration_attribution.py::test_traceback_marker_cannot_substitute_for_actual_assertion_message`
- `tests/hexagonal/test_calibration_attribution.py::test_unapplied_fault_and_process_timeout_cannot_earn_detection`
- `tests/hexagonal/test_completion_evidence.py::test_explicit_aggregate_completion_and_later_state_are_adjudicated`
- `tests/hexagonal/test_completion_evidence.py::test_one_claude_stop_with_another_held_callback_cannot_pass`
- `tests/hexagonal/test_completion_evidence.py::test_retired_state_does_not_override_current_native_idle`
- `tests/hexagonal/test_completion_evidence.py::test_sustained_fault_calibrates_the_completion_assertion`
- `tests/hexagonal/test_completion_evidence.py::test_turn_result_without_aggregate_state_is_unknown_not_idle`
- `tests/hexagonal/test_completion_evidence.py::test_unrelated_normalization_failure_is_not_completion_detection`
- `tests/hexagonal/test_native_completion_state.py::test_actual_acp_completion_observation_follows_pending_transition`
- `tests/hexagonal/test_native_completion_state.py::test_actual_completion_snapshot_keeps_held_callback_visible_to_checker`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_ambiguous_attempt_order_is_invalid_evidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_argument_disagreement_survives_normalization_and_is_rejected`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_busy_tool_requires_submission_during_hold_but_can_reply_after_release`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_conflicting_native_work_observation_is_not_hidden_by_good_one`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_callback_work_redirect_cannot_override_retained_session`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_wrong_callback_cannot_substitute_for_the_persisted_effect`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_duplicate_raw_record_identity_cannot_overwrite_counterevidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_equal_work_reply_and_completion_timestamps_do_not_prove_before`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_native_work_scope_is_preserved_without_forcing_session_default`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_null_native_argument_cannot_be_erased`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_injected_work_id_cannot_redirect_executed_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_attempt_with_invalid_recipient_cannot_disappear`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_unsuccessful_attempt_cannot_be_masked_by_old_success`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_latest_success_is_selected_without_erasing_unsuccessful_history`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_malformed_raw_evidence_has_no_pass`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_native_argument_comparison_preserves_json_types`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_observed_timestamp_order_uses_timezone_offsets`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_persisted_receipt_actor_cannot_disagree_with_native_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_record_result_disagreement_is_not_hidden`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_unfinished_work_requires_reply_before_completion`

## A1

Preserved assertions:

- `tests/hexagonal/test_work_acceptance.py::test_acceptance_policy_substitutes_without_weakening_required_proof`
- `tests/hexagonal/test_work_acceptance.py::test_required_assessment_policy_and_authorization_remain_independent`
- `tests/hexagonal/test_work_acceptance.py::test_no_requirements_or_acceptance_policy_required_for_simple_messaging`
- `tests/hexagonal/test_composition.py::CompositionTests::test_installed_factory_extends_capabilities_without_source_changes`
- `tests/hexagonal/test_delivery.py::test_policy_substitution_scheduling_selection_and_authorization`

Added repair assertions:

- `tests/hexagonal/test_assessment_inputs.py::test_actual_cli_tool_evaluator_binds_context_and_provider_evidence`
- `tests/hexagonal/test_assessment_inputs.py::test_contribution_input_and_actor_context_remain_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_every_mutable_work_input_supplied_to_evaluator_is_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_explicit_work_projection_can_consume_and_bind_title`
- `tests/hexagonal/test_assessment_inputs.py::test_known_bad_stale_input_fault_is_detected_by_intended_assertion`
- `tests/hexagonal/test_assessment_inputs.py::test_material_change_during_acceptance_cannot_commit`
- `tests/hexagonal/test_assessment_inputs.py::test_material_inputs_invalidate_assessment_and_persisted_acceptance`
- `tests/hexagonal/test_assessment_inputs.py::test_material_json_type_changes_do_not_compare_as_equal`
- `tests/hexagonal/test_assessment_inputs.py::test_mechanical_only_acceptance_does_not_depend_on_unused_evaluator_inputs`
- `tests/hexagonal/test_assessment_inputs.py::test_mutable_evaluator_dependencies_changed_during_assessment_are_historical`
- `tests/hexagonal/test_assessment_inputs.py::test_title_and_unrelated_work_changes_preserve_assessment`
- `tests/hexagonal/test_assessment_inputs.py::test_unrelated_evaluator_failure_is_not_target_detection`

## A2

Preserved assertions:

- `tests/hexagonal/test_verification.py::test_production_receipt_faults_have_specific_diagnostics`
- `tests/hexagonal/test_verification.py::test_lifecycle_summary_without_raw_records_cannot_manufacture_a_pass`
- `tests/hexagonal/test_delivery_adversarial.py::test_native_resolution_rejects_misbound_completion`
- `tests/hexagonal/test_work_acceptance.py::test_insufficient_or_misbound_measurement_cannot_pass`
- `tests/hexagonal/test_work_acceptance.py::test_unrelated_provider_change_does_not_invalidate_current_measurements`
- `tests/hexagonal/test_work_acceptance.py::test_unrelated_evaluator_change_does_not_invalidate_selected_assessment`

Added repair assertions:

- `tests/hexagonal/test_assessment_inputs.py::test_actual_cli_tool_evaluator_binds_context_and_provider_evidence`
- `tests/hexagonal/test_assessment_inputs.py::test_contribution_input_and_actor_context_remain_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_every_mutable_work_input_supplied_to_evaluator_is_bound`
- `tests/hexagonal/test_assessment_inputs.py::test_explicit_work_projection_can_consume_and_bind_title`
- `tests/hexagonal/test_assessment_inputs.py::test_known_bad_stale_input_fault_is_detected_by_intended_assertion`
- `tests/hexagonal/test_assessment_inputs.py::test_material_change_during_acceptance_cannot_commit`
- `tests/hexagonal/test_assessment_inputs.py::test_material_inputs_invalidate_assessment_and_persisted_acceptance`
- `tests/hexagonal/test_assessment_inputs.py::test_material_json_type_changes_do_not_compare_as_equal`
- `tests/hexagonal/test_assessment_inputs.py::test_mechanical_only_acceptance_does_not_depend_on_unused_evaluator_inputs`
- `tests/hexagonal/test_assessment_inputs.py::test_mutable_evaluator_dependencies_changed_during_assessment_are_historical`
- `tests/hexagonal/test_assessment_inputs.py::test_title_and_unrelated_work_changes_preserve_assessment`
- `tests/hexagonal/test_assessment_inputs.py::test_unrelated_evaluator_failure_is_not_target_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_callback_failure_does_not_classify_enclosing_native_effect_as_not_started`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f02_known_bad_callback_reassignment_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_known_bad_not_started_classification_is_detected`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_f03_no_replacement_control_completes_and_allows_later_effect`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_observed_callback_cannot_acquire_replacement_authority`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_post_effect_reconnect_preserves_fence_observation_and_recovery`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection`
- `tests/hexagonal/test_bridge_repair_calibration.py::test_unbound_missing_and_stale_native_calls_cannot_gain_new_authority`
- `tests/hexagonal/test_completion_evidence.py::test_explicit_aggregate_completion_and_later_state_are_adjudicated`
- `tests/hexagonal/test_completion_evidence.py::test_one_claude_stop_with_another_held_callback_cannot_pass`
- `tests/hexagonal/test_completion_evidence.py::test_retired_state_does_not_override_current_native_idle`
- `tests/hexagonal/test_completion_evidence.py::test_sustained_fault_calibrates_the_completion_assertion`
- `tests/hexagonal/test_completion_evidence.py::test_turn_result_without_aggregate_state_is_unknown_not_idle`
- `tests/hexagonal/test_completion_evidence.py::test_unrelated_normalization_failure_is_not_completion_detection`
- `tests/hexagonal/test_delivery_scope.py::test_delivery_inspection_authorizes_all_record_sections`
- `tests/hexagonal/test_delivery_scope.py::test_f01_sustained_global_snapshot_fault_is_detected`
- `tests/hexagonal/test_delivery_scope.py::test_f01_unrelated_inspection_error_receives_no_detection_credit`
- `tests/hexagonal/test_delivery_scope.py::test_native_inspection_projects_historical_aggregate_results`
- `tests/hexagonal/test_delivery_scope.py::test_native_observation_list_authorizes_each_session`
- `tests/hexagonal/test_delivery_scope.py::test_native_source_cannot_lend_scope_to_historical_target`
- `tests/hexagonal/test_delivery_scope.py::test_receipt_policy_keeps_trusted_cross_work_join`
- `tests/hexagonal/test_delivery_scope.py::test_same_work_message_restriction_is_not_bypassed`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_cached_acceptance_requires_current_basis_and_authority`
- `tests/hexagonal/test_native_cache_currentness.py::test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call`
- `tests/hexagonal/test_native_cache_currentness.py::test_cached_accept_work_cannot_replay_superseded_decision`
- `tests/hexagonal/test_native_cache_currentness.py::test_known_bad_cache_bypass_is_detected`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_cannot_recreate_missing_idempotent_operation`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_cache_inside_external_bridge_revalidates`
- `tests/hexagonal/test_native_cache_currentness.py::test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect`
- `tests/hexagonal/test_native_cache_currentness.py::test_unrelated_failure_cannot_satisfy_currentness_assertion`
- `tests/hexagonal/test_native_completion_state.py::test_actual_acp_completion_observation_follows_pending_transition`
- `tests/hexagonal/test_native_completion_state.py::test_actual_completion_snapshot_keeps_held_callback_visible_to_checker`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_ambiguous_attempt_order_is_invalid_evidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_argument_disagreement_survives_normalization_and_is_rejected`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_conflicting_native_work_observation_is_not_hidden_by_good_one`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_callback_work_redirect_cannot_override_retained_session`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_consistent_wrong_callback_cannot_substitute_for_the_persisted_effect`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_duplicate_raw_record_identity_cannot_overwrite_counterevidence`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_native_work_scope_is_preserved_without_forcing_session_default`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_explicit_null_native_argument_cannot_be_erased`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_injected_work_id_cannot_redirect_executed_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_attempt_with_invalid_recipient_cannot_disappear`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_later_unsuccessful_attempt_cannot_be_masked_by_old_success`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_latest_success_is_selected_without_erasing_unsuccessful_history`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_malformed_raw_evidence_has_no_pass`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_native_argument_comparison_preserves_json_types`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_persisted_receipt_actor_cannot_disagree_with_native_callback`
- `tests/hexagonal/test_raw_evidence_calibration.py::test_record_result_disagreement_is_not_hidden`
