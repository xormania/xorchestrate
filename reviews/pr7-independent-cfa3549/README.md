# PR #7 independent verification

Pinned subject: `cfa3549490ee9db1c3e6cad918523cd854902e51`. Review branch: `verification/pr7-independent-cfa3549`. xor remains final authority.

**The controlled negative result reproduces.** Structured checks plus probes resolve 24/24 final actions; P4 resolves 19/24 and retains two false clears. No production adoption is supported. This is not a general verdict about Jev on operational incidents.

- [Independent report and prioritized findings](REPORT.md)
- [E01–E14 verdicts](VERDICTS.md)
- [Exact execution results, hosted identity, and unexercised checks](EXECUTION.md)
- [Keyless replay and minimal reproduction commands](REPLAY.md)
- [Raw-results recomputation](results/recomputed.json) and [economic/supplement recomputation](results/economics-recomputed.json)
- [Behavioral and tamper controls](notes/ADVERSARIAL-CHECKS.md)
- [Methodology](notes/methodology.md), [provenance](notes/provenance.md), [raw recomputation note](notes/RECOMPUTATION-NOTE.md), and [economics review](notes/ECONOMICS-REVIEW.md)
- [Fresh hosted run](https://github.com/xormania/xorchestrate/actions/runs/40131762088): 699 tests +172 subtests, no failures/errors/skips; [retained original artifact ZIP](evidence/actions-40131762088.zip)
- [Review file inventory](SHA256.json)

The review separates correct implementation, measurement validity, and economic usefulness. It confirms original numbers while identifying redundant-probe attribution, confounded allocation comparisons, a semantic-replay validation gap, and robustness supplied partly by deterministic composition. Exact native model attribution remains unverified. All original outcomes are preserved; no implementation repair, paid model call, merge, or deployment occurred.
