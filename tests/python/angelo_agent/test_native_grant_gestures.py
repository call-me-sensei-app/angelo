# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A gesture's consent ends with the gesture (grant_gestures.py; the 2.0.0 reviews of 9c8b4e57077).

A slider drag keeps its press's consent for its next move (``press_hold``, ``dragged``). Nothing of it may outlive
the gesture: the release spends the last hold and holds nothing more, a hold lives seconds (a release Angelo never
sees ends it too), and every release a surface handles says so (``final=True``). Against the strict twin of C.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import angelo_guard_twin as twin  # noqa: E402
from angelo_agent import grant_gestures  # noqa: E402
from angelo_agent.grant_guard import value_digest  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
SLIDER = ("canvas.", "popup_slider", "effort")


def held(idname):
    """The live tickets C holds for ``idname`` now."""
    return [ticket for ticket in twin._tickets if ticket[0] == idname]


class GestureConsentEndsWithTheGesture(unittest.TestCase):
    def setUp(self):
        twin.strict()
        twin.reset()
        self.idname = SLIDER[0] + SLIDER[1]
        self.moves = []

    def drag(self, steps):
        """The person's press on the slider and ``steps`` moves; the hold the last move left."""
        twin.press(self.idname, value_digest(SLIDER[2]))
        hold = grant_gestures.press_hold(*SLIDER, lambda: self.moves.append(twin.granting()))
        for _ in range(steps):
            hold = grant_gestures.dragged(hold, lambda: self.moves.append(twin.granting()))
        return hold

    def test_each_move_runs_in_the_press_consent(self):
        self.assertIsNotNone(self.drag(3))
        self.assertEqual(self.moves, [True] * 4)

    def test_no_held_ticket_outlives_the_release(self):
        hold = self.drag(2)
        self.assertIsNone(grant_gestures.dragged(hold, lambda: self.moves.append(twin.granting()), final=True))
        self.assertEqual(self.moves, [True] * 4, "the release's own value is still the person's")
        self.assertEqual(held(self.idname), [], "nothing is held past the release")

    def test_a_hold_lives_seconds_not_a_dialogs_minutes(self):
        self.drag(1)
        self.assertTrue(held(self.idname))
        self.assertTrue(all(ticket[3] <= twin.TICKET_SECONDS for ticket in held(self.idname)),
                        "a release Angelo never sees still ends the gesture in seconds")

    def test_a_drag_no_press_began_holds_nothing(self):
        hold = grant_gestures.press_hold(*SLIDER, lambda: self.moves.append(twin.granting()))
        self.assertIsNone(hold)
        self.assertEqual(self.moves, [False])
        self.assertEqual(held(self.idname), [])


class EveryReleaseEndsItsHold(unittest.TestCase):
    def test_every_release_branch_passes_final(self):
        """A ``grant_gestures.dragged`` call under a ``== 'RELEASE'`` test, in any add-on module, is a release:
        it passes ``final=True``."""
        missing, found = [], 0
        for path in sorted(ADDON.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.If) and "'RELEASE'" in ast.unparse(node.test)):
                    continue
                for call in (n for statement in node.body for n in ast.walk(statement)):
                    if isinstance(call, ast.Call) and ast.unparse(call.func).endswith("grant_gestures.dragged"):
                        found += 1
                        if not any(k.arg == "final" and ast.unparse(k.value) == "True" for k in call.keywords):
                            missing.append("%s:%d" % (path.name, call.lineno))
        self.assertTrue(found, "the slider's release is found")
        self.assertEqual(missing, [])


class OneGuardPerProcess(unittest.TestCase):
    def test_no_module_takes_names_from_the_guard_at_import(self):
        """The guard keeps one registry of what it wraps (grant_guard.ENTRIES). A module that takes names from it at
        import (``from .grant_guard import x``) imports it afresh where ``from . import grant_guard`` finds the package
        attribute another import left, so a process can hold two guards, each with part of the registry
        (v201-firsthour's co-run of test_native_chat_destinations then test_native_grant_operators: 40 failures, ten
        entries of thirty). Modules import the guard as a module and read its names at call time."""
        found = []
        for path in sorted(ADDON.rglob("*.py")):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in (
                        "grant_guard", "grant_authority", "grant_gestures"):
                    found.append("%s:%d" % (path.name, node.lineno))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
