# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host regressions for closed fields, live flags and incoming-cache ownership."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_scene_queries import FakeID, FakeObject, FakeRows, fake_bpy, queries, window
from angelo_agent.scene_read_fields import (CONSTRAINT_COMMON_FIELDS, CONSTRAINT_TYPE_FIELDS,
                                           KNOWN_CONSTRAINTS, TEXTURE_MAPPING_FIELDS, COLOR_MAPPING_FIELDS)


class Record:
    def __init__(self, **values):
        self.__dict__.update(values)


def live_mesh():
    layers = SimpleNamespace(deform=SimpleNamespace(active=None), float={}, int={})
    verts = FakeRows(Record(co=co, select=False, hide=False) for co in ([0., 0., 0.], [1., 0., 0.], [0., 1., 0.]))
    verts.layers = layers
    edges = FakeRows(Record(verts=pair, select=False, hide=False, smooth=True, seam=False)
                     for pair in ((verts[0], verts[1]), (verts[1], verts[2]), (verts[2], verts[0])))
    faces = FakeRows([Record(verts=verts, loops=[Record(edge=e) for e in edges],
                            select=False, hide=False, material_index=0, smooth=False)])
    faces.layers = layers
    return SimpleNamespace(verts=verts, edges=edges, faces=faces,
                           loops=SimpleNamespace(layers=SimpleNamespace(uv={})))


def noise_tree():
    ramp = SimpleNamespace(interpolation="LINEAR", color_mode="RGB", hue_interpolation="NEAR",
                           elements=[SimpleNamespace(position=0., color=[0., 0., 0., 1.])])
    texture = SimpleNamespace(vector_type="POINT", translation=[0., 0., 0.], rotation=[0., 0., 0.], scale=[1., 1., 1.],
                              min=[0., 0., 0.], max=[1., 1., 1.], use_min=False, use_max=False,
                              mapping_x="X", mapping_y="Y", mapping_z="Z", mapping="FLAT")
    color = SimpleNamespace(use_color_ramp=False, brightness=1., contrast=1., saturation=1.,
                            blend_type="MIX", blend_color=[1., 1., 1.], blend_factor=0., color_ramp=ramp)
    socket = lambda name, value: SimpleNamespace(name=name, identifier=name, type="VALUE", is_linked=False, default_value=value)
    node = SimpleNamespace(name="noise", label="", bl_idname="ShaderNodeTexNoise", mute=False,
                           noise_dimensions="3D", noise_type="FBM", normalize=True,
                           inputs=[socket("Scale", 5.)], outputs=[socket("Fac", .5)],
                           texture_mapping=texture, color_mapping=color)
    tree = FakeID(700, "Closed Noise")
    tree.nodes, tree.links = [node], []
    return tree, node


