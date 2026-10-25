# Imported agent instructions

These skills are source material from the Agentscient extraction, not installed
Xorchestrate instructions. They were moved out of the root `.agents/skills`
discovery location so their role hierarchy and tool restrictions do not become
implicit policy for current work.

The imported-runtime CI job stages them at their original location in its own
checkout to exercise the historical source. The new application does not read or
install them. See [the current architecture](../../docs/hexagonal-architecture.md).

`extraction-manifest.json` retains the original extraction paths and hashes; this
relocation does not change the source provenance of the individual skill files.
