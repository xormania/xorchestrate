# Contributing

Create a branch from `master` and target `master` in the PR. The owner decides
when a change is ready to merge. Keep each PR coherent and describe the
resulting behavior, validation evidence, and unavailable checks. Follow the current
review and merge authorization for the effort.

Use [the application architecture](docs/hexagonal-architecture.md) to keep
orchestration independent of infrastructure and to keep policy choices explicit.
Imported implementations and tests are reusable source material, not compatibility
requirements. Carry forward useful mechanisms and revise historical assumptions
when they conflict with the intended behavior. Keep simple configurations simple.
See [CI](.github/CI.md) and the [extraction provenance](docs/extraction.md).

Keep original license and source notices. New work is attributed to xormania;
do not add AI co-author trailers.