class SceneFreshnessTests(unittest.TestCase):
    def setUp(self):
        queries.invalidate()
        self.obj, self.other = FakeObject(101, "Parent", mesh=True), FakeObject(102, "Incoming")
        self.window = window([self.obj, self.other])

    def detail(self, sections):
        return queries.execute(self.window, "angelo_scene_object", {"object": queries.object_handle(self.obj), "sections": sections})

    def test_supported_constraint_fields_change_exact_revisions_and_deltas(self):
        regressions = {"LIMIT_LOCATION": ("use_min_x", "use_min_y", "use_min_z", "use_max_x", "use_max_y", "use_max_z"),
                       "LIMIT_SCALE": ("use_min_x", "use_min_y", "use_min_z", "use_max_x", "use_max_y", "use_max_z"),
                       "COPY_SCALE": ("power", "use_add"), "CHILD_OF": ("set_inverse_pending",),
                       "LIMIT_ROTATION": ("use_legacy_behavior",)}
        self.assertEqual(KNOWN_CONSTRAINTS, frozenset(CONSTRAINT_TYPE_FIELDS))
        for kind, fields in regressions.items():
            for field in fields:
                with self.subTest(kind=kind, field=field):
                    self.assertIn(field, CONSTRAINT_TYPE_FIELDS[kind])
                    constraint = SimpleNamespace(name="Closed constraint", type=kind, **{field: False if field != "power" else 1.})
                    self.obj.constraints = [constraint]
                    before = queries.execute(self.window, "angelo_scene_search", {})
                    revision = queries.object_revision(self.window, self.obj)
                    setattr(constraint, field, True if field != "power" else 2.)
                    self.assertNotEqual(queries.object_revision(self.window, self.obj), revision)
                    delta = queries.execute(self.window, "angelo_scene_delta", {"observation_token": before["observation_token"]})
                    self.assertTrue(delta["delta_exact"])
                    self.assertTrue(any("rig" in row.get("fields", ()) for row in delta["changes"]))
        self.assertIn("enabled", CONSTRAINT_COMMON_FIELDS)

    def test_live_face_edge_shading_and_seams_without_mesh_sync(self):
        bm = live_mesh()
        self.obj.mode = "EDIT"
        self.obj.data.attributes = FakeRows(SimpleNamespace(name=n) for n in ("sharp_face", "sharp_edge", "uv_seam"))
        with patch.dict(sys.modules, {"bmesh": SimpleNamespace(from_edit_mesh=lambda mesh: bm)}):
            for component, field in ((bm.faces[0], "smooth"), (bm.edges[0], "smooth"), (bm.edges[0], "seam")):
                with self.subTest(field=field):
                    before = queries.execute(self.window, "angelo_scene_search", {})
                    revision = queries.object_revision(self.window, self.obj)
                    setattr(component, field, not getattr(component, field))
                    self.assertNotEqual(queries.object_revision(self.window, self.obj), revision)
                    delta = queries.execute(self.window, "angelo_scene_delta", {"observation_token": before["observation_token"]})
                    self.assertTrue(delta["delta_exact"])
                    self.assertTrue(any("geometry" in row.get("fields", ()) for row in delta["changes"]))
            self.obj.data.attributes.append(SimpleNamespace(name="unread_custom_float"))
            with self.assertRaisesRegex(ValueError, "live_edit_custom_mesh_layers"):
                queries.object_revision(self.window, self.obj)

    def test_incoming_relationship_details_are_never_target_only_cached(self):
        self.other.parent = self.obj
        revision = queries.object_revision(self.window, self.obj)
        with patch.object(queries._relations, "read", wraps=queries._relations.read) as read:
            self.detail(["RELATIONSHIPS"])
            self.detail(["RELATIONSHIPS"])
            self.assertEqual(read.call_count, 2)
        self.assertTrue(any(key[1] == "detail" for key in queries._cache))
        self.other.name = "Renamed incoming"
        queries.invalidate(depsgraph=SimpleNamespace(updates=[SimpleNamespace(id=self.other)]))
        fresh = self.detail(["RELATIONSHIPS"])
        self.assertEqual(queries.object_revision(self.window, self.obj), revision)
        self.assertTrue(any(edge["from"]["name"] == "Renamed incoming" for edge in fresh["sections"]["relationships"]))
        self.other.parent = None  # Unknown edit, no handler.
        self.assertFalse(any(edge["kind"] == "PARENT" for edge in self.detail(["RELATIONSHIPS"])["sections"]["relationships"]))
        self.detail(["MESH"])
        self.assertTrue(any(key[1] == "detail" for key in queries._cache))

    def test_native_users_added_without_target_revision_change_are_fresh(self):
        fake_users = {self.obj: set()}
        with patch.object(fake_bpy, "data", SimpleNamespace(objects=[self.obj, self.other], collections=[], scenes=[],
                                                            user_map=lambda **kw: fake_users)):
            self.detail(["RELATIONSHIPS"])
            revision = queries.object_revision(self.window, self.obj)
            fake_users[self.obj].add(self.other)
            fresh = self.detail(["TRANSFORM", "RELATIONSHIPS"])
            self.assertEqual(queries.object_revision(self.window, self.obj), revision)
            self.assertTrue(any(edge["kind"] == "ID_USER" for edge in fresh["sections"]["relationships"]))
            fake_users[self.obj].clear()
            self.assertFalse(any(edge["kind"] == "ID_USER" for edge in self.detail(["RELATIONSHIPS"])["sections"]["relationships"]))

    def test_warm_rich_details_reuse_mesh_only_after_actual_fresh_relation_read(self):
        self.other.parent = self.obj
        order = []
        real_read, real_cached = queries._relations.read, queries._cached
        def read(*args):
            order.append("read")
            return real_read(*args)
        def cached(key):
            if key[1] == "detail":
                order.append("cache")
            return real_cached(key)
        with patch.object(queries._relations, "read", side_effect=read) as relations, \
             patch.object(queries, "_cached", side_effect=cached), \
             patch.object(queries._geometry, "mesh", wraps=queries._geometry.mesh) as mesh:
            first = self.detail(["MESH", "RELATIONSHIPS"])
            first["sections"]["mesh"]["vertices"] = 999
            warm = self.detail(["MESH", "RELATIONSHIPS"])
        self.assertEqual(relations.call_count, 2)
        self.assertEqual(order, ["read", "cache", "read", "cache"])
        self.assertEqual(sum(call.kwargs.get("details", False) for call in mesh.call_args_list), 1)
        self.assertEqual(warm["sections"]["mesh"]["vertices"], 2)

    def test_missed_handler_edits_change_relation_key_not_target_revision(self):
        self.other.parent = self.obj
        revision = queries.object_revision(self.window, self.obj)
        self.detail(["MESH", "RELATIONSHIPS"])
        for field, value, reads in (("name", "Unreported rename", 1), ("parent", None, 1), ("parent", self.obj, 0)):
            setattr(self.other, field, value)
            with patch.object(queries._geometry, "mesh", wraps=queries._geometry.mesh) as mesh:
                result = self.detail(["MESH", "RELATIONSHIPS"])
            self.assertEqual(sum(call.kwargs.get("details", False) for call in mesh.call_args_list), reads)
            self.assertEqual(queries.object_revision(self.window, self.obj), revision)
            if self.other.parent:
                self.assertTrue(any(e["from"]["name"] == self.other.name for e in result["sections"]["relationships"]))

    def test_incomplete_relation_scan_bypasses_complete_cache_and_stays_truthful(self):
        self.other.parent = self.obj
        self.detail(["MESH", "RELATIONSHIPS"])
        read = queries._relations.read
        def exhausted(*args):
            args[4].items = 0  # Exhaust only the independent relationship pass.
            return read(*args)
        with patch.object(queries._relations, "read", side_effect=exhausted) as relations, \
             patch.object(queries._geometry, "mesh", wraps=queries._geometry.mesh) as mesh:
            for _ in range(2):
                result = self.detail(["MESH", "RELATIONSHIPS"])
                self.assertTrue(result["truncated"])
                self.assertIn({"field": "relationships", "reason": "work_budget"}, result["truncations"])
                self.assertFalse(result["sections"]["relationships"])
        self.assertEqual(relations.call_count, 2)
        self.assertEqual(sum(call.kwargs.get("details", False) for call in mesh.call_args_list), 2)
        queries.invalidate(window=self.window)
        with patch.object(queries, "MAX_OBJECTS", 1):
            result = self.detail(["RELATIONSHIPS"])
        self.assertTrue(result["truncated"])
        self.assertFalse(any(key[1] == "detail" for key in queries._cache))

    def test_complete_rich_cache_preserves_output_limits_and_truncations(self):
        self.other.parent = self.obj
        args = {"object": queries.object_handle(self.obj), "sections": ["MESH", "RELATIONSHIPS"], "limit": 1}
        first = queries.execute(self.window, "angelo_scene_object", args)
        warm = queries.execute(self.window, "angelo_scene_object", args)
        self.assertTrue(warm["truncated"])
        self.assertEqual(len(warm["sections"]["relationships"]), 1)
        self.assertEqual(first["truncations"], warm["truncations"])

    def test_noise_numeric_settings_mappings_ramp_sockets_and_links_are_exact(self):
        tree, node = noise_tree()
        material = FakeID(701, "Closed material")
        material.use_nodes, material.node_tree = True, tree
        self.obj.material_slots = [SimpleNamespace(material=material, link="DATA")]
        observed = self.detail(["MATERIALS"])
        graph = observed["sections"]["materials"][0]["node_tree"]
        self.assertFalse(graph["revision_limitations"])
        captured = graph["nodes"][0]
        self.assertEqual(set(captured["texture_mapping"]), set(TEXTURE_MAPPING_FIELDS))
        self.assertEqual(set(captured["color_mapping"]), set(COLOR_MAPPING_FIELDS) | {"color_ramp"})
        mutations = [(node, "noise_dimensions", "4D"), (node, "noise_type", "MULTIFRACTAL"), (node, "normalize", False),
                     (node.texture_mapping, "translation", [1., 0., 0.]), (node.texture_mapping, "use_max", True),
                     (node.color_mapping, "saturation", .25), (node.color_mapping.color_ramp.elements[0], "position", .2),
                     (node.inputs[0], "default_value", 4.), (node.outputs[0], "default_value", .75)]
        for owner, field, value in mutations:
            with self.subTest(field=field):
                before = queries.object_revision(self.window, self.obj)
                setattr(owner, field, value)
                self.assertNotEqual(queries.object_revision(self.window, self.obj), before)
        node.bl_idname = "ShaderNodeScript"  # Generic types are fingerprinted; denied ones never are.
        with self.assertRaisesRegex(ValueError, "unfingerprinted_node"):
            queries.object_revision(self.window, self.obj)
        node.bl_idname = "ShaderNodeTexNoise"
        with patch.object(queries, "MAX_METADATA_ITEMS", 1), self.assertRaisesRegex(ValueError, "budget"):
            queries.object_revision(self.window, self.obj)


if __name__ == "__main__":
    unittest.main()
