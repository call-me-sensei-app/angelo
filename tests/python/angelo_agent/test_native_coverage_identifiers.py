# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short identifiers and inferred bindings reach every native-coverage reference position.

Dependency-free. Each object reference of the native-coverage kinds and
checks is typed with the shared HANDLE or check TARGET type, so
``identifiers.expand_call`` resolves it, and the positions it resolves are
exactly what ``affected_references``/``check_references`` bind (and so what
inferred bindings declare).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.action_catalog import affected_references  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.validation_spec import check_references  # noqa: E402

EPOCH = "a" * 32
OBJ, OTHER, SCENE = "scn:%s:11" % EPOCH, "scn:%s:12" % EPOCH, "scn:%s:13" % EPOCH
REV, OTHER_REV, SCENE_REV = "1" * 64, "2" * 64, "3" * 64


def table():
    result = IdentifierTable()
    observation = {"objects": [{"handle": OBJ, "object_revision": REV}, {"handle": OTHER, "object_revision": OTHER_REV}]}
    result.compact(observation)
    result.observe(observation)
    # angelo_scene_settings shows the scene as a handle/revision pair.
    result.compact({"handle": SCENE, "revision": SCENE_REV, "values": {"camera": OTHER}})
    return result


OPERATIONS = [
    {"kind": "property_set", "target": "o3", "parameters": {"values": {"camera": "o2", "render.fps": 30}}},
    {"kind": "world_configure", "target": "o3", "parameters": {"strength": 2}},
    {"kind": "property_set", "target": "o1", "parameters": {"values": {"hide_render": True,
                                                                     "data.dof.focus_object": {"object": "o2"},
                                                                     "active_material.blend_method": "o99"}}},
    {"kind": "modifier_add", "target": "o1", "parameters": {"type": "SHRINKWRAP", "settings": {"target": "o2"}}},
    {"kind": "modifier_add", "target": "o1", "parameters": {"type": "CURVE", "settings": {"object": "o2"}}},
    {"kind": "constraint_add", "target": "o1", "parameters": {"type": "CHILD_OF", "target": "o2"}},
    {"kind": "object_duplicate", "target": None, "parameters": {"source": "o2", "alias": "twin", "name": "o2"}},
    {"kind": "collection_link", "target": "o1", "parameters": {"collection": "o2"}},
    {"kind": "keyframe_delete", "target": "o1", "parameters": {"property": "location", "frame": 3}},
    {"kind": "modifier_remove", "target": "o1", "parameters": {"modifier": "o2"}},
]
CHECKS = [
    {"kind": "property", "target": "o3", "values": {"camera": "o2", "frame_end": 9}},
    {"kind": "world_settings", "target": "o3", "values": {"strength": 2}},
    {"kind": "modifier_stack", "target": "o1", "absent": ["o2"]},
    {"kind": "object_exists", "target": "$twin", "expected": True},
    {"kind": "collection_membership", "target": "o1", "collections": ["o2"]},
    {"kind": "keyframes", "target": "o1", "property": "location", "absent": [3]},
    {"kind": "shape_key", "target": "o1", "name": "o2", "exists": False},
    {"kind": "nla", "target": "o1", "minimum_tracks": 0},
]


