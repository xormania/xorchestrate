# Shared MCP transport and dispatch-record interpretation

Owner-selected consolidation, 2026-09-23, based on dev a91284eccdc532e63ef8c9de84ee19776e64bf51.
This dev-lane boundary changes implementation ownership, not either server's
external contract. It is independent of the Symfony application.

`tools/mcp_transport.py` owns bounded newline framing, draining oversized
frames, JSON decoding, JSON-RPC envelopes, notification silence, metadata
removal, version negotiation and ping. It accepts a binary input stream,
a domain-routing callable and a response-sending callable. Malformed frames
emit -32700 and do not prevent the next frame from being processed. The
65,536-byte bound includes the newline. Notifications never invoke domain
operations. Invalid envelopes and parameter objects retain their existing
-32600/-32602 responses. The three existing protocol versions remain accepted.

`lane_mcp.py` and `coordination_mcp.py` supply server identity, instructions,
tool/resource routing and serialization. Coordination opts into its existing
strict initialize schema; the lane server retains its legacy tolerance.
Coordination still owns its whole-response bound, capability/identity checks,
mutation dispatch, domain refusal details and exception conversion. Lane
resources retain their existing error shape. Neither server obtains authority
from the transport, and no request chooses its routing callable.

`tools/dispatch_observation.py` interprets already-acquired record data. It
owns normalization of result/envelope/changed-path fields, on-disk phase,
recorded completion outcome, and dispatch token/count metrics. The precedence
is explicit: missing record, terminal record, exit marker, running phase;
invalid/tripped envelope, died record, successful closed record for outcomes.
A successful write with an empty changed-path list is a no-op; a read with
that same list is a completion. A phase is not proof of process liveness.

Monitor, watchdog and lane reads share this interpretation. File acquisition,
symlink/FIFO policy, byte limits, missing/unreadable distinctions, process
identity checks, cache invalidation, alert cadence and domain-specific output
remain with their existing owners. In particular, monitor's strict argv/cwd/
session proof and watchdog's weaker harness-word liveness check are not
interchangeable; this consolidation does not weaken either boundary.

The shared modules have no durable state and do not open files, spawn
processes, access a network or import a domain adapter. Each staged caller
must include its shared dependencies. Existing end-to-end record, hostile-file,
read-only, capability and response-bound tests remain acceptance evidence.

Discovery's implementation fingerprint includes both extracted modules along
with lane_read.py, lane_mcp.py and lane_refs.py, so a shared dependency change
changes the advertised implementation identity. The fixed-path declarations
are updated accordingly. This extends the existing fingerprint; it does not
claim to implement the separately specified trusted boot-loader mechanism.
