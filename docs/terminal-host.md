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
| `close_terminal(name, grace=None)` | Stop the terminal's work and forget it. The terminal's own command is asked to stop first and gets the whole `grace` to stop its own children. Whatever remains after that is terminated, given a short wait, then forced. Returns the final observation and `cleanup_verified`, which is true only when everything is gone. |
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

## Adapters

| Adapter | `survives_controller` | `interactive` | Mechanism |
| --- | --- | --- | --- |
| `tmux` | yes (no with `owned`) | yes | One private server per host. Each terminal is a session with one pane. |
| `detached-process` | yes (no with `owned`) | no | Each terminal is a process in its own session, with output in its log. |

Both take the placement the operator layer supplies (`name`, `location`,
`socket`) and these options:

- `owned` (default false): the host's mechanism runs as the controller's
  child, and everything stops when the controller closes. On Linux it also
  stops when the controller dies. Adoption is refused. The test kit uses this
  mode for its leak checks.
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

## Known limitations

- `doctor` probes readiness at a placement under the home's server directory,
  not at each project's placement.
- `recent_output` for `tmux` reads the pane's history. The log written through
  `log` is the raw terminal stream.
- Owned mode's die-with-controller guarantee for a crashed controller relies on
  Linux's parent-death signal.
