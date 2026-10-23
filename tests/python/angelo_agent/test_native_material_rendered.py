# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An assigned material is a rendered one: a slot no face uses leaves the object looking unchanged.

Recorded (1.0.5 A/B, kitchen, Opus and Sol, 16 of 16 trials): material_assign with ``also`` added the material as a new
slot on each ``also`` object but moved none of its faces, and the host still reported its material_assignment checks
as passed, because they read the slots only. Now, where an object's base faces decide what it renders
(``material_faces.faces_decide``), material_assignment needs a face that renders the material. The ``also`` kernel
itself is pinned by the Blender suite test_blender_material_sharing.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import material_faces  # noqa: E402
from angelo_agent.node_edit_spec import SOURCE  # noqa: E402
from angelo_agent.program_material_provenance import selected_material_report  # noqa: E402
from angelo_agent.scene_validation import VerificationContext, verify  # noqa: E402


class Material(dict):
    """A material ID: hashable by identity, ID properties by key, a session UID."""
    _next = [100]

    def __init__(self, name, source=None):
        super().__init__({SOURCE: source} if source is not None else {})
        self.name, self.session_uid = name, self._next[0]
        self._next[0] += 1

    __hash__ = object.__hash__

    def __bool__(self):   # An ID is truthy whatever its properties.
        return True

    def __eq__(self, other):
        return self is other


class Polygons(list):
    def foreach_get(self, name, out):
        out[:] = [getattr(polygon, name) for polygon in self]


def mesh_object(slots, face_slots, modifiers=(), particles=0, mode="OBJECT"):
    polygons = Polygons(NS(vertices=(0, 1, 2), material_index=index) for index in face_slots)
    mesh = NS(vertices=[NS(co=[0, 0, 0], groups=[]), NS(co=[1, 0, 0], groups=[]), NS(co=[0, 1, 0], groups=[])],
              edges=[NS(vertices=(0, 1)), NS(vertices=(1, 2)), NS(vertices=(0, 2))], polygons=polygons,
              loops=list(range(3 * len(polygons))), materials=list(slots), is_editmode=mode == "EDIT")
    return NS(type="MESH", name="QA Cabinet", mode=mode, data=mesh, material_slots=[NS(material=m) for m in slots],
              modifiers=list(modifiers), particle_systems=[NS()] * particles, constraints=[], parent=None,
              matrix_world=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], animation_data=None)


PRIMER, WALNUT = Material("Primer"), Material("Walnut")


def assignment(obj):
    context = VerificationContext({"$obj": obj}, [obj])
    return verify(context, [{"kind": "material_assignment", "target": "$obj", "material": "Walnut",
                             "all_faces": False}])


