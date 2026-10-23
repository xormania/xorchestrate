package contracts

import "list"

// CLOSED DEFINITION: SQL metadata is limited to the four neutral observation keys.
// PLAN.md SS3.2 lines 91-108 (inferred; APPROACH.md SS5.1 fixes this carrier).
#SchemaColumn: close({
	// PLAN.md SS3.2 lines 98-108 (stated for documented names; otherwise left as string).
	Name=name!: string
	// PLAN.md SS3.2 lines 91-108 (PLAN.md gives no SQL types; optional observation carrier).
	type?: _
	// PLAN.md SS3.2 lines 91-108 (PLAN.md gives no nullability; optional observation carrier).
	notnull?: _
	// PLAN.md SS3.2 lines 91-108 (only claims.wo_id is pinned below; optional observation carrier).
	pk?: _
	// PLAN.md SS3.2 lines 98-108 (inferred helper over the neutral carrier).
	_nameValue: Name
})

// OPEN DEFINITION: PLAN.md fixes documented table semantics, not every observation key.
// PLAN.md SS3.2 lines 91-108 (inferred; APPROACH.md SS5.1 fixes this carrier).
#SchemaTable: {
	// PLAN.md SS3.2 lines 91-108 (stated).
	Name=name!: string
	// PLAN.md SS3.2 lines 91-108 (column form is supplied only where the table lists names).
	columns?: [...#SchemaColumn]
	// PLAN.md SS3.2 lines 98-108 (stated only for claims.wo_id; optional elsewhere).
	primary_key?: [...string]

	if Name == "artifacts" {
		// PLAN.md SS3.2 lines 98-104 (stated column names; exhaustive set, order unconstrained).
		Cols=columns: _
		_columnNames: [for c in Cols {c._nameValue}]
		_columnCount: len(_columnNames) & 4
		_documentedColumns: [
			list.Contains(_columnNames, "digest"),
			list.Contains(_columnNames, "size"),
			list.Contains(_columnNames, "media_type"),
			list.Contains(_columnNames, "class"),
		] & [...true]
	}

	if Name == "attempts" {
		// PLAN.md SS2 lines 56-56 (stated fields; other prose-named fields remain open).
		Cols=columns: _
		_columnNames: [for c in Cols {c._nameValue}]
		_recordKind:     list.Contains(_columnNames, "record_kind") & true
		_exhaustiveness: list.Contains(_columnNames, "exhaustiveness") & true
		_agent:          list.Contains(_columnNames, "agent") & true
	}

	if Name == "claims" {
		// PLAN.md SS3.2 lines 98-105 (stated column names and primary key; exhaustive set, order unconstrained).
		Cols=columns: _
		_columnNames: [for c in Cols {c._nameValue}]
		_columnCount: len(_columnNames) & 5
		_documentedColumns: [
			list.Contains(_columnNames, "wo_id"),
			list.Contains(_columnNames, "agent"),
			list.Contains(_columnNames, "note"),
			list.Contains(_columnNames, "acquired_at"),
			list.Contains(_columnNames, "expires_at"),
		] & [...true]
		primary_key: ["wo_id"]
	}

	if Name == "events" {
		// PLAN.md SS3.2 lines 98-101 (stated column names; exhaustive set, order unconstrained).
		Cols=columns: _
		_columnNames: [for c in Cols {c._nameValue}]
		_columnCount: len(_columnNames) & 7
		_documentedColumns: [
			list.Contains(_columnNames, "wo_id"),
			list.Contains(_columnNames, "seq"),
			list.Contains(_columnNames, "hash"),
			list.Contains(_columnNames, "prev"),
			list.Contains(_columnNames, "time"),
			list.Contains(_columnNames, "payload"),
			list.Contains(_columnNames, "state_sha256"),
		] & [...true]
	}

	if Name == "gate_kinds" {
		// PLAN.md SS3.2 lines 98-103 (stated column names; exhaustive set, order unconstrained).
		Cols=columns: _
		_columnNames: [for c in Cols {c._nameValue}]
		_columnCount: len(_columnNames) & 3
		_documentedColumns: [
			list.Contains(_columnNames, "kind"),
			list.Contains(_columnNames, "version"),
			list.Contains(_columnNames, "spec_json"),
		] & [...true]
	}

	if Name == "sessions" {
		// PLAN.md SS3.2 lines 98-106 (stated column names; exhaustive set, order unconstrained).
		Cols=columns: _
		_columnNames: [for c in Cols {c._nameValue}]
		_columnCount: len(_columnNames) & 7
		_documentedColumns: [
			list.Contains(_columnNames, "session_id"),
			list.Contains(_columnNames, "agent"),
			list.Contains(_columnNames, "wo_id"),
			list.Contains(_columnNames, "status"),
			list.Contains(_columnNames, "started_at"),
			list.Contains(_columnNames, "last_seen"),
			list.Contains(_columnNames, "ttl_seconds"),
		] & [...true]
	}

	if Name == "work_orders" {
		// PLAN.md SS3.2 lines 98-100 (stated column names; exhaustive set, order unconstrained).
		Cols=columns: _
		_columnNames: [for c in Cols {c._nameValue}]
		_columnCount: len(_columnNames) & 6
		_documentedColumns: [
			list.Contains(_columnNames, "wo_id"),
			list.Contains(_columnNames, "objective"),
			list.Contains(_columnNames, "kind"),
			list.Contains(_columnNames, "kind_version"),
			list.Contains(_columnNames, "spec_sha256"),
			list.Contains(_columnNames, "created_at"),
		] & [...true]
	}
	...
}

// CLOSED DEFINITION: facts has exactly migrations and tables.
// PLAN.md SS3.2 lines 91-108 (inferred; APPROACH.md SS5.1 fixes the facts carrier).
#Schema: close({
	// PLAN.md SS3.2 lines 91-108 (inferred; this is the neutral facts envelope).
	facts!: close({
		// PLAN.md SS6 lines 361-364 (inferred from numbered forward-only SQL and 00NN_*.sql).
		// PLAN.md SS8 lines 468-479 (inferred four-digit filename form).
		Migrations=migrations!: [...(string & =~"^[0-9]{4}_[^/]*\\.sql$")] & list.MinItems(1)
		// PLAN.md SS13 lines 574-576 (stated).
		_foundationMigration: list.Contains(Migrations, "0001_foundation.sql") & true

		// PLAN.md SS3.2 lines 91-108 (stated exact table set; row order is not constrained).
		Tables=tables!: [...#SchemaTable]
		_tableNames: [for table in Tables {table.name}]
		_tableCount: len(Tables) & 9
		_requiredTables: [
			list.Contains(_tableNames, "work_orders"),
			list.Contains(_tableNames, "events"),
			list.Contains(_tableNames, "wo_state"),
			list.Contains(_tableNames, "gate_kinds"),
			list.Contains(_tableNames, "artifacts"),
			list.Contains(_tableNames, "claims"),
			list.Contains(_tableNames, "sessions"),
			list.Contains(_tableNames, "attempts"),
			list.Contains(_tableNames, "schema_migrations"),
		] & [...true]
	})
	// PLAN.md SS1 lines 21-24 (inferred; evidence provenance is deliberately unconstrained).
	provenance!: _
})
