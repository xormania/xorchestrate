# Contract Driven Development — draft, for the owner to place

Owner named it 2026-08-30: "contract driven development". Drafted in the
conductor's own directory rather than `ops/process/`, because that directory
is the shared operating contract and adding to it is the owner's call.

## The claim

TDD makes the test the specification. Measured over one session, that fails
three ways, all of them observed here and none of them rare:

- **the test can be wrong.** grok's `stamp.ref` assertion would have refused a
  correct implementation. The skeptic's verdict was literally "Mutant CORRECT".
- **the test can own both sides of its oracle.** The tool-guard suite pinned
  membership against a 21-name fixture it authored: 226 tests, six skeptic
  passes, an `approve`, and 21 legitimate tools refused.
- **the test can be an enumeration wearing a property's clothes.**
  `named_volume_bind_device` was a one-token shape list, so `rbind` walked past
  it.

CDD keeps the red-green discipline and moves the authority. **The contract is
the specification. The test is an instrument of the contract. Code is judged
against tests; tests are judged against the contract; and when they disagree,
the contract arbitrates — never whoever is holding the keyboard.**

## The clauses

1. **The contract is written first and ratified**, with a reason, a premise,
   and who ratified it. A clause states a property, not an example.
2. **Every clause declares its tier** — unrepresentable, guaranteed, or denied
   — and a tier-3 clause says why no tier-2 form was available.
3. **Every clause needs an oracle that executes.** A guarantee nobody can
   measure fails the same silent way an incomplete deny list does.
4. **Tests are derived from the contract, never from the code.** The test
   author does not read the implementation.
5. **A skeptic judges the tests against the contract, and may find the contract
   wrong.** This is what makes clause 6 safe.
6. **The implementer may not edit tests.** If a test looks wrong it says so and
   stops. Reasoning roles do not mutate the tree (`github.com/minspec`: "AI
   reasons. MinSpec commands mutate.").
7. **Only the conductor amends the contract**, and an amendment names the
   finding that forced it.
8. **No role can move the goalposts alone.** Contract author, test author,
   skeptic, implementer and reviewers are separate, and separately recorded.
9. **Records carry the evidence a decision rested on**, not merely its outcome.

## What it costs, measured

The skeptic stage did not converge by count: mean P1 per pass 3.14, 2.20, 1.60,
2.25, 2.00, 1.25, 3.00, while unit cost rose, so cost per finding was four times
worse by pass 6. 88% of $290.86 went to the test loop.

Read by *kind* the passes separate cleanly, and three of tonight's P1s were
findings against the **contract**, not the tests — the stage telling the
conductor the law was unwritten. That is CDD working, and it is also the signal
to stop dispatching and go write the clause.

## The open weakness

Steps 4 and 5 are readings, and readings are the expensive way to find detector
blindness. `xormania/loopstrap` carries `tests/mutation-check.sh` — "sampled
causal checks against detector blindness" — which finds mechanically what a
skeptic hunts by hand. If a mutation gate sits before the skeptic, the skeptic
becomes what you buy when the gate is green and something still smells.

Not yet read; named here so the method is not mistaken for finished.
