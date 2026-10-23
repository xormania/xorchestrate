package agentscient

#NonEmpty: string & !=""
#OutcomeClass: "rejection" | "denial" | "abort" | "finding"
#Refusal: close({
	name!:   =~"^[a-z][a-z0-9-]*(\\.[a-z][a-z0-9-]*)+$"
	class!:  #OutcomeClass
	remedy!: #NonEmpty
})
