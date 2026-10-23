# keepalive — holding a harness credential alive between logins

Standing: PROPOSED. Source of demand: the owner, 2026-09-16, on a third
consecutive morning of logging grok in — "grok done - but this is third
day in a row - do i have to do this every day?"

Inherits [the instrument conventions](instrument-conventions.md): the
refusal wire, the four exit classes, identity on writing verbs, UTC
stamps. Only what is specific to this lever is stated here.

## What it is for

`ops/devlane/harness/isolation.py` refuses a grok dispatch whose host
credential is older than `credential_age.max_hours` (24, from LD-18:
a refresh was accepted at 29.4 h and two dispatches died "Not signed
in" at 30.1 h). The CLI renews its refresh token during normal use and
writes the renewal into whichever home it ran under; LD-41 carries that
renewal back to the host copy, so the host file ages from the last
renewal rather than the last login.

The consequence the owner met: on a day when no grok job ran, nothing
renewed, and the credential aged out overnight. The remedy is a run —
any run — inside each window. This lever is that run, and nothing else.

## The verb

    python3 ops/conductor/tools/keepalive.py grok [--dry-run]

Reads the host credential's age, runs one trivial invocation in an
isolated home built the way the launcher builds one, carries a renewal
back through the same `carry_back`, and prints the age before and
after. `--dry-run` reads and prints without invoking anything.

One line on stdout, exit 0:

    keepalive grok: before <h>h; after <h>h; <renewed|unchanged>; carry <outcome>

`renewed` means the credential's `create_time` moved. `unchanged` means
it did not: the run happened and the CLI did not renew. Both are
answers. The lever never reports success from the invocation's exit
code alone, because the exit code says the command ran, not that the
credential moved.

## Refusals

| class | when |
|---|---|
| `harness` | the named harness has no isolation entry, or no `credential_age` rule to hold alive |
| `credential` | the credential is already past `max_hours`, or absent — a keepalive cannot log in, and pretending it can would hide the one case the owner must act on |
| `keepalive-error` | the invocation could not be run: no executable on PATH, or it exceeded its timeout |

The `credential` refusal names the remedy the isolation entry declares
(`grok login --device-code`), so the owner reads what to type rather
than a diagnosis.

## Installing it

The lever does not install itself, and nothing here schedules it. The
owner installs one of these by hand, twice inside each 24 h window so a
missed run is not a lockout:

    # crontab -e
    0 */12 * * * cd /home/scientdb/projects/uscient/agentscient && python3 ops/conductor/tools/keepalive.py grok >> ~/.local/state/agentscient/keepalive.log 2>&1

A systemd timer is the same shape: `OnCalendar=*-*-* 00,12:00:00` with
`ExecStart=` the command above.

## What is not measured

Whether grok's cheapest invocation renews the token is not measured
here, and the lever does not assume it: it reports `renewed` or
`unchanged` from the credential itself. If a window of `unchanged`
readings accumulates, the invocation is the thing to change, and the
readings are the evidence for changing it.

## Placement

The tool is a conductor instrument (`ops/conductor/tools/`), not a
harness lever beside `graft.py`, because the placement gate
(`test_state_placement.py`, `TOOLS = ops/conductor/tools`) and the
conformance registry (`ops/conductor/registry/`) are both scoped to
that directory: measured 2026-09-16, `graft.py` carries neither a
placement file nor a registry row. A lever that must declare its state
and prove its wire lives where the gates can see it. It reaches
`isolation.py` through that module's public names only.
