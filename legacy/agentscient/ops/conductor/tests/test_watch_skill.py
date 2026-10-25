"""The watch skill's two recipes against the contract's list of lines.

Contracts (only): `ops/conductor/watchdog-lever.md` §The lines and
§Deviations, and the page under test, `.claude/skills/watch/SKILL.md`.
The tool is neither read nor run here; the subject is the two grep
recipes the page tells a session to arm, and the contract is the
inventory of lines they are judged against.

Origin: round two of #244 — codex 20260916T091534Z-review-codex-78168f#2
and grok 20260916T091513Z-review-grok-bf7932#1, disposed by the Lead on
2026-09-16 at 09:40Z: "a row runs every line the tool prints through the
fallback filter so none can miss again", and "the description names
harness gone and watchdog error, or the filter drops them; pick the
former". The defect they caught: the fallback filter anchored
`unobservable ` while the line reads `worker unobservable`, so a live
condition was dropped in silence. LD-70.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
PAGE = REPO / ".claude" / "skills" / "watch" / "SKILL.md"
CONTRACT = REPO / "ops" / "conductor" / "watchdog-lever.md"

RECIPE = re.compile(r"grep -E --line-buffered '([^']*)'")
BULLET = re.compile(r"^- `([^`<]*)", re.MULTILINE)

PASS = "passed"
DROP = "dropped"

# Every line family the contract names, one row each, with a sample
# built from the contract's own template and the treatment the page's
# fallback filter owes it. A dropped row is a family the first tick of
# an old checkout replays; a passed row is one that cannot be a replay,
# which is what that recipe says it keeps.
LINES = (
    ("closed", DROP,
     "closed 20260904T100400Z-code-grok-aa3105 status=closed env=ok changed=3 after 12 min"),
    ("drift", DROP,
     "drift 20260904T100400Z-code-grok-aa3105: branch lane/session-x outside the deliverable (lane/session-y)"),
    ("deviation", DROP,
     "deviation envelope 20260904T100400Z-code-grok-aa3105"),
    ("no record copy", DROP,
     "no record copy 20260904T100800Z-code-grok-aa3109: cannot judge (no record.json in the job directory)"),
    ("stall", PASS,
     "stall lane/session-x: no dispatch running, 61 min since 20260904T100400Z-code-grok-aa3105"),
    ("stall ladder", PASS,
     "stall lane/session-x: ladder 20260904T100400Z-plan-claude-aa3110 stopped changes at review"),
    ("harness gone", PASS,
     "harness gone 20260904T100100Z-code-grok-aa3102 after 31 min: pid 4242 dead, no exit written"),
    ("worker unobservable", PASS,
     "worker unobservable 20260904T100000Z-code-grok-aa3101: no usable worker PID, no exit written"),
    ("exit written", PASS,
     "exit written, not collected 20260904T100300Z-code-grok-aa3104: 7 min since exit, record still running"),
    ("long", PASS,
     "long 20260904T100000Z-code-grok-aa3101: 31 min running, wall 40 min"),
    ("launchers", PASS,
     ("launchers fewer than running records: 1 launch.py processes, 3 running "
      "(heuristic: the record does not name its launcher)")),
    ("ci fail", PASS,
     "ci fail #244 at cf067ff4f841a9fff24460c356abd8cd82680efb: tests"),
    ("ci cancel", PASS,
     "ci cancel #244 at cf067ff4f841a9fff24460c356abd8cd82680efb: tests"),
    ("heartbeat", PASS,
     ("heartbeat: running=20260904T100000Z-code-grok-aa3101 3/40; settling=0; "
      "launchers=1; open PRs into dev=3; deviations=2")),
    ("watchdog error", PASS,
     ("watchdog error: InputUnavailable: job inputs unavailable for "
      "20260904T101900Z-code-grok-aa3119 (tick 3; the loop continues)")),
    ("watchdog armed", PASS,
     "watchdog armed: store /tmp/dispatch, 4 records ignored as backlog, tick 120s"),
)

# The primary recipe, token by token, beside the word its description
# owes each token. The page says the description names exactly what the
# filter passes; this table is that sentence, checkable.
DESCRIBED = (
    ("stall ", "stalls"),
    ("deviation ", "deviations"),
    ("no record copy ", "no record copy"),
    ("harness gone ", "harness gone"),
    ("watchdog error", "watchdog errors"),
    ("ci fail ", "CI"),
    ("ci cancel ", "CI"),
)


def samples(treatment: str) -> tuple[str, ...]:
    return tuple(line for _, owed, line in LINES if owed == treatment)


class WatchPageRecipes(unittest.TestCase):
    """The page's recipes are measured against the contract's lines."""

    def setUp(self):
        self.world = World(prefix="watch-skill-")
        self.addCleanup(self.world.close)
        self.page = PAGE.read_text(encoding="utf-8")
        self.contract = CONTRACT.read_text(encoding="utf-8")

    def recipes(self) -> tuple[str, str]:
        found = RECIPE.findall(self.page)
        self.assertEqual(
            len(found), 2,
            f"INVALID: the page should carry a live recipe and a fallback; found {len(found)}",
        )
        return found[0], found[1]

    def contract_lines(self) -> str:
        head = self.contract.index("## The lines")
        tail = self.contract.index("## ", head + len("## The lines"))
        return self.contract[head:tail]

    def grep(self, pattern: str, lines: tuple[str, ...]):
        return run(
            [self.world.require("grep"), "-E", pattern],
            cwd=REPO,
            env=self.world.env(),
            stdin="\n".join(lines) + "\n",
            timeout=20,
        )

    def test_every_line_the_contract_names_has_a_row(self):
        """The inventory is the contract's, not this table's: a line
        family added there without a row here is the miss that codex
        78168f#2 found, in advance."""
        prefixes = [p for p in BULLET.findall(self.contract_lines()) if p.strip()]
        self.assertGreaterEqual(len(prefixes), 13, prefixes)
        rows = [line for _, _, line in LINES]
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    any(line.startswith(prefix) for line in rows),
                    f"{prefix!r} is a line the contract names and this table does not carry",
                )

    def test_the_fallback_filter_passes_every_live_line(self):
        _, fallback = self.recipes()
        live = samples(PASS)
        result = self.grep(fallback, live)
        self.assertEqual(result.stdout.splitlines(), list(live), result.stdout)
        self.assertEqual(result.exit, 0, result.stderr)

    def test_the_fallback_filter_drops_every_replayable_line(self):
        _, fallback = self.recipes()
        result = self.grep(fallback, samples(DROP))
        self.assertEqual(result.stdout, "", result.stdout)
        self.assertEqual(result.exit, 1, result.stderr)

    def test_the_live_recipe_description_names_what_its_filter_passes(self):
        primary, _ = self.recipes()
        self.assertEqual(
            sorted(primary.split("|")),
            sorted(token for token, _ in DESCRIBED),
            primary,
        )
        description = re.search(r'description: "([^"]*)"', self.page)
        self.assertIsNotNone(description, "INVALID: the recipe carries no description")
        said = description.group(1)
        for token, word in DESCRIBED:
            with self.subTest(token=token):
                self.assertIn(word, said, f"{token!r} passes, and {word!r} is not named")

    def test_the_live_recipe_keeps_the_forge_on(self):
        """A recipe that advertises CI and passes --no-forge cannot
        report it (codex ee08a7#2, round one)."""
        primary, _ = self.recipes()
        self.assertIn("ci fail ", primary)
        recipe = self.page.split("Monitor(")[1].split("```")[0]
        self.assertNotIn("--no-forge", recipe, recipe)


if __name__ == "__main__":
    unittest.main()
