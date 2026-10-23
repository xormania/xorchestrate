package agentscient

#NonEmpty: string & !=""
#OutcomeClass: "rejection" | "denial" | "abort" | "finding"
#Refusal: close({name!: =~"^[a-z][a-z0-9-]*(\\.[a-z][a-z0-9-]*)+$", class!: #OutcomeClass, remedy!: #NonEmpty})
#Iface: close({name!: #NonEmpty, members!: [#NonEmpty, ...#NonEmpty]})
#Dep: close({id!: #NonEmpty, iface!: #NonEmpty})
#Tag: "read" | "write" | "surface.resource" | "command" | "listener" | "handler" | "migrates"
#Capability: close({
	id!: =~"^[a-z][a-z0-9]*(\\.[a-z][a-z0-9]*)*$"
	layer!: 0 | 1 | 2
	module!: string
	iface!: [#Iface, ...#Iface]
	deps!: [...#Dep]
	tags!: [...#Tag]
	stores!: [...#NonEmpty]
	serves!: [...#NonEmpty]
})
#Manifest: close({
	id!: =~"^[a-z][a-z0-9]*(\\.[a-z][a-z0-9]*)*$"
	layer!: 0 | 1 | 2
	module!: string
	iface!: [#Iface, ...#Iface]
	deps!: [...#Dep]
	tags!: [...#Tag]
	stores!: [...#NonEmpty]
	serves!: [...#NonEmpty]
	refusals!: [...#Refusal]
})
#Anatomy: close({
	"capability.json"!: true
	"CONTRACT.md"!: true
	"contract.cue"!: true
	module!: #NonEmpty
	"migrations/"?: true
	"tests/"!: true
	"faults/"!: true
	"guide.md"!: true
})

#AnatomyRefusals: {
	"anatomy.cue-unreachable": {name: "anatomy.cue-unreachable", class: "abort", remedy: "install cue 0.14.1 on PATH"}
	"anatomy.extra": {name: "anatomy.extra", class: "rejection", remedy: "remove unnamed directory entries"}
	"anatomy.fault-intact": {name: "anatomy.fault-intact", class: "finding", remedy: "plant a fault that differs from its source"}
	"anatomy.fault-mismatch": {name: "anatomy.fault-mismatch", class: "finding", remedy: "make the fixture draw its meta refusal"}
	"anatomy.iface-unresolved": {name: "anatomy.iface-unresolved", class: "rejection", remedy: "define every declared Protocol class"}
	"anatomy.manifest": {name: "anatomy.manifest", class: "rejection", remedy: "satisfy #Manifest"}
	"anatomy.member-missing": {name: "anatomy.member-missing", class: "rejection", remedy: "implement every declared interface member"}
	"anatomy.member-undeclared": {name: "anatomy.member-undeclared", class: "rejection", remedy: "declare every public Protocol member"}
	"anatomy.migrates-empty": {name: "anatomy.migrates-empty", class: "rejection", remedy: "ship a migration entry"}
	"anatomy.missing": {name: "anatomy.missing", class: "rejection", remedy: "add the required directory entry"}
	"anatomy.refusals-disagree": {name: "anatomy.refusals-disagree", class: "rejection", remedy: "make all three refusal sets agree"}
	"anatomy.undemonstrated": {name: "anatomy.undemonstrated", class: "finding", remedy: "ship a fault per declared refusal"}
	"anatomy.unparsed": {name: "anatomy.unparsed", class: "finding", remedy: "make the module parseable"}
}
