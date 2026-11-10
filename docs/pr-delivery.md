# PR preparation capabilities

The `pr` provider prepares an identified source revision, preserves local work,
opens a draft and reports the observed checks and discussions. It never marks a
PR ready, merges it, rebases it or force pushes. GitHub's current repository rules
remain authoritative for integration.

Configure it in the project's approved configuration:

```json
{
  "providers": {
    "delivery": {
      "adapter": "pr",
      "options": {
        "read_repositories": ["owner/read-only-repository"],
        "github": {"token_variables": ["GH_TOKEN"], "token_command": []},
        "git": {
          "workspaces": {
            "change": {
              "path": "/absolute/path/to/owned-worktree",
              "repository": "owner/repository",
              "remote": "origin"
            }
          }
        }
      }
    }
  }
}
```

Credentials are resolved only by the adapters. They are never capability
arguments or retained operation results. The hosting adapter accepts the existing
GitHub adapter's HTTPS API URL, hostname, token-variable/command and timeout
options. `required_checks` defaults to `CI result` and `Hexagonal architecture`;
`max_pages` bounds each remote collection. Configure these names for another
repository. The Git adapter accepts an executable, timeout and commit attribution.
Workspace names resolve only to the configured owned roots.

The built-in provider's PR read scope is the union of configured workspace
repositories and the optional `read_repositories` list. Names are compared without
case sensitivity. An omitted or empty list adds no repositories; a provider with
neither workspaces nor explicit read repositories denies every PR read.
`read_repositories` can configure a provider used only for inspection, with no Git
workspace. It grants no publication or refresh capability for those repositories.

`pr.inspect` and `pr.handoff` reject an out-of-scope repository before hosting or
credential lookup, including direct calls to the configured provider's inspection
method. Hosting reads use the same scope for derived source-repository lookups,
including canonical branch freshness and review applicability; add a fork's
repository explicitly if those lookups are wanted. An out-of-scope fork leaves
inspection incomplete with `pr_repository_denied`, without querying that source
repository.
The existing application guardrail still controls which actors may invoke these
capabilities. The lower-level `PullRequestOperations` class accepts an explicit
read scope when composed programmatically; omitting that scope is for a trusted
host composition, rather than the built-in untrusted capability surface.

Invoke these through the existing `invoke` application/MCP action, for example
`delivery/pr.inspect`. Use a fresh invocation key for each current observation.
Replaying a completed key returns its historical result and timestamps.

| Capability | Principal inputs | Result |
| --- | --- | --- |
| `pr.inspect` | Repository, PR number, optional expected source head | Candidate identity, observed check/review/thread state, qualified technical validation or an explicit incomplete/superseded result |
| `branch.refresh` | Owned workspace, expected source head, exact target commit, explicitly selected tracked paths | No change, refreshed revision, or retained checkpoint and merge conflicts |
| `pr.open_draft` | Owned workspace/repository/branch/base, expected head, title/body, review record | Verified publication and newly created or adopted draft; superseded observation when the source or owner-controlled PR state changed |
| `pr.handoff` | Repository, PR number, expected head and optional verification/review references | A fresh observed handoff with readiness unchanged |

The publication review record identifies `head`, `tree`, `scope`, `reviewer`,
`findings` and `verification` references. It is a recorded assertion about the
review, not machine proof of reviewer independence or competence. Publication
verifies that this source tree is the one being pushed. An existing compatible
draft is adopted without rewriting its body. A ready, closed, retargeted or
ambiguous PR is reported instead of reversing the owner's decision.

## Candidate and observation qualification

Inspection reads the full bounded PR state twice, including checks and discussion
state. A change between reads produces `superseded`, even when the Git SHAs did
not change. Collection/authentication failures produce `incomplete`; they never
mean there are no blockers. An observation is not an atomic promise about future
GitHub state. The current loose ruleset can still permit the base to advance
between inspection and merge.
Each snapshot also resolves canonical source and target branch refs separately
from the PR payload. A missing ref or disagreement makes the current observation
incomplete, even when repeated PR payloads are unchanged. Reported/tested merge
provenance remains historical evidence; it is never relabeled with the new refs.

