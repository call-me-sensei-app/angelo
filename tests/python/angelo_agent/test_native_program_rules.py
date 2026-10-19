# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cross-operation refusals before approval (``program_rules``): linked data and the active material.

Dependency-free. Regressions: linked duplicates edited later were
committed unlinked; slot hops through an operator or a material assignment were
refused only at staging, naming a staged private copy; a material write before
a slot removal was listed as a change and lost; and a keyed slot index was
refused only after approval.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.rna_rules import PropertyPathError  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
OTHER = "scn:" + "a" * 32 + ":9"
REV = "b" * 64


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(operations):
    return validate_program("angelo_scene_program", {"mode": "APPLY", "operations": operations,
                                                      "write_set": [{"handle": h, "revision": REV} for h in (OBJ, OTHER)]})


DUP = op("object_duplicate", None, source=OBJ, alias="d", linked=True)
EXTRUDE = lambda target: op("mesh_extrude", target, face_selector={"normal": [0, 0, 1]}, translation=[0, 0, 1.0])
BATCH = lambda source, linked=True: op("batch", None, sources=[source], linked=linked, array=[{"step": [3, 0, 0], "count": 3}])
ROUGH = op("property_set", values={"active_material.roughness": .9})


class LinkedDataTests(unittest.TestCase):
    def test_a_later_data_edit_of_linked_data_is_refused(self):
        for operations in ([DUP, EXTRUDE("$d")], [DUP, EXTRUDE(OBJ)],
                           [DUP, op("material_principled", "$d", name="Dup Paint")],
                           [DUP, op("object_duplicate", None, source="$d", alias="e", linked=True), EXTRUDE("$e")],
                           [DUP, BATCH("$d"), EXTRUDE("$d_2")], [BATCH(OBJ), EXTRUDE(OBJ)],
                           [DUP, op("object_join", OBJ, sources=["$d"])], [DUP, op("object_join", "$d", sources=[OTHER])],
                           [DUP, op("operator_run", "$d", operator="transform.translate", properties={"value": [0, 0, 1]})],
                           [DUP, op("material_principled", OTHER, name="P", also=["$d"])],
                           [DUP, op("material_principled", OTHER, name="P", also=[OBJ])]):
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                with self.assertRaisesRegex(ValueError, "LINKED_EDIT") as raised:
                    program(operations)
                self.assertNotIn("scn:", str(raised.exception), "Never a raw handle")

    def test_the_same_material_on_every_member_is_allowed(self):
        """Members made here without slots, every one given the material on all its faces (a request that was
        refused before); the shared data then holds what each was given."""
        unit = op("create_primitive", None, primitive="CUBE", name="Unit", alias="unit")
        dup = op("object_duplicate", None, source="$unit", alias="d", linked=True)
        paint = lambda target, **fields: op("material_principled", target, **dict({"name": "Stone"}, **fields))
        assign = lambda target, **fields: op("material_assign", target, **dict({"material": "Stone"}, **fields))
        for operations in ([unit, dup, paint(OTHER, also=["$unit", "$d"])], [unit, dup, paint("$unit"), assign("$d")],
                           [unit, dup, paint(OTHER, also=["$unit"]), assign("$d", source=OTHER)],
                           [unit, BATCH("$unit"), paint("$unit", also=["$unit_2", "$unit_3"])],
                           [unit, dup, paint("$d", also=["$unit"]), assign("$unit", slot=0)]):
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                program(operations)
        for operations in ([DUP, paint(OTHER, also=[OBJ, "$d"])],  # A declared object may hold slots already.
                           [unit, paint("$unit", name="Old"), dup, paint("$unit", also=["$d"])],  # Slots before.
                           [unit, dup, paint("$unit"), assign("$d", material="Brick")],  # Different materials.
                           [unit, dup, paint("$unit"), paint("$d", face_indices=[0])],  # Only some faces of one.
                           [unit, dup, paint("$unit", slot=2), paint("$d")],  # A slot past the end.
                           [unit, dup, assign("$unit", source=OTHER, copy_as="Mine"),  # Two copies of a copy.
                            assign("$d", source=OTHER, copy_as="Mine")],
                           [unit, BATCH("$unit"), paint("$unit", also=["$unit_2"])],  # A copy left out.
                           [unit, dup, paint("$unit", also=["$d"]), EXTRUDE("$d")]):  # Another data edit.
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                with self.assertRaisesRegex(ValueError, "LINKED_EDIT"):
                    program(operations)

    def test_a_linked_group_material_stated_twice_with_other_values_is_refused_before_approval(self):
        """Its members hold no material yet, so staging would refuse the second statement (MATERIAL_REDEFINED)."""
        unit = op("create_primitive", None, primitive="CUBE", name="Unit", alias="unit")
        dup = op("object_duplicate", None, source="$unit", alias="d", linked=True)
        first = op("material_principled", "$unit", name="Stone", inputs={"Roughness": .5})
        program([unit, dup, first, op("material_principled", "$d", name="Stone", inputs={"Roughness": .5})])
        program([unit, dup, first, op("material_principled", "$d", name="Stone")])  # Its name alone: reused.
        with self.assertRaisesRegex(ValueError, "MATERIAL_REDEFINED"):
            program([unit, dup, first, op("material_principled", "$d", name="Stone", inputs={"Roughness": .9})])
        # Outside a linked group a target that holds the material edits it in place (action_nodes): admitted.
        program([op("material_principled", OBJ, name="Stone", inputs={"Roughness": .5}),
                 op("material_principled", OBJ, name="Stone", inputs={"Roughness": .9})])

    def test_object_settings_and_earlier_edits_stay_allowed(self):
        for operations in ([EXTRUDE(OBJ), DUP], [DUP, op("transform", "$d", mode="SET", location=[0, 4, 0])],
                           [DUP, op("modifier_add", "$d", type="SUBSURF", name="S")], [DUP, BATCH("$d")],
                           [op("object_duplicate", None, source=OBJ, alias="d", linked=False), EXTRUDE("$d")],
                           [BATCH(OBJ, linked=False), EXTRUDE("$copy1_0_2")]):
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                program(operations)