class CoverageIdentifierTests(unittest.TestCase):
    def test_every_reference_resolves_and_names_stay_names(self):
        program = {"mode": "APPLY", "write_set": [{"handle": "o3", "revision": SCENE_REV[:8]}],
                   "operations": OPERATIONS, "expected_checks": CHECKS}
        resolved = expand_call("angelo_scene_program", program, table())
        operations, checks = resolved["operations"], resolved["expected_checks"]
        self.assertEqual(resolved["write_set"][0], {"handle": SCENE, "revision": SCENE_REV})
        self.assertEqual(operations[0]["target"], SCENE)
        self.assertEqual(operations[0]["parameters"]["values"]["camera"], OTHER)
        self.assertEqual(operations[2]["parameters"]["values"]["data.dof.focus_object"], {"object": OTHER})
        self.assertEqual(operations[2]["parameters"]["values"]["active_material.blend_method"], "o99",
                         "Text in an open settings map (o99 was never shown) is not a handle")
        self.assertEqual(operations[3]["parameters"]["settings"]["target"], OTHER)
        self.assertEqual(operations[4]["parameters"]["settings"]["object"], OTHER)
        self.assertEqual(operations[5]["parameters"]["target"], OTHER)
        self.assertEqual(operations[6]["parameters"]["source"], OTHER)
        self.assertEqual(operations[6]["parameters"]["name"], "o2", "An object name is not a handle")
        self.assertEqual(operations[7]["parameters"]["collection"], "o2", "A collection name is not a handle")
        self.assertEqual(operations[9]["parameters"]["modifier"], "o2", "A modifier name is not a handle")
        self.assertEqual(checks[0], {"kind": "property", "target": SCENE, "values": {"camera": OTHER, "frame_end": 9}})
        self.assertEqual([check["target"] for check in checks[1:]], [SCENE, OBJ, "$twin", OBJ, OBJ, OBJ, OBJ])
        self.assertEqual(checks[4]["collections"], ["o2"])
        self.assertEqual(checks[6]["name"], "o2")

    def test_a_handle_written_alone_in_an_open_map_is_an_object(self):
        """open_values: a shown short handle, a long handle or an earlier $alias, never a name or other text."""
        values = {"data.dof.focus_object": "o2", "active_material.blend_method": "OPAQUE", "data.body": "Cube"}
        program = {"mode": "APPLY", "operations": [
            {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "alias": "aim"}},
            {"kind": "property_set", "target": "o1", "parameters": {"values": values}},
            {"kind": "property_set", "target": "o1", "parameters": {"values": {"data.dof.focus_object": "$aim"}}},
            {"kind": "constraint_add", "target": "o1", "parameters": {"type": "FOLLOW_PATH", "settings": {
                "target": OTHER, "offset": 1}}}]}
        canonical = validate_program("angelo_scene_program", expand_call("angelo_scene_program", program, table()))
        operations = canonical["operations"]
        self.assertEqual(operations[1]["parameters"]["values"], {"data.dof.focus_object": {"object": OTHER},
                                                                 "active_material.blend_method": "OPAQUE",
                                                                 "data.body": "Cube"})
        self.assertEqual(operations[2]["parameters"]["values"], {"data.dof.focus_object": {"object": "$aim"}})
        self.assertEqual(operations[3]["parameters"]["settings"]["target"], {"object": OTHER})
        self.assertEqual(canonical["read_set"], [{"handle": OTHER, "revision": OTHER_REV}], "A declared dependency")
        text = {"mode": "APPLY", "write_set": [{"handle": OBJ, "revision": REV}], "operations": [
            {"kind": "property_set", "target": OBJ, "parameters": {"values": {"data.body": "$aim"}}}]}
        self.assertEqual(validate_program("angelo_scene_program", text)["operations"][0]["parameters"]["values"],
                         {"data.body": "$aim"}, "No program alias named so: it is text")

    def test_inferred_bindings_cover_object_references_and_the_scene_is_declared(self):
        program = {"mode": "APPLY", "write_set": [{"handle": "o3", "revision": SCENE_REV[:8]}],
                   "operations": OPERATIONS, "expected_checks": CHECKS}
        resolved = expand_call("angelo_scene_program", program, table())
        self.assertEqual({entry["handle"] for entry in resolved["write_set"]}, {SCENE, OBJ})
        self.assertEqual(resolved["read_set"], [{"handle": OTHER, "revision": OTHER_REV}])
        validated = validate_program("angelo_scene_program", resolved, derive_checks=True)
        self.assertEqual(len(validated["operations"]), len(OPERATIONS))
        undeclared = {"mode": "APPLY", "operations": OPERATIONS[:1]}
        with self.assertRaisesRegex(IdentifierError, "no revision in your latest observation"):
            expand_call("angelo_scene_program", undeclared, table())

    def test_resolved_positions_are_the_bound_references(self):
        resolved = expand_call("angelo_scene_program", {"mode": "APPLY", "write_set": [
            {"handle": "o3", "revision": SCENE_REV[:8]}], "operations": OPERATIONS, "expected_checks": CHECKS}, table())
        for operation in resolved["operations"]:
            for reference in affected_references(operation):
                self.assertTrue(reference.startswith(("scn:", "$")), (operation["kind"], reference))
        references = {reference for check in resolved["expected_checks"] for reference in check_references(check)}
        self.assertEqual(references, {SCENE, OBJ, OTHER, "$twin"})


    def test_the_scene_camera_takes_either_pointer_form(self):
        """Regression: in a camera one-shot {"object": ...} was refused by Claude Code's schema check, each
        refusal costing a round of written tokens, though every other pointer uses it."""
        bare = {"mode": "APPLY", "write_set": [{"handle": "o3", "revision": SCENE_REV[:8]}],
                "operations": [OPERATIONS[0]]}
        wrapped = {"mode": "APPLY", "write_set": [{"handle": "o3", "revision": SCENE_REV[:8]}], "operations": [
            {"kind": "property_set", "target": "o3", "parameters": {"values": {"camera": {"object": "o2"},
                                                                              "render.fps": 30}}}]}
        resolved = expand_call("angelo_scene_program", wrapped, table())
        self.assertEqual(resolved["operations"][0]["parameters"]["values"]["camera"], {"object": OTHER})
        self.assertEqual(resolved["read_set"], [{"handle": OTHER, "revision": OTHER_REV}])
        canonical = validate_program("angelo_scene_program", resolved)
        self.assertEqual(canonical["operations"][0]["parameters"]["values"]["camera"], OTHER, "One stored form")
        self.assertEqual(canonical, validate_program("angelo_scene_program", expand_call(
            "angelo_scene_program", bare, table())), "The same program either way")
        created = validate_program("angelo_scene_program", {"mode": "APPLY", "operations": [
            {"kind": "camera_create", "target": None, "parameters": {"alias": "cam", "name": "Shot"}},
            {"kind": "property_set", "target": SCENE, "parameters": {"values": {"camera": {"object": "$cam"}}}}],
            "write_set": [{"handle": SCENE, "revision": SCENE_REV}]})
        self.assertEqual(created["operations"][1]["parameters"]["values"]["camera"], "$cam")
        with self.assertRaises(ValueError):  # Not a pointer, and not anything else.
            validate_program("angelo_scene_program", {"mode": "APPLY", "write_set": [
                {"handle": SCENE, "revision": SCENE_REV}], "operations": [{"kind": "property_set", "target": SCENE,
                "parameters": {"values": {"camera": {"object": OTHER, "extra": 1}}}}]})


if __name__ == "__main__":
    unittest.main()
