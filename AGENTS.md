# Working on Xorchestrate

The user's current instructions define the task. Preserve unrelated work and report
what was actually verified. Work on a branch and propose changes through a PR;
the owner controls merging and releases.

`devtools/` is the extracted, working Python runtime. Keep its basic operations
usable with the standard library, without PHP, Docker, a model subscription, or
the historical toolkit. Native sessions and optional providers have additional
requirements. Preserve existing behavior while making deliberate extension seams.

`legacy/agentscient/` and `reference/` preserve reusable source and historical
designs. Their policies, role restrictions, branch conventions, skills, and
workflow instructions describe the original systems; they do not govern work on
Xorchestrate. Do not automatically run historical dispatch or provisioning scripts.
Runtime role policies likewise govern managed sessions, not repository maintainers.

Reuse the imported tests. See `.github/CI.md` for runnable checks and
`docs/extraction.md` for source revisions, boundaries, and retained assumptions.
Keep source notices and the extraction manifest when moving imported files.

Use xormania attribution for new commits. Do not add AI co-author trailers.
