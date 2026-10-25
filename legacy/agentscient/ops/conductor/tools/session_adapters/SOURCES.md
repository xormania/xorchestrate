# Sources — session_adapters

Adapted from `/home/scientdb/projects/poc/multi-harness-proof` at `a944f31`.
That repository's own license, verbatim:

```
MIT License

Copyright (c) 2026 xor

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Manifest

Each adapted function beside its origin, `file:line` at `a944f31`. A row
is added in the commit that lands the function it names.

| here | origin | what was taken |
|---|---|---|
| `framing.py: Parser.feed` | `mhproof/rpc.py:68-92` (`RPC.read`) | the JSON-lines parse and the split of a received line into a request, a notification or a response correlated by id, made synchronous: one line in, one frame out or a `Refused` naming the line |
| `framing.py: Parser.sent` | `mhproof/rpc.py:37-52` (`RPC.request`, its `pending` book) | a response correlates only with a request this side sent |
| `framing.py: Refused` | `mhproof/rpc.py:6-9` (`RPCError`) | a typed exception carrying the reading, not the raw packet |
| `sessions.py: ownership` | `mhproof/sessions.py:38-42` (`Tmux.owned`) | the owner option read with `show-options -qv` on the exact session component, `=name:` |
| `sessions.py: launch` (the opening tmux call) | `mhproof/sessions.py:44-51` (`Tmux.create`), `:53-64` (`Tmux.launch`) | the owned session or window created detached with `-P -F` printing the pane, the owner option set on creation, `remain-on-exit on` so a dead pane keeps its capture; `launch` refuses a session it does not own |
| `sessions.py: do_attach` | `mhproof/sessions.py:91-94` (`Tmux.attach`) | attach only to an owned session, `switch-client` inside tmux and `attach-session` outside |
| `sessions.py: do_export` (`scrub`) | `mhproof/sessions.py:74-82` (`Tmux.finish_peer`) | a terminal capture is redacted of the run's secrets before it is written out |
| `claude.py: Adapter.diagnostics` | `mhproof/sessions.py:74-82` (`Tmux.finish_peer`) | the terminal capture kept as evidence, named by path |
| `channel.py: Channel.send`, `Channel.request`, `Pending` | `mhproof/rpc.py:37-52` (`RPC.request`) | an id per request, a pending book, the answer or the error settling the waiter, a timeout naming the method; made synchronous with an `Event` |
| `channel.py: Channel.notify` | `mhproof/rpc.py:54-55` (`RPC.notify`) | a notification written under the same writer lock |
| `channel.py: Channel.answer` | `mhproof/rpc.py:57-66` (`RPC.answer`) | an answer to the other side's request, dropped quietly when the channel is closed |
| `channel.py: Channel._read` | `mhproof/rpc.py:68-102` (`RPC.read`) | one reader over the process's stdout, responses to their waiters, everything else to the handler; at the end every waiter settled with the channel's failure. Here the handler is a queue the bridge drains, and each line passes `framing.Parser` |
| `channel.py: Channel.close` | `mhproof/rpc.py:104-115` (`RPC.close`) | stdin closed, then terminate and kill after a bounded wait |
| `grok.py: Adapter.handshake` | `mhproof/adapters.py:273-291` (`Grok.start`) | `initialize` with protocolVersion 1, `authenticate` with `xai.api_key` when the key is set and advertised and `cached_token` otherwise, `session/new` for the session id. `mcpServers` is empty: the profile registers the coordination server (sessions-lever.md, Required of the wall, 3) |
| `grok.py: Adapter.answer` | `mhproof/adapters.py:293-320` (`Grok.request`) | `session/request_permission` never approved automatically; here there is no terminal prompt, so the answer is the offered reject option, else `cancelled` |
| `grok.py: Adapter.observe` | `mhproof/adapters.py:321-376` (`Grok.notification`) | `session/update` kinds read as output, tool and activity; `_x.ai/session/prompt_complete` with `promptId` read as the end of the turn |
| `grok.py: Adapter.submit` | `mhproof/adapters.py:377-408` (`Grok.prompt`, `Grok.deliver`) | `session/prompt` when idle, `_x.ai/interject` with an `interjectionId` when busy |

`Parser.answered`, the duplicate-request refusal and the id type check are
not in the proof; they come from the contract's framing faults.
`codex.py` is not adapted from the proof: its method names and frame shapes
are the installed Codex's (0.155.1), read from its generated schema.

## Not adapted

The proof's test-suite termination policy, its `queue.Queue`, `relay.py`,
`contract.py`, `suite.py`, and `controller()` (`mhproof/sessions.py:221-227`,
which calls `suite.run_suite`). None of these cross into the lane: the
termination policy and the queue are proof-harness test machinery; `relay.py`
and `contract.py` carry proof-only wire framing and tool-restriction prose
that this lane does not use; `suite.py` and `controller()` run the proof's
own multi-peer trial, not a managed lane session.
