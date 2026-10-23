package agentscient

#NonEmpty: string & !=""
#Digest: =~"^[0-9a-f]{64}$"
#Sha: =~"^[0-9a-f]{40}$"
#Date: =~"^\\d{4}-\\d{2}-\\d{2}$"
#DeclaredAbsent: close({declared_absent: #NonEmpty})
#Interface: close({
	name: #NonEmpty
	declared_at: #NonEmpty
	fields?: [#NonEmpty, ...#NonEmpty]
	argv?: [#NonEmpty, ...#NonEmpty]
	output?: #NonEmpty
})

#Premise: close({text: #NonEmpty, tag: "measured" | "read" | "memory" | "assumed", source: #NonEmpty})
#Parent: close({id: #NonEmpty, sha256: #Digest, unit: #NonEmpty, bound_to: "sidecar" | "prose"})
#Scope: close({owner_words: #NonEmpty, dated: #Date, source: #NonEmpty})
#Settled: close({id: #NonEmpty, ruled_by: "owner" | "conductor", dated: #Date, where: #NonEmpty})
#Decision: close({
	id: #NonEmpty
	sentence: #NonEmpty
	premises: [#Premise, ...#Premise]
	new: false
	ratified_by: "parent"
	status: "pending" | "standing" | "revisit" | "superseded"
}) | close({
	id: #NonEmpty
	sentence: #NonEmpty
	premises: [#Premise, ...#Premise]
	new: true
	ratified_by: "owner" | "pending" | "conductor"
	status: "pending" | "standing" | "revisit" | "superseded"
})
#Open: close({what: #NonEmpty, delegated_to: #NonEmpty})
#Deliverable: close({path: #NonEmpty, interface: #Interface | #DeclaredAbsent})
#Gate: close({
	name: #NonEmpty
	argv: [#NonEmpty, ...#NonEmpty]
	fires_on: #NonEmpty
	quiet_on: #NonEmpty
	cost: "cheap"
	boundary_at?: "plan" | "tests" | "check" | "code" | "review" | "done"
}) | close({
	name: #NonEmpty
	argv: [#NonEmpty, ...#NonEmpty]
	fires_on: #NonEmpty
	quiet_on: #NonEmpty
	cost: "boundary"
	boundary_at: "plan" | "tests" | "check" | "code" | "review" | "done"
})
#Unit: close({id: #NonEmpty, deliverables: [#Deliverable, ...#Deliverable], needs: [...#NonEmpty], stage: "plan" | "tests" | "check" | "code" | "review" | "done"})
#Discharge: close({decisions_to: #NonEmpty, design_to: #NonEmpty, lessons_to: #NonEmpty})
#Economics: close({write: close({minutes: int & >0, tag: "measured" | "assumed"})})

#Plan: close({
	id: #NonEmpty
	parent: #Parent | #DeclaredAbsent
	ref: #Sha
	scope: #Scope
	premises: [#Premise, ...#Premise]
	settled: [...#Settled]
	decisions: [...#Decision]
	open: [...#Open]
	deliverables: [#Deliverable, ...#Deliverable]
	gates: [#Gate, ...#Gate]
	units: [...#Unit]
	refusals: [...#NonEmpty] | #DeclaredAbsent
	discharge: #Discharge
	denied: [...#NonEmpty]
	economics: #Economics
})
