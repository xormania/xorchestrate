# Project server

Agents use Xorchestrate through MCP; people use the CLI. Instead of one MCP
process per harness, one long-running server owns the application, its state
store, its drivers and their live sessions. Several agent harnesses connect to
it at the same time:

```
harness ──stdio──▶ xorchestrate connect ──unix socket──▶ server ──▶ application
```

A harness launches `xorchestrate connect` as its MCP command. `connect` starts
the server if none is running and relays MCP frames between its stdio and the
server. Closing a harness closes its connection, not the sessions it started.

Projects (discovery, worktree identity, approvals and release handoff) are not
part of this version. A server belongs to one configuration: the resolved
`--config` path, or the working directory when no configuration is given (see
[one server per configuration](#one-server-per-configuration)).

## Commands

| Command | Effect |
| --- | --- |
| `serve` | Unchanged: one MCP session over this process's stdin/stdout until EOF, with the `--actor` of this process. Same as `serve --transport stdio`. |
| `serve --transport unix [--socket PATH] [--max-connections N]` | Run the configuration's shared server in the foreground. Prints one `{"serving": {...}}` line when it is listening. Refused if a server for the configuration is already running. |
| `connect [--socket PATH] [--actor NAME] [--start-timeout S]` | The harness's MCP entry. Relays stdio to the configuration's server, starting a detached `serve --transport unix` with the same global options when none is running. |
| `up [--socket PATH] [--start-timeout S]` | Start the server if it is not running; print its status and whether this call started it. |
| `down [--socket PATH] [--detach] [--timeout S]` | Drain and stop the server (see [stopping](#stopping)). |
| `status [--socket PATH]` | Print the server's pid, socket, configuration, connected actors, in-flight calls, whether it is draining, and its live sessions. Exit status 3 and `{"running": false}` when none is running. |

`describe`, `call` and `run` remain for direct use and do not use the server.
A one-shot `call` builds its own runtime, so it can read the server's durable
records (with SQLite) but not use live session handles held by the server.

Example harness entry:

```json
{"command": "xorchestrate", "args": ["--config", "/absolute/path/team.json", "connect", "--actor", "reviewer"]}
```

## Sessions and actors

Each connection is its own MCP session: it initializes independently, keeps
its own request identifiers and receives only its own replies. The actor is
bound per connection: `connect --actor NAME` asserts it (default: the global
`--actor`, which defaults to `owner`). It cannot be replaced by tool arguments
or request fields. The actor is self-asserted by a process running as the
owning user, the same trust level as `--actor` on any other command.

All sessions share one application and one set of bounded worker pools
(`--request-workers`, `--control-workers`, `--max-pending-requests`).
Admission limits apply across the whole server, not per connection, and the
reserved control workers keep observation and lifecycle control responsive
while effect calls are held.

Sessions started through the server belong to the server's runtime. When a
client disconnects, calls it already started still finish and the sessions
remain live; another connection with the owning actor can keep using them.

## Access and connections

The socket is created with mode `0600` (under a restrictive umask, so it is
never briefly more permissive), and every peer's uid is checked with
`SO_PEERCRED` where the platform offers it; connections from another uid are
closed without a reply. Clients make the same check in the other direction:
after connecting and before sending their hello, `connect`, `up`, `down` and
`status` read the listener's `SO_PEERCRED` and refuse with `peer_untrusted`,
sending nothing, unless it runs as their own uid. This matters when an
explicit `--socket` lies in a directory another user can write to. On
platforms without peer credentials, the file mode and the private directories
are the boundary in both directions. The server is a local trust
boundary, not network authentication.

The first frame on a connection is a hello naming its purpose (`mcp` or
`control`), its actor and optional attributes; MCP frames follow unchanged.
The server answers `welcome`, or `refused` with a reason:

| Reason | When |
| --- | --- |
| `server_full` | `--max-connections` MCP clients are connected (default 64). `connect` exits with an error; it never starts a second server. |
| `draining` | The server is stopping (see below). `connect` waits for it to exit and then starts a new one, within `--start-timeout`. |
| `handshake_timeout` | No complete hello arrived within 5 seconds in total. Clients report this, and their own unanswered hello, as `handshake_timeout`, never as `connection_refused`. |
| `invalid_handshake` | The first frame was not a valid hello. |

Each hello is read on its own thread with a total deadline, so an idle or
slow peer never delays other clients, `status`/`down`, signal handling or
auto-start. Control connections (`status`, `down`) have their own allowance
of 8 and do not count against `--max-connections`, so they work on a full
server. A client that stops reading for longer than the 30-second write
timeout loses its connection; any failed or partial write closes the
connection, so a reply is never appended to a truncated one.

The owner-only listener and peer-credential check are shared with the
[session bridge](external-session-bridge.md) host; the bridge keeps its own RPC
protocol.

## One server per configuration

A configuration has at most one server, whatever the environment of each
client. The server's files live under `<home>/servers/`, named by the first
16 hex digits of the SHA-256 of the configuration identity:

| File | Purpose |
| --- | --- |
| `<key>.lock` | Held by the server for its whole life; the kernel releases it if the process dies. |
| `<key>.start.lock` | Serializes clients that may start the server. |
| `<key>.json` | Discovery record written by the running server: its socket, pid and configuration. |
| `<key>.log` | Output of a server started by `connect` or `up`. |

`<home>` is `$XORCHESTRATE_HOME` (it must be absolute) or `.xorchestrate` in
the account's home directory from the user database (not `$HOME`). It is the
one setting that selects a different set of servers, the same way a different
user account would; tests and scripts use it for isolation. Directories are
created with mode `0700`, and every file is opened without following symlinks
and refused unless it is a regular file owned by this user with no group or
other permissions.

Clients never compute the socket path: they read the discovery record (or use
`--socket`). The server chooses where to listen: `--socket` if given; else
`$XORCHESTRATE_RUNTIME_DIR` (must be absolute; refused if too long); else
`<home>/servers/<key>.sock` when that fits the Unix socket limit (107 bytes on
Linux, 103 elsewhere); else `$XDG_RUNTIME_DIR/xorchestrate`; else
`<tmp>/xorchestrate-<uid>`. Because the choice is recorded, clients with
different runtime-directory variables or temporary directories still reach
the same server.

A client passing `--socket` connects only there. If the configuration's
server runs at another socket, that client does not start a second one; it
fails after `--start-timeout` and names the running socket.

## Starting

`connect` and `up` first try the recorded socket. If no server answers, they
take the start lock, then start one detached server only if no process holds
the instance lock. Simultaneous callers wait on the start lock and then
connect to the server the first caller started, so exactly one server starts.

Clients probe the instance lock with a shared lock, and a starting server
retries its exclusive lock for up to 2 seconds, so a probe can never make a
starting server give up. While holding the instance lock, the server replaces
a socket file left at its path only if it is this user's socket and nothing
listens on it; a live server is never replaced even if its lock file is gone.
If the started server exits during startup, the client reports
`server_unavailable` with its exit code and the log path.

The detached server is started with this interpreter, the client's global
options (`--config` resolved to an absolute path, frame and worker limits),
`--socket` if the client gave one, and the client's working directory. The
transport is a machine-level choice made by the command, not by configuration.

## Stopping

`down` drains, then stops:

1. **Refusal check.** If sessions owned by the server are live (`starting`,
   `active` or `unknown`) and `--detach` is not given, `down` is refused at
   once, naming them, and nothing changes.
2. **Drain.** New effect tool calls are refused with JSON-RPC error `-32003`,
   and new MCP clients are refused with `draining`. The reserved
   observation/control tools (`poll_session`, `stop_session`,
   `detach_session`, `get_operation`, `list_sessions`, `events`) stay
   available on existing connections, so clients can watch, stop or detach
   their sessions. `initialize`, `ping` and `tools/list` keep working.
   Control connections keep working. Calls already admitted run to completion,
   however long they take.
3. **Re-check.** If a call admitted before the drain started a session and
   `--detach` is not given, the drain is lifted and `down` is refused.
4. **Stop.** Every tool call is refused from here. The server waits for
   remaining calls, closes every connection, closes the runtime, removes its
   discovery record and exits.

The decision to stop does not depend on the reply reaching the client, or on
anything else that happens on that control connection after the decision:
`down --timeout` bounds only how long the command waits. If it gives up, the
server still finishes the drain and exits on its own; `status` reports
`draining: true` meanwhile and never stops the server.

With `--detach`, the server stops even with live sessions and records them
`disconnected`, as any runtime close does; drivers that support resume can
resume them from their stored records. Connected MCP clients alone do not
block `down`. `SIGTERM` behaves like `down --detach` and also waits for calls
already in flight, including held ones.

## Ports and extension

`ClientTransport` and `ClientConnection` in `xorchestrate/ports.py` are the
driving-side seam. `xorchestrate/adapters/stdio.py` and
`xorchestrate/adapters/unix.py` implement them; a transport with a handshake
may also accept an admission policy, which the server uses to refuse clients
in the handshake reply. `xorchestrate/mcp.py` runs one MCP session per
connection over the shared `MCPDispatcher`, and `xorchestrate/server.py`
accepts connections, answers control requests and drains. A connection's
`PeerIdentity` carries the verified uid and pid, the asserted actor and
purpose, and further asserted `attributes`, where later connection context
(such as worktree identity) can travel to the MCP session without changing the
port.

## Known limitations

- The hello deadline (5 s), write timeout (30 s), control allowance (8) and
  number of concurrent unfinished hellos (32) are fixed defaults, not CLI
  options.
- `--detach` does not call drivers' detach operation; it closes the runtime.
- Without `SO_PEERCRED`, peer uids are not observed by the server or by
  clients, so an explicit `--socket` in a directory another user can write to
  is not protected there.
- An explicit `--socket` is not checked for a private parent directory; the
  client-side uid check is what refuses another user's listener.
- A server that never drains (a held call that never returns) keeps running
  until it is killed.
