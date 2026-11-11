# Terminal host

Xorchestrate runs long-lived local work in a **terminal host**: a named group of
terminals it can list, drive, attach to and recover. Today the project server
runs there, and so do [hosted sessions](#hosted-sessions).

The host is a port, `TerminalHost` in `xorchestrate/ports.py`. The application
sees hosted terminals, receipts and capabilities, not a particular
multiplexer, pane or socket. Adapters are selected by name from the user's
home, like the other operator adapters.

## The port

| Operation | Meaning |
| --- | --- |
| `readiness()` | Why this host cannot run terminals here now, or `None`. Never raises. |
| `open_terminal(name, command, log=None)` | Start a `TerminalCommand` (argv, cwd, the exact environment, optional rows/columns). With `log`, everything the terminal prints is also appended to that private file, so the output outlives the terminal. |
| `terminals()` | The terminals this controller opened or adopted, including exited ones until they are closed. |
| `send_input(name, text)` | Type text literally. Interactive hosts only. |
| `interrupt(name)` | Ask the terminal's work to stop; the terminal stays open. |
| `recent_output(name, lines=None)` | The last lines of output, capped by the adapter. Works after exit until the terminal is closed. |
| `attach_instruction(name=None)` | A command for the operator to attach to one terminal or the whole host. It checks that the location is still private and that the terminal still matches before answering. Interactive hosts only. |
| `attach_instruction_for(receipt)` | For a process that is not the terminal's controller: how to attach to the terminal a receipt names. The receipt is verified as `adopt` verifies it, but no control is taken, so the terminal stays with its controller. Works for owned hosts too. Interactive hosts only. |
| `close_terminal(name, grace=None)` | Stop the terminal's work and forget it. The terminal's own command is asked to stop first and gets the whole `grace` to stop its own children. Whatever remains after that is terminated, given a short wait, then forced. Returns the final observation and `cleanup_verified`, which is true only when everything is gone. |
| `inventory()` | Everything at the host's location: name, running or exited, and whether this controller holds it. Read only, with no process identifiers. |
| `sweep()` | Remove every exited terminal at the host that this controller does not hold, and return their names. Running terminals are never touched; the host ends when nothing is left. |
| `adopt(receipt)` | Take back a terminal after the controller restarted, verified against what is actually running. |
| `shutdown()` | Stop every terminal of the host and remove its private state. |
| `close()` | Release this controller. Terminals survive if `survives_controller` is true. |

Two flags describe a host: `survives_controller` (whether terminals outlive the
process that opened them, so they can be adopted) and `interactive` (whether
they take input and can be attached to). A missing capability fails with
`terminal_operation_unsupported`.

**Names.** `open_terminal` refuses a name (`terminal_exists`) only while a
terminal of that name is running at the host. An exited terminal of that name
is replaced, because its output is already in its log or has been read. For a
running terminal that this controller does not hold, the error details of an
interactive host include an `attach` command, so the operator can look at it.

**Ownership.** A controller controls only what it opened or adopted. Anything
else at the host's location stays untouchable until `adopt(receipt)` has
verified it. Receipts are opaque JSON produced by the adapter. They carry the
adapter's own identity evidence (process start time and boot, server and pane
ids). The port never accepts or exposes process identifiers. `adopt` accepts
only what the host itself launched under that name:

- `detached-process` compares the receipt with its private record of each
  launch.
- `tmux` requires the pane to be on its own private server and labelled with
  the host and terminal names.

This protects against stale, corrupt or foreign receipts and records, and a
corrupt record is treated as missing. Other processes of the same user are
trusted, as elsewhere in the product.

**Exit.** `running` is false only when the adapter knows the command exited.
`exit_status` (a code) or `exit_signal` says how; both are `None` when it
cannot know. For example, a terminal adopted by another process may not have
its wait status.

## Lifecycle

The same rules hold for both adapters and both modes, except where noted.

- **Start.** A host has no process and no endpoint until its first terminal
  opens. Under `tmux`, a surviving host's server starts in the same client
  call that opens its first terminal, so a server never exists without a
  terminal, and an open that fails leaves no server. An owned host's server
  (`-D`, which tmux keeps alive while empty) starts as the controller's child
  just before the first terminal opens.
- **End.** A host ends when its last terminal is closed or swept. Under `tmux`
  the server exits and its socket is removed. Under `detached-process` the
  host's records, its own logs and its empty location are removed. `shutdown`
  ends a host at once, stopping every terminal at it.
- **Who may end what.** A controller closes only the terminals it opened or
  adopted. `close` releases a controller: a surviving host's terminals keep
  running, and an owned controller ends only what it started or controls.
  `shutdown` stops every terminal at the host. `sweep` removes only exited
  terminals that this controller does not hold, and never a running one.
- **Controller exits or is killed.** Surviving host: nothing changes, and its
  terminals keep running for a later controller to adopt. Owned host: an
  orderly close stops everything and verifies it. If the controller is killed
  outright, the host and each terminal's command are stopped (see `owned`
  under Adapters).
- **Finished terminals.** An exited terminal keeps its status, and its output
  stays readable, until it is closed, swept or replaced by a new terminal of
  the same name.
