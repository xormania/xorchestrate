package contracts

// OPEN DEFINITION: source-file metadata is an optional observation carrier.
// PLAN.md SS3.2 lines 110-121
#EventTypeObservation: {
	// PLAN.md SS3.2 lines 110-121
	value!: string
	// PLAN.md SS3.2 lines 110-121
	files?: _
	...
}

// OPEN DEFINITION: type-specific keys remain extensible.
// PLAN.md SS3.1 lines 71-89
#EventShapeObservation: {
	// PLAN.md SS3.2 lines 110-121
	type!: string
	// PLAN.md SS3.1 lines 73-83
	keys!: [...string]
	// PLAN.md SS3.1 lines 73-83 (source file is optional observation metadata).
	file?: _
	// PLAN.md SS3.1 lines 73-83 (source line is optional observation metadata).
	line?: _

	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
	for _, key in keys if key == "seq" {
		// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
		_has_seq: true
	}

	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
	for _, key in keys if key == "prev" {
		// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
		_has_prev: true
	}

	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
	for _, key in keys if key == "time" {
		// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
		_has_time: true
	}

	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
	for _, key in keys if key == "type" {
		// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
		_has_type: true
	}

	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
	for _, key in keys if key == "state_sha256" {
		// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
		_has_state_sha256: true
	}

	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
	for _, key in keys if key == "hash" {
		// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated)
		_has_hash: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "argv" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_argv: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "cwd" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_cwd: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "exit_code" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_exit_code: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "duration_ms" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_duration_ms: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "stdout_sha256" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_stdout_sha256: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "stderr_sha256" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_stderr_sha256: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "agent" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_agent: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "head_sha" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_head_sha: true
	}

	// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
	for _, key in keys if key == "tree_dirty" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_tree_dirty: true
	}

	// PLAN.md SS3.3 lines 130-139 -- EVT-S05 (stated)
	for _, key in keys if key == "test_manifest" {
		// PLAN.md SS3.3 lines 130-139 -- EVT-S05 (stated)
		_has_test_manifest: true
	}

	// PLAN.md SS3.6 lines 200-207 -- EVT-S06 (stated)
	for _, key in keys if key == "sha" {
		// PLAN.md SS3.6 lines 200-207 -- EVT-S06 (stated)
		_has_sha: true
	}

	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated for every event).
	_has_seq!: true
	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated for every event).
	_has_prev!: true
	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated for every event).
	_has_time!: true
	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated for every event).
	_has_type!: true
	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated for every event).
	_has_state_sha256!: true
	// PLAN.md SS3.1 lines 73-83 -- EVT-S03 (stated for every event).
	_has_hash!: true

	// PLAN.md SS3.3 lines 123-128 -- EVT-S04 (stated)
	if type == "receipt.red" || type == "receipt.green" || type == "receipt.check" {
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_argv!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_cwd!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_exit_code!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_duration_ms!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_stdout_sha256!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_stderr_sha256!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_agent!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_head_sha!: true
		// PLAN.md SS3.3 lines 125-128 -- EVT-S04 (stated)
		_has_tree_dirty!: true
	}

	// PLAN.md SS3.3 lines 130-139 -- EVT-S05 (stated)
	if type == "receipt.red" {
		// PLAN.md SS3.3 lines 130-139 -- EVT-S05 (stated)
		_has_test_manifest!: true
	}

	// PLAN.md SS3.6 lines 200-207 -- EVT-S06 (stated)
	if type == "commit.landed" {
		// PLAN.md SS3.6 lines 200-207 -- EVT-S06 (stated)
		_has_sha!: true
	}
	...
}

// OPEN DEFINITION: PLAN.md permits additional event types as data.
// PLAN.md SS3.1 lines 71-89
#EventFacts: {
	// PLAN.md SS3.2 lines 110-121
	types!: [...#EventTypeObservation]
	// PLAN.md SS3.1 lines 73-83
	shapes?: [...#EventShapeObservation]

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "artifact.recorded" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_artifact_recorded: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_artifact_recorded!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "commit.landed" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_commit_landed: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_commit_landed!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "marker.close" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_marker_close: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_marker_close!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "marker.open" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_marker_open: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_marker_open!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "outcome" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_outcome: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_outcome!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "receipt.check" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_receipt_check: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_receipt_check!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "receipt.green" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_receipt_green: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_receipt_green!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "receipt.red" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_receipt_red: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_receipt_red!: true

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	for _, eventType in types if eventType.value == "stage.entered" {
		// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
		_has_stage_entered: true
	}

	// PLAN.md SS3.2 lines 110-121 -- EVT-S01 (stated)
	_has_stage_entered!: true
	...
}

// OPEN DEFINITION: provenance and observation carriers remain extensible.
// PLAN.md SS3.1 lines 71-89
#Events: {
	// PLAN.md SS3.1 lines 71-89
	facts!: #EventFacts
	// PLAN.md SS3.1 lines 71-89
	provenance!: _
	...
}
