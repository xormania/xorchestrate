package contracts

import "list"

// OPEN DEFINITION: PLAN.md constrains effective flags, not argparse carrier fields.
// PLAN.md SS4 lines 261-265
#GlobalArgument: {
	// PLAN.md SS4 lines 261-265
	flags!: [string]
	// PLAN.md SS4 lines 261-265
	dest: *null | string
	// PLAN.md SS4 lines 261-265
	action: *null | string
	// PLAN.md SS4 lines 261-265
	nargs: *null | string
	// PLAN.md SS4 lines 261-265
	choices: *null | [...string]
	// PLAN.md SS4 lines 261-265
	default_env: *null | string
	...
}

// OPEN DEFINITION: refinement for a globally observed JSON flag.
// PLAN.md SS4 lines 264-265 -- CLI-S03 (stated)
#GlobalJsonArgument: #GlobalArgument & {
	// PLAN.md SS4 lines 264-265 -- CLI-S03 (stated)
	flags: ["--json"]
	...
}

// OPEN DEFINITION: PLAN.md does not exhaustively specify flag observation metadata.
// PLAN.md SS4 lines 243-265
#FlagArgument: {
	// PLAN.md SS4 lines 243-265 (flag observations are distinct from positionals).
	positional?: _|_
	// PLAN.md SS4 lines 243-265
	flags!: [string]
	// PLAN.md SS4 lines 243-265
	action: *null | string
	// PLAN.md SS4 lines 243-265
	nargs: *null | string
	// PLAN.md SS4 lines 243-265
	choices: *null | [...string]
	// PLAN.md SS4 lines 243-265
	required: *null | bool
	// PLAN.md SS4 lines 243-265
	_all_flags: flags
	// PLAN.md SS4 lines 243-265
	_is_positional: false
	// PLAN.md SS4 lines 243-265
	_id: flags[0]

	// PLAN.md SS4 lines 246-251 -- CLI-S02 (stated)
	if _id == "--kind" || _id == "--objective" || _id == "--by" {
		// PLAN.md SS4 lines 246-251 -- CLI-S02 (stated)
		required: true | null
	}

	// PLAN.md SS4 lines 243-265 -- CLI-S02 (stated)
	if _id != "--kind" && _id != "--objective" && _id != "--by" && _id != "--agent" {
		// PLAN.md SS4 lines 243-265 -- CLI-S02 (stated)
		required: false | null
	}

	// PLAN.md SS5 lines 299-301 -- CLI-I01 (inferred)
	if _id == "--source" {
		// PLAN.md SS5 lines 299-301 -- CLI-I01 (inferred)
		action: "append"
	}
	...
}

// OPEN DEFINITION: PLAN.md does not exhaustively specify positional observation metadata.
// PLAN.md SS4 lines 243-259
#PositionalArgument: {
	// PLAN.md SS4 lines 243-259 (positional observations are distinct from flags).
	flags?: _|_
	// PLAN.md SS4 lines 243-259
	positional!: string
	// PLAN.md SS4 lines 243-259
	action: *null | string
	// PLAN.md SS4 lines 243-259
	nargs: *null | string
	// PLAN.md SS4 lines 243-259
	choices: *null | [...string]
	// PLAN.md SS4 lines 243-259
	required: *null | bool
	// PLAN.md SS4 lines 243-259
	_all_flags: []
	// PLAN.md SS4 lines 243-259
	_is_positional: true
	// PLAN.md SS4 lines 243-259
	_id: positional
	...
}

// OPEN DEFINITION: refinement for the documented marker action.
// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
#MarkerChoiceArgument: #PositionalArgument & {
	// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated marker action position).
	positional: "open|close"
	// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
	choices: ["open", "close"] | ["close", "open"]
	// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
	nargs: null
	// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
	required: true | null
	...
}

// OPEN UNION DEFINITION: flag and positional observations remain extensible.
// PLAN.md SS4 lines 243-265
#CommandArgument: #FlagArgument | #PositionalArgument

// OPEN DEFINITION: unresolved observation details have no PLAN-defined form.
// PLAN.md SS4 lines 276-283
#Unresolved: {
	// PLAN.md SS4 lines 276-283
	unresolved!: string
	...
}

