package contracts

import "strings"

// OPEN DEFINITION: PLAN.md fixes required message evidence, not every observation field.
// PLAN.md SS5 lines 289-319
#CommitMsgFacts: {
	// PLAN.md SS5 lines 305-319
	trailer_keys!: [...string]
	// PLAN.md SS5 lines 305-315
	subject_literals!: [...string]
	// PLAN.md SS5 lines 305-315
	literals!: [...string]

	// PLAN.md SS5 lines 307-315 -- MSG-S01 (stated)
	for _, key in trailer_keys if key == "WO" {
		// PLAN.md SS5 lines 307-315 -- MSG-S01 (stated)
		_has_wo: true
	}

	// PLAN.md SS5 lines 307-315 -- MSG-S01 (stated)
	_has_wo!: true

	// PLAN.md SS5 lines 307-315 -- MSG-S02 (stated)
	for _, key in trailer_keys if key == "Stage" {
		// PLAN.md SS5 lines 307-315 -- MSG-S02 (stated)
		_has_stage: true
	}

	// PLAN.md SS5 lines 307-315 -- MSG-S02 (stated)
	_has_stage!: true

	// PLAN.md SS5 lines 307-315 -- MSG-S03 (stated)
	for _, key in trailer_keys if key == "Event" {
		// PLAN.md SS5 lines 307-315 -- MSG-S03 (stated)
		_has_event: true
	}

	// PLAN.md SS5 lines 307-315 -- MSG-S03 (stated)
	_has_event!: true

	// PLAN.md SS5 lines 289-315 -- MSG-S04 (stated)
	for _, key in trailer_keys if key == "Source" {
		// PLAN.md SS5 lines 289-315 -- MSG-S04 (stated)
		_has_source: true
	}

	// PLAN.md SS5 lines 289-315 -- MSG-S04 (stated)
	_has_source!: true

	// PLAN.md SS5 lines 307-319 -- MSG-S05 (stated)
	for _, key in trailer_keys if key == "Co-Authored-By" {
		// PLAN.md SS5 lines 307-319 -- MSG-S05 (stated)
		_has_coauthor: true
	}

	// PLAN.md SS5 lines 307-319 -- MSG-S05 (stated)
	_has_coauthor!: true

	// PLAN.md SS5 lines 305-315 -- MSG-S06 (stated)
	for _, subject in subject_literals if subject =~ #"^wf\([^/\n]+/[^)\n]+\): [^\n]+$"# {
		// PLAN.md SS5 lines 305-315 -- MSG-S06 (stated)
		_has_subject_format: true
	}

	// PLAN.md SS5 lines 305-315 -- MSG-S06 (stated)
	_has_subject_format!: true

	// PLAN.md SS5 lines 289-315 -- MSG-S07 (stated)
	for _, literal in literals if strings.Contains(literal, "Source: generated: wf") {
		// PLAN.md SS5 lines 289-315 -- MSG-S07 (stated)
		_has_generated_source: true
	}

	// PLAN.md SS5 lines 289-315 -- MSG-S07 (stated)
	_has_generated_source!: true
	...
}

// OPEN DEFINITION: provenance and observation carriers remain extensible.
// PLAN.md SS5 lines 289-319
#CommitMsg: {
	// PLAN.md SS5 lines 289-319
	facts!: #CommitMsgFacts
	// PLAN.md SS5 lines 305-319
	provenance!: _
	...
}
