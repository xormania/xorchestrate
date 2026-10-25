package contracts

// CLOSED DEFINITION: a discovered json.dumps call has no undocumented carrier fields.
// PLAN.md SS13 lines 574-575 (inferred; APPROACH.md SS5.8 fixes the carrier).
#CanonicalCall: close({
	// PLAN.md SS13 lines 574-575 (inferred; source location is observation metadata).
	line!: int
	// PLAN.md SS2 lines 52-52 (stated).
	keywords!: {
		// PLAN.md SS2 lines 52-52 (stated).
		sort_keys!: true
		// PLAN.md SS2 lines 52-52 (stated).
		separators!: [",", ":"]
		// PLAN.md SS2 lines 52-52 (inferred: extra explicit defaults may preserve byte identity).
		[string]: _
	}
})

// CLOSED DEFINITION: only the two neutral observation fields are admitted under facts.
// PLAN.md SS2 lines 52-52 (inferred; APPROACH.md SS5.8 fixes the carrier).
#Canonical: close({
	// PLAN.md SS2 lines 52-52 (inferred; this is the neutral facts envelope).
	facts!: close({
		// PLAN.md SS13 lines 574-575 (inferred: every surfaced call must use the recipe; zero calls exposes a static-observation gap).
		json_dumps_calls!: [...#CanonicalCall]
		// PLAN.md SS13 lines 574-575 (stated SHA-256 presence; inferred exclusion of other algorithms).
		hashlib_algorithms!: ["sha256"]
	})
	// PLAN.md SS1 lines 21-24 (inferred; evidence provenance is deliberately unconstrained).
	provenance!: _
})