class CheckTests(unittest.TestCase):
    def test_a_slot_no_face_uses_is_not_an_assignment(self):
        report = assignment(mesh_object([PRIMER, WALNUT], [0, 0, 0]))
        self.assertFalse(report["passed"])
        self.assertEqual(report["checks"][0]["observed"]["rendered_faces"], 0)

    def test_faces_on_the_slot_render_it(self):
        self.assertTrue(assignment(mesh_object([PRIMER, WALNUT], [1, 1, 0]))["passed"])

    def test_an_index_past_the_last_slot_renders_the_last_slot(self):
        self.assertTrue(assignment(mesh_object([PRIMER, WALNUT], [7, 7, 7]))["passed"])

    def test_a_missing_material_still_fails(self):
        self.assertFalse(assignment(mesh_object([PRIMER], [0]))["passed"])

    def test_only_the_slots_decide_where_evaluation_can_map_faces_to_other_slots(self):
        for name, extra in (("no faces", {}), ("geometry nodes", {"modifiers": [NS(type="NODES")]}),
                            ("particle hair", {"particles": 1}), ("edit mode", {"mode": "EDIT"}),
                            ("solidify offset", {"modifiers": [NS(type="SOLIDIFY", material_offset=1,
                                                                  material_offset_rim=0)]}),
                            ("bevel material", {"modifiers": [NS(type="BEVEL", material=1)]}),
                            ("wireframe offset", {"modifiers": [NS(type="WIREFRAME", material_offset=1)]}),
                            ("array caps", {"modifiers": [NS(type="ARRAY", start_cap=NS(), end_cap=None)]}),
                            ("unknown modifier", {"modifiers": [NS(type="REMESH")]})):
            with self.subTest(name):
                obj = mesh_object([PRIMER, WALNUT], [] if name == "no faces" else [0, 0], **extra)
                report = assignment(obj)
                self.assertTrue(report["passed"])
                self.assertEqual(report["checks"][0]["observed"]["scope"], "object_material_slots")

    def test_a_spent_budget_reads_the_slots_and_says_why(self):
        from unittest.mock import patch
        from angelo_agent.validation_reader import Inconclusive, ValidationReader

        def spent(self, amount):
            raise Inconclusive("Verification resource budget exhausted")
        obj = mesh_object([PRIMER, WALNUT], [0, 0])
        with patch.object(ValidationReader, "spend", spent):
            observed = assignment(obj)["checks"][0]["observed"]
        self.assertEqual(observed["scope"], "object_material_slots")
        self.assertIn("budget", observed["faces_unread"])

    def test_index_keeping_modifiers_leave_the_faces_deciding(self):
        for modifier in (NS(type="SUBSURF"), NS(type="MIRROR"), NS(type="WELD"), NS(type="MASK"),
                         NS(type="DATA_TRANSFER"), NS(type="BEVEL", material=-1),
                         NS(type="SOLIDIFY", material_offset=0, material_offset_rim=0),
                         NS(type="ARRAY", start_cap=None, end_cap=None)):
            with self.subTest(modifier.type):
                self.assertFalse(assignment(mesh_object([PRIMER, WALNUT], [0, 0], [modifier]))["passed"])


class BoundSlotTests(unittest.TestCase):
    """The identity-proved slot (a bound material) is held to the same rule."""

    @staticmethod
    def report(material, holder, slot):
        return selected_material_report(material, {"kind": "material_assignment", "target": "$obj",
                                                   "material": "Walnut", "all_faces": False}, holder, slot)

    def test_the_bound_slot_must_be_rendered(self):
        self.assertFalse(self.report(WALNUT, mesh_object([PRIMER, WALNUT], [0, 0]), 1)["success"])
        self.assertTrue(self.report(WALNUT, mesh_object([PRIMER, WALNUT], [0, 1]), 1)["success"])

    def test_any_slot_holding_the_same_live_material_counts(self):
        # Two private copies of one live material (staging copies per object): faces on either render it.
        live = Material("Walnut")
        first, second = Material("Walnut", source=live.session_uid), Material("Walnut.001", source=live.session_uid)
        self.assertTrue(self.report(first, mesh_object([first, PRIMER, second], [2, 2]), 0)["success"])

    def test_no_holder_reads_the_slot_alone(self):
        self.assertTrue(self.report(WALNUT, None, None)["success"])


class IdentityTests(unittest.TestCase):
    def test_private_copies_of_one_live_material_are_one_material(self):
        live = Material("Walnut")
        copy = Material("Walnut.002", source=live.session_uid)
        holder = mesh_object([PRIMER, copy], [0])
        self.assertEqual(material_faces.slots_holding(holder, live), [1])
        self.assertEqual(material_faces.slots_holding(holder, Material("Walnut")), [])


