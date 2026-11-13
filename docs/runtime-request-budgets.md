# Runtime request budgets

Adapters spend a monotonic allowance across the blocking phases of a request.
Socket fragments, writer contention and nested calls consume the remaining
allowance; they do not each receive the original timeout again. UTC timestamps
remain labels, not duration clocks. Existing configuration names and defaults
are unchanged, and test allowance scaling does not alter these product limits.

| Boundary | Allowance covers | Expiry and cleanup |
|---|---|---|
| MCP capability connection | Initialization, initialized notification and the requested capability exchange share the invocation deadline | Existing connection cleanup and effect classification remain in force |
| Bridge peer request | Writer admission, complete frame send and correlated response | Expiry before writer admission sends nothing; an uncertain frame write fences the connection; after a complete send the request retains its ID for a late response |
| Hosted attachment | Connection retries, connect, attachment and activation share `attach_timeout` | Before connection the existing start-failure path applies; after admission the effect may be unknown. Terminal operations and failure cleanup retain their own port policies |
| Unix client handshake | Connect, hello send and fragmented welcome read | Failed construction closes the socket and reader |
| Unix control request | Send and the complete bounded response frame | A transport timeout closes the client so a partial response cannot be mistaken for a later request's response |
| Native gateway client | Connect, send and fragmented response read | The connection closes on return or failure; a timeout cannot prove the application effect did not occur |
| Native gateway server | Input frame admission and response I/O share the configured allowance | Expired input does not invoke the handler. A handler admitted in time remains governed by its application execution policy; an expired response allowance may close without a reply |

Bridge peer requests accept an optional internal `timeout` keyword. A caller's
remaining allowance can shorten the configured request timeout but cannot expand
it. The bridge session adapter carries a hosted attachment's remainder through
its connect, attach and activate phases. Ordinary direct bridge callers retain
their configured per-request allowance.

These are execution and I/O budgets, not preemption or a promise of exact total
return latency. Encoding, scheduling, application handlers and port-specific
cleanup are cooperative. A response already retained at expiry can still be
observed according to the existing future/buffer behavior. Explicitly unbounded
Unix client requests (`timeout=None`) remain unbounded. Long-lived service reader
loops are not assigned a new whole-service deadline.

The reusable `runtime-budgets` test family exercises the phase contract with
controlled clocks and transport effects, real socket pairs and fragmented TCP
frames, the actual gateway request handler, and hosted start/attach/resume/reconnect
composition. Healthy controls preserve native identity, source, incarnation and
unknown-effect behavior. Existing native, hosted and recovery calibration remains
separate from those contract tests.
