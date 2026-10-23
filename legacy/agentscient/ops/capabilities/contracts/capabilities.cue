package agentscient

#NonEmpty: string & !=""
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
#Registry: close({capabilities!: [...#Capability]})

#RegistryRefusals: {
	"registry.absent-key": {name: "registry.absent-key", class: "rejection", remedy: "state every capability field"}
	"registry.contract": {name: "registry.contract", class: "rejection", remedy: "satisfy the closed registry contract"}
	"registry.cue-unreachable": {name: "registry.cue-unreachable", class: "abort", remedy: "install cue 0.14.1 on PATH"}
	"registry.duplicate-id": {name: "registry.duplicate-id", class: "rejection", remedy: "use unique capability ids"}
	"registry.fault-intact": {name: "registry.fault-intact", class: "finding", remedy: "plant a fault that differs from its source"}
	"registry.fault-mismatch": {name: "registry.fault-mismatch", class: "finding", remedy: "make the fixture draw its meta refusal"}
	"registry.layer": {name: "registry.layer", class: "rejection", remedy: "depend only on a lower layer"}
	"registry.serves-duplicate": {name: "registry.serves-duplicate", class: "rejection", remedy: "serve each name once"}
	"registry.stale-export": {name: "registry.stale-export", class: "rejection", remedy: "refresh the digest sidecar"}
	"registry.undemonstrated": {name: "registry.undemonstrated", class: "finding", remedy: "add a firing fault fixture"}
	"registry.unknown-dep": {name: "registry.unknown-dep", class: "rejection", remedy: "name a registered dependency"}
	"registry.unknown-iface": {name: "registry.unknown-iface", class: "rejection", remedy: "name an interface exported by the dependency"}
}
