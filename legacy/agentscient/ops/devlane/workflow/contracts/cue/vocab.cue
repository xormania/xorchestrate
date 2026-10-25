package contracts

import "strings"

#WallReserved: {
	// D-VOCAB-1: reserved for the prod lane (the House product).
	"vocab.kernel-outside-homes": true
	// D-VOCAB-3: compose's term is reserved outside its stated homes.
	"vocab.service-outside-infra": true
}

// OPEN DEFINITION: PLAN.md fixes the reserved vocabulary, not every observation field.
// PLAN.md SS10 lines 499-507
#VocabFacts: {
	// PLAN.md SS10 lines 501-507
	literals!: [...string]
	// PLAN.md SS10 lines 501-507
	joined!: strings.Join(literals, "\n")

	// PLAN.md SS10 lines 501-502 -- VOC-S01 (stated)
	for _, literal in literals if strings.Contains(literal, "work order") {
		// PLAN.md SS10 lines 501-502 -- VOC-S01 (stated)
		_has_work_order: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S01 (stated)
	_has_work_order!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S02 (stated)
	for _, literal in literals if strings.Contains(literal, "gate") {
		// PLAN.md SS10 lines 501-502 -- VOC-S02 (stated)
		_has_gate: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S02 (stated)
	_has_gate!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S03 (stated)
	for _, literal in literals if strings.Contains(literal, "stage") {
		// PLAN.md SS10 lines 501-502 -- VOC-S03 (stated)
		_has_stage: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S03 (stated)
	_has_stage!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S04 (stated)
	for _, literal in literals if strings.Contains(literal, "receipt") {
		// PLAN.md SS10 lines 501-502 -- VOC-S04 (stated)
		_has_receipt: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S04 (stated)
	_has_receipt!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S05 (stated)
	for _, literal in literals if strings.Contains(literal, "red") {
		// PLAN.md SS10 lines 501-502 -- VOC-S05 (stated)
		_has_red: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S05 (stated)
	_has_red!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S06 (stated)
	for _, literal in literals if strings.Contains(literal, "green") {
		// PLAN.md SS10 lines 501-502 -- VOC-S06 (stated)
		_has_green: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S06 (stated)
	_has_green!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S07 (stated)
	for _, literal in literals if strings.Contains(literal, "frozen set") {
		// PLAN.md SS10 lines 501-502 -- VOC-S07 (stated)
		_has_frozen_set: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S07 (stated)
	_has_frozen_set!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S08 (stated)
	for _, literal in literals if strings.Contains(literal, "chain") {
		// PLAN.md SS10 lines 501-502 -- VOC-S08 (stated)
		_has_chain: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S08 (stated)
	_has_chain!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S09 (stated)
	for _, literal in literals if strings.Contains(literal, "claim") {
		// PLAN.md SS10 lines 501-502 -- VOC-S09 (stated)
		_has_claim: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S09 (stated)
	_has_claim!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S10 (stated)
	for _, literal in literals if strings.Contains(literal, "marker") {
		// PLAN.md SS10 lines 501-502 -- VOC-S10 (stated)
		_has_marker: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S10 (stated)
	_has_marker!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S11 (stated)
	for _, literal in literals if strings.Contains(literal, "packet") {
		// PLAN.md SS10 lines 501-502 -- VOC-S11 (stated)
		_has_packet: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S11 (stated)
	_has_packet!: true

	// PLAN.md SS10 lines 501-502 -- VOC-S12 (stated)
	for _, literal in literals if strings.Contains(literal, "wf") {
		// PLAN.md SS10 lines 501-502 -- VOC-S12 (stated)
		_has_wf: true
	}

	// PLAN.md SS10 lines 501-502 -- VOC-S12 (stated)
	_has_wf!: true

	// U1 §2 -- VOC-S13 (stated)
	for _, literal in literals if strings.Contains(literal, "capability") {
		_has_capability: true
		"vocab.capability-absent": true
	}

	// U1 §2 -- VOC-S13 (stated)
	_has_capability!: true
	"vocab.capability-absent"!: true

	// U1 §2 -- VOC-S14 (stated; "capabilities" does not contain "capability")
	for _, literal in literals if strings.Contains(literal, "capabilities") {
		_has_capabilities: true
	}

	// U1 §2 -- VOC-S14 (stated)
	_has_capabilities!: true
	...
}

// OPEN DEFINITION: provenance and observation carriers remain extensible.
// PLAN.md SS10 lines 499-507
#Vocab: {
	// PLAN.md SS10 lines 501-507
	facts!: #VocabFacts
	// PLAN.md SS10 lines 501-507
	provenance!: _
	...
}
