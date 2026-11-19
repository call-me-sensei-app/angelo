# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_state``'s window read (``external_state.read``) against a Blender-shaped window, without Blender.

The fake below has the attributes ``bpy`` has (names and types as Blender 5 reports them), so the reader's every
field is exercised; the GUI proof reads a real window. Where Blender owns the answer the fake asks Blender's source:
a workspace's tools are keyed by the context modes ``rna_context.cc`` defines (``EDIT_TEXT``, never a rebuilt
``EDIT_FONT``), each paint mode's settings hold their own ``unified_paint_settings`` (none on the tool settings), and
the brush's own ``use_unified_size``/``use_unified_strength`` decide (the unified settings' are deprecated).
test_blender_external_state reads every context mode on real RNA. Contract: doc/angelo/external_mcp.rst, "State".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import external_state as state  # noqa: E402

RNA_CONTEXT = Path(__file__).resolve().parents[3] / "source/blender/makesrna/intern/rna_context.cc"
CONTEXT_MODES = set(re.findall(r'\{CTX_MODE_\w+, "(\w+)"', RNA_CONTEXT.read_text(encoding="utf-8")))


def from_space_view3d_mode(mode, create=False):
    """Blender's ``WorkSpaceTools.from_space_view3d_mode``: ``mode`` is an enum of the context modes."""
    if mode not in CONTEXT_MODES:
        raise TypeError("enum \"%s\" not found in context modes" % mode)
    return NS(idname="builtin.select_box", mode=mode)

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
UNIT_BOX = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]


def obj(name, selected, location=(0.0, 0.0, 0.0), mode="OBJECT", kind="MESH", data=None):
    matrix = [row[:] for row in IDENTITY]
    for axis in range(3):
        matrix[axis][3] = location[axis]
    return NS(name=name, mode=mode, type=kind, data=data, matrix_world=matrix, bound_box=UNIT_BOX,
              select_get=lambda view_layer=None, s=selected: s)


class Objects(list):
    """A view layer's objects: iterating it is a walk over every object, which the reader must not do."""

    def __init__(self, items, active):
        super().__init__(items)
        self.active = active
        self.selected = [obj for obj in items if obj.select_get()]

    def __iter__(self):
        raise AssertionError("the state read walked every object of the view layer")


def window(active_mode="OBJECT", objects=None, active_index=0, brush=False, kind="MESH"):
    mesh = NS(total_vert_sel=3, total_edge_sel=2, total_face_sel=1)
    objects = objects if objects is not None else [obj("Cube", True, mode=active_mode, kind=kind, data=mesh),
                                                   obj("Lamp", True, location=(4.0, 0.0, 1.0)), obj("Floor", False)]
    view_layer = NS(name="ViewLayer", objects=Objects(objects, objects[active_index] if objects else None))
    sculpt_brush = NS(name="Draw", size=50, strength=0.5, use_unified_size=True, use_unified_strength=False)
    tool_settings = NS(use_snap=True, snap_elements={"INCREMENT"}, use_proportional_edit=False,
                       transform_pivot_point="MEDIAN_POINT", mesh_select_mode=(True, False, False),
                       sculpt=NS(brush=sculpt_brush if brush else None,   # The deprecated flags say the opposite:
                                 unified_paint_settings=NS(use_unified_size=False, size=72,
                                                           use_unified_strength=True, strength=0.9)),
                       vertex_paint=NS(brush=NS(name="Paint", size=30, strength=1.0, use_unified_size=False,
                                                use_unified_strength=True) if brush else None,
                                       unified_paint_settings=NS(use_unified_size=True, size=99,
                                                                 use_unified_strength=False, strength=0.25)),
                       particle_edit=NS(brush=NS(size=40, strength=0.75) if brush else None, tool="COMB"))
    scene = NS(name="Scene", frame_current=12, frame_start=1, frame_end=250, use_preview_range=False,
               frame_preview_start=1, frame_preview_end=10, camera=NS(name="Camera"), tool_settings=tool_settings,
               transform_orientation_slots=[NS(type="GLOBAL")],
               unit_settings=NS(system="METRIC", scale_length=1.0),
               render=NS(fps=24, fps_base=1.0, engine="BLENDER_EEVEE_NEXT", resolution_x=1920, resolution_y=1080,
                         resolution_percentage=100, image_settings=NS(file_format="PNG"),
                         filepath="/Users/someone/private/render_"))
    region = NS(view_perspective="PERSP", view_distance=12.5, view_location=(0.0, 0.0, 1.0),
                view_rotation=(0.8, 0.4, 0.2, 0.4))
    space = NS(shading=NS(type="SOLID", light="STUDIO", color_type="MATERIAL", show_xray=False),
               overlay=NS(show_overlays=True, show_wireframes=False), show_gizmo=True, region_3d=region, lens=50.0,
               local_view=None)
    areas = [NS(type="VIEW_3D", ui_type="VIEW_3D", width=1200, height=800, spaces=NS(active=space)),
             NS(type="PROPERTIES", ui_type="PROPERTIES", width=300, height=800, spaces=NS(active=None))]
    tools = NS(from_space_view3d_mode=from_space_view3d_mode)
    return NS(scene=scene, view_layer=view_layer, workspace=NS(name="Layout", tools=tools),
              screen=NS(name="Layout", areas=areas, is_animation_playing=False))


