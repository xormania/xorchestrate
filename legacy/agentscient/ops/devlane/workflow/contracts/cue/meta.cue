package contracts

import "list"

// CLOSED DEFINITION: meta facts expose exactly the neutral carrier; raw's form stays unconstrained.
// PLAN.md SS6 lines 361-364 (inferred; APPROACH.md SS5.4 fixes the carrier).
#Meta: close({
	// PLAN.md SS6 lines 361-364 (inferred; this is the neutral facts envelope).
	facts!: close({
		// PLAN.md SS6 lines 361-364 (a file carrying a format version is nonempty).
		bytes!: int & >0
		// PLAN.md SS6 lines 361-364 (stated: a file carrying a format version must be JSON here).
		is_json!: true
		// PLAN.md SS6 lines 361-364 (content is resolved JSON; no object/key/value form is imposed).
		raw!: null | bool | number | (string & !="unresolved") | [...] | {[string]: _}
		// PLAN.md SS6 lines 361-364 (a readable format version is witnessed; no key spelling is imposed).
		version_keys!: [...string] & list.MinItems(1)
	})
	// PLAN.md SS1 lines 21-24 (inferred; evidence provenance is deliberately unconstrained).
	provenance!: _
})
