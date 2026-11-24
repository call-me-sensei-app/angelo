# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A module that acts as the person holds the harness scope through the twin's own module hooks
(``angelo_guard_twin.stand_in_for_the_person``). Re-read of 1c2c4edaf48, M-2: a module that defined its own
``setUpModule`` after that call replaced the twin's, so the scope was never entered (56 PermissionErrors) and the
twin's ``tearDownModule`` still took the depth below zero. A replaced hook now fails loudly. No Blender."""

import suite_isolation  # noqa: F401

import unittest

import angelo_guard_twin


class HookTests(unittest.TestCase):
    def test_a_replaced_set_up_fails_loudly_and_takes_no_depth(self):
        module = {}
        angelo_guard_twin.stand_in_for_the_person(module)
        before = angelo_guard_twin._depth(angelo_guard_twin.HARNESS)
        module["setUpModule"] = lambda: None   # A module's own hook, defined after the call, rebinding the name.
        module["setUpModule"]()
        with self.assertRaises(RuntimeError):
            module["tearDownModule"]()
        self.assertEqual(angelo_guard_twin._depth(angelo_guard_twin.HARNESS), before)

    def test_a_replaced_tear_down_is_refused_before_any_depth_is_taken(self):
        """Re-read of ccdfea52edb, NIT: a module that replaced tearDownModule would hold the person's scope into every
        later module of a one-process suite."""
        module = {}
        angelo_guard_twin.stand_in_for_the_person(module)
        before = angelo_guard_twin._depth(angelo_guard_twin.HARNESS)
        module["tearDownModule"] = lambda: None
        with self.assertRaises(RuntimeError) as raised:
            module["setUpModule"]()
        self.assertEqual(angelo_guard_twin._depth(angelo_guard_twin.HARNESS), before)
        # Re-read of dd87b2d5aa3, N-4: the message advised a chained tearDownModule, which this check also refuses.
        self.assertIn("addModuleCleanup", str(raised.exception))
        self.assertNotIn("chain", str(raised.exception))

    def test_the_twins_own_hooks_enter_and_leave_once(self):
        module = {}
        angelo_guard_twin.stand_in_for_the_person(module)
        before = angelo_guard_twin._depth(angelo_guard_twin.HARNESS)
        module["setUpModule"]()
        self.assertEqual(angelo_guard_twin._depth(angelo_guard_twin.HARNESS), before + 1)
        module["tearDownModule"]()
        self.assertEqual(angelo_guard_twin._depth(angelo_guard_twin.HARNESS), before)


if __name__ == "__main__":
    unittest.main()
