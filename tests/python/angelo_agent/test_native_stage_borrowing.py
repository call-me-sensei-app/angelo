# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live materials a material-blind program reads in place instead of copying; no bpy needed."""

import itertools
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import stage_borrowing as borrowing  # noqa: E402
from angelo_agent import program_dependencies as dependencies  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":1"
POINTERS = itertools.count(100)


class Block:
    id_type = "ID"

    def __init__(self, name, **values):
        self.name, self.pointer, self.session_uid = name, next(POINTERS), next(POINTERS)
        self.library = self.override_library = self.animation_data = None
        self.removed = False
        self.__dict__.update(values)

    def as_pointer(self):
        if self.removed:
            raise ReferenceError("removed")
        return self.pointer


class Material(Block):
    id_type = "MATERIAL"


class NodeTree(Block):
    id_type = "NODETREE"


class Image(Block):
    id_type = "IMAGE"


class Object(Block):
    id_type = "OBJECT"


BPY = SimpleNamespace(types=SimpleNamespace(Image=Image, NodeTree=NodeTree), data=SimpleNamespace(materials=[], node_groups=[]))


def op(kind, **parameters):
    return {"kind": kind, "target": HANDLE, "parameters": parameters}


def links_of(graph):
    return lambda block: [(block, None, target) for target in graph.get(block, ())]


class Staged:
    """A staged object whose data-linked slots are a list and whose object-linked slots carry a link mode."""

    def __init__(self, data_materials, object_slots=()):
        self.data = SimpleNamespace(materials=list(data_materials))
        self.material_slots = [SimpleNamespace(link=link, material=material) for link, material in object_slots]


