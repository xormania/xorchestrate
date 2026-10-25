"""Ordinary allocation cannot strand an unresolved decision without an investigator."""

import json
import unittest
from unittest.mock import patch

from fixtures import commands as test_commands
from fixtures.commands import CommandFixture
from fixtures.runtime import RuntimeFixture

from devtools.api import API
from devtools.role_packages import catalog


def configure(state, *, enabled=True, reserved=1, role_override=False):
    policy = {"features": {"selection": enabled and not role_override, "advice": False,
                            "filtering": False, "completion": False},
              "decisions": {"reserved_sessions": reserved}}
    overlay = {"version": 1, "project": policy}
    if role_override:
        overlay["roles"] = {"research": {"features": {"selection": enabled}}}
    team = json.loads((state / "team.json").read_text())
    team["project"]["configuration_file"] = "capacity-settings.json"
    (state / "team.json").write_text(json.dumps(team))
    (state / "capacity-settings.json").write_text(json.dumps(overlay))


class RuntimeDecisionCapacityTests(unittest.TestCase):
    setUp = RuntimeFixture.setUp
    write = RuntimeFixture.write
    vendor = RuntimeFixture.vendor
    xo = RuntimeFixture.xo

    def allocate(self, coordinator, role):
        config = coordinator.commands._config()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            slot = coordinator.commands._slot(db, config, role=role)
            coordinator.control.bind_in(db, slot["name"], role,
                                        coordinator.control.current()["work_order"], None, coordinator.member)
        return slot

    def test_runtime_preserves_reserve_for_active_and_role_override_decisions(self):
        coordinator = self.xo()
        for role_override in (False, True):
            with self.subTest(role_override=role_override):
                configure(self.state, role_override=role_override)
                policy = json.loads((self.state / "roles.json").read_text())
                policy["max_sessions"] = 3
                self.write("roles", policy)
                worker = self.allocate(coordinator, "coder")
                with self.assertRaisesRegex(ValueError, "reserved"):
                    self.allocate(coordinator, "research")
                investigator = self.allocate(coordinator, "arbitrator")
                self.assertNotEqual(worker["name"], investigator["name"])
                coordinator.control.unbind(worker["name"])
                coordinator.control.unbind(investigator["name"])

    def test_explicitly_disabled_decisions_do_not_reduce_capacity(self):
        coordinator = self.xo()
        configure(self.state, enabled=False, reserved=2)
        policy = json.loads((self.state / "roles.json").read_text())
        policy["max_sessions"] = 2
        self.write("roles", policy)
        self.allocate(coordinator, "coder")
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.allocate(coordinator, "research")


class FixedDecisionCapacityTests(unittest.TestCase):
    setUp = CommandFixture.setUp

    def test_selected_member_cannot_consume_only_eligible_arbitrator_slot(self):
        self.co.call("direct", test_commands.GOAL)
        coordinator = API(self.state, "xo")
        configure(self.state)
        team = json.loads((self.state / "team.json").read_text())
        for member in team["members"]:
            if member["name"] in {"coder", "audit", "spare"}:
                member.update(harness="claude" if member["name"] == "spare" else "codex", binary="/bin/true")
        (self.state / "team.json").write_text(json.dumps(team))
        packages = catalog()
        packages["roles"]["arbitrator"]["runtime"]["harnesses"] = ["claude"]
        candidate = next(row for row in team["members"] if row["name"] == "spare")
        with patch("devtools.role_packages._catalog", return_value=packages), self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "reserved"):
                coordinator.commands._slot(db, team, role="coder", selected=candidate)
            worker = coordinator.commands._slot(db, team, role="coder")
            self.assertEqual("coder", worker["name"])
            investigator = coordinator.commands._slot(db, team, role="arbitrator")
            self.assertEqual("spare", investigator["name"])


if __name__ == "__main__":
    unittest.main()
