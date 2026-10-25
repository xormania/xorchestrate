package board

#NonEmpty: string & !=""
#Timestamp: string & =~"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$"
#SHA: string & =~"^[0-9a-f]{40}$"
#Digest: string & =~"^[0-9a-f]{64}$"
#ObservationID: string & =~"^obs\\.[a-z0-9][a-z0-9.-]*$"
#DecisionID: string & =~"^[a-z0-9]+(-[a-z0-9]+)*$"
#Evidence: [#ObservationID, ...#ObservationID]

#Ref: #SHA | close({
	not_applicable: #NonEmpty
})

#Environment: close({
	lane?:            "dev"
	METERS_DB?:       #NonEmpty
	DISPATCH_JOBS?:   #NonEmpty
	XDG_STATE_HOME?:  #NonEmpty
	LANG?:            #NonEmpty
	LC_ALL?:          #NonEmpty
	TZ?:              #NonEmpty
})

#ObservationFields: {
	state:      "measured" | "failed"
	started_at: #Timestamp
	ended_at:   #Timestamp
	command:    [#NonEmpty, ...#NonEmpty]
	cwd:        #NonEmpty
	environment: #Environment
	ref:        #Ref
	exit:       int
	raw_sha256: #Digest
}

#MeasuredObservation: close(#ObservationFields & {
	state: "measured"
	exit:  0
})

#FailedObservation: close(#ObservationFields & {
	state: "failed"
	exit:  int & !=0
})

#Observation: #MeasuredObservation | #FailedObservation

#Refusal: close({
	code:     #NonEmpty
	expected: #NonEmpty
	found:    #NonEmpty
	satisfy:  #NonEmpty
})

#Unavailable: close({
	state:    "unmeasured"
	refusal:  #Refusal
	evidence: #Evidence
})

#Derived: close({
	expression: #NonEmpty
	inputs:     #Evidence
})

#Branch: close({
	lineage:    #NonEmpty
	tip:        #SHA
	target:     #NonEmpty
	target_tip: #SHA
	ahead:      int & >0
	open_pr:    false
	scope:      "local-head-ref"
	evidence:   #Evidence
	derived?:   #Derived
})

#Checks: close({
	passing:          [...#NonEmpty]
	failing:          [...#NonEmpty]
	pending:          [...#NonEmpty]
	skipped:          [...#NonEmpty]
	cancelled:        [...#NonEmpty]
	unreadable:       [...#NonEmpty]
	missing_expected: [...#NonEmpty]
})

#Review: close({
	last_review_at: #Timestamp | null
	reviewer:       #NonEmpty | null
	age_seconds:    (int & >=0) | null
})

#Thread: close({
	path: #NonEmpty
	line?: int & >=1
})

#PR: close({
	number:                  int & >=1
	title:                   #NonEmpty
	lineage:                 #NonEmpty
	head_sha:                #SHA
	base:                    #NonEmpty
	draft:                   bool
	merge_state:             #NonEmpty
	checks:                  #Checks
	blocking_at_observation: #NonEmpty | null
	review:                  #Review
	unresolved_threads:      [...#Thread]
	evidence:                #Evidence
})

#Decision: close({
	id:         #DecisionID
	question:   #NonEmpty
	path:       "ops/docs/TODO.md"
	line:       int & >=1
	source_ref: #SHA
	evidence:   #Evidence
})

#Record: close({
	path:    #NonEmpty
	lineage: #NonEmpty
	unit:    #NonEmpty
	job:     #NonEmpty
})

#Dispatch: close({
	id:                   #NonEmpty
	state:                "running" | "finished" | "tripped" | "DIED" | "unlaunched"
	stage:                #NonEmpty
	harness:              #NonEmpty
	record?:              #Record | #Unavailable
	launched_at:          #Timestamp | #Unavailable
	age_since_launch_s:   (int & >=0) | #Unavailable
	evidence:             #Evidence
	derived?:             #Derived
})

#Item: #Branch | #PR | #Decision | #Dispatch

#MeasuredSection: close({
	state:    "measured"
	evidence: #Evidence
	items:    [...#Item]
})

#CachedSection: close({
	state:                      "cached"
	evidence:                   #Evidence
	items:                      [...#Item]
	observed_at:                #Timestamp
	read_at:                    #Timestamp
	age_seconds:                int & >=0
	successful_observation:     #ObservationID
	current_observation:        #ObservationID
	failed_refresh_observation?: #ObservationID
})

#UnmeasuredSection: close({
	state:    "unmeasured"
	evidence: #Evidence
	refusal:  #Refusal
})

#Section: #MeasuredSection | #CachedSection | #UnmeasuredSection

#Repository: close({
	root:     #NonEmpty
	head:     #SHA
	branch:   #NonEmpty | null
	evidence: #Evidence
})

#Board: close({
	schema:     "agentscient.board/v1"
	query:      "branches" | "prs" | "decisions" | "dispatches" | "all"
	started_at: #Timestamp
	ended_at:   #Timestamp
	repository: #Repository
	observations: {
		[#ObservationID]: #Observation
	}
	sections: close({
		branches?:   #Section
		prs?:        #Section
		decisions?:  #Section
		dispatches?: #Section
	})
})