- **Inventory.** `inventory()` lists everything at the host's location (name,
  running or exited, held by this controller or not) without taking control.


| Adapter | `survives_controller` | `interactive` | Mechanism |
| --- | --- | --- | --- |
| `tmux` | yes (no with `owned`) | yes | One private server per host. Each terminal is a session with one pane. |
| `detached-process` | yes (no with `owned`) | no | Each terminal is a process in its own session, with output in its log. |

Both take the placement the operator layer supplies (`name`, `location`,
`socket`) and these options:

- `owned` (default false): the host's mechanism runs as the controller's
  child, and adoption is refused. The test kit uses this mode for its leak
  checks.
  - An orderly close (`close` or `shutdown`) stops everything and verifies it.
  - If the controller is killed outright (Linux, through the parent-death
    signal), the host is stopped. Each terminal's command is also stopped: the
    `detached-process` leader is killed, and under `tmux` it is hung up when
    the server dies. A process that detached from its terminal's session, or
    that ignores hangup, can survive. A caller that must prove nothing is left
    behind should track descendants itself, as the test kit does.
- `output_lines` (default 2000) and `grace` (default 10 seconds).

`tmux` also takes `binary`, `rows`, `columns` (default 48×160),
`terminal_type` (default `tmux-256color`) and `control_timeout` (default 5 s).

How `tmux` works:

- It starts its server with `-f /dev/null`, so no user configuration is
  loaded, on a socket in a private directory.
- The command starts through a small exec shim. The shim reads the caller's
  exact environment from a private file, removes the file and execs the
  command, so no environment value appears on a command line, which other users
  can read. The environment is exactly the caller's plus `TERM`; the
  multiplexer's own nesting variables are removed.
- The attach command clears the multiplexer's nesting variables itself, so it
  works from inside another session.
- Exited panes stay until they are closed (their status and output remain
  readable).
- The server ends with its last terminal.
- Every control call first checks the server's process identity.

How `detached-process` works:

- It is the server's former detached launch, generalised.
- It is adopted and signalled by process identity (pid, start time, boot,
  owner).
- It notes which process holds each name in a private record, so a stale
  receipt for a reopened name is refused as `terminal_identity_mismatch`
  rather than reported as gone.

## Selection

`settings.json` `terminal_host` selects the adapter, with an optional
`fallback` used when the selected host is not ready on this machine:

```json
{"terminal_host": {"adapter": "tmux", "options": {"output_lines": 5000},
                   "fallback": {"adapter": "detached-process"}}}
```

Without a setting, the default is `tmux` with `detached-process` as its
fallback. When the fallback is used, the switch is reported, not silent.
`doctor` reports a `terminal_host` check: a warning naming the reason and
whether terminals survive and are interactive. `status` reports the active
host under `terminal_host`. Without a `fallback`, an unready host is the
refusal `terminal_host_unavailable`.

## The project server

`connect`/`up` start the server as terminal `server` in the project's host:

- The output goes to the existing server log.
- The host is placed beside the server's files: its socket follows the server
  socket's location rule (`<key>.terminals.sock`).
- The placement and the terminal's receipt are saved privately beside the
  discovery record (`<key>.terminal.json`).
- Later commands read that record instead of recomputing the placement from
  their own environment. `down` uses it to close the stopped server's
  terminal, and `status` to report it.
- A server terminal left from a crash is closed before a new one opens.
- A server that exits during startup leaves its output in the log and no
  terminal behind.
- `up`, `down` and `status` (when no server answers) sweep the project's host of
  exited terminals. They do this under the start lock, and only while no server
  holds the instance lock. `doctor` reports what the host holds (running,
  exited, and untracked by any record) and how to end it.
- With `detached-process`, the server runs exactly as before.
- The server runs with the launching client's environment, as before. With
  `tmux` it reaches the server through the private environment file, never a
  command line.

## Hosted sessions

A session that should survive the server is configured as `hosted`, wrapping
the native session's own selection:

```json
{"sessions": {"worker": {"adapter": "hosted", "options": {
  "session": {"adapter": "codex", "options": {"command": ["codex", "app-server", "--listen", "stdio://"],
                                               "cwd": "${repo}"}},
  "application_tools": true}}}}
```

Each hosted session is a bridge host (`python -m xorchestrate.bridge`) that
Xorchestrate opens in the project's terminal host and attaches to with the
existing bridge driver. There is no new session protocol, and owned drivers
are unchanged.

