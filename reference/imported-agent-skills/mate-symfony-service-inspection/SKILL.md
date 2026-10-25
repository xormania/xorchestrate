---
name: mate-symfony-service-inspection
description: Diagnose dependency-injection wiring, missing services, aliases, tags, factories and decorators.
---

Inspect installed schemas through `framework_inspect(kind="mate", name=<diagnostic>)`.
Execute by adding `parameters`. Locate candidates with `name="symfony-services"`
and parameters such as `{"query":"MailerInterface","limit":10}` or
`{"tag":"kernel.event_listener","limit":10}`. Narrow a truncated list.
In multi-kernel projects, results are grouped by context. Select the relevant returned
context and carry it into subsequent list/detail calls; the same id can exist in
different containers. Read one exact returned id with `name="symfony-service-detail"`
and `parameters={"id":<id>,"context":<selected context>}`. Omit `context` when none is returned.

Interpret the compiled definition:
- Wrong implementation: query the interface, then inspect its alias id; aliases resolve
  to the target class, arguments and tags.
- Missing listener/tag: inspect `tags`, their event/priority attributes, `autoconfigured`,
  and the expected interface/attribute. `container.excluded` and a tag's `source` can
  explain resource exclusions or abstract classes.
- Unexpected construction: inspect `factory`, constructor `arguments` and setter `calls`.
  Follow service references one level at a time; collections describe chains/tagged groups.
- Synthetic services are supplied at runtime; empty constructor arguments can also
  mean there are no arguments or injection happens through setters.

Ids are case-sensitive; copy the returned id instead of guessing. An empty filtered
map differs from a missing compiled container. Report missing setup through
`guidance_request`; use PHP environment inspection when XML parsing may be unavailable.
The available compiled environment may differ from the failing request's environment.
Masked scalar arguments do not expose secrets; do not reconstruct them from masks.
