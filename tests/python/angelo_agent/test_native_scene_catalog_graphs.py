# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host closed graph admission; native fixture proves actual RNA compatibility."""

import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_scene_queries import FakeID, FakeObject, FakeRows, fake_bpy, queries, window
from angelo_agent.legacy_names import NATIVE_GRAPH
from angelo_agent.scene_catalog_graphs import INTERFACE_DEFAULTS, PROVENANCE_KEY


class Group(dict):
    def __getattr__(self, name):
        return self[name]


class Fallback(Group):
    @property
    def type(self):
        return "FALLBACK" if self["type"] == 0 else "UNKNOWN"


class Record:
    next_pointer = 1000

    def __init__(self, **values):
        self.__dict__.update(values)
        self.custom = {}
        Record.next_pointer += 1
        self.pointer = Record.next_pointer

    def as_pointer(self):
        return self.pointer

    def keys(self):
        return self.custom.keys()


class NamedRows(FakeRows):
    def get(self, name):
        return next((row for row in self if row.name == name or getattr(row, "identifier", None) == name), None)

    def __getitem__(self, key):
        return self.get(key) if isinstance(key, str) else super().__getitem__(key)


class Graph(FakeID):
    def __init__(self):
        super().__init__(700, "Closed catalog graph")
        self.bl_idname, self.is_tool, self.node_tool_idname = "GeometryNodeTree", False, ""
        self.custom = {}

    def keys(self):
        return self.custom.keys()

    def get(self, field):
        assert field in NATIVE_GRAPH, "Never read arbitrary custom values"
        return self.custom.get(field)


def socket(name, identifier, kind, default=None):
    s = Record(name=name, identifier=identifier, type=kind, is_linked=False, enabled=True, hide_value=False,
               is_unavailable=False, is_multi_input=False, link_limit=1,
               bl_idname="NodeSocketVirtual" if kind == "CUSTOM" else "NodeSocket" + kind)
    if default is not None:
        s.default_value = default
    return s


def closed_graph():
    tree = Graph()
    interface = [Record(name="Geometry", identifier="Socket_0", in_out="INPUT", item_type="SOCKET",
                        socket_type="NodeSocketGeometry", bl_socket_idname="NodeSocketGeometry", **INTERFACE_DEFAULTS),
                 Record(name="Geometry", identifier="Socket_1", in_out="OUTPUT", item_type="SOCKET",
                        socket_type="NodeSocketGeometry", bl_socket_idname="NodeSocketGeometry", **INTERFACE_DEFAULTS)]
    tree.interface = SimpleNamespace(items_tree=interface)
    source = Record(name="in", label="in", bl_idname="NodeGroupInput", mute=False, inputs=NamedRows(),
                    outputs=NamedRows([socket("Geometry", "Socket_0", "GEOMETRY"), socket("", "__extend__", "CUSTOM")]))
    shade = Record(name="shade", label="shade", bl_idname="GeometryNodeSetShadeSmooth", domain="FACE", mute=False,
                   inputs=NamedRows([socket("Mesh", "Geometry", "GEOMETRY"), socket("Selection", "Selection", "BOOLEAN", True),
                                     socket("Shade Smooth", "Shade Smooth", "BOOLEAN", True)]),
                   outputs=NamedRows([socket("Mesh", "Geometry", "GEOMETRY")]))
    output = Record(name="out", label="out", bl_idname="NodeGroupOutput", is_active_output=True, mute=False,
                    inputs=NamedRows([socket("Geometry", "Socket_1", "GEOMETRY"), socket("", "__extend__", "CUSTOM")]), outputs=NamedRows())
    tree.nodes = NamedRows([source, shade, output])
    tree.links = FakeRows()
    for a, b in ((source.outputs[0], shade.inputs[0]), (shade.outputs[0], output.inputs[0])):
        a.is_linked = b.is_linked = True
        tree.links.append(SimpleNamespace(from_node=source if a == source.outputs[0] else shade,
                                          from_socket=a, to_node=shade if b == shade.inputs[0] else output,
                                          to_socket=b, is_valid=True, is_muted=False))
    parameters = {"name": "Closed catalog graph", "nodes": [{"id": "in", "type": "GROUP_INPUT"},
                   {"id": "shade", "type": "SET_SHADE_SMOOTH"}, {"id": "out", "type": "GROUP_OUTPUT"}],
                  "links": [{"from_node": "in", "from_socket": "Geometry", "to_node": "shade", "to_socket": "Geometry"},
                            {"from_node": "shade", "from_socket": "Geometry", "to_node": "out", "to_socket": "Geometry"}]}
    tree.custom[PROVENANCE_KEY] = json.dumps(parameters)
    modifier = SimpleNamespace(name="Closed catalog graph", type="NODES", node_group=tree, show_viewport=True,
                               show_render=True, show_in_editmode=True, show_on_cage=False, bakes=[], panels=[],
                               properties=SimpleNamespace(inputs=Group(Socket_0=Fallback(type=0)), outputs=Group(), panels=Group()))
    return tree, modifier, parameters


