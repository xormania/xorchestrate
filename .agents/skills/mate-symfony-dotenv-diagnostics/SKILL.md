---
name: mate-symfony-dotenv-diagnostics
description: Diagnose missing or overridden environment variables using masked provenance instead of raw secret values.
---

Inspect `framework_inspect(kind="mate", name="symfony-dotenv-check")`, then execute
with `parameters={"key":"APP_ENV"}` for one name or a small `limit` for discovery.
The diagnostic reports declarations, resolution in the diagnostic process, length,
masked preview and placeholder hints; it does not reveal the raw value.

Interpret `state`:
- `file`: resolves to a value declared in a project file.
- `ambient_override`: resolves differently from its file declarations.
- `ambient_only`: resolves without a file declaration; discover this with an explicit key.
- `declared_empty_in_file`: the winning file value is empty.
- `declared_not_resolved_in_this_process`: a nonempty declaration was not resolved in
  the diagnostic process; this alone does not prove the application's Dotenv is broken.
- `not_set`: an explicit key has neither a declaration nor a resolved value.

Investigate unexpected ambient overrides and file parse errors. Missing optional local
files are normal. An absent diagnostic may mean the optional Dotenv dependency is missing.
Compare the diagnostic process/environment with the application before concluding.
Use masks for shape checks only; do not reconstruct values or request unmasked dumps.