- **Where it runs.** The server binds its terminal host to hosted drivers. The
  terminal is named after the configured entry plus a short key. Each session
  has a private directory (`options.directory`, default `hosted` under the
  project's data directory). It holds the generated host configuration, the
  host's state file and the terminal's log. The bridge endpoint sits beside the
  host's socket.
- **Start.** The driver opens the terminal and retries attaching while it runs,
  up to `attach_timeout` (default 30 seconds). A failed or timed-out start
  returns the terminal's recent output and the log location. A `hosted_terminal`
  observation records whether the session survives the server, and gives the
  attach command when the host is interactive.
- **`stop_session`** detaches and closes the terminal. If cleanup cannot be
  verified, it says so. The private directory and state file remain, so a later
  resume reopens the terminal with the same state. Pruning old directories is
  later work.
- **`detach_session`** and the server stopping only detach: the terminal keeps
  running.
- **`reconnect_session`/resume** adopts the terminal from the session's
  receipt and reconnects through the bridge. If the terminal is gone (after a
  reboot or a `stop`), it reopens it with the same state, so the bridge resumes
  the native conversation. Resuming needs a native driver that supports it.
- **Approval.** The approval summary and `doctor` list the wrapped session's
  command like any other launched command.

## What "recoverable" means

- **The server, its terminal or an SSH connection dies.** With `tmux`, the
  multiplexer keeps the terminals. The operator can attach, and a restarted
  controller re-adopts them from their receipts.
- **The machine reboots.** The multiplexer is gone, and receipts are refused
  because the boot changed. The process is not recovered. For hosted sessions,
  the bridge's saved state will relaunch the session host in a fresh terminal,
  so the conversation is recovered.
- **`detached-process`** keeps processes running past the controller, but
  there is no terminal to attach to.

## Recovering after a container or machine failure

A container failure or a reboot loses every process at once: the server, the
terminal host (a multiplexer server, detached processes), each hosted session's
bridge host, and the harnesses they run. Treat it as total loss of processes.

**What survives** is the Xorchestrate home, which must be on persistent storage:

- the project's store (sessions, work, the effect journal);
- each hosted session's private directory (`hosted` under the project's data
  directory), with its bridge state file, its generated host configuration and
  its terminal log;
- the server's records under `<home>/servers` (discovery, terminal placement
  and receipt) and its log.

**What does not survive:** processes, terminals and their unsaved output,
sockets, and anything a harness held only in memory. Files in the runtime or
socket location may or may not survive. Stale sockets, lock files and records
whose process no longer exists are normal starting states.

**To recover:**

1. Run `xorchestrate up` (or connect). A stale socket the host owns is removed
   once nothing answers on it. A record whose process is gone is set aside, and
   the server starts in a fresh terminal.
2. The server detects the hard restart from durable state: a discovery record
   left by a server that did not stop cleanly, or sessions persisted as live
   that it does not hold. It asks the project's `recovery_policy` which of these
   interrupted sessions to recover, and recovers them in the background while it
   serves. With the default `reconnect` policy, every session whose driver can
   reconnect comes back without a call. With `manual`, nothing is recovered
   automatically.
3. `xorchestrate status` shows `recovery` (`running`, the last `report`, and
   what was `detected` and `selected`) and `sessions_needing_reconnect`.
   `xorchestrate recover` (or the `recover` action) recovers the rest; it is
   idempotent.

Recovering a session reconnects it through its driver. For a hosted session,
the adapter checks that the old terminal is gone, removes its own stale
endpoint, reopens the terminal with the same state so the bridge resumes the
native conversation, and reconnects. A session whose driver cannot reconnect is
marked `disconnected` (reason `hard_restart`); resume it later with
`start_session` and `resume_session_id` if its driver resumes. A start that was
in flight is marked unknown for reconciliation.

- **Identity.** Reconnecting creates a new session. The report maps each old
  session to its replacement. The old record is `superseded` and names its
  replacement, and a call on the old identifier fails with `stale_session`,
  naming the replacement.
- **Concurrency.** The automatic run, an explicit `recover` and a client's own
  `reconnect_session` can overlap. They make exactly one reconnect per session,
  and the others report it as in progress or refused. Until a session is
  recovered, calls on it fail as for any session the server does not hold.
- **Authority.** A recovered session keeps its original actor and work binding
  and gains nothing; the reconnect runs as that actor. Who may call `recover`
  is decided by the guardrail, like any action.
- **Policy.** `recovery_policy` is a project configuration section, like
  `retry_policy`: `{"adapter": "reconnect"}` (the default) or
  `{"adapter": "manual"}`. It answers one question: which interrupted sessions
  to recover automatically. Recovery runs only in the server, never in one-shot
  commands.

**What stays uncertain:** effects that were in flight at the failure. Their
outcome is unknown; they are not replayed or re-sent, and they stay in the
effect journal for reconciliation (`resolve_operation`). A native driver
without resume cannot continue its conversation: start a fresh session.

## Known limitations

- `sweep` cannot see what another live controller holds, so it would also
  remove an exited terminal that such a controller still holds. The CLI sweeps
  only while no server holds the project's instance lock, under the start lock
  (`up` before launching, `down` after the stop, `status` when no server
  answers), and running terminals are never swept.
- An owned host's controller that is killed outright can leave behind a process
  that detached or ignores hangup (see `owned`).

- `doctor` probes readiness at a placement under the home's server directory,
  not at each project's placement.
- `recent_output` for `tmux` reads the pane's history. The log written through
  `log` is the raw terminal stream.
- When an owned host's controller is killed outright, only the host and each
  terminal's command are stopped (through Linux's parent-death signal). A
  process that detached or ignores hangup can survive; see `owned` above.
