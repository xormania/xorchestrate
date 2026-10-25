package contracts

import (
	"list"
	"strings"
)

// CLOSED DEFINITION: each captured ignore line has exactly the neutral carrier fields.
// PLAN.md SS8 lines 453-479 (inferred; APPROACH.md SS5.5 fixes the carrier).
#GitignoreDbLine: close({
	// PLAN.md SS8 lines 453-479 (inferred; PLAN.md does not name the .gitignore file).
	file!: string
	// PLAN.md SS8 lines 453-479 (stated: the captured rule must concern db files).
	line!: string & =~"db"
})

// CLOSED DEFINITION: facts has only files, dirs, and gitignore; the two path lists
// remain extensible for documented growth.
// PLAN.md SS13 lines 565-570 (stated).
#Layout: close({
	// PLAN.md SS8 lines 468-479 (inferred; this is the neutral facts envelope).
	facts!: close({
		// PLAN.md SS13 lines 565-570 (stated; additional files are allowed).
		Files=files!: [...string]
		// PLAN.md SS8 lines 468-479 (stated; additional directories are allowed).
		Dirs=dirs!: [...string]
		// PLAN.md SS8 lines 453-479 (inferred; the exact ignore-file location and syntax are not stated).
		Ignore=gitignore!: [...#GitignoreDbLine]

		// PLAN.md SS8 lines 468-479 (stated).
		_requiredTreeFiles: [
			// PLAN.md SS8 lines 468-479 (stated).
			list.Contains(Files, "PLAN.md"),
			// PLAN.md SS8 lines 468-479 (stated).
			list.Contains(Files, "wf.py"),
			// PLAN.md SS8 lines 468-479 (stated).
			list.Contains(Files, "meta.json"),
		] & [...true]

		// PLAN.md SS13 lines 565-570 (stated).
		_requiredModuleFiles: [
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Files, "canonical.py"),
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Files, "store.py"),
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Files, "chain.py"),
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Files, "receipts.py"),
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Files, "gitio.py"),
		] & [...true]

		// PLAN.md SS13 lines 574-576 (stated).
		_foundationMigration: list.Contains(Files, "migrations/0001_foundation.sql") & true

		// PLAN.md SS8 lines 468-479 (stated).
		_requiredEvidenceDirs: [
			// PLAN.md SS8 lines 468-479 (stated).
			list.Contains(Dirs, "chains"),
			// PLAN.md SS8 lines 468-479 (stated).
			list.Contains(Dirs, "attempts"),
			// PLAN.md SS8 lines 468-479 (stated).
			list.Contains(Dirs, "objects"),
		] & [...true]

		// PLAN.md SS13 lines 565-570 (stated).
		_requiredDataDirs: [
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Dirs, "migrations"),
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Dirs, "gate-kinds"),
			// PLAN.md SS13 lines 565-570 (stated).
			list.Contains(Dirs, "tests"),
		] & [...true]

		// PLAN.md SS6 lines 361-364 (inferred from numbered forward-only and the 00NN_ illustration).
		// PLAN.md SS8 lines 468-479 (inferred four-digit path form).
		_migrationFiles: [for f in Files if strings.HasPrefix(f, "migrations/") {
			f & =~"^migrations/[0-9]{4}_[^/]*\\.sql$"
		}]

		// PLAN.md SS8 lines 468-479 (stated path form; nonempty presence is inferred from source-of-truth data).
		_gateKindFiles: [for f in Files if strings.HasPrefix(f, "gate-kinds/") {
			f & =~"^gate-kinds/[^/]+\\.json$"
		}] & list.MinItems(1)

		// PLAN.md SS8 lines 453-479 (stated ignore requirement; inferred nonempty-observation proxy).
		_wfDbIgnoreLines: [for entry in Ignore if strings.Contains(entry.line, "wf.db") {entry}]
		// PLAN.md SS8 lines 453-479 (stated: the illustrated wf.db is gitignored).
		// PLAN.md SS12 lines 538-539 (stated: wf.db is gitignored).
		_wfDbIgnoreEvidence: {
			// PLAN.md SS12 lines 538-539 (stated: reject missing wf.db ignore evidence).
			"wf.db": (!list.Contains(Files, "wf.db") || len(_wfDbIgnoreLines) > 0) & true
		}

		// PLAN.md SS6 lines 328-330 (stated path form for a named gate kind).
		// PLAN.md SS11 lines 513-519 (stated: tdd and artifact are seeded).
		_seedGateKinds: {
			// PLAN.md SS6 lines 328-330 (stated path form for a named gate kind).
			// PLAN.md SS11 lines 513-519 (stated: artifact is seeded).
			"artifact.json": list.Contains(Files, "gate-kinds/artifact.json") & true
			// PLAN.md SS6 lines 328-330 (stated path form for a named gate kind).
			// PLAN.md SS11 lines 513-519 (stated: tdd is seeded).
			"tdd.json": list.Contains(Files, "gate-kinds/tdd.json") & true
		}
	})
	// PLAN.md SS1 lines 21-24 (inferred; evidence provenance is deliberately unconstrained).
	provenance!: _
})