class CatalogGeometryTests(unittest.TestCase):
    def setUp(self):
        # The actual action graph auditor is pure over these fake native rows;
        # its unused evaluation helpers need neither host numpy nor mathutils.
        context = ModuleType("angelo_agent.action_context")
        context._local = context._require_subdivision = lambda value: None
        native = patch.dict(sys.modules, {"bpy": fake_bpy, "angelo_agent.action_context": context})
        native.start()
        self.addCleanup(native.stop)
        queries.invalidate()
        self.obj = FakeObject(101, "Catalog object", mesh=True)
        self.window = window([self.obj])
        self.tree, self.modifier, self.parameters = closed_graph()
        self.obj.modifiers = [self.modifier]

    def detail(self):
        return queries.execute(self.window, "angelo_scene_object", {"object": queries.object_handle(self.obj), "sections": ["MODIFIERS"]})

    def test_actual_catalog_graph_admits_exact_revision_and_immutable_details(self):
        revision = queries.object_revision(self.window, self.obj)
        result = self.detail()
        graph = result["sections"]["modifiers"][0]["node_tree"]
        self.assertEqual(graph["scope"], "CLOSED_CATALOG_GEOMETRY")
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(graph["modifier_inputs"]["type"], "FALLBACK")
        self.assertEqual(result["object_revision"], revision)
        graph["nodes"][0]["name"] = "Caller mutation"
        self.assertEqual(self.detail()["sections"]["modifiers"][0]["node_tree"]["nodes"][0]["name"], "in")

    def test_provenance_saved_under_the_pre_rename_key_is_still_admitted(self):
        self.tree.custom[NATIVE_GRAPH[1]] = self.tree.custom.pop(PROVENANCE_KEY)
        queries.invalidate()
        # The key name is part of the revision, so only admission is compared.
        self.assertEqual(len(queries.object_revision(self.window, self.obj)), 64)
        self.assertEqual(self.detail()["sections"]["modifiers"][0]["node_tree"]["scope"], "CLOSED_CATALOG_GEOMETRY")
        # Both keys at once is not a closed catalog graph.
        self.tree.custom[PROVENANCE_KEY] = self.tree.custom[NATIVE_GRAPH[1]]
        queries.invalidate()
        with self.assertRaisesRegex(ValueError, "untracked_properties"):
            queries.object_revision(self.window, self.obj)

    def test_actual_settings_tamper_cannot_use_valid_metadata_as_proof(self):
        self.tree.nodes["shade"].inputs["Shade Smooth"].default_value = False
        with self.assertRaisesRegex(ValueError, "catalog_geometry_integrity"):
            queries.object_revision(self.window, self.obj)
        before = queries.execute(self.window, "angelo_scene_search", {})
        delta = queries.execute(self.window, "angelo_scene_delta", {"observation_token": before["observation_token"]})
        self.assertFalse(delta["delta_exact"])
        self.assertGreater(delta["uncertain_count"], 0)

    def test_matching_closed_metadata_and_actual_settings_change_revision(self):
        old = queries.object_revision(self.window, self.obj)
        before = queries.execute(self.window, "angelo_scene_search", {})
        self.parameters["nodes"][1]["inputs"] = {"Shade Smooth": False}
        self.tree.custom[PROVENANCE_KEY] = json.dumps(self.parameters)
        self.tree.nodes["shade"].inputs["Shade Smooth"].default_value = False
        self.assertNotEqual(old, queries.object_revision(self.window, self.obj))
        delta = queries.execute(self.window, "angelo_scene_delta", {"observation_token": before["observation_token"]})
        self.assertTrue(delta["delta_exact"])
        self.assertEqual(delta["changes"][0]["fields"], ["modifiers"])

    def test_untracked_values_are_refused_without_reading_or_emitting_them(self):
        secret = "catalog-secret-value-must-never-be-emitted"
        for values in (self.tree.custom, self.tree.nodes["shade"].custom, self.modifier.properties.inputs):
            with self.subTest(owner=type(values).__name__):
                values["untracked"] = secret
                with self.assertRaisesRegex(ValueError, "catalog_geometry_untracked_properties"):
                    queries.object_revision(self.window, self.obj)
                self.assertNotIn(secret, json.dumps(self.detail()))
                del values["untracked"]

    def test_interface_unknown_resource_animation_and_missing_provenance_fail_closed(self):
        self.tree.interface.items_tree[0].default_attribute_name = "not_read_as_a_path"
        with self.assertRaisesRegex(ValueError, "catalog_geometry_interface_settings"):
            queries.object_revision(self.window, self.obj)
        self.tree.interface.items_tree[0].default_attribute_name = ""
        self.tree.animation_data = SimpleNamespace(action=None, drivers=[], nla_tracks=[])
        with self.assertRaisesRegex(ValueError, "catalog_geometry_size_or_animation"):
            queries.object_revision(self.window, self.obj)
        self.tree.animation_data = None
        self.tree.custom.clear()
        with self.assertRaisesRegex(ValueError, "catalog_geometry_untracked_properties"):
            queries.object_revision(self.window, self.obj)

    def test_replaced_known_fallback_storage_cannot_smuggle_a_dependency(self):
        slot = self.modifier.properties.inputs.Socket_0
        for raw in (True, 1, "FALLBACK", self.obj):
            with self.subTest(raw_type=type(raw).__name__):
                slot["type"] = raw
                with self.assertRaisesRegex(ValueError, "catalog_geometry_modifier_input_storage"):
                    queries.object_revision(self.window, self.obj)
        slot["type"] = 0
        self.assertEqual(len(queries.object_revision(self.window, self.obj)), 64)

    def test_metadata_nonfinite_duplicate_oversized_and_work_budgets(self):
        for encoded in ('{"nodes":[],"nodes":[]}', '{"nodes":NaN}', "x" * 65537):
            with self.subTest(encoded=encoded[:30]):
                self.tree.custom[PROVENANCE_KEY] = encoded
                with self.assertRaisesRegex(ValueError, "catalog_geometry"):
                    queries.object_revision(self.window, self.obj)
        self.tree.custom[PROVENANCE_KEY] = json.dumps(self.parameters)
        with patch.object(queries, "MAX_METADATA_ITEMS", 1), self.assertRaisesRegex(ValueError, "budget"):
            queries.object_revision(self.window, self.obj)


if __name__ == "__main__":
    unittest.main()
