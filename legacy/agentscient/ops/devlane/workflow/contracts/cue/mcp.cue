package contracts

import "list"

// OPEN DEFINITION: import and literal observations may grow; PLAN.md fixes only
// the read-only boundary and the three served resource verbs.
// PLAN.md SS9 lines 483-488
#McpFacts: {
	// PLAN.md SS9 lines 485-488 (stated behavior, but no observation form; optional carrier).
	// PLAN.md SS13 lines 565-570 (chain appends; gitio exports/commits/cross-binds; neither is the query layer).
	server_imports?: [...(string & !="gitio" & !="chain")]
	// PLAN.md SS9 lines 485-488 (no launch-import requirement is stated; optional carrier).
	launch_imports?: _
	// PLAN.md SS9 lines 485-488
	verb_literals!: [...string]
	// PLAN.md SS9 lines 485-488 (resources are named, but no URI form is stated; optional carrier).
	uri_literals?: _

	// PLAN.md SS9 lines 485-488 (stated: the server imports the query layer).
	if server_imports != _|_ {
		// PLAN.md SS9 lines 485-488 (stated: the server imports the query layer).
		_query_imported: list.Contains(server_imports, "query") & true
	}

	// PLAN.md SS9 lines 485-488 -- MCP-S01 (stated)
	for _, verb in verb_literals if verb == "status" {
		// PLAN.md SS9 lines 485-488 -- MCP-S01 (stated)
		_serves_status: true
	}

	// PLAN.md SS9 lines 485-488 -- MCP-S01 (stated)
	_serves_status!: true

	// PLAN.md SS9 lines 485-488 -- MCP-S02 (stated)
	for _, verb in verb_literals if verb == "next" {
		// PLAN.md SS9 lines 485-488 -- MCP-S02 (stated)
		_serves_next: true
	}

	// PLAN.md SS9 lines 485-488 -- MCP-S02 (stated)
	_serves_next!: true

	// PLAN.md SS9 lines 485-488 -- MCP-S03 (stated)
	for _, verb in verb_literals if verb == "log" {
		// PLAN.md SS9 lines 485-488 -- MCP-S03 (stated)
		_serves_log: true
	}

	// PLAN.md SS9 lines 485-488 -- MCP-S03 (stated)
	_serves_log!: true
	...
}

// OPEN DEFINITION: provenance and observation carriers remain extensible.
// PLAN.md SS9 lines 483-488
#Mcp: {
	// PLAN.md SS9 lines 485-488
	facts!: #McpFacts
	// PLAN.md SS9 lines 485-488
	provenance!: _
	...
}
