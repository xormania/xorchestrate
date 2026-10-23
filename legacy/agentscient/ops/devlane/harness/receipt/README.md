# receipt/ — extracted engine receipt modules

`canonical.py` (blob `dbc04433`), `gitio.py` (blob `d27994fe`) and
`errors.py` (blob `8c8b7540`) are byte-for-byte copies from the previous
repository's workflow engine at commit 99334f7, under hand grant 89
(owner, 2026-09-11: extract rather than rewrite). `receipts.py` originated
there as blob `ff2afe19` and was adapted here at 64de462 to return raw
captured output bytes, so the lever hashes non-UTF-8 output without a
lossy text decode. Their `Source:` trailers name that path and commit.

What the lever uses: `canonical` (digests, canonical JSON, the wire time),
`receipts.run` (a wrapped command with both outputs captured) and
`errors.Refusal` (mapped to this lane's wire). What waits for the later
frozen-set gate: `receipts.Scope`, the manifest functions and
`toolchain_string`. What is dead here and kept for fidelity: the `gitio`
functions that take the engine's store as an argument.

`head_sha` in a receipt is the sha256 of the commit id's hex: the contract
wants a 64-hex digest and git here gives 40. `verify` recomputes it the
same way, so the two always agree or the tree has moved.