def bpy_with(steps=("Original", "Move", "Scale"), active=2, mode="OBJECT"):
    """``mode``: Blender's context mode for the overridden scene and view layer (what ``bpy.context.mode`` says)."""
    stack = NS(steps=[NS(name=name) for name in steps], active_index=active)
    context = NS(window_manager=NS(undo_stack=stack), mode=None, overrides=[])

    @contextlib.contextmanager
    def temp_override(**members):
        context.overrides.append(members)
        context.mode = mode
        try:
            yield
        finally:
            context.mode = None
    context.temp_override = temp_override
    return NS(context=context, data=NS(filepath="/Users/someone/private/scene.blend", is_dirty=True))


class Jobs:
    queue = ["j2"]

    def active(self):
        return {"id": "j1", "kind": "RENDER_STILL", "status": "running", "progress": 0.4256, "eta_s": 31}


class ReadTests(unittest.TestCase):
    def test_every_section_reads_a_blender_shaped_window(self):
        got = state.read(bpy_with(), window(), Jobs())
        self.assertEqual(list(got), list(state.SECTIONS))
        self.assertEqual(got["mode"], {"mode": "OBJECT", "active_object": "Cube"})
        self.assertEqual(got["tool"]["tool"], "builtin.select_box")
        self.assertEqual(got["tool"]["snap_elements"], ["INCREMENT"])
        self.assertEqual(got["selection"]["selected"], ["Cube", "Lamp"])
        self.assertEqual(got["selection"]["bounds"], {"min": [-1.0, -1.0, -1.0], "max": [5.0, 1.0, 2.0]})
        self.assertEqual(got["workspace"]["editors"][0]["type"], "VIEW_3D")
        self.assertEqual(got["viewport"]["view"]["perspective"], "PERSP")
        self.assertEqual(got["scene"]["range"], [1, 250])
        self.assertEqual(got["scene"]["camera"], "Camera")
        self.assertEqual(got["render"], {"engine": "BLENDER_EEVEE_NEXT", "resolution": [1920, 1080],
                                         "percentage": 100, "format": "PNG", "frame_range": [1, 250]})
        self.assertEqual(got["jobs"], {"running": {"id": "j1", "kind": "RENDER_STILL", "status": "running",
                                                   "progress": 0.4256, "eta_seconds": 31}, "queued": 1})
        self.assertEqual(got["undo"], {"steps": [{"name": "Original", "active": False}, {"name": "Move",
                                                 "active": False}, {"name": "Scale", "active": True}],
                                       "total": 3, "can_redo": False})
        self.assertEqual(got["file"], {"saved": True, "unsaved_changes": True})

    def test_never_a_file_path(self):
        text = json.dumps(state.read(bpy_with(), window(), Jobs()))
        self.assertNotIn("/Users/", text)
        self.assertNotIn("scene.blend", text)
        self.assertNotIn("render_", text)

    def test_edit_mode_counts_the_selected_elements(self):
        got = state.read(bpy_with(mode="EDIT_MESH"), window(active_mode="EDIT"), None)
        self.assertEqual(got["selection"]["edit"], {"vertices": 3, "edges": 2, "faces": 1,
                                                    "select_mode": [True, False, False]})
        self.assertEqual(got["tool"]["context_mode"], "EDIT_MESH")
        self.assertEqual(got["jobs"], {"running": None, "queued": 0})

    def test_a_sculpt_brush_reports_the_size_and_strength_that_apply(self):
        got = state.read(bpy_with(mode="SCULPT"), window(active_mode="SCULPT", brush=True), None)
        self.assertEqual(got["tool"]["brush"], {"name": "Draw", "diameter_px": 72, "strength": 0.5})

    def test_each_paint_mode_reads_the_unified_settings_of_its_own_paint_settings(self):
        """Audit P2-6: the unified size and strength live on the paint settings that supplied the brush, and the
        brush's own flags say whether they apply."""
        got = state.read(bpy_with(mode="PAINT_VERTEX"), window(active_mode="VERTEX_PAINT", brush=True), None)
        self.assertEqual(got["tool"]["brush"], {"name": "Paint", "diameter_px": 30, "strength": 0.25})
        got = state.read(bpy_with(mode="PARTICLE"), window(active_mode="PARTICLE_EDIT", brush=True), None)
        self.assertEqual(got["tool"]["brush"], {"name": "COMB", "diameter_px": 40, "strength": 0.75})

    def test_the_tool_is_looked_up_under_blenders_own_context_mode(self):
        """Audit P2-5: a text object in Edit Mode is ``EDIT_TEXT`` and a metaball ``EDIT_METABALL``; the object's
        type (``FONT``, ``META``) names no context mode. The mode is Blender's, read for the window's scene and view
        layer."""
        for kind, mode in (("FONT", "EDIT_TEXT"), ("META", "EDIT_METABALL"), ("CURVES", "EDIT_CURVES")):
            with self.subTest(kind=kind):
                bpy, shown = bpy_with(mode=mode), window(active_mode="EDIT", kind=kind)
                got = state.read(bpy, shown, None)["tool"]
                self.assertNotIn("unavailable", got)
                self.assertEqual((got["context_mode"], got["tool"]), (mode, "builtin.select_box"))
                self.assertEqual(bpy.context.overrides[0], {"scene": shown.scene, "view_layer": shown.view_layer})

    def test_the_fake_tools_know_blenders_context_modes(self):
        self.assertIn("EDIT_TEXT", CONTEXT_MODES)
        self.assertIn("SCULPT_GREASE_PENCIL", CONTEXT_MODES)
        self.assertNotIn("EDIT_FONT", CONTEXT_MODES)

    def test_the_undo_list_is_the_newest_steps_and_says_when_redo_is_possible(self):
        names = ["step %d" % i for i in range(30)]
        got = state.read(bpy_with(names, active=20), window(), None)["undo"]
        self.assertEqual(len(got["steps"]), state.MAX_UNDO)
        self.assertEqual(got["steps"][-1]["name"], "step 29")
        self.assertEqual([s["name"] for s in got["steps"] if s["active"]], ["step 20"])
        self.assertTrue(got["can_redo"])

    def test_no_undo_history_yet_is_an_empty_list(self):
        bpy = bpy_with()
        bpy.context.window_manager.undo_stack = None   # As Blender's before its first Undo push.
        self.assertEqual(state.read(bpy, window(), None)["undo"], {"steps": [], "total": 0, "can_redo": False})

    def test_many_selected_objects_are_counted_exactly_and_listed_capped(self):
        objects = [obj("o%d" % i, True) for i in range(state.MAX_NAMES + 5)]
        got = state.read(bpy_with(), window(objects=objects), None)["selection"]
        self.assertEqual(got["selected_count"], state.MAX_NAMES + 5)
        self.assertEqual(len(got["selected"]), state.MAX_NAMES)
        self.assertTrue(got["truncated"])

    def test_bounds_are_measured_over_a_bounded_selection(self):
        objects = [obj("o%d" % i, True, location=(float(i), 0.0, 0.0)) for i in range(state.MAX_BOUNDS + 10)]
        got = state.read(bpy_with(), window(objects=objects), None)["selection"]
        self.assertTrue(got["bounds_partial"])
        self.assertEqual(got["bounds"]["max"][0], state.MAX_BOUNDS)   # The first MAX_BOUNDS: x up to MAX_BOUNDS-1+1.

    def test_one_unreadable_section_never_hides_the_others(self):
        broken = window()
        broken.screen.areas = None   # As a closing window can be.
        got = state.read(bpy_with(), broken, None)
        self.assertIn("unavailable", got["workspace"])
        self.assertIn("unavailable", got["viewport"])
        self.assertEqual(got["scene"]["scene"], "Scene")

    def test_no_viewport_is_said(self):
        bare = window()
        bare.screen.areas = [a for a in bare.screen.areas if a.type != "VIEW_3D"]
        self.assertEqual(state.read(bpy_with(), bare, None)["viewport"],
                         {"unavailable": "no 3D viewport in this workspace"})


if __name__ == "__main__":
    unittest.main()
