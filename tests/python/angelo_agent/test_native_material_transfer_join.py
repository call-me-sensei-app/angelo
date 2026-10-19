# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Copied-material group restoration reaches native transfer observations; no Blender."""

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import material_sharing, node_edit_commit, program_identities
from angelo_agent.program_jobs import ProgramError


class Block(dict):
    def __init__(self, name, **markers):
        super().__init__(markers)
        self.name = name
        self.session_uid = id(self)
        self.nodes = []

    def as_pointer(self):
        return id(self)


class Material(Block):
    pass


class MaterialTransferJoinTests(unittest.TestCase):
    def test_copied_material_nested_group_restoration_is_observed(self):
        live = Block("Live Group")
        clone = Block("Private Group", **{node_edit_commit.SOURCE: live.session_uid})
        node = Block("Group Node")
        node.node_tree = clone
        tree = Block("Material Tree")
        tree.nodes = [node]
        material = Material("Copy")
        material.node_tree = tree
        stage = SimpleNamespace(owned_ids=[material, tree, clone], context=SimpleNamespace(created_ids=[]))
        bpy = SimpleNamespace(types=SimpleNamespace(Material=Material),
                              data=SimpleNamespace(node_groups=[live, clone], images=[]))
        mesh_helpers = SimpleNamespace(_mesh=None, _face_selection=None, _readback=None)
        images = SimpleNamespace(IMAGE_MARKER="unused_image_marker")
        world = SimpleNamespace(resolve_image=lambda *_args: self.fail("No image lookup belongs to this fixture"))
        with patch.dict(sys.modules, {"bpy": bpy, "angelo_agent.action_context": mesh_helpers,
                                      "angelo_agent.action_node_graphs": images, "angelo_agent.action_world": world}):
            materials = importlib.import_module("angelo_agent.action_materials")
            material[materials.COPY] = True
            observed = []
            with patch.object(materials, "bpy", bpy):
                program_identities._assign_images(stage, record_transfer=lambda *args: observed.append(args))
        self.assertIs(node.node_tree, live)
        self.assertNotIn(materials.COPY, material)
        self.assertEqual(len(observed), 1)
        source, actual, reason, occurrence = observed[0]
        self.assertIs(source, clone)
        self.assertIs(actual, live)
        self.assertEqual(reason, "nested_source_restored")
        self.assertEqual(occurrence, "node_tree:%s/node:%s/node_tree" % (tree.as_pointer(), node.as_pointer()))

    def test_changed_material_users_remain_a_classified_stale_refusal(self):
        staged, live = Block("Staged"), Block("Live")
        staged[material_sharing.USERS] = '["Original User"]'
        with patch.object(material_sharing, "scene_users", return_value=([], "new outside user")):
            with self.assertRaises(ProgramError) as caught:
                material_sharing.verify(staged, live, object(), set(), {})
        self.assertEqual(caught.exception.code, "STALE_DEPENDENCY")
        self.assertIn("users", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
