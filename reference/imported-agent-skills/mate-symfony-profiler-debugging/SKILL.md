---
name: mate-symfony-profiler-debugging
description: Diagnose a specific failed, slow or incorrect Symfony request using its profiler data.
---

Inspect schemas with `framework_inspect(kind="mate", name=<diagnostic>)`; add
`parameters` to execute. Find a profile using `symfony-profiler-list`, filtering by
known `url`, `method`, `statusCode`, time range or a small `limit`. For example,
`parameters={"statusCode":500,"limit":5}`. `symfony-profiler-get` with `{"token":<token>}`
returns metadata, not the collector list.

Read the returned profile URI with `framework_inspect(kind="mate_resource", resource=<URI>)`.
It lists available collector URIs. Read only relevant returned URIs:
- Failure: `exception` for class/message/file/line/trace, then `request` and `logger`
  if the profile has no exception. Check a sub-request's parent profile when indicated.
- Slow success: `time` first, then `db` or `memory` as the timing indicates. Grouped SQL
  counts and duplicate-query counts distinguish N+1 patterns from one slow query.
- Incorrect output/input: `request` for route/status and sanitized request/session bags.
- Request-specific logs: `logger` for counts and messages. Global trends need other evidence.

No profiles can mean disabled/cleared storage or no recorded requests. Request retained
logs/evidence rather than inventing profiler data. Re-list a stale token. If a collector
is missing, re-read the profile's collector list. Prefer collector summaries before
full data. Respect truncation indicators and redacted fields.
