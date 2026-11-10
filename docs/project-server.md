# Project server

Agents use Xorchestrate through MCP; people use the CLI. Instead of one MCP
process per harness, one long-running server owns the application, its state
store, its drivers and their live sessions. Several agent harnesses connect to
it at the same time:

```
harness ──stdio──▶ xorchestrate connect ──unix socket──▶ server ──▶ application
```

A harness launches `xorchestrate connect` as its MCP command. `connect` starts
the server if none is listening and relays MCP frames between its stdio and the
server. Closing a harness closes its connection, not the sessions it started.

Projects (discovery, worktree identity, approvals and release handoff) are not
part of this version. A server is selected by its socket path, which is given
explicitly or derived from the resolved `--config` path (see
[socket location](#socket-location)).

## Commands

| Command | Effect |
| --- | --- |
| `serve` | Unchanged: one MCP session over this process's stdin/stdout until EOF, with the `--actor` of this process. Same as `serve --transport stdio`. |
| `serve --transport unix [--socket PATH] [--max-connections N]` | Run the shared server in the foreground. Prints one `{"serving": {...}}` line when it is listening. |
| `connect [--socket PATH] [--actor NAME] [--start-timeout S]` | The harness's MCP entry. Relays stdio to the server, starting a detached `serve --transport unix` with the same global options when none is running. |
| `up [--socket PATH]` | Start the server if it is not running; print its status and whether this call started it. |
| `down [--socket PATH] [--detach] [--timeout S]` | Drain and stop the server (see [stopping](#stopping)). |
| `status [--socket PATH]` | Print the server's pid, configuration, connected actors, in-flight calls and live sessions. Exit status 3 and `{"running": false}` when none is running. |

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

## Access

The socket file is mode `0600`, and every peer's uid is checked with
`SO_PEERCRED` where the platform offers it; connections from another uid are
closed without a reply. On platforms without peer credentials, the file mode
and the private runtime directory are the boundary. The first frame on a
connection is a hello naming its purpose (`mcp` or `control`), its actor and
optional attributes; MCP frames follow unchanged. The server is a local trust
boundary, not network authentication.

The owner-only listener and peer-credential check are shared with the
[session bridge](external-session-bridge.md) host; the bridge keeps its own RPC
protocol.

## Starting

`connect` and `up` first try to connect. If no server answers, they take a
start lock beside the socket, then start one detached server only if no server
holds the instance lock. Simultaneous callers wait on the start lock and then
connect to the server the first caller started, so exactly one server starts.
The server holds `<socket>.lock` for its whole life; the kernel releases it if
the process dies, and a socket file left by a crashed server is replaced only
while that lock is held. The detached server's output goes to `<socket>.log`.

The detached server is started with this interpreter and the client's global
options (`--config` resolved to an absolute path, frame and worker limits) and
the client's working directory. The transport is a machine-level choice made by
the command, not by configuration.

## Stopping

`down` drains: the server stops admitting new tool calls and new MCP clients,
lets calls already in flight finish, then closes every connection, closes the
runtime and exits. `down` waits up to `--timeout` seconds for that; if the
timeout passes first, the server still finishes the drain.

`down` is refused, and the server keeps serving, while sessions owned by the
server are live (`starting`, `active` or `unknown`), because closing the
runtime would end them. The refusal names those sessions. Stop or detach them
first, or pass `--detach`: the server then stops anyway and records those
sessions `disconnected`, as any runtime close does; drivers that support resume
can resume them from their stored records. Connected MCP clients alone do not
block `down`; their connections are closed after the drain. `SIGTERM` behaves
like `down --detach`.

## Socket location

`--socket PATH` selects the socket explicitly. Otherwise the name is the first
16 hex digits of the SHA-256 of the resolved `--config` path (or of the working
directory when there is no configuration, since that is where the default state
lives), with `.sock` appended, in the first of these directories:

1. `$XORCHESTRATE_RUNTIME_DIR` (refused if the path would be too long);
2. `$XDG_RUNTIME_DIR/xorchestrate`;
3. `<temporary directory>/xorchestrate-<uid>`.

Any directory that would make the path exceed the Unix socket limit
(107 bytes on Linux, 103 elsewhere) is skipped in favour of the short
temporary one. The directory is created with mode `0700` and refused unless it
is owned by this user and private. Every client of one configuration therefore
reaches one server without coordination, and two configurations never share
one. A later version that adds projects can replace this rule with a
per-project path without changing the transport.

## Ports and extension

`ClientTransport` and `ClientConnection` in `xorchestrate/ports.py` are the
driving-side seam. `xorchestrate/adapters/stdio.py` and
`xorchestrate/adapters/unix.py` implement them; `xorchestrate/mcp.py` runs one
MCP session per connection over the shared `MCPDispatcher`, and
`xorchestrate/server.py` accepts connections, answers control requests and
drains. A connection's `PeerIdentity` carries the verified uid and pid, the
asserted actor and purpose, and further asserted `attributes`, where later
connection context (such as worktree identity) can travel to the MCP session
without changing the port.
