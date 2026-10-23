// Maintainer-owned capability packages. Runtime profiles remain operator-owned.
package rolepackages

import "list"

#Text: string & !="" & !~"\\x00"
#Strings: [...#Text] & list.UniqueItems()
#Harness: #Text
#Option: {
	default: *null | #Text
	allowed: *null | ([#Text, ...#Text] & list.UniqueItems())
	if allowed != null && default != null {default: or(allowed)}
}
#Runtime: {
	harnesses: *["codex", "claude", "grok"] | ([#Harness, ...#Harness] & list.UniqueItems())
	model:     #Option
	reasoning: #Option
	profiles:  "operator"
}
#Hooks: {
	events: ["PreToolUse", "PostToolUse", "SessionStart", "Stop"]
	policy:            "mcp_only"
	native_delegation: false
}
#Capabilities: {
	bound: *["work_context", "work_report", "guidance_request"] | #Strings
	session: ["onboard", "skill_read", "readiness_report"]
	command: *[] | #Strings
	advice: *[] | #Strings
	job: *[] | #Strings
	job_framework: *[] | #Strings
	work_order: *[] | #Strings
	work_order_framework: *[] | #Strings
}
#Role: {
	version:        2
	behavior:       #Text
	responsibility: #Text
	skills: {
		shared: *"agentscient-context" | #Text
		role:   #Text
		framework: *[] | #Strings
	}
	runtime: #Runtime
	hooks:   #Hooks
	mcp:     #Capabilities
	workspace: {
		editor: *false | bool
		paths: *[] | #Strings
		serena: *false | bool
		if !editor {paths: []}
		if editor {paths: [#Text, ...#Text]}
	}
}

#Catalog: {
	version:          2
	framework_skills: #Strings
	defaults: {[string]: #Text}
	roles: {[string]: #Role}
}

packages: #Catalog & {
	version: 2
	defaults: {
		co:          "co"
		xo:          "xo"
		nco:         "nco"
		planner:     "planner"
		coder:       "coder"
		audit:       "audit"
		docs:        "docs"
		research:    "research"
		arbitrator:  "arbitrator"
		test_writer: "test_writer"
		architect:   "architect"
	}
	framework_skills: ["mate-system-information", "mate-php-environment-check", "mate-symfony-service-inspection", "mate-symfony-request-triage", "mate-symfony-profiler-debugging", "mate-symfony-dotenv-diagnostics"]
	roles: {
		co: {
			behavior:         "co"
			"responsibility": "Act only as xor's interface: relay xor's instructions, scope/specification and context to XO, and relay system messages back to xor. Do not assess, coordinate, execute or accept work."
			"skills": {
				"role": "agentscient-co"
			}
			"mcp": {
				"command": [
					"direct",
					"monitor",
					"roll_call",
				]
			}
		}
		xo: {
			behavior:         "xo"
			"responsibility": "Commission the work-order planner and adopt its jobs. Coordinate one job directly, or appoint one NCO per job for a multi-job plan. Own cross-job coordination, superior steering, job acceptance and final work-order delivery; report the outcome through CO to xor."
			"skills": {
				"role": "agentscient-xo"
				"framework": [
					"mate-system-information",
					"mate-php-environment-check",
					"mate-symfony-service-inspection",
					"mate-symfony-request-triage",
					"mate-symfony-profiler-debugging",
					"mate-symfony-dotenv-diagnostics",
				]
			}
			"mcp": {
				"command": [
					"conclude",
					"monitor",
					"orchestrate",
					"plan_adopt",
					"steer",
				]
				"advice": [
					"work_assess",
				]
				"job": [
					"work_inspect"
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
				]
				"work_order": [
					"work_inspect",
					"work_refresh",
					"work_submit",
					"review_respond",
				]
				"work_order_framework": [
					"work_check"
				]
			}
		}
		coder: {
			behavior:         "coder"
			"responsibility": "Implement the bounded change using installed framework features, preserve unrelated work, validate behavior and submit evidence."
			"skills": {
				"role": "agentscient-coder"
				"framework": [
					"mate-system-information",
					"mate-php-environment-check",
					"mate-symfony-service-inspection",
					"mate-symfony-request-triage",
					"mate-symfony-profiler-debugging",
					"mate-symfony-dotenv-diagnostics",
				]
			}
			"mcp": {
				"advice": [
					"work_assess"
				]
				"job": [
					"work_inspect",
					"work_save",
					"work_refresh",
					"work_recover",
					"work_submit",
					"review_respond",
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
					"framework_generate",
				]
			}
			"workspace": {
				"editor": true
				"paths": [
					"src/",
					"config/",
					"packages/",
					"tests/",
					"templates/",
					"assets/",
					"translations/",
					"public/",
					"migrations/",
					"composer.json",
					"composer.lock",
					"symfony.lock",
					"phpunit.dist.xml",
				]
				"serena": true
			}
		}
		audit: {
			behavior:         "audit"
			"responsibility": "Independently review the submitted immutable revision against the objective; report reproducible findings and do not modify the producer's source."
			"skills": {
				"role": "agentscient-audit"
				"framework": [
					"mate-system-information",
					"mate-php-environment-check",
					"mate-symfony-service-inspection",
					"mate-symfony-request-triage",
					"mate-symfony-profiler-debugging",
					"mate-symfony-dotenv-diagnostics",
				]
			}
			"mcp": {
				"advice": [
					"work_assess"
				]
				"job": [
					"work_inspect",
					"review_record",
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
				]
			}
			"workspace": {
				"serena": true
			}
		}
		security_auditor: {
			behavior:       "audit"
			responsibility: "Independently inspect the submitted immutable revision for security defects within the assigned scope; report reproducible findings with impact and evidence without editing the producer's source."
			skills: {
				role:      "agentscient-security-auditor"
				framework: audit.skills.framework
			}
			mcp:       audit.mcp
			workspace: audit.workspace
		}
		adversarial_review: {
			behavior:       "audit"
			responsibility: "Independently challenge the submitted immutable revision's assumptions and completion claims with counterexamples and boundary cases; report reproducible defects and evidence without editing the producer's source."
			skills: {
				role:      "agentscient-adversarial-review"
				framework: audit.skills.framework
			}
			mcp:       audit.mcp
			workspace: audit.workspace
		}
		docs: {
			behavior:         "docs"
			"responsibility": "Keep user and developer documentation accurate to observed behavior, with runnable examples and explicit limitations."
			"skills": {
				"role": "agentscient-docs"
				"framework": [
					"mate-system-information",
					"mate-symfony-service-inspection",
				]
			}
			"mcp": {
				"advice": [
					"work_assess"
				]
				"job": [
					"work_inspect",
					"work_save",
					"work_refresh",
					"work_recover",
					"work_submit",
					"review_respond",
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
				]
			}
			"workspace": {
				"editor": true
				"paths": [
					"docs/",
					"README.md",
				]
				"serena": true
			}
		}
		research: {
			behavior:         "research"
			"responsibility": "Investigate the question using available sources, distinguish evidence from inference, and report a supported recommendation without source edits."
			"skills": {
				"role": "agentscient-research"
				"framework": [
					"mate-system-information",
					"mate-php-environment-check",
					"mate-symfony-service-inspection",
					"mate-symfony-request-triage",
					"mate-symfony-profiler-debugging",
					"mate-symfony-dotenv-diagnostics",
				]
			}
			"mcp": {
				"advice": [
					"work_assess"
				]
				"job": [
					"work_inspect"
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
				]
			}
			"workspace": {
				"serena": true
			}
		}
		arbitrator: {
			behavior:       "arbitrator"
			responsibility: "Investigate the assigned pending decision using its current context and evidence, then resolve it with a supported rationale within the owner's scope. Do not edit source or coordinate execution."
			skills: role: "agentscient-arbitrator"
			mcp: {
				bound: ["work_context", "work_report", "guidance_request", "decision_resolve"]
				job: ["work_inspect"]
			}
			workspace: serena: true
		}
		test_writer: {
			behavior:         "test_writer"
			"responsibility": "Design behavior tests that expose the relevant failure, cover meaningful boundaries, and retain measured results within test paths."
			"skills": {
				"role": "agentscient-test-writer"
				"framework": [
					"mate-system-information",
					"mate-php-environment-check",
					"mate-symfony-service-inspection",
					"mate-symfony-request-triage",
					"mate-symfony-profiler-debugging",
					"mate-symfony-dotenv-diagnostics",
				]
			}
			"mcp": {
				"advice": [
					"work_assess"
				]
				"job": [
					"work_inspect",
					"work_save",
					"work_refresh",
					"work_recover",
					"work_submit",
					"review_respond",
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
					"framework_generate",
				]
			}
			"workspace": {
				"editor": true
				"paths": [
					"tests/",
					"phpunit.dist.xml",
				]
				"serena": true
			}
		}
		architect: {
			behavior:         "architect"
			"responsibility": "Inspect existing capabilities, compare designs and tradeoffs, and record the smallest supported design within the documentation scope."
			"skills": {
				"role": "agentscient-architect"
				"framework": [
					"mate-system-information",
					"mate-symfony-service-inspection",
				]
			}
			"mcp": {
				"advice": [
					"work_assess"
				]
				"job": [
					"work_inspect",
					"work_save",
					"work_refresh",
					"work_recover",
					"work_submit",
					"review_respond",
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
				]
			}
			"workspace": {
				"editor": true
				"paths": [
					"docs/"
				]
				"serena": true
			}
		}
		nco: {
			behavior:         "nco"
			"responsibility": "Coordinate specialist role assignments within your one assigned job; inspect their evidence and report the checked job ready for XO acceptance. Request planning and scope changes through XO; do not commission planners or NCOs."
			"skills": {
				"role": "agentscient-nco"
				"framework": [
					"mate-system-information",
					"mate-php-environment-check",
					"mate-symfony-service-inspection",
					"mate-symfony-request-triage",
					"mate-symfony-profiler-debugging",
					"mate-symfony-dotenv-diagnostics",
				]
			}
			"mcp": {
				"command": [
					"monitor",
					"orchestrate",
					"steer",
				]
				"advice": [
					"work_assess",
				]
				"job": [
					"work_inspect"
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
				]
				"work_order": [
					"work_inspect"
				]
				"work_order_framework": [
					"work_check"
				]
			}
		}
		planner: {
			behavior:         "planner"
			"responsibility": "Inspect XO's work-order context and existing capabilities, then propose jobs with scope, requirements, dependencies and completion criteria. Report the retained plan to XO without editing source or coordinating execution."
			"skills": {
				"role": "agentscient-planner"
				"framework": [
					"mate-system-information",
					"mate-php-environment-check",
					"mate-symfony-service-inspection",
					"mate-symfony-request-triage",
					"mate-symfony-profiler-debugging",
					"mate-symfony-dotenv-diagnostics",
				]
			}
			"mcp": {
				"advice": [
					"work_assess"
				]
				"job": [
					"work_inspect"
				]
				"job_framework": [
					"framework_inspect",
					"work_check",
				]
			}
			"workspace": {
				"serena": true
			}
		}
	}
}
