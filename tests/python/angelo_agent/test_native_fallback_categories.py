# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-derived reviewed-Python categories and main-thread peaks; no Blender."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.fallback_categories import CATEGORIES, fallback_categories
from angelo_agent.request_fences import RequestFence


class FallbackCategoryTests(unittest.TestCase):
    def test_each_domain_is_recognized_from_actual_blender_api_use(self):
        cases = {
            "geometry": "import bmesh\nbm = bmesh.new()\nbm.verts.new((0, 0, 0))",
            "modifiers": "mod = obj.modifiers.new('Bevel', 'BEVEL')\nmod.width = .02",
            "materials": "mat = bpy.data.materials.new('Skin')\nmat.use_nodes = True",
            "geometry_nodes": "tree = bpy.data.node_groups.new('Scatter', 'GeometryNodeTree')",
            "rigging": "arm = bpy.data.armatures.new('Rig')\nbone = arm.edit_bones.new('Root')",
            "animation": "obj.location.x = 1\nobj.keyframe_insert('location', frame=10)",
            "camera_lighting": "light = bpy.data.lights.new('Key', 'AREA')\nlight.energy = 800",
            "render_world": "scene.render.engine = 'CYCLES'\nscene.render.resolution_x = 1920",
            "external_io": "bpy.ops.wm.obj_import(filepath='/tmp/model.obj')",
        }
        for category, code in cases.items():
            with self.subTest(category=category):
                self.assertIn(category, fallback_categories(code))

    def test_operator_modules_and_node_types_map_to_their_domains(self):
        self.assertEqual(fallback_categories("bpy.ops.object.modifier_add(type='SUBSURF')"), ("modifiers",))
        self.assertEqual(fallback_categories("bpy.ops.mesh.subdivide(number_cuts=2)"), ("geometry",))
        self.assertEqual(fallback_categories("tree.nodes.new('ShaderNodeBsdfPrincipled')"), ("materials",))
        self.assertEqual(fallback_categories("tree.nodes.new('GeometryNodeMeshGrid')"), ("geometry_nodes",))
        self.assertEqual(fallback_categories("with open('notes.txt') as handle:\n    pass"), ("external_io",))
        self.assertEqual(fallback_categories("from pathlib import Path\nPath('x').exists()"), ("external_io",))

    def test_object_level_only_applies_without_a_more_specific_domain(self):
        self.assertEqual(fallback_categories("obj = bpy.data.objects['Cube']\nobj.location = (0, 0, 1)"), ("objects",))
        self.assertEqual(fallback_categories("bpy.ops.object.shade_smooth()"), ("objects",))
        mixed = fallback_categories("bpy.ops.object.mode_set(mode='EDIT')\nbpy.ops.mesh.select_all(action='SELECT')")
        self.assertEqual(mixed, ("geometry",))
        self.assertEqual(fallback_categories("result = 1 + 1"), ("other",))
        self.assertEqual(fallback_categories("def broken(:"), ("other",))

    def test_a_script_touching_several_domains_counts_once_in_each_in_fixed_order(self):
        code = ("mat = bpy.data.materials.new('M')\nobj.data.materials.append(mat)\n"
                "obj.modifiers.new('S', 'SUBSURF')\nobj.keyframe_insert('location')\n"
                "obj.data.materials.append(mat)")
        self.assertEqual(fallback_categories(code), ("modifiers", "materials", "animation"))

    def test_labels_are_closed_and_never_echo_script_content(self):
        code = "SECRET = 'GeometryNodePRIVATE'\nimport os\nos.environ['TOKEN']\nobj['PASSWORD'] = 1"
        labels = fallback_categories(code)
        self.assertTrue(set(labels) <= set(CATEGORIES))
        self.assertFalse(any(word in "".join(labels) for word in ("SECRET", "PRIVATE", "TOKEN", "PASSWORD")))


class ControllerMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("factory-runtime", 17))
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def test_admitted_reviewed_script_counts_its_categories_once(self):
        replies = []
        arguments = {"description": "Bevel the cube", "code": "import bmesh\nobj.modifiers.new('B', 'BEVEL')"}
        fence = RequestFence.capture(self.c)
        self.c.request_tool("script-1", "angelo_execute_python", arguments, replies.append, request_fence=fence)
        self.c.request_tool("script-1", "angelo_execute_python", arguments, replies.append, request_fence=fence)
        self.c.request_tool("script-2", "angelo_execute_python", {"description": "Broken", "code": "def x(:"},
                            replies.append, request_fence=fence)
        counts = self.c._request_metrics.snapshot()["counts"]
        self.assertEqual(counts["python_fallbacks"], 1)
        self.assertEqual(counts["python_fallback_geometry"], 1)
        self.assertEqual(counts["python_fallback_modifiers"], 1)
        self.assertEqual(counts["invalid_calls"], 1)

    def test_main_thread_peak_covers_tool_execution_and_event_pumps(self):
        replies = []
        self.c.request_tool("inspect-1", "angelo_inspect_scene", {}, replies.append,
                            request_fence=RequestFence.capture(self.c))
        self.c.pump_tools(SlowExecutor(), self.c.document, budget=ToolDispatchBudget(seconds=60))
        self.c.pump_events()
        self.assertTrue(replies)
        self.assertGreaterEqual(self.c._request_metrics.snapshot()["phases"]["main_thread_block_ms"], 30)
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        self.assertIn("main_thread_block_ms", self.c.metrics_report()["phases"])

    def test_the_turns_completion_persistence_is_in_its_main_thread_peak(self):
        """The pump that ends a turn also saves it (runtime entry, ledger, session): that stall is the turn's.

        ``total_ms`` still ends where the turn ended, before that persistence.
        """
        metrics, saved, save_started = self.c._request_metrics, self.c.save, []

        def slow_save(**options):  # The turn's end saves with compact_if_due=True (SessionStore.compact_if_due).
            save_started.append((time.monotonic() - metrics.started) * 1000)
            time.sleep(.06)
            saved(**options)

        self.c.save = slow_save
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        self.assertTrue(metrics.finished)
        self.assertGreaterEqual(self.c.timings.get("main_thread_block_ms", 0), 60)
        self.assertLessEqual(self.c.timings["total_ms"], save_started[0])

    def test_direct_approval_execution_outside_the_pump_is_measured(self):
        replies = []
        self.c.request_tool("inspect-2", "angelo_inspect_scene", {}, replies.append,
                            request_fence=RequestFence.capture(self.c))
        call = next(iter(self.c.pending.values()))
        self.c.execute_tool(call, SlowExecutor(), self.c.document)
        self.assertTrue(replies)
        self.assertGreaterEqual(self.c._request_metrics.snapshot()["phases"]["main_thread_block_ms"], 30)


class SlowExecutor:
    def precondition(self, name, arguments):
        return "scene"

    def execute(self, name, arguments):
        time.sleep(.03)
        return {"success": True, "objects": []}


if __name__ == "__main__":
    unittest.main()
