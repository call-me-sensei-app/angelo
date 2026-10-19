# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private names of staged copies and the public names commit gives back (``stage_names``), without Blender.

test_blender_stage_id_hazards.py proves the same on real data: a name only a staged copy held is free, a new
datablock takes the number Blender gives, and no private name reaches the file.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import stage_names  # noqa: E402


class Block:
    def __init__(self, name, id_type="MATERIAL", slots=(), hint=None):
        self.name, self.id_type, self.slots = name, id_type, list(slots)
        self.animation_data = SimpleNamespace(last_slot_identifier=hint) if hint is not None else None


class Slot:
    def __init__(self, code, name):
        self.code, self.name_display = code, name

    @property
    def identifier(self):
        return self.code + self.name_display


class StageNames(unittest.TestCase):
    def setUp(self):
        self.extras = {stage_names.STAGE: "ab12cd34"}

    def test_a_copy_takes_a_private_name_and_records_the_public_one(self):
        copy = stage_names.hide(self.extras, Block("Wood.001"), "Wood")
        self.assertEqual(copy.name, "__angelo_clone_ab12cd34_Wood")
        self.assertEqual(stage_names.public(self.extras, copy.name), "Wood")
        self.assertEqual(stage_names.public(self.extras, copy.name + ".001"), "Wood")  # A kernel's copy of the copy.
        self.assertEqual(stage_names.public({}, copy.name + ".002"), "Wood.002")  # Without the record: the stage id goes.
        self.assertEqual(stage_names.public(self.extras, "Wood.001"), "Wood.001")  # An artist's name is never touched.

    def test_results_and_slot_identifiers_show_public_names(self):
        stage_names.hide(self.extras, Block("Camera.001"), "Camera")
        value = {"data": "__angelo_clone_ab12cd34_Camera", "slot": "CA__angelo_clone_ab12cd34_Camera", "n": 3,
                 "rows": [{"material": "Stone"}]}
        self.assertTrue(stage_names.mentions(value))
        self.assertEqual(stage_names.shown(self.extras, value),
                         {"data": "Camera", "slot": "CACamera", "n": 3, "rows": [{"material": "Stone"}]})
        self.assertFalse(stage_names.mentions({"name": "Camera.001"}))

    def test_publish_renames_kept_copies_slots_and_hints_and_journals_each_rename(self):
        stage_names.hide(self.extras, Block("Camera.001", "CAMERA"), "Camera")
        data = Block("__angelo_clone_ab12cd34_Camera", "CAMERA", hint="CA__angelo_clone_ab12cd34_Camera")
        action = Block("__angelo_clone_ab12cd34_Camera", "ACTION", slots=[Slot("CA", "__angelo_clone_ab12cd34_Camera")])
        plain = Block("Lens Action", "ACTION", slots=[Slot("CA", "Camera")])
        journal = []
        stage_names.publish(self.extras, [data, action, plain], journal)
        self.assertEqual((data.name, data.animation_data.last_slot_identifier), ("Camera", "CACamera"))
        self.assertEqual((action.name, action.slots[0].identifier), ("Camera", "CACamera"))
        self.assertEqual((plain.name, plain.slots[0].identifier), ("Lens Action", "CACamera"))
        self.assertEqual([(attribute, value) for _holder, attribute, value in journal],
                         [("name", "__angelo_clone_ab12cd34_Camera"),
                          ("last_slot_identifier", "CA__angelo_clone_ab12cd34_Camera"),
                          ("name", "__angelo_clone_ab12cd34_Camera"),
                          ("name_display", "__angelo_clone_ab12cd34_Camera")])


class Taken(Block):
    """A block whose wanted name another ID took meanwhile: Blender gives it the next free suffix."""

    def __init__(self, name, gives):
        self.gives = gives
        super().__init__(name)

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = self.gives.get(value, value)


