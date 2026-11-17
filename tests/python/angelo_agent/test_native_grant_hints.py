# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's press on a grant control before it armed is answered quietly, with a hint (grant_hints; the
coordinator's ruling on the 0.3 s arm, 2026-11-16; doc/angelo/grant_operators.rst, "Authority in C").

C records such a press (``unarmed``: the control and where). A surface's click that minted no ticket and reaches a
guarded operator, and a press on Blender's own button for one, refuse without a report and show the hint there; a
guarded setting's refused change too. Any other refusal (an agent script running, no press at all) reports as before.
Real operator classes against the stand-in for Blender, under the strict twin of C.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import angelo_guard_twin as twin
from test_native_grant_operators import AUDIT, GuardCase, operator

SPOT = (0x1000, 300, 200, 120, 24)


class AFastPressIsAnsweredWithAHint(GuardCase):
    def setUp(self):
        self.assertIsNotNone(self.guard, "no grant guard")
        twin.reset()
        self.addCleanup(twin.reset)
        from angelo_agent import grant_hints
        self.hints = grant_hints
        self.shown = []
        patcher = patch.object(grant_hints, "show", self.shown.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def execute(self, idname):
        cls = self.cls_of(idname)
        calls = []
        op = operator(cls)
        with patch.object(cls, "_granted_execute", lambda op, ctx: calls.append(op) or {'FINISHED'}):
            result = cls.execute(op, SimpleNamespace())
        return result, calls, [message for _kind, message in op.report.reports]

    def test_a_surfaces_click_before_its_control_armed_refuses_quietly_and_hints(self):
        for idname in AUDIT:
            with self.subTest(operator=idname):
                self.shown.clear()
                twin.unarmed_press("canvas.approve", self.guard.value_digest("call"), SPOT)
                outcome = self.guard.ticketed("canvas.", "approve", "call", self.execute, idname)
                self.assertEqual(outcome[:2], ({'CANCELLED'}, []), "nothing granted")
                self.assertEqual(outcome[2], [], "no error report")
                self.assertEqual(self.shown, [SPOT], "the hint on the control")

    def test_a_click_reaching_no_grant_shows_no_hint(self):
        twin.unarmed_press("canvas.details", self.guard.value_digest(None), SPOT)
        self.assertEqual(self.guard.ticketed("canvas.", "details", None, lambda: "opened"), "opened")
        self.assertEqual(self.shown, [])

    def test_a_press_on_blenders_own_button_before_it_armed_refuses_quietly_and_hints(self):
        for idname in AUDIT:
            with self.subTest(operator=idname):
                self.shown.clear()
                twin.unarmed_press(idname, "", SPOT)
                self.assertEqual(self.execute(idname), ({'CANCELLED'}, [], []))
                self.assertEqual(self.shown, [SPOT])

    def test_without_the_persons_press_the_refusal_reports_as_before(self):
        for idname in AUDIT:
            with self.subTest(operator=idname):
                result, calls, reports = self.execute(idname)
                self.assertEqual((result, calls), ({'CANCELLED'}, []))
                self.assertIn(self.guard.NOT_YOURS, reports)
        self.assertEqual(self.shown, [])

    def test_an_agents_call_never_takes_the_persons_press(self):
        """An agent tool calling the same operator within the press's freshness is refused as the agent's (the error
        it gets is NOT_YOURS), and the person's record stays for their own press (the review of 69d589a, NIT)."""
        from angelo_agent import agent_tool_scope
        idname = next(iter(AUDIT))
        twin.unarmed_press(idname, "", SPOT)
        with agent_tool_scope.agent_tool():
            self.assertIn(self.guard.NOT_YOURS, self.execute(idname)[2])
        self.assertEqual(self.shown, [])
        self.assertEqual(self.execute(idname), ({'CANCELLED'}, [], []), "the person's own press: still the hint")
        self.assertEqual(self.shown, [SPOT])

    def test_another_buttons_press_is_not_this_operators(self):
        idname = next(iter(AUDIT))
        twin.unarmed_press("angelo.some_other_operator", "", SPOT)
        self.assertIn(self.guard.NOT_YOURS, self.execute(idname)[2])
        self.assertEqual(self.shown, [])

    def test_a_refused_setting_change_hints_on_its_checkbox(self):
        from angelo_agent import grant_guard
        twin.unarmed_press("prop.allow_local_cli", "", SPOT)
        prefs = SimpleNamespace(allow_local_cli=True)
        with patch.dict(grant_guard._accepted, {"allow_local_cli": False}):
            grant_guard.setting("allow_local_cli")(prefs, None)
        self.assertFalse(prefs.allow_local_cli, "refused: put back")
        self.assertEqual(self.shown, [SPOT])


if __name__ == "__main__":
    unittest.main()