Technical qualification consumes the CI aggregate's version 2 result and version
1 candidate context. It checks the source head separately from the generated
merge checkout, its ordered parents and tree, producer run/attempt and PR identity.
The check association names the source head; this is distinct from the commit
actually checked out by the workflow. Older aggregate schemas remain historical
evidence and are reported as unqualified by this provider.

The latest relevant PR attempt's authoritative start determines the observed
state, including a retry of an older run. A green attempt cannot hide a newer
material failure or unfinished attempt. Missing or ambiguous start ordering is
incomplete. Artifact lookup and JSON/
ZIP reads are bounded. Duplicate members, traversal names, symlinks, excessive
size and contradictory candidate/producer identities are rejected. Signed artifact
redirects receive no authorization header. The provider does not execute artifacts.

Read-only public REST requests can run anonymously. GitHub GraphQL discussion
inspection and draft writes require a supported token. If it is unavailable,
inspection reports incomplete state. Approval-required or missing eligible PR
checks remain visible through the observed run/check state. A manual diagnostic
run is never promoted into eligible PR evidence.

## Workspace custody and recovery

Refresh requires the expected HEAD, an exact available target commit and explicit
tracked checkpoint paths. It refuses existing merge/rebase/cherry-pick state,
unsafe paths, symlink traversal and tracked changes outside the selection.
Unrelated untracked files are not staged. A private index builds a verified
immutable checkpoint tree. The standard Git index lock protects the live-index
comparison, expected-HEAD update and atomic index installation; another writer's
staging is preserved or reported as a conflict. A merge conflict retains the checkpoint,
index and worktree for the agent's ordinary editing tools; nothing is reset.
An already-current clean branch is a no-op.

Participating writers use the shared application's existing operation records to
claim a canonical repository/branch target. Another unresolved mutation must be
inspected and reconciled before a different key can mutate it. Independent targets
can progress in parallel. Workspace flock custody coordinates participating local
preparation. External editors and GitHub users are handled by content/revision
checks and final observations; these mechanisms do not lock those external users.

The same operation retains the intended candidate, current step, confirmed pushed
head and created PR number. A crash does not expire an unknown operation's target
custody. If a write's response is lost, read the remote branch/PR and the operation's
progress, then use the existing `resolve_operation` action with the verified result
and expected operation version. Replaying the original key then returns that
settled result. Do not issue a new mutation key to bypass an unknown operation.
No watcher, durable queue or independent PR database is introduced.
The configured state engine governs and records each `operation.progress`
transition. Operation lifecycle definition version 2 adds this pending-state
transition; custom definitions must explicitly support it to use this provider.

Workspace Git commands use a small credential-free environment and disable
candidate hooks, filters, external diff programs and custom merge drivers.
Publication runs from a new trusted bare Git directory which reads only candidate
objects as data. Candidate Git configuration, URL rewrites, hooks, filters and
credential helpers are absent from the credentialed publication process. HTTPS
credentials are supplied only when its remote matches the configured hosting
repository. Local Git remotes support disposable tests. Pushes are ordinary
non-force pushes; remote advancement requires inspection.
Remote arguments use option terminators and reject option-like and unsupported
SSH/SCP values. Local paths resolve explicitly against the owned workspace.
The verified HTTPS publication process retains the runtime's proxy and CA-trust
variables. Local publication and candidate-processing commands do not receive them.

Run the controller from its trusted installed environment. The workspace adapter
does not run Python, tests or scripts from the proposed checkout. Launching the
controller itself from candidate-controlled Python imports is outside this trust
boundary.

The local acceptance tests exercise real temporary Git repositories and the real
application/MCP journal with controlled hosting. They do not establish that a real
token's writes generate eligible GitHub PR events. That hosted credential/event
witness must use the deployed token and repository settings; connector-created
draft CI is separate evidence.

## Reuse provenance

The revision reread, compatible-draft adoption and separate head/test-merge
observations adapt the mechanisms in
[Agentscient's GitHub operations](https://github.com/xormania/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/github.py).
Checkpointed merge refresh and non-force submission follow its
[workspace operations](https://github.com/xormania/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/workspaces.py).
The implementation uses Xorchestrate's current capability provider, effect journal,
state engine and injected purpose ports. It does not retain historical workorder
roles, storage coupling, automatic readiness or refresh policies.