class ActiveMaterialTests(unittest.TestCase):
    def assert_conflict(self, operations):
        with self.assertRaises(PropertyPathError) as raised:
            program(operations)
        self.assertEqual((raised.exception.code, raised.exception.refusal_code), ("ACTIVE_SLOT_CONFLICT", "VALIDATION"))
        self.assertIn("separate program", str(raised.exception))

    def test_slot_changes_beside_a_material_write_are_refused(self):
        for operations in ([ROUGH, op("operator_run", operator="object.material_slot_remove")],
                           [op("operator_run", operator="object.material_slot_remove"), ROUGH],
                           [op("operator_run", operator="object.material_slot_add"), ROUGH],
                           [op("material_assign", material="Paint B", slot=0), ROUGH],
                           [ROUGH, op("material_assign", material="Paint B", copy_as="Paint C", slot=0)],
                           [ROUGH, op("material_principled", name="New Paint")],
                           [op("property_set", values={"active_material_index": 1}), ROUGH],
                           [ROUGH, op("material_principled", OTHER, name="Shared", also=[OBJ])]):
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                self.assert_conflict(operations)

    def test_a_local_copy_before_the_write_and_slot_keepers_are_allowed(self):
        for operations in ([op("material_assign", material="LibMat", copy_as="LibMat Local", slot=0), ROUGH],
                           [op("material_principled", name="New Paint"), ROUGH],
                           [ROUGH, op("operator_run", operator="object.material_slot_assign")],
                           [ROUGH, op("operator_run", OTHER, operator="object.material_slot_remove")],
                           [op("operator_run", operator="object.material_slot_move"), ROUGH]):
            with self.subTest(kinds=[operation["kind"] for operation in operations]):
                program(operations)

    def test_a_keyed_slot_index_is_refused_at_validation(self):
        with self.assertRaisesRegex(PropertyPathError, "NOT_ANIMATABLE"):
            program([op("property_keyframe", values={"active_material_index": 1}, frame=3)])


if __name__ == "__main__":
    unittest.main()
