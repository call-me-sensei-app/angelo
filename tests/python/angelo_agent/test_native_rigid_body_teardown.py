# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rigid body teardown (Blender #91959, #164309), read from the sources.

Bullet's bodies, constraints and worlds are freed by Blender in any order (doc/angelo/rigid_body_teardown.rst), so
the one file that calls Bullet, ``intern/rigidbody/rb_bullet_api.cpp``, records who is where and detaches before
each free. These tests read that every constraint is made known to its bodies, that no free reaches a body through
the constraint's own references, and that only the world something is in takes it out. ``rigidbody_test`` (C++,
freed memory poisoned) and ``test_blender_rigid_body_teardown.py`` (the app, freed memory filled) run the orders.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "intern/rigidbody/rb_bullet_api.cpp"


def body(text, signature):
    start = text.index(signature)
    index, depth = text.index("{", start), 0
    while True:
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        index += 1
        if depth == 0:
            return text[start:index]


class RigidBodyTeardownSources(unittest.TestCase):
    def setUp(self):
        self.text = API.read_text(encoding="utf-8")

    def test_every_new_constraint_is_made_known_to_its_bodies(self):
        makers = re.findall(r"^rbConstraint \*(RB_constraint_new_\w+)\(", self.text, re.M)
        self.assertGreaterEqual(len(makers), 9)
        for name in makers:
            function = body(self.text, "rbConstraint *%s(" % name)
            self.assertEqual(re.findall(r"\breturn\b[^;]*;", function), ["return constraint_register(con, rb1, rb2);"],
                             "%s returns its constraint through constraint_register" % name)

    def test_a_constraint_is_freed_without_asking_it_for_its_bodies(self):
        """A body may have been freed first (Undo, file load): only bodies the links still name are visited, and the
        constraint leaves its world first (its object may have been removed while the world lists it)."""
        function = body(self.text, "void RB_constraint_delete(")
        self.assertNotIn("getRigidBodyA", function)
        self.assertNotIn("getRigidBodyB", function)
        self.assertLess(function.index("constraint_leave_world(links)"), function.index("delete constraint"))
        self.assertIn("if (object == nullptr) {", function)

    def test_a_body_takes_its_constraints_out_of_their_worlds_before_it_goes(self):
        function = body(self.text, "void RB_body_delete(")
        self.assertLess(function.index("constraint_leave_world(links)"), function.index("delete body"))
        self.assertLess(function.index("body_leave_world(object)"), function.index("delete body"))

    def test_only_the_world_something_is_in_takes_it_out(self):
        """An object freed without a known world asks every scene's world; a rebuild asks the new world for the
        old world's constraints."""
        self.assertIn("if (object->world == world) {", body(self.text, "void RB_dworld_remove_body("))
        self.assertIn("if (links->world == world) {", body(self.text, "void RB_dworld_remove_constraint("))
        for name in ("RB_dworld_remove_body", "RB_dworld_remove_constraint"):
            self.assertNotIn("dynamicsWorld->remove", body(self.text, "void %s(" % name),
                             "Bullet is asked only through the *_leave_world helpers")

    def test_a_constraint_that_lost_a_body_is_not_added_to_a_world(self):
        function = body(self.text, "void RB_dworld_add_constraint(")
        self.assertLess(function.index("links->bodies[0] == nullptr || links->bodies[1] == nullptr"),
                        function.index("addConstraint("))

    def test_a_freed_world_is_forgotten_by_what_was_in_it(self):
        function = body(self.text, "void RB_dworld_delete(")
        self.assertLess(function.index("->world = nullptr"), function.index("delete world->dynamicsWorld"))
        self.assertEqual(function.count("->world = nullptr"), 2, "its constraints and its bodies")

    def test_the_tests_and_the_documentation_exist(self):
        cmake = (ROOT / "intern/rigidbody/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("blender_add_test_executable(rigidbody ", cmake)
        self.assertTrue((ROOT / "intern/rigidbody/tests/rb_teardown_test.cc").is_file())
        manifest = (ROOT / "tests/python/angelo_agent/native_suite_manifest.py").read_text(encoding="utf-8")
        self.assertIn('Suite("test_blender_rigid_body_teardown.py", BACKGROUND', manifest)
        self.assertTrue((ROOT / "doc/angelo/rigid_body_teardown.rst").is_file())
        self.assertIn("doc/angelo/rigid_body_teardown.rst", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