// OPEN DEFINITION: PLAN.md constrains effective command behavior, not parser placement.
// PLAN.md SS4 lines 243-283
#Command: {
	// PLAN.md SS4 lines 261-265 (carrier supplied by #CliFacts for placement-neutral flags).
	_global_arguments!: [...#GlobalArgument]
	// PLAN.md SS4 lines 243-259
	name!: string
	// PLAN.md SS4 lines 243-265
	arguments!: [...#CommandArgument]
	// PLAN.md SS4 lines 254-265
	next_json_keys: *"n/a" | [...string] | #Unresolved
	// PLAN.md SS4 lines 243-265
	helpers_applied?: [...string]

	// PLAN.md SS4 lines 243-265
	for _, argument in arguments {
		// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--to" {
			// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
			_has_to: true
		}

		// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--reason" {
			// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
			_has_reason: true
		}

		// PLAN.md SS4 lines 261-264 -- CLI-S04 (stated)
		for _, flag in argument._all_flags if flag == "--agent" {
			// PLAN.md SS4 lines 261-264 -- CLI-S04 (stated)
			_has_agent: true
			// PLAN.md SS4 lines 261-264 -- CLI-S04 (stated)
			if argument.required == false || argument.required == null {
				// PLAN.md SS4 lines 261-264 -- CLI-S04 (stated)
				_has_env_compatible_agent: true
			}
		}

		// PLAN.md SS5 lines 289-303 -- CLI-S07 (stated)
		for _, flag in argument._all_flags if flag == "--source" {
			// PLAN.md SS5 lines 299-301 -- CLI-S07 (stated)
			_has_source: true
			// PLAN.md SS5 lines 299-301 -- CLI-I01 (inferred)
			if argument.action == "append" {
				// PLAN.md SS5 lines 299-301 -- CLI-I01 (inferred)
				_has_repeatable_source: true
			}
		}

		// PLAN.md SS5 lines 289-303 -- CLI-S06 (stated)
		for _, flag in argument._all_flags if flag == "--no-commit" {
			// PLAN.md SS5 lines 289-303 -- CLI-S06 (stated)
			_has_no_commit: true
		}

		// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--verify-only" {
			// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
			_has_verify_only: true
		}

		// PLAN.md SS12 lines 542-543 -- CLI-S08 (stated)
		for _, flag in argument._all_flags if flag == "--allow-dirty" {
			// PLAN.md SS12 lines 542-543 -- CLI-S08 (stated)
			_has_allow_dirty: true
		}

		// PLAN.md SS4 lines 247-247 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--ttl" {
			// PLAN.md SS4 lines 247-247 -- CLI-S02 (stated)
			_has_ttl: true
		}

		// PLAN.md SS4 lines 245-245 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--rebuild" {
			// PLAN.md SS4 lines 245-245 -- CLI-S02 (stated)
			_has_rebuild: true
		}

		// PLAN.md SS4 lines 256-256 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--attempts" {
			// PLAN.md SS4 lines 256-256 -- CLI-S02 (stated)
			_has_attempts: true
		}

		// PLAN.md SS4 lines 251-252 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--note" {
			// PLAN.md SS4 lines 251-252 -- CLI-S02 (stated)
			_has_note: true
		}

		// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--kind" {
			// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
			_has_kind: true
		}

		// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--objective" {
			// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
			_has_objective: true
		}

		// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--by" {
			// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
			_has_by: true
		}

		// PLAN.md SS4 lines 258-258 -- CLI-S02 (stated)
		for _, flag in argument._all_flags if flag == "--deep" {
			// PLAN.md SS4 lines 258-258 -- CLI-S02 (stated)
			_has_deep: true
		}

		// PLAN.md SS4 lines 264-265 -- CLI-S03 (stated)
		for _, flag in argument._all_flags if flag == "--json" {
			// PLAN.md SS4 lines 264-265 -- CLI-S03 (stated)
			_has_json: true
		}
	}

	// PLAN.md SS4 lines 261-265 (stated requirements are placement-neutral).
	for _, argument in _global_arguments {
		// PLAN.md SS4 lines 261-264 (stated).
		for _, flag in argument.flags if flag == "--agent" {
			// PLAN.md SS4 lines 261-264 (stated).
			_has_agent: true
			// PLAN.md SS4 lines 261-264 (stated: $WF_AGENT is the alternative source).
			_has_env_compatible_agent: true
		}

		// PLAN.md SS5 lines 299-301 (stated).
		for _, flag in argument.flags if flag == "--source" {
			// PLAN.md SS5 lines 299-301 (stated).
			_has_source: true
			// PLAN.md SS5 lines 299-301 (stated: repeatable).
			if argument.action == "append" {
				// PLAN.md SS5 lines 299-301 (stated: repeatable).
				_has_repeatable_source: true
			}
		}

		// PLAN.md SS5 lines 289-303 (stated).
		for _, flag in argument.flags if flag == "--no-commit" {
			// PLAN.md SS5 lines 289-303 (stated).
			_has_no_commit: true
		}

		// PLAN.md SS12 lines 542-543 (stated).
		for _, flag in argument.flags if flag == "--allow-dirty" {
			// PLAN.md SS12 lines 542-543 (stated).
			_has_allow_dirty: true
		}

		// PLAN.md SS4 lines 264-265 (stated).
		for _, flag in argument.flags if flag == "--json" {
			// PLAN.md SS4 lines 264-265 (stated).
			_has_json: true
		}
	}

	// PLAN.md SS4 lines 264-265 (stated: every command has JSON output mode).
	_has_json!: true

	// PLAN.md SS4 lines 243-259
	for _, argument in arguments if argument._is_positional {
		// PLAN.md SS4 lines 246-257 -- CLI-S02 (stated)
		if argument._id == "wo" {
			// PLAN.md SS4 lines 246-257 -- CLI-S02 (stated)
			_has_wo: true
			// PLAN.md SS4 lines 246-256 -- CLI-S02 (stated)
			if argument.nargs == null && (argument.required == true || argument.required == null) {
				// PLAN.md SS4 lines 246-256 -- CLI-S02 (stated)
				_has_required_wo: true
			}

			// PLAN.md SS4 lines 254-257 -- CLI-I02 (brackets state optionality; argparse form is not specified).
			if (argument.nargs == "?" || argument.nargs == null) && (argument.required == false || argument.required == null) {
				// PLAN.md SS4 lines 254-257 -- CLI-I02 (stated optionality, unconstrained carrier form).
				_has_optional_wo: true
			}
		}

		// PLAN.md SS4 lines 248-250 -- CLI-S02 (stated)
		if argument._id == "cmd" {
			// PLAN.md SS4 lines 248-250 -- CLI-S02 (stated)
			_has_cmd: true
			// PLAN.md SS4 lines 248-249 -- CLI-S02 (stated)
			if argument.required == true || argument.required == null {
				// PLAN.md SS4 lines 248-249 -- CLI-S02 (stated)
				_has_required_cmd: true
			}

			// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
			if argument.required == false || argument.required == null {
				// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
				_has_optional_cmd: true
			}
		}

		// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
		if argument._id == "check-name" {
			// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
			_has_check_name: true
			// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
			if argument.nargs == null && (argument.required == true || argument.required == null) {
				// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
				_has_required_check_name: true
			}
		}

		// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
		if argument._id == "value" {
			// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
			_has_value: true
			// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
			if argument.nargs == null && (argument.required == true || argument.required == null) {
				// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
				_has_required_value: true
			}
		}

		// PLAN.md SS4 lines 252-252 -- CLI-S02 (stated)
		if argument._id == "id" {
			// PLAN.md SS4 lines 252-252 -- CLI-S02 (stated)
			_has_id: true
			// PLAN.md SS4 lines 252-252 -- CLI-S02 (stated)
			if argument.nargs == null && (argument.required == true || argument.required == null) {
				// PLAN.md SS4 lines 252-252 -- CLI-S02 (stated)
				_has_required_id: true
			}
		}
	}

	// PLAN.md SS4 lines 243-259 -- CLI-S02 (stated)
	_duplicate_argument_id: false
	// PLAN.md SS4 lines 243-259 -- CLI-S02 (stated)
	for leftIndex, left in arguments {
		// PLAN.md SS4 lines 243-259 -- CLI-S02 (stated)
		for rightIndex, right in arguments if leftIndex < rightIndex && left._id == right._id {
			// PLAN.md SS4 lines 243-259 -- CLI-S02 (stated)
			_duplicate_argument_id: true
		}
	}

	// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
	for _, argument in arguments if argument.choices != null {
		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		for _, choice in argument.choices if choice == "open" {
			// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
			_has_open_choice: true
		}

		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		for _, choice in argument.choices if choice == "close" {
			// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
			_has_close_choice: true
		}

		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		if argument._is_positional && argument._id != "wo" && argument._id != "id" && len(argument.choices) == 2 && ((argument.choices[0] == "open" && argument.choices[1] == "close") || (argument.choices[0] == "close" && argument.choices[1] == "open")) {
			// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
			_has_marker_action: true
		}
	}

	// PLAN.md SS5 lines 289-303 -- CLI-S04/CLI-S06/CLI-S07 (stated)
	if name == "open" || name == "red" || name == "green" || name == "check" || name == "outcome" || name == "marker" || name == "advance" {
		// PLAN.md SS4 lines 261-264 -- CLI-S04 (stated)
		_has_agent!: true
		// PLAN.md SS4 lines 261-264 -- CLI-S04 (stated)
		_has_env_compatible_agent!: true
		// PLAN.md SS5 lines 289-303 -- CLI-S06 (stated)
		_has_no_commit!: true
		// PLAN.md SS5 lines 299-301 -- CLI-S07 (stated)
		_has_source!: true
		// PLAN.md SS5 lines 299-301 -- CLI-I01 (inferred)
		_has_repeatable_source!: true
	}

	// PLAN.md SS12 lines 542-543 -- CLI-S08 (stated)
	if name == "red" || name == "green" || name == "check" {
		// PLAN.md SS12 lines 542-543 -- CLI-S08 (stated)
		_has_allow_dirty!: true
	}

	// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
	if name == "advance" {
		// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--to" | "--reason" | "--agent" | "--source" | "--no-commit" | "--json"})]
		// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
		_has_to!: true
		// PLAN.md SS4 lines 253-253 -- CLI-S02 (stated)
		_has_reason!: true
	}

	// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
	if name == "check" {
		// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "check-name" | "cmd" | "--verify-only" | "--agent" | "--source" | "--no-commit" | "--allow-dirty" | "--json"})]
		// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
		_has_required_check_name!: true
		// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
		_has_optional_cmd!: true
		// PLAN.md SS4 lines 250-250 -- CLI-S02 (stated)
		_has_verify_only!: true
	}

	// PLAN.md SS4 lines 247-247 -- CLI-S09 (stated)
	if name == "claim" {
		// PLAN.md SS4 lines 247-247 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--agent" | "--ttl" | "--json"})]
		// PLAN.md SS4 lines 247-247 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 247-247 -- CLI-S09 (stated)
		_has_agent!: true
		// PLAN.md SS4 lines 247-247 -- CLI-S02 (stated)
		_has_ttl!: true
	}

	// PLAN.md SS4 lines 257-257 -- CLI-S02/CLI-I02 (stated/inferred)
	if name == "commit" {
		// PLAN.md SS4 lines 257-257 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--json"})]
		// PLAN.md SS4 lines 257-257 -- CLI-I02 (inferred)
		_has_optional_wo!: true
	}

	// PLAN.md SS4 lines 259-259 -- CLI-S02 (stated)
	if name == "export" {
		// PLAN.md SS4 lines 259-259 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "--json"})]
	}

	// PLAN.md SS4 lines 249-249 -- CLI-S02 (stated)
	if name == "green" {
		// PLAN.md SS4 lines 249-249 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "cmd" | "--agent" | "--source" | "--no-commit" | "--allow-dirty" | "--json"})]
		// PLAN.md SS4 lines 249-249 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 249-249 -- CLI-S02 (stated)
		_has_required_cmd!: true
	}

	// PLAN.md SS4 lines 245-245 -- CLI-S02 (stated)
	if name == "init" {
		// PLAN.md SS4 lines 245-245 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "--rebuild" | "--json"})]
		// PLAN.md SS4 lines 245-245 -- CLI-S02 (stated)
		_has_rebuild!: true
	}

	// PLAN.md SS4 lines 256-256 -- CLI-S02 (stated)
	if name == "log" {
		// PLAN.md SS4 lines 256-256 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--attempts" | "--json"})]
		// PLAN.md SS4 lines 256-256 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 256-256 -- CLI-S02 (stated)
		_has_attempts!: true
	}

	// PLAN.md SS4 lines 252-252 -- CLI-S02/CLI-S10 (stated)
	if name == "marker" {
		// PLAN.md SS4 lines 252-252 -- CLI-S02/CLI-S10 (stated)
		arguments: [...((#CommandArgument & {_id: "wo" | "id" | "--note" | "--agent" | "--source" | "--no-commit" | "--json"}) | #MarkerChoiceArgument)]
		// PLAN.md SS4 lines 252-252 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 252-252 -- CLI-S02 (stated)
		_has_required_id!: true
		// PLAN.md SS4 lines 252-252 -- CLI-S02 (stated)
		_has_note!: true
		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		_has_open_choice!: true
		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		_has_close_choice!: true
		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		_has_marker_action!: true
		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		_duplicate_marker_action: false
		// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
		for leftIndex, left in arguments if left._is_positional && left._id != "wo" && left._id != "id" {
			// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
			for rightIndex, right in arguments if leftIndex < rightIndex && right._is_positional && right._id != "wo" && right._id != "id" {
				// PLAN.md SS4 lines 252-252 -- CLI-S10 (stated)
				_duplicate_marker_action: true
			}
		}
	}

	// PLAN.md SS4 lines 254-254 -- CLI-I02 (inferred)
	if name == "next" {
		// PLAN.md SS4 lines 254-254 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--json"})]
		// PLAN.md SS4 lines 254-254 -- CLI-I02 (inferred)
		_has_optional_wo!: true
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		next_json_keys: [...string]
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_duplicate_next_key: false
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for leftIndex, left in next_json_keys {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			for rightIndex, right in next_json_keys if leftIndex < rightIndex && left == right {
				// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
				_duplicate_next_key: true
			}
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for _, key in next_json_keys if key == "wo" {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			_next_has_wo: true
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for _, key in next_json_keys if key == "kind" {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			_next_has_kind: true
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for _, key in next_json_keys if key == "stage" {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			_next_has_stage: true
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for _, key in next_json_keys if key == "satisfied" {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			_next_has_satisfied: true
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for _, key in next_json_keys if key == "missing" {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			_next_has_missing: true
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for _, key in next_json_keys if key == "advances" {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			_next_has_advances: true
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		for _, key in next_json_keys if key == "claim" {
			// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
			_next_has_claim: true
		}

		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_next_has_wo!: true
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_next_has_kind!: true
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_next_has_stage!: true
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_next_has_satisfied!: true
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_next_has_missing!: true
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_next_has_advances!: true
		// PLAN.md SS4 lines 276-283 -- CLI-X01 (illustrative sketch)
		_next_has_claim!: true
	}

	// PLAN.md SS4 lines 243-259
	if name != "next" {
		// PLAN.md SS4 lines 243-259
		next_json_keys: "n/a"
	}

	// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
	if name == "open" {
		// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--kind" | "--objective" | "--agent" | "--source" | "--no-commit" | "--json"})]
		// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
		_has_kind!: true
		// PLAN.md SS4 lines 246-246 -- CLI-S02 (stated)
		_has_objective!: true
	}

	// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
	if name == "outcome" {
		// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "value" | "--by" | "--note" | "--agent" | "--source" | "--no-commit" | "--json"})]
		// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
		_has_required_value!: true
		// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
		_has_by!: true
		// PLAN.md SS4 lines 251-251 -- CLI-S02 (stated)
		_has_note!: true
	}

	// PLAN.md SS4 lines 248-248 -- CLI-S02 (stated)
	if name == "red" {
		// PLAN.md SS4 lines 248-248 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "cmd" | "--agent" | "--source" | "--no-commit" | "--allow-dirty" | "--json"})]
		// PLAN.md SS4 lines 248-248 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 248-248 -- CLI-S02 (stated)
		_has_required_cmd!: true
	}

	// PLAN.md SS4 lines 247-247 -- CLI-S09 (stated)
	if name == "release" {
		// PLAN.md SS4 lines 247-247 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--agent" | "--json"})]
		// PLAN.md SS4 lines 247-247 -- CLI-S02 (stated)
		_has_required_wo!: true
		// PLAN.md SS4 lines 247-247 -- CLI-S09 (stated)
		_has_agent!: true
	}

	// PLAN.md SS4 lines 255-255 -- CLI-I02 (inferred)
	if name == "status" {
		// PLAN.md SS4 lines 255-255 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "wo" | "--json"})]
		// PLAN.md SS4 lines 255-255 -- CLI-I02 (inferred)
		_has_optional_wo!: true
	}

	// PLAN.md SS4 lines 258-258 -- CLI-S02 (stated)
	if name == "verify" {
		// PLAN.md SS4 lines 258-258 -- CLI-S02 (stated)
		arguments: [...(#CommandArgument & {_id: "--deep" | "--json"})]
		// PLAN.md SS4 lines 258-258 -- CLI-S02 (stated)
		_has_deep!: true
	}
	...
}

// OPEN DEFINITION: global parser placement and observation metadata are not exhaustive.
// PLAN.md SS4 lines 236-283
#CliFacts: {
	// PLAN.md SS4 lines 261-265 (the required capabilities may be registered globally or per command).
	GlobalArguments=global_arguments?: [...#GlobalArgument]

	// OPEN DEFINITION: binds the optional global carrier into each observed command.
	// PLAN.md SS4 lines 261-265
	#ObservedCommand: #Command & {
		// PLAN.md SS4 lines 261-265 (no global carrier means no globally supplied flags).
		if GlobalArguments == _|_ {
			_global_arguments: []
		}

		// PLAN.md SS4 lines 261-265 (globally registered documented flags satisfy each command).
		if GlobalArguments != _|_ {
			_global_arguments: GlobalArguments
		}
		...
	}

	// PLAN.md SS4 lines 238-259 -- CLI-S01 (stated exact command set; observation order is unconstrained).
	Commands=commands!: [...#ObservedCommand]
	// PLAN.md SS4 lines 238-259 -- CLI-S01 (stated exact command count).
	_commandCount: len(Commands) & 16
	// PLAN.md SS4 lines 238-259 -- CLI-S01 (helper over the stated command set).
	_commandNames: [for command in Commands {command.name}]
	// PLAN.md SS4 lines 238-259 -- CLI-S01 (stated exact command set).
	_requiredCommands: [
		list.Contains(_commandNames, "init"),
		list.Contains(_commandNames, "open"),
		list.Contains(_commandNames, "claim"),
		list.Contains(_commandNames, "release"),
		list.Contains(_commandNames, "red"),
		list.Contains(_commandNames, "green"),
		list.Contains(_commandNames, "check"),
		list.Contains(_commandNames, "outcome"),
		list.Contains(_commandNames, "marker"),
		list.Contains(_commandNames, "advance"),
		list.Contains(_commandNames, "next"),
		list.Contains(_commandNames, "status"),
		list.Contains(_commandNames, "log"),
		list.Contains(_commandNames, "commit"),
		list.Contains(_commandNames, "verify"),
		list.Contains(_commandNames, "export"),
	] & [...true]
	// PLAN.md SS4 lines 261-265
	env_literals!: [...string]

	// PLAN.md SS4 lines 261-263 -- CLI-S05 (stated)
	for _, env in env_literals if env == "WF_AGENT" {
		// PLAN.md SS4 lines 261-263 -- CLI-S05 (stated)
		_has_wf_agent: true
	}

	// PLAN.md SS4 lines 261-263 -- CLI-S05 (stated)
	_has_wf_agent!: true
	...
}

// OPEN DEFINITION: provenance and observation carriers remain extensible.
// PLAN.md SS4 lines 236-283
#Cli: {
	// PLAN.md SS4 lines 236-283
	facts!: #CliFacts
	// PLAN.md SS4 lines 236-283
	provenance!: _
	...
}
