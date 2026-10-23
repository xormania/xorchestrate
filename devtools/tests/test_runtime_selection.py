"""Assignment runtime decisions survive launch and cannot silently change profile."""

import copy
import json
import os
import unittest
from unittest.mock import patch

from fixtures.runtime_selection import RuntimeSelectionFixture

from devtools import runtime
from devtools.api import API
from devtools.control import Control
from devtools.launch import configuration, initialize, team_config
from devtools.store import canonical, stamp


class RuntimeSelectionTests(RuntimeSelectionFixture, unittest.TestCase):


    def test_nonfirst_profile_reaches_configuration_and_survives_ordinary_restart(self):
        scope, choice = self.choose()
        self.assertEqual("codex", runtime.resolve(self.state, "coder")["harness"])
        for _ in range(2):
            config = configuration(self.state, scope["member"])
            self.assertEqual("claude", config["harness"])
            self.assertEqual("model-claude", config["model"])
            self.assertEqual(choice["selection"], config["selection"])
            generation = self.store.begin_session(scope["member"], os.getpid(), settings=config)
            self.store.session(scope["member"], generation=generation, state="stopped", pid=None)
        self.assertEqual(choice, runtime.selected(self.store, scope, required=True))

    def test_changed_policy_does_not_silently_update_an_assignment_model(self):
        scope, _ = self.choose()
        self.policy["profiles"]["claude"]["model"] = "new-model"
        self.write_policy()
        with self.assertRaisesRegex(ValueError, "Runtime policy changed"):
            configuration(self.state, scope["member"])
        choice = runtime.resolve(self.state, "coder", profile="claude")
        with self.store.connect() as db:
            runtime.retain(self.store, db, scope, choice)
        self.assertEqual("new-model", configuration(self.state, scope["member"])["model"])

    def test_unavailable_selected_vendor_or_binary_never_falls_to_first_profile(self):
        scope, _ = self.choose()
        with patch("devtools.runtime.shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "No runtime available"):
            runtime.selected(self.store, scope)
        vendors = json.loads((self.state / "vendors.json").read_text())
        vendors["vendors"]["anthropic"]["available"] = False
        (self.state / "vendors.json").write_text(json.dumps(vendors))
        self.assertEqual("codex", runtime.resolve(self.state, "coder")["harness"])
        with self.assertRaisesRegex(ValueError, "No runtime available"):
            configuration(self.state, scope["member"])

    def test_changed_work_content_needs_an_explicit_new_selection(self):
        scope, _ = self.choose()
        other = self.operator.work.work_order_create(title="Different work", objective="Changed scope",
                                                     done_when="New decision")["work_order"]["id"]
        self.operator.control.bind("owner", "co", other)
        current = self.operator.control.bind(scope["member"], "coder", other, reports_to="owner")
        with self.assertRaisesRegex(ValueError, "Work binding changed"):
            runtime.selected(self.store, scope)
        with self.assertRaisesRegex(ValueError, "Work binding changed"):
            configuration(self.state, scope["member"])
        choice = runtime.resolve(self.state, "coder", profile="grok")
        with self.store.connect() as db:
            runtime.retain(self.store, db, current, choice)
            db.execute("UPDATE work_orders SET objective='Revised objective' WHERE id=?", (other,))
        current = Control(self.store, scope["member"]).snapshot()
        with self.assertRaisesRegex(ValueError, "Work binding changed"):
            runtime.selected(self.store, current)
        with self.store.connect() as db:
            runtime.retain(self.store, db, current, choice)
        self.assertEqual("grok", configuration(self.state, scope["member"])["harness"])

    def test_pause_guidance_and_reporting_transfer_preserve_same_assignment_selection(self):
        scope, choice = self.choose()
        self.store.initialize([{"name": "new-manager", "harness": "external", "workspace": str(self.workspace)}])
        self.operator.control.bind("new-manager", "xo", self.order, reports_to="owner")
        for paused in (1, 0):
            with self.store.connect() as db:
                db.execute("UPDATE member_bindings SET paused=?,brief='Continue the current assignment',"
                           "reports_to='new-manager',revision=revision+1 WHERE member=?", (paused, scope["member"]))
            current = Control(self.store, scope["member"]).snapshot()
            self.assertEqual(choice, runtime.selected(self.store, current, required=True))
        self.assertEqual("claude", configuration(self.state, scope["member"])["harness"])

    def test_legacy_operator_binding_can_resolve_but_required_decision_cannot_be_missing(self):
        scope, _ = self.choose(retain=False)
        self.assertEqual("codex", configuration(self.state, scope["member"])["harness"])
        with self.assertRaisesRegex(ValueError, "needs a retained runtime selection"):
            runtime.selected(self.store, scope, required=True)

    def test_new_decision_marker_cannot_lose_its_persisted_profile_and_use_a_default(self):
        scope, _ = self.choose(retain=False)
        with self.store.connect() as db:
            assigned = db.execute("INSERT INTO role_assignments(work_order,name,role,brief,details,created_by,created,updated) "
                                  "VALUES (?,?,?,?,?,?,?,?)", (self.order, "Selected assignment", "coder", "{}",
                                  canonical({"selection_decision": {"decisions": [1]}}), "owner", stamp(), stamp())).lastrowid
            db.execute("UPDATE member_bindings SET assignment_id=? WHERE member=?", (assigned, scope["member"]))
        with self.assertRaisesRegex(ValueError, "needs a retained runtime selection"):
            configuration(self.state, scope["member"])

    def test_allocation_and_retention_reject_forged_or_stale_choice_without_side_effects(self):
        choice = runtime.resolve(self.state, "coder", profile="claude")
        forged = copy.deepcopy(choice)
        forged["model"] = "unconfigured-model"
        before = self.store.members()
        for attempted in (forged, {**choice, "selection": {**choice["selection"], "role": "research"}}):
            with self.subTest(choice=attempted), self.store.connect() as db, self.assertRaisesRegex(ValueError, "Runtime"):
                runtime.allocate(self.store, db, team_config(self.state), "coder", selected=attempted)
        self.assertEqual(before, self.store.members())
        scope, _ = self.choose()
        with self.store.connect() as db:
            retained = tuple(db.execute("SELECT * FROM runtime_selections WHERE member=?", (scope["member"],)).fetchone())
            with self.assertRaisesRegex(ValueError, "profile changed"):
                runtime.retain(self.store, db, scope, forged)
            self.assertEqual(retained, tuple(db.execute("SELECT * FROM runtime_selections WHERE member=?", (scope["member"],)).fetchone()))
        self.policy["max_sessions"] += 1
        self.write_policy()
        with self.store.connect() as db, self.assertRaisesRegex(ValueError, "Runtime policy changed"):
            runtime.allocate(self.store, db, team_config(self.state), "coder", existing=scope, selected=choice)

    def test_fixed_team_uses_selected_member_and_rechecks_availability(self):
        target = self.root / "fixed"
        initialize(target, self.workspace, ["owner:external", "first:external", "second:external"], co="owner")
        operator = API(target, "owner", operator=True)
        config = team_config(target)
        first, second = config["members"][1:]
        order = operator.work.work_order_create(title="Fixed selection", objective="Choose second member",
                                                done_when="No first-member fallback")["work_order"]["id"]
        operator.control.bind("owner", "co", order)
        with operator.store.connect() as db:
            chosen = operator.commands._slot(db, config, role="coder", selected=second)
            self.assertEqual("second", chosen["name"])
            bound = operator.control.bind_in(db, "second", "coder", order, reports_to="owner")
            with self.assertRaisesRegex(ValueError, "no longer available"):
                operator.commands._slot(db, config, role="coder", selected=second)
            self.assertEqual(second, operator.commands._slot(db, config, existing=bound, role="coder", selected=second))
            with self.assertRaisesRegex(ValueError, "must retain"):
                operator.commands._slot(db, config, existing=bound, role="coder", selected=first)
            with self.assertRaisesRegex(ValueError, "settings"):
                operator.commands._slot(db, config, role="coder", selected={**first, "workspace": "/changed"})

    def test_unbind_and_release_retire_the_previous_selection(self):
        from devtools.delivery import Delivery

        for release in ("unbind", "release"):
            with self.subTest(release=release):
                scope, _ = self.choose()
                if release == "unbind":
                    self.operator.control.unbind(scope["member"])
                else:
                    with self.store.connect() as db:
                        Delivery.release(db, scope["member"])
                with self.store.connect() as db:
                    self.assertIsNone(db.execute("SELECT * FROM runtime_selections WHERE member=?", (scope["member"],)).fetchone())

    def test_failed_assignment_transaction_rolls_back_seat_binding_and_selection(self):
        choice = runtime.resolve(self.state, "coder", profile="claude")
        before = self.store.members()
        with self.assertRaisesRegex(ValueError, "later assignment failed"):
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                slot = runtime.allocate(self.store, db, team_config(self.state), "coder", selected=choice)
                scope = self.operator.control.bind_in(db, slot["name"], "coder", self.order, reports_to="owner")
                runtime.retain(self.store, db, scope, choice)
                raise ValueError("later assignment failed")
        self.assertEqual(before, self.store.members())
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM runtime_selections").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
