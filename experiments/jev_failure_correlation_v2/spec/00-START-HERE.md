# Xorchestrate Jev v2 — Codex Cloud handoff
Prepared for xor on 2026-10-24. Package version 2.0.0.

## Purpose
Implement and execute a reproducible experiment that tests whether Jev helps correlate failure patterns, choose useful probes, and allocate additional investigation economically. Preserve Xorchestrate's configurable hexagonal architecture and established behavior. The experiment must distinguish useful diagnosis, consequential improvement, and reusable learning.

This package is an execution handoff, not completed experimental evidence. No v2 model calls or product changes were made while preparing it. Whether prior published escalations were economically worthwhile remains unproven; neither their usefulness nor their worthlessness is a premise.

## Start the implementation task
Use a fresh Codex Cloud task with access to xormania/xorchestrate. Make this package and the companion Xorchestrate-Jev-v2-Codex-Cloud.md available in its workspace. The companion contains the same current instructions in one document; the ZIP contains retained reference evidence as well.

Send this launch instruction:

> Read Xorchestrate-Jev-v2-Codex-Cloud.md in full and inspect the accompanying Xorchestrate-Jev-v2-Package.zip. Execute 01-EXECUTION-PROMPT.md, governed by the experiment, telemetry/economics, and verification requirements in this package. Preserve v1 and the parallel repair work. Use direct Jev API calls from the configured Codex Cloud environment as the primary route; authenticate and verify the pinned model before measurement. GitHub-hosted Actions remains a fallback. Deliver a draft PR, exact-candidate evidence, and a reproducible economic assessment. Do not merge. If a required external execution route is unavailable, finish independent work and provide the precise resumable blocker without presenting the experiment as complete.

## Contents and reading order
1. 01-EXECUTION-PROMPT.md — the implementation agent's assignment.
2. 02-EXPERIMENT-SPEC.md — scope, comparisons, cases, probes, and acceptance requirements.
3. 03-TELEMETRY-AND-ECONOMICS.md — decision records, economic assessment, and audit design.
4. 04-RESEARCH-AND-SOURCES.md — findings, caveats, and sources.
5. 05-INDEPENDENT-VERIFICATION.md — a separate reviewer's assignment after implementation.
6. reference/ — frozen v1 excerpts and the original independent-review ZIP.
7. reference/SOURCE-INDEX.json and MANIFEST.sha256 — provenance and package integrity.

The original owner brief inside the review ZIP is historical capability/rationale evidence. Do not execute its old implementation assignment. This v2 package does not restart F01–F10 repairs.

The reference/v1 directory is a selected, provenance-checked snapshot, not a standalone project checkout. Use the pinned repository for full replay and implementation. Original archives and reference files are preserved as source material; the six current numbered documents define this assignment.

## Environment and credentials
Python 3.11+ is supported by the inspected project. Prepare its declared test dependencies and git; retain access to GitHub-hosted Actions. Probe tmux/PTY/socket capabilities only for tests that need them. Docker and xorder images are optional execution adapters, not universal prerequisites.

Preferred Jev route, explicitly selected by xor: direct API calls from Codex Cloud. The owner reports that this Cloud UI has no Network secrets control and that an environment variable has already been added. Use TYPESAFE_API_KEY as the expected environment-variable name. Verify its presence during the agent phase without printing its value. If the configured name differs, report the name expected by the client; do not request the credential in chat.

In the actual environment settings, enable agent internet access and allow api.typesafe.ai, including POST if methods are restricted. Save the configuration and start a fresh task with that environment. Use the normal Authorization bearer header and honor the configured HTTPS proxy. Establish usability with a bounded authenticated smoke request to https://api.typesafe.ai/v1/systemone before measurement. A direct environment variable is readable by task processes: never print headers, credential values, or complete environment dumps, and keep the value out of prompts, scripts, artifacts, and git. Log only sanitized access status and model/schema verification.

OpenAI documents more than one Cloud environment surface. Its legacy surface makes ordinary environment variables available through the agent phase, while its Secrets fields are setup-only. The newer surface additionally supports proxy-substituted Network secrets. Do not require the newer control in this owner's UI, move a working runtime variable to a setup-only secret, or copy setup-only secrets into files to bypass their lifecycle. If a later environment uses network-secret placeholders, support that route without trying to reveal the raw key. Runtime smoke evidence establishes which configuration works.

The existing GitHub Actions TYPESAFE_API_KEY secret remains a fallback for hosted execution; it is separate from the Cloud environment variable and does not automatically appear in a Cloud process. Access has not been tested from this preparation session.

Reasoning-model access is a separate prerequisite. Discover an existing authorized provider or a supported, recorded native-session delegation route. A ChatGPT subscription does not establish an API key. If external API access is needed, use the provider's actual secret name and configuration; do not assume OPENAI_API_KEY exists. Do not silently substitute models or simulate missing live evaluations.

For environment UI details, consult the official Cloud environments source in 04-RESEARCH-AND-SOURCES.md. The task must establish actual access with a bounded smoke test.

## Independent verification
After the implementation task publishes its immutable handoff, start a separate session with that evidence and 05-INDEPENDENT-VERIFICATION.md. The reviewer needs the measured commit/tree and run manifests, not just the implementation agent's summary. xor retains merge and release control.