class BlindKindsTests(unittest.TestCase):
    def test_only_transform_and_typed_modifier_kinds_are_material_blind(self):
        blind = [op("transform", location=[1, 2, 3]), op("modifier_configure", modifier="Bevel", type="BEVEL", settings={}),
                 op("modifier_add", name="Array", type="ARRAY", settings={}), op("modifier_remove", modifier="Bevel"),
                 op("modifier_move", modifier="Bevel", index=0)]
        writing = [op("modifier_add", name="GN", type="NODES", settings={}), op("modifier_configure", modifier="W", type="WAVE"),
                   op("material_principled", name="Red"), op("material_assign", material="Red"), op("node_edit"),
                   op("shader_nodes"), op("property_set", values={}), op("operator_run"), op("batch"),
                   op("object_duplicate"), op("modifier_apply", modifier="Bevel")]
        self.assertEqual([borrowing.material_blind(item) for item in blind], [True] * len(blind))
        self.assertEqual([borrowing.material_blind(item) for item in writing], [False] * len(writing))

    def test_a_program_borrows_only_when_every_operation_is_blind_and_no_material_is_bound(self):
        def binding(*operations, mode="APPLY"):
            return {"program": {"mode": mode, "operations": list(operations)}}
        with patch("angelo_agent.program_material_obligations.needs_material_binding", return_value=False):
            self.assertTrue(borrowing.program_borrows(binding(op("transform"), op("modifier_remove", modifier="B"))))
            self.assertFalse(borrowing.program_borrows(binding(op("transform"), op("material_principled"))))
            self.assertFalse(borrowing.program_borrows(binding(op("transform"), mode="PREVIEW")))  # Retained: private.
        with patch("angelo_agent.program_material_obligations.needs_material_binding", return_value=True):
            self.assertFalse(borrowing.program_borrows(binding(op("transform"))))


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(dependencies, "_bpy", return_value=BPY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def family(self, material, graph):
        return borrowing._family(material, links_of(graph))

    def test_a_local_unanimated_family_of_trees_and_images_is_borrowed_whole(self):
        material, tree, group, image = Material("M"), NodeTree("T"), NodeTree("G"), Image("I")
        rows, blocks, visits = self.family(material, {material: [tree], tree: [group, group, image], group: [image]})
        self.assertEqual([row[0] for row in rows], ["MATERIAL", "NODETREE", "NODETREE"])
        self.assertEqual(blocks, [material, tree, group])
        self.assertEqual(visits, 4, "A group used by two nodes is cloned twice: one visit per path bounds the clones")

    def test_animation_libraries_other_links_and_recursion_are_copied_instead(self):
        def fresh():
            material, tree, group = Material("M"), NodeTree("T"), NodeTree("G")
            return material, tree, group, {material: [tree], tree: [group]}
        cases = {}
        for name in ("material_animation", "tree_animation", "group_animation", "library", "override"):
            material, tree, group, graph = fresh()
            target = {"material": material, "tree": tree, "group": group}[name.split("_")[0]] if "_" in name else group
            if name.endswith("animation"):
                target.animation_data = object()
            else:
                setattr(target, name if name == "library" else "override_library", object())
            cases[name] = self.family(material, graph)
        material, tree, group, graph = fresh()
        graph[group] = [Object("Empty")]  # A Texture Coordinate object, a text or another material.
        cases["object_link"] = self.family(material, graph)
        material, tree, group, graph = fresh()
        graph[group] = [tree]
        cases["recursion"] = self.family(material, graph)
        chain = [Material("M")] + [NodeTree("G%d" % index) for index in range(borrowing.MAX_DEPTH + 2)]
        cases["depth"] = self.family(chain[0], {block: [chain[index + 1]] for index, block in enumerate(chain[:-1])})
        levels = [[NodeTree("L%d%d" % (level, side)) for side in range(2)] for level in range(12)]
        diamond = {block: levels[level + 1] for level, pair in enumerate(levels[:-1]) for block in pair}
        material = Material("M")
        diamond[material] = levels[0]
        cases["paths"] = self.family(material, diamond)  # 2^12 paths through 24 groups.
        self.assertEqual(cases, {name: None for name in cases})

    def test_a_group_two_trees_share_is_one_family_member(self):
        material, tree, shared = Material("M"), NodeTree("T"), NodeTree("S")
        other = NodeTree("O")
        rows, _blocks, _visits = self.family(material, {material: [tree], tree: [shared, other], other: [shared]})
        self.assertEqual(len(rows), 4)

    def test_nothing_is_borrowed_when_anything_the_stage_copies_is_animated(self):
        """An animated staged ID could write a borrowed material through a slot path when the stage evaluates."""
        material, tree = Material("M"), NodeTree("T")
        obj = Object("Cube", data=SimpleNamespace(materials=[material]), material_slots=[])
        animated = lambda **values: SimpleNamespace(**dict(dict(action=None, nla_tracks=[], drivers=[]), **values))
        graph = links_of({material: [tree]})
        for name, data in (("action", animated(action=object())), ("nla", animated(nla_tracks=[object()])),
                           ("drivers", animated(drivers=[object()]))):
            with self.subTest(animation=name):
                block = Block("Mesh", animation_data=data)
                with patch.object(dependencies, "_mutable_dependencies", return_value=[obj, block]):
                    self.assertIsNone(borrowing._plan({HANDLE: obj}, graph))
        with patch.object(dependencies, "_mutable_dependencies", return_value=[obj, Block("Mesh", animation_data=animated())]):
            plan = borrowing._plan({HANDLE: obj}, graph)  # Empty animation data animates nothing.
        self.assertEqual(list(plan.plan), [material.pointer])
        self.assertEqual(plan.plan[material.pointer][2], 2)


    def test_the_data_and_its_shape_keys_are_checked_even_where_the_closure_misses_them(self):
        """What copy_object copies by name: a walk that missed a data type still cannot make the stage borrow."""
        material = Material("M")
        animated = SimpleNamespace(action=object(), nla_tracks=[], drivers=[])
        for name in ("data", "shape_keys"):
            with self.subTest(animated=name):
                keys = Block("Key", animation_data=animated if name == "shape_keys" else None)
                data = SimpleNamespace(materials=[material], shape_keys=keys,
                                       animation_data=animated if name == "data" else None)
                obj = Object("Grease Pencil", data=data, material_slots=[])
                with patch.object(dependencies, "_mutable_dependencies", return_value=[obj]):
                    self.assertIsNone(borrowing._plan({HANDLE: obj}, links_of({})))
        obj = Object("Empty", data=None, material_slots=[])
        with patch.object(dependencies, "_mutable_dependencies", return_value=[obj]):
            self.assertEqual(borrowing._plan({HANDLE: obj}, links_of({})).plan, {})


class ResidencyTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(dependencies, "_bpy", return_value=BPY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.first, self.second = Material("First"), Material("Second")
        self.staged = Staged([self.first, None], [("DATA", None), ("OBJECT", self.second)])
        self.stage = SimpleNamespace(objects={HANDLE: self.staged}, extras={}, context=None,
                                     borrowing=borrowing.Borrowing())
        for material in (self.first, self.second):
            self.stage.borrowing.plan[material.as_pointer()] = (((material.id_type, material.session_uid,
                                                                  material.pointer),), [material], 3)
        borrowing.record(self.stage, HANDLE, "DATA", 0, self.first)
        borrowing.record(self.stage, HANDLE, "OBJECT", 1, self.second)
        self.links = {}  # The families' links as hydration's eligibility walk reads them.
        links = patch.object(dependencies, "_embedded_links", side_effect=lambda block: iter(
            [(block, None, target) for target in self.links.get(block, ())]))
        links.start()
        self.addCleanup(links.stop)

    def test_a_new_stage_owning_an_animated_copy_unborrows_instead_of_failing(self):
        """finish_stage asks before the first evaluation; afterwards assert_unanimated still refuses."""
        self.stage.owned_ids = [Block("Staged copy")]
        self.assertFalse(borrowing.owns_animated(self.stage))
        self.stage.owned_ids.append(Block("Staged data", animation_data=SimpleNamespace(
            action=None, nla_tracks=[object()], drivers=[])))
        self.assertTrue(borrowing.owns_animated(self.stage))
        self.stage.owned_ids[1].animation_data = SimpleNamespace(action=None, nla_tracks=[], drivers=[])
        self.assertFalse(borrowing.owns_animated(self.stage), "Empty animation data animates nothing")
        self.stage.owned_ids[1].animation_data.drivers = [object()]
        self.stage.extras = {}
        self.assertFalse(borrowing.owns_animated(self.stage), "A stage that borrows nothing has nothing to unborrow")

    def test_privatizing_every_borrowed_slot_is_bounded_before_staging_ends(self):
        self.assertEqual(borrowing.clone_bound(self.stage), 6)

    def test_privatizing_clones_each_live_material_once_per_object(self):
        """Every slot of an object holding one borrowed material gets the same clone, as copy_object makes it."""
        borrowing.record(self.stage, HANDLE, "DATA", 1, self.first)  # The first material in a second data slot,
        self.staged.data.materials[1] = self.first
        borrowing.record(self.stage, HANDLE, "OBJECT", 2, self.first)  # and in an object-linked slot.
        self.staged.material_slots.append(SimpleNamespace(link="OBJECT", material=self.first))
        self.assertEqual(borrowing.clone_bound(self.stage), 6, "The bound counts each live material once per object")
        made = []
        borrowing.privatize(self.stage, lambda material: made.append(Material("Copy of " + material.name)) or made[-1])
        self.assertEqual([item.name for item in made], ["Copy of First", "Copy of Second"])
        first, second = made
        self.assertIs(self.staged.data.materials[0], first)
        self.assertIs(self.staged.data.materials[1], first)
        self.assertEqual([slot.material for slot in self.staged.material_slots[1:]], [second, first])
        self.assertNotIn(borrowing.BORROWED, self.stage.extras)
        self.assertIsNone(self.stage.borrowing)

    def test_a_family_edited_while_the_job_waited_refuses_hydration(self):
        borrowing.release(self.stage)
        self.links[self.second] = [NodeTree("A group added while waiting")]
        with self.assertRaises(ProgramError) as caught:
            borrowing.restore(self.stage)
        self.assertEqual(caught.exception.code, "STALE_DEPENDENCY")
        self.links.clear()
        self.second.animation_data = object()
        with self.assertRaises(ProgramError) as caught:
            borrowing.restore(self.stage)
        self.assertEqual(caught.exception.code, "STALE_DEPENDENCY")
        self.assertEqual(self.staged.data.materials[0], None, "Nothing is put back before every family is proven")

    def test_release_clears_every_borrowed_slot_and_restore_puts_the_same_ids_back(self):
        self.assertEqual(borrowing.resident_pointers(self.stage), {self.first.pointer, self.second.pointer})
        borrowing.release(self.stage)
        self.assertEqual((self.staged.data.materials[0], self.staged.material_slots[1].material), (None, None))
        borrowing.restore(self.stage)
        self.assertEqual((self.staged.data.materials[0], self.staged.material_slots[1].material), (self.first, self.second))

    def test_a_slot_that_changed_while_resident_is_an_internal_error(self):
        self.staged.data.materials[0] = Material("Other")
        with self.assertRaises(ProgramError) as caught:
            borrowing.resident_pointers(self.stage)
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")
        self.staged.data.materials[0] = self.first
        self.staged.material_slots[1].link = "DATA"
        with self.assertRaises(ProgramError):
            borrowing.release(self.stage)

    def test_a_removed_or_replaced_live_material_refuses_hydration_as_stale(self):
        borrowing.release(self.stage)
        self.second.removed = True
        with self.assertRaises(ProgramError) as caught:
            borrowing.restore(self.stage)
        self.assertEqual(caught.exception.code, "STALE_DEPENDENCY")

    def test_a_bundle_hydration_finds_the_same_live_ids_by_pointer_and_session_uid(self):
        borrowing.release(self.stage)
        self.stage.borrowing = None  # A stage hydrated from a file bundle has no live wrappers yet.
        BPY.data.materials[:] = [self.second, self.first]
        self.addCleanup(BPY.data.materials.clear)
        borrowing.restore(self.stage)
        self.assertEqual(self.staged.data.materials[0], self.first)
        self.stage.borrowing, self.staged.data.materials[0] = None, None
        self.staged.material_slots[1].material = None
        BPY.data.materials[:] = [Material("Second")]  # A new ID under the same name is not the borrowed one.
        with self.assertRaises(ProgramError) as caught:
            borrowing.restore(self.stage)
        self.assertEqual(caught.exception.code, "STALE_DEPENDENCY")

    def test_a_suspended_slot_that_kept_a_live_reference_is_an_internal_error(self):
        with self.assertRaises(ProgramError) as caught:
            borrowing.restore(self.stage)
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")

    def test_evaluation_refuses_once_a_borrowed_block_is_animated_or_removed(self):
        context = SimpleNamespace(borrowing=self.stage.borrowing, extras=self.stage.extras)
        borrowing.assert_unanimated(context)
        self.first.animation_data = object()
        with self.assertRaises(ProgramError) as caught:
            borrowing.assert_unanimated(context)
        self.assertEqual(caught.exception.code, "STALE_DEPENDENCY")
        with self.assertRaises(ProgramError) as caught:
            borrowing.assert_unanimated(SimpleNamespace(borrowing=None, extras=self.stage.extras))
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")
        borrowing.assert_unanimated(SimpleNamespace())  # Preflight stand-ins and unborrowing stages.
        self.first.animation_data = None
        staged = Block("Staged copy", animation_data=SimpleNamespace(action=object(), nla_tracks=[], drivers=[]))
        with self.assertRaises(ProgramError) as caught:
            borrowing.assert_unanimated(SimpleNamespace(borrowing=self.stage.borrowing, extras=self.stage.extras,
                                                        owned_ids={staged}))
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")


if __name__ == "__main__":
    unittest.main()
