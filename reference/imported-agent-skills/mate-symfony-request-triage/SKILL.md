---
name: mate-symfony-request-triage
description: Choose profiler, service inspection or retained logs when the source of a request failure is unclear.
---

For a wiring symptom (missing service, wrong implementation, absent listener), load
`mate-symfony-service-inspection`. If a profile token or diagnostic source is already
known, go directly to that source.

Otherwise inspect the schema of `symfony-profiler-list` with `framework_inspect`,
then call it using `kind="mate", name="symfony-profiler-list", parameters={"limit":1}`.
Filter by a known URL or status when appropriate. A matching profile leads to
`mate-symfony-profiler-debugging`.

If profiles are unavailable, use supplied log/evidence references through `work_context`.
Discover any installed log diagnostic with `framework_inspect(kind="mate")` before
calling it. Request missing evidence through `guidance_request`; do not invent a tool.
Cross-request timing/frequency questions need logs or aggregated evidence, not one profile.
