# Role capability packages

> Historical reference: this document describes the imported Agentscient runtime in
> `devtools/`. Its roles, defaults, restrictions, and setup procedures are not
> requirements of the current Xorchestrate application. Start with the
> [current architecture](hexagonal-architecture.md) and [README](../README.md).

Agentscient configures standalone sessions from versioned role packages. The
maintained definitions are in [role-packages.cue](../devtools/role-packages.cue);
[role-packages.json](../devtools/role-packages.json) is its concrete, checked-in
export. Normal Python services load the export without requiring CUE, PHP or
Symfony. Package files are trusted repository configuration, never agent inputs.

| Package field | Used by |
| --- | --- |
| `behavior` | Installed role adapter semantics, independent of the role's configured identity |
| `responsibility`, `skills` | Shared and role onboarding plus relevant installed skill references |
| `runtime` | Permitted harnesses, model/reasoning defaults and optional allowlists |
| `hooks` | Managed harness profiles using the shared MCP-only gate and lifecycle observations; the operator CO console uses observations only |
| `mcp` | Role tool ceilings, grouped by binding, project and provider prerequisites |
| `workspace` | Editing eligibility, default editable paths and Serena eligibility |

The package is a maximum. The current service binding, job/assignment, reporting
relationship, plan revision, pause state, provider availability and source-path
rules determine whether an operation can run. MCP services check these before
performing effects. Backing command, workspace, development, delivery and advice
services also check the declared capability ceiling; retained workers recheck it
before executing queued work. Managed-session hooks and native permission adapters
reject calls outside the current role catalog early; they do not replace those
service checks. Serena is included only for an eligible current workspace binding.
Managed agent creation and Git operations, including branch names, remain behind MCP.

The catalog is a map of configured role identities, with no required role count
or fixed identity list. Each package selects a registered `role` adapter from
`devtools/adapters.json`; the adapter supplies implemented behavior and workspace
permission ceilings. Several role identities may share a behavior. The catalog's
`defaults` map selects an identity when a workflow needs a behavior automatically;
without a default, exactly one matching identity must exist. An unavailable or
ambiguous behavior fails when requested.

The supplied `co` behavior supplies interface tools and instructions; it has no
assessment, execution, coordination or acceptance tools. The supplied coordination,
planning and specialist adapters preserve their command boundaries regardless of
the configured role names. A new adapter is an installed implementation referenced
by the trusted manifest; changing a role name or URL cannot implement new behavior.
Native delegation remains explicitly disabled for managed collaborators. The
package's `mcp_only` and `native_delegation: false` hook declarations govern those
managed transports. The appointed interactive CO is the operator's native session,
including when its role identity is an alias for CO behavior. Its native controls,
tools and settings remain available; its hooks observe activity without applying
the managed tool gate. Its Agentscient MCP calls still use the same role and work
checks. Skills and execution options do not expand command authority.

## Operator runtime configuration

The generated repository file **`devtools/role-packages.json`** is separate from
the existing private **`<state>/roles.json`** and **`<state>/vendors.json`**. Those
operator files keep their current format: ordered profile choices by role,
availability, custom model/reasoning strings, binary paths and providers supported
by the selected harness adapter. Runtime preferences may cover a nonempty subset
of catalog roles; a missing requested role has no implicit preferences.

Package model/reasoning defaults and allowlists remain `null`: they do not lock
roles to a vendor or model. The maintained [initial runtime policy](../devtools/runtime-defaults.json)
supplies explicit role/profile choices for fresh shared teams; private operator
choices take over after initialization. Setting a private profile's model or
reasoning to `null` restores native inheritance.
An explicit package allowlist requires an explicitly permitted configured value
or a permitted package default. An unknown inherited value cannot satisfy it.
Invalid profiles are rejected before workspace preparation or new seat allocation.
Existing bindings are retained when an assignment attempt is rejected.

Project `paths` settings may narrow a package's editable paths. Settings outside
that ceiling are rejected before promotion, recovery or checkpointing; widening
a role requires a reviewed package change. Job and assignment paths narrow the
result further.

`onboard` includes the package role, behavior, version and content/adapter revision. Private native
profiles record the package and binding revision. Session startup telemetry records
the selected package alongside its execution settings. Changing runtime preferences
applies to a later start. The operator may also change the running CO through native
model/reasoning controls. Neither change alters its command authority.

## Maintainer changes

Edit the CUE definition and regenerate with the pinned CI version, CUE v0.17.1:

```bash
cue fmt devtools/role-packages.cue
python3 -m devtools.role_packages --write
python3 -m devtools.role_packages --check
AGENTSCIENT_CUE=cue PYTHONPATH=devtools/tests:. python3 -m unittest devtools.tests.test_role_packages -v
```

Generation vets the entire CUE source before exporting its concrete `packages`
value. The Python loader checks raw field presence, supported shapes, installed
adapter contracts, role defaults, skill references, behavior permission ceilings,
hook invariants and source/export correspondence. The MCP registry
rejects unknown tool references at startup. CI repeats CUE validation, export-drift
checks and rejection controls in the existing dev-lane configuration job.
Pull-request CI runs for every base branch, including an unmerged parent in a
stack. The change selector still compares against that parent and selects the
affected suites; the required `lane` check keeps the same completion rules.

Deploy the matching source and generated export together, then restart control
services and managed sessions to load package changes consistently. Validated
catalog reads are cached by source, export and adapter-manifest contents, so changed
files are revalidated. Current job/revision/role authorization uses the current
catalog on each MCP call. A running managed session retains its launch settings and
native tool surface; restart it to use expanded managed capabilities. The interactive
CO's native controls remain available independently of that managed profile policy.
