# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An inferred binding without an observed revision names the real reason and the lookup that returns one.

In a recorded GPT 6.1 Sol tree build a script's read-back left the scene settings' revision out for the host's
own budget; the next program set a scene setting and was refused with "revision_omitted says
why; OVER_LIMIT is angelo_execute_python's ... from a lookup", and the model's lookup (angelo_inspect_scene)
returns no revision. The read-back now keeps that revision (test_native_script_after); this pins what every
remaining refusal says, for each reason a row can lack a revision. The handles and rows are the recorded
``after`` of that script (long handles restored); the reasons beyond it come from the turn context's own
omissions. Host only; no Blender.
"""

import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import binding_refusals, context_ledger  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.program_admission import observed_binding  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402

EPOCH = "f91816344c1642658a77cf05ff70e623"
SCENE, FOLIAGE, GROUND = ("scn:%s:%d" % (EPOCH, uid) for uid in (1, 706, 709))
# The tree build's angelo_execute_python call: the recorded ``after`` (revisions widened to full length).
BUDGET = {"reason": "BUDGET", "detail": "cooperative_turn_context_budget"}
RECORDED_AFTER = {"scene_settings": {"handle": SCENE, "engine": "BLENDER_EEVEE", "revision_omitted": BUDGET},
                  "objects": [{"name": "Tree Foliage", "type": "MESH", "handle": FOLIAGE,
                               "object_revision": "0516" * 16},
                              {"name": "Ground", "type": "MESH", "handle": GROUND, "revision_omitted": BUDGET}]}
SCENE_SETTING = {"kind": "property_set", "parameters": {"values": {"render.resolution_x": 1280}}}


def table_after(after):
    table = IdentifierTable()
    table.compact({"after": after})
    context_ledger.observe_committed(table, {"after": after})
    return table


def program(*operations):
    return {"mode": "APPLY", "operations": list(operations)}


def transform(target):
    return {"kind": "transform", "target": target, "parameters": {"location": [0, 0, 1]}}


class RefusalTests(unittest.TestCase):
    def refused(self, table, arguments):
        with self.assertRaises(IdentifierError) as caught:
            expand_call("angelo_scene_program", arguments, table)
        return str(caught.exception)

    def test_the_recorded_scene_settings_refusal_names_the_budget_and_angelo_scene_settings(self):
        table = table_after(RECORDED_AFTER)
        text = self.refused(table, program(SCENE_SETTING))
        self.assertEqual(text, "o1 has no revision in your latest observation (the host's read time for that "
                               "observation ran out before it): angelo_scene_settings returns the scene's revision; "
                               "declare it in read_set or write_set")
        self.assertNotIn("OVER_LIMIT", text)
        self.assertNotIn("angelo_inspect_scene", text, "It returns no revision")

    def test_an_object_left_out_for_the_budget_names_angelo_scene_object_with_its_handle(self):
        table = table_after(RECORDED_AFTER)
        text = self.refused(table, program(transform("o3")))
        self.assertIn("o3 has no revision in your latest observation (the host's read time", text)
        self.assertIn('angelo_scene_object {"object":"o3"} returns its object_revision', text)
        expand_call("angelo_scene_program", program(transform("o2")), table)  # The object it did read binds.

    def test_each_reason_states_itself(self):
        cases = {
            "OVER_LIMIT": ({"reason": "OVER_LIMIT", "detail": "program_input_mesh_limits",
                            "counts": "194,940 vertices; at most 100,000", "route": "angelo_execute_python"},
                           "o2 is over the typed programs' mesh input limits (194,940 vertices; at most 100,000), "
                           "so no program can bind it; only typed programs have these limits: the mesh can stay as "
                           "it is, and angelo_execute_python edits a mesh of any size"),
            "UNSUPPORTED": ({"reason": "UNSUPPORTED", "detail": "unfingerprinted_data:SURFACE"},
                            "o2's revision cannot be read (unfingerprinted_data:SURFACE), so no program can bind it; "
                            "angelo_execute_python can edit it"),
            "read budget": ({"reason": "BUDGET", "detail": "original_state_work_budget"},
                            "o2's revision cannot be read (original_state_work_budget), so no program can bind it; "
                            "angelo_execute_python can edit it"),
            "selected": ({"reason": "OVER_LIMIT", "detail": "selected_revision_count"},
                         "o2 has no revision in your latest observation (more objects are selected than the "
                         'observation gives revisions (8)): angelo_scene_object {"object":"o2"} returns its '
                         "object_revision; declare it in read_set or write_set"),
        }
        for name, (omitted, expected) in cases.items():
            with self.subTest(name):
                after = {"objects": [{"name": "Grass", "type": "MESH", "handle": FOLIAGE, "revision_omitted": omitted}]}
                table = IdentifierTable()
                table.short_handle(SCENE)  # o1, as in the recorded context.
                table.compact({"after": after})
                context_ledger.observe_committed(table, {"after": after})
                self.assertEqual(self.refused(table, program(transform("o2"))), expected)

    def test_a_row_listed_without_a_revision_and_an_unlisted_handle(self):
        observation = {"objects": [{"name": "Stone 1", "type": "MESH", "handle": FOLIAGE, "location": [0, 0, 0]}]}
        table = IdentifierTable()
        table.compact(observation)
        table.observe(observation)
        self.assertIn("(it is listed without one: the observation gives revisions to the active object",
                      self.refused(table, program(transform("o1"))))
        table.short_handle(GROUND)  # Shown once (a change-feed row), never as a listed object.
        self.assertIn("o2 has no revision in your latest observation (it is not one of its listed objects): "
                      'angelo_scene_object {"object":"o2"} returns its object_revision',
                      self.refused(table, program(transform("o2"))))

    def test_a_revision_shown_later_replaces_the_reason(self):
        table = table_after(RECORDED_AFTER)
        context_ledger.observe_committed(table, {"after": {"objects": [
            {"name": "Ground", "type": "MESH", "handle": GROUND, "object_revision": "9" * 64}]}})
        resolved = expand_call("angelo_scene_program", program(transform("o3")), table)
        self.assertEqual(resolved["write_set"], [{"handle": GROUND, "revision": "9" * 64}])

    def test_an_undeclared_dependency_states_the_same_reason(self):
        table = table_after(RECORDED_AFTER)

        class Ground:
            name = "Ground"

        controller = type("Controller", (), {"_identifiers": table})()
        # The production store needs Blender; observed_binding reads only its handle function.
        with patch.dict(sys.modules, {"angelo_agent.scene_queries": SimpleNamespace(object_handle=lambda obj: GROUND)}):
            with self.assertRaises(ProgramError) as caught:
                observed_binding(controller, Ground())
        self.assertIn("('Ground': o3 has no revision in your latest observation (the host's read time",
                      str(caught.exception))
        self.assertIn('angelo_scene_object {"object":"o3"}', str(caught.exception))

    def test_a_dependency_the_context_never_saw_is_named_by_a_handle_it_can_use(self):
        """Regression: an undeclared dependency the table never numbered read 'this object' and
        angelo_scene_object {"object":"this object"}."""
        table = table_after(RECORDED_AFTER)
        parent = "scn:%s:%d" % (EPOCH, 215)

        class Parent:
            name = "QA Rig Parent"

        class Scene:
            objects = {"QA Rig Parent": Parent.__new__(Parent)}  # Any other object of that name is not it.

        controller = type("Controller", (), {"_identifiers": table})()
        with patch.dict(sys.modules, {"angelo_agent.scene_queries": SimpleNamespace(object_handle=lambda obj: parent)}):
            with self.assertRaises(ProgramError) as caught:
                observed_binding(controller, Parent())  # No scene given: the table's reason.
            self.assertIn("o4 has no revision in your latest observation (it is not one of its listed objects): "
                          'angelo_scene_object {"object":"o4"}', str(caught.exception))
            self.assertEqual(table.known_handle("o4"), parent, "o4 now expands to that dependency")
            with self.assertRaises(ProgramError) as caught:
                observed_binding(controller, Parent(), Scene())
            self.assertIn("is not in the bound scene, so no lookup returns its revision", str(caught.exception))

    def test_a_later_turn_observation_drops_an_earlier_reason(self):
        table = table_after(RECORDED_AFTER)
        context_ledger.compact_observation({"objects": [{"name": "Tree Foliage", "type": "MESH", "handle": FOLIAGE,
                                                         "object_revision": "0516" * 16}]}, table)
        self.assertIn("(it is not one of its listed objects)", self.refused(table, program(transform("o3"))))

    def test_text_where_a_handle_belongs_names_the_fix(self):
        self.assertEqual(binding_refusals.not_a_handle("Tree\nTrunk"),
                         "'Tree Trunk' is not an object handle: target an existing object by its o<n> handle from the "
                         "observation, never its name, and an object this program creates by its $alias")


if __name__ == "__main__":
    unittest.main()