class SupersededTests(unittest.TestCase):
    """A later material operation of the program moves an object's faces on: its earlier assignment is superseded."""

    def test_a_later_also_drops_the_earlier_derived_check_as_a_target_does(self):
        from angelo_agent.derived_checks import derive_checks

        def assign(target, name, also):
            return {"kind": "material_assign", "target": target, "parameters": {
                "material": name, "source": None, "slot": -1, "face_indices": [], "copy_as": None, "also": also,
                "index_scope": "INITIAL", "topology_step": 0, "face_selector": None}}
        checks = derive_checks([assign("$a", "First", ["$b"]), assign("$c", "Second", ["$b"])])
        on_b = sorted(check["material"] for check in checks
                      if check["kind"] == "material_assignment" and check["target"] == "$b")
        self.assertEqual(on_b, ["Second"])

    @staticmethod
    def rendered_flags(operations, target):
        from angelo_agent.program_material_obligations import material_checks
        checks = material_checks({"operations": operations})[0]
        return {check["material"]: check["__host_material"].get("rendered", False) for check in checks
                if check["kind"] == "material_assignment" and check["target"] == target}

    def test_only_an_objects_most_recent_assignment_must_be_rendered(self):
        def primitive(alias):
            return {"kind": "create_primitive", "target": None,
                    "parameters": {"primitive": "CUBE", "name": alias, "alias": alias}}

        def principled(target, name, inputs=None, also=()):
            return {"kind": "material_principled", "target": target, "parameters": {
                "name": name, "inputs": {"Base Color": [1, 0, 0, 1]} if inputs is None else inputs, "slot": -1,
                "face_indices": [], "face_selector": None, "also": list(also), "shared": "REFUSE",
                "index_scope": "INITIAL", "topology_step": 0}}

        def assign(target, name, also=()):
            return {"kind": "material_assign", "target": target, "parameters": {
                "material": name, "source": None, "slot": -1, "face_indices": [], "copy_as": None,
                "also": list(also), "index_scope": "INITIAL", "topology_step": 0, "face_selector": None}}
        # A, then B, then A again (faces end on A): A must render, B is superseded (break-it review, round 3).
        self.assertEqual(self.rendered_flags([primitive("t"), principled("$t", "A"), principled("$t", "B"),
                                              assign("$t", "A")], "$t"), {"A": True, "B": False})
        self.assertEqual(self.rendered_flags([primitive("x"), primitive("y"), primitive("z"), primitive("t"),
                                              principled("$x", "A", also=["$t"]), principled("$y", "B", also=["$t"]),
                                              assign("$z", "A", also=["$t"])], "$t"), {"A": True, "B": False})
        # A name-only restatement gives its target every face when none uses the material (as also does), so A is
        # the most recent placement and must render: never certified from the slots while the object shows B.
        self.assertEqual(self.rendered_flags([primitive("t"), principled("$t", "A"), principled("$t", "B"),
                                              principled("$t", "A", inputs={})], "$t"), {"A": True, "B": False})


class InPlaceEditTests(unittest.TestCase):
    """An in-place edit of a held material moves no face (action_nodes): it is no placement (fresh break-it review)."""

    def test_an_in_place_edit_does_not_ask_for_rendered_faces_nor_supersede_a_placement(self):
        rendered_flags = SupersededTests.rendered_flags

        def primitive(alias):
            return {"kind": "create_primitive", "target": None,
                    "parameters": {"primitive": "CUBE", "name": alias, "alias": alias}}

        def principled(target, name, inputs, also=(), shared="REFUSE", faces=()):
            return {"kind": "material_principled", "target": target, "parameters": {
                "name": name, "inputs": inputs, "slot": -1, "face_indices": list(faces), "face_selector": None,
                "also": list(also), "shared": shared, "index_scope": "INITIAL", "topology_step": 0}}

        def assign(target, name):
            return {"kind": "material_assign", "target": target, "parameters": {
                "material": name, "source": None, "slot": -1, "face_indices": [], "copy_as": None, "also": [],
                "index_scope": "INITIAL", "topology_step": 0, "face_selector": None}}
        red, rough = {"Base Color": [1, 0, 0, 1]}, {"Roughness": 0.9}
        # Stone on pillar and beam, Iron on the beam, then Stone made rougher through the beam: the beam shows Iron.
        program = [primitive("p"), primitive("b"), principled("$p", "Stone", red, also=["$b"]),
                   principled("$b", "Iron", red), principled("$b", "Stone", rough, shared="ALL_USERS")]
        self.assertEqual(rendered_flags(program, "$b"), {"Stone": False, "Iron": True})
        # Given a face field, the edit does put faces on it.
        program[-1] = principled("$b", "Stone", rough, shared="ALL_USERS", faces=[0])
        self.assertEqual(rendered_flags(program, "$b"), {"Stone": True, "Iron": False})


if __name__ == "__main__":
    unittest.main()