class ReservedNames(unittest.TestCase):
    """Regression: a copy commit keeps beside its original (an isolated material, a shared Action's copy, a
    World other users keep) was reported under its original's name; it commits as "<name>.001"."""

    def setUp(self):
        self.extras = {stage_names.STAGE: "ab12cd34"}

    def test_a_kept_copy_reserves_the_exact_name_commit_gives_it(self):
        live = {"Stone", "Stone.001"}
        first = stage_names.hide(self.extras, Block("Stone.002"), "Stone")
        second = Block(first.name + ".001")  # Blender's name for a second private copy of the same material.
        self.assertEqual(stage_names.reserve(self.extras, first, live.__contains__), "Stone.002")
        self.assertEqual(stage_names.reserve(self.extras, first, live.__contains__), "Stone.002")  # Stable.
        self.assertEqual(stage_names.reserve(self.extras, second, live.__contains__), "Stone.003")  # Not reserved twice.
        free = stage_names.hide(self.extras, Block("Brick.001"), "Brick")
        self.assertEqual(stage_names.reserve(self.extras, free, live.__contains__), "Brick")  # Its public name if free.
        self.assertEqual(stage_names.reserve(self.extras, Block("Wood"), live.__contains__), "Wood")  # Not private.
        long = "Castle keep outer wall weathered stone A big"
        copy = stage_names.hide(self.extras, Block(long + ".001"), long)
        self.assertEqual(stage_names.reserve(self.extras, copy, {long}.__contains__), long + ".001")

    def test_publish_gives_reserved_names_first_and_records_a_name_taken_meanwhile(self):
        reserved = stage_names.hide(self.extras, Taken("Stone.001", {}), "Stone")
        stage_names.reserve(self.extras, reserved, {"Stone"}.__contains__)
        other = stage_names.hide(self.extras, Block("Wood.001"), "Wood")
        taken = stage_names.hide(self.extras, Taken("Brick.001", {"Brick.001": "Brick.002"}), "Brick")
        stage_names.reserve(self.extras, taken, {"Brick"}.__contains__)
        journal = []
        stage_names.publish(self.extras, [other, taken, reserved], journal)
        self.assertEqual([block.name for block in (reserved, taken, other)], ["Stone.001", "Brick.002", "Wood"])
        self.assertEqual(journal[0][2], "__angelo_clone_ab12cd34_Brick")  # Reserved blocks were renamed first.
        self.assertEqual(self.extras[stage_names.CHANGED], {"Brick.001": "Brick.002"})

    def test_a_committed_result_is_corrected_to_the_names_commit_gave(self):
        result = {"status": "committed", "orphan_data": {"names": ["Brick.001"]},
                  "operation_results": [{"kind": "node_edit", "tree": {"name": "Brick.001"}},
                                        {"kind": "world_configure", "world": {"name": "World.001"}},
                                        {"kind": "material_assign", "material": "World.001"}],
                  "delta": {"details": [{"changes": {"world": {"before": {"name": "World"},
                                                               "after": {"name": "World.001"}}}}]},
                  "checks": [{"observed": {"material": "Brick.001"}}],
                  stage_names.FIXES: {"names": {"Brick.001": "Brick.002"}, "world": ["World.001", "World"]}}
        corrected = stage_names.correct(result)
        self.assertNotIn(stage_names.FIXES, corrected)
        self.assertEqual(corrected["operation_results"][0]["tree"]["name"], "Brick.002")
        self.assertEqual(corrected["checks"][0]["observed"]["material"], "Brick.002")
        self.assertEqual(corrected["operation_results"][1]["world"]["name"], "World")
        self.assertEqual(corrected["operation_results"][2]["material"], "World.001")  # Only World rows.
        self.assertEqual(corrected["delta"]["details"][0]["changes"]["world"],
                         {"before": {"name": "World"}, "after": {"name": "World"}})
        self.assertEqual(corrected["orphan_data"], {"names": ["Brick.001"]})  # The orphan that took the name.
        self.assertEqual(stage_names.correct({"status": "committed"}), {"status": "committed"})


if __name__ == "__main__":
    unittest.main()
