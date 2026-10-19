# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Facts where typed programs end, so a refusal or a result names the way on.

Recorded GPT trials met three ends and lost rounds at each:

* a typed node key's closed list ("MATH ... unsupported value"), read as no GREATER_THAN at all, although
  ``ShaderNodeMath`` by its bl_idname took it at once: a refused value now lists what the key allows and names the
  node's own type;
* the input mesh limit: a bare "exceeds bounded native program limits" or "has no revision", after which the model
  cut its own grass mesh to a guessed size: the refusal and the turn context now give the counts, the limits and
  the script route;
* leftover data: the result said a deleted object's data "needs no cleanup script", true for the mesh a save drops
  but not for the material only that mesh uses, which a save still writes: that data is named apart, with the one
  call that removes both (typed programs have no purge).
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import action_node_types  # noqa: E402,F401  (fills the typed node alternatives)
from angelo_agent.action_schema_types import (MAX_ALLOWED_LISTED, MAX_EDGES, MAX_FACES, MAX_VERTICES,  # noqa: E402
                                              allowed, mesh_over_limits, typed_node_alternative)
from angelo_agent.program_commit_report import HELD_NOTE, LEFTOVER_NOTE, ProgramCommitReportMixin, held_only_by  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":5"


def shader_program(node):
    return {"mode": "APPLY", "read_set": [], "write_set": [{"handle": HANDLE, "revision": "f" * 64}],
            "expected_checks": [], "operations": [{"kind": "shader_nodes", "target": HANDLE, "parameters": {
                "name": "M", "nodes": [node, {"id": "o", "type": "OUTPUT_MATERIAL"}],
                "links": [{"from_node": "t", "from_socket": "Value", "to_node": "o", "to_socket": "Surface"}]}}]}


class TypedNodeKeys(unittest.TestCase):
    def test_a_refused_typed_value_lists_the_keys_values_and_names_blenders_node(self):
        with self.assertRaises(ValueError) as caught:
            validate_program("angelo_scene_program", shader_program(
                {"id": "t", "type": "MATH", "settings": {"operation": "GREATER_THAN"}}))
        message = str(caught.exception)
        self.assertIn("unsupported value 'GREATER_THAN'; allowed: ADD, SUBTRACT, MULTIPLY, DIVIDE, MINIMUM, MAXIMUM",
                      message)
        self.assertIn("type ShaderNodeMath takes every setting and input Blender has", message)

    def test_blenders_node_by_its_bl_idname_takes_the_value(self):
        validate_program("angelo_scene_program", shader_program(
            {"id": "t", "type": "ShaderNodeMath", "settings": {"operation": "GREATER_THAN"}}))

    def test_the_route_is_offered_only_for_a_node_settings_or_input_error(self):
        self.assertIn("GeometryNodeExtrudeMesh", typed_node_alternative(
            {"id": "e", "type": "EXTRUDE_MESH"}, "nodes[1].settings.mode has an unsupported value"))
        self.assertEqual(typed_node_alternative({"id": "e", "type": "MATH"}, "nodes[1].type is wrong"), "")
        self.assertEqual(typed_node_alternative({"kind": "MATH"}, "x.settings.y"), "", "not a node")
        self.assertEqual(typed_node_alternative({"id": "e", "type": "BEVEL"}, "x.settings.y"), "", "not a node key")

    def test_a_long_enum_is_listed_in_part(self):
        values = ["V%d" % index for index in range(MAX_ALLOWED_LISTED + 5)]
        self.assertEqual(allowed(values), "allowed: " + ", ".join(values[:MAX_ALLOWED_LISTED]) + " and 5 more")


class MeshLimits(unittest.TestCase):
    def mesh(self, vertices, edges=0, faces=0):
        return SimpleNamespace(vertices=[None] * vertices, edges=[None] * edges, polygons=[None] * faces)

    def test_each_count_over_its_limit_is_named_with_the_limit(self):
        limits = (MAX_VERTICES, MAX_EDGES, MAX_FACES)
        self.assertIsNone(mesh_over_limits(self.mesh(MAX_VERTICES, MAX_EDGES, MAX_FACES), limits))
        self.assertEqual(mesh_over_limits(self.mesh(4, 9, 3), (3, 8, 3)), "4 vertices; at most 3, 9 edges; at most 8")

    def test_the_bind_refusal_names_the_object_its_counts_and_the_script_route(self):
        from angelo_agent.program_admission import admit_input
        from angelo_agent.program_jobs import ProgramError
        obj = SimpleNamespace(mode="OBJECT", library=None, is_editable=True, instance_collection=None, type="MESH",
                              name="Meadow Grass Blades", data=self.mesh(5))
        with self.assertRaises(ProgramError) as caught:
            admit_input(obj, (4, 10, 10))
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        # The limits are typed programs' only and the mesh may stay as it is (models otherwise cut their own grass
        # mesh to fit them).
        self.assertEqual(str(caught.exception), "'Meadow Grass Blades' is over the typed programs' mesh limits "
                         "(5 vertices; at most 4); only typed programs have these limits: the mesh can stay as it is, "
                         "and angelo_execute_python edits a mesh of any size, its material included")


class Block(SimpleNamespace):
    def __init__(self, name, users, materials=(), fake=False, library=None, pointer=None):
        super().__init__(name=name, users=users, materials=list(materials), use_fake_user=fake, library=library,
                         is_embedded_data=False)
        self._pointer = pointer if pointer is not None else id(self)

    def as_pointer(self):
        return self._pointer


class Leftovers(unittest.TestCase):
    def test_a_material_only_unused_data_holds_is_named_apart_with_the_purge_call(self):
        material = Block("Material", 1)
        shared = Block("Shared", 2)  # Also on a mesh the save keeps.
        mesh = Block("Cube", 0, [material, shared])
        report = ProgramCommitReportMixin._leftovers([], [mesh, Block("Light", 0)])["orphan_data"]
        self.assertEqual((report["count"], report["names"]), (2, ["Cube", "Light"]))
        self.assertEqual(report["held_only_by_them"], {"count": 1, "names": ["Material"]})
        self.assertEqual(report["note"], LEFTOVER_NOTE + HELD_NOTE)
        self.assertIn("orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)", report["note"])

    def test_a_block_both_replaced_and_released_counts_once(self):
        """Found natively: a deleted object's data came back in both lists, so its material's one user counted twice."""
        material = Block("Material", 1)
        mesh = Block("Cube", 0, [material])
        report = ProgramCommitReportMixin._leftovers([(mesh, None)], [mesh])["orphan_data"]
        self.assertEqual(report["held_only_by_them"], {"count": 1, "names": ["Material"]})

    def test_two_unused_meshes_sharing_a_material_hold_it_alone(self):
        material = Block("Stone", 2)
        self.assertEqual(held_only_by([Block("A", 0, [material]), Block("B", 0, [material])]), ["Stone"])
        self.assertEqual(held_only_by([Block("A", 0, [material])]), [], "a user outside the leftovers keeps it")

    def test_fake_user_linked_and_no_material_data_is_never_named(self):
        cases = [Block("Kept", 1, fake=True), Block("Linked", 1, library=object())]
        self.assertEqual(held_only_by([Block("M", 0, cases), Block("Empty data", 0)]), [])
        report = ProgramCommitReportMixin._leftovers([], [Block("Cube", 0)])["orphan_data"]
        self.assertNotIn("held_only_by_them", report)
        self.assertEqual(report["note"], LEFTOVER_NOTE)
        self.assertNotIn("no cleanup", report["note"], "the earlier claim is gone")


if __name__ == "__main__":
    unittest.main()
