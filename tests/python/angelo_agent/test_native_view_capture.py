# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host orchestration tests for view_capture with fake bpy/gpu/mathutils.

These pin coalescing, invalidation, REUSE, display restoration and failure
cleanup. They do not prove GPU pixels or Blender's RNA update side effects;
test_blender_view_observation.py runs those checks in the compiled GUI.
"""

import contextlib
import importlib
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import view_cache  # Shared with view_capture; import before patching.
from angelo_agent.references import ReferenceStore
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_fakes import Collection, msgbus_listing  # noqa: E402

MODES = {"OBJECT": 0, "EDIT": 1, "VERTEX_PAINT": 4, "SCULPT": 8, "WEIGHT_PAINT": 16}  # Blender's mode flags.


class Objects(Collection):
    """``bpy.data.objects``: ``foreach_get("mode")`` gives each object's mode as Blender's flag value."""

    def foreach_get(self, name, buffer):
        if name != "mode":
            return super().foreach_get(name, buffer)
        buffer[:] = type(buffer)(buffer.typecode, [MODES[item.mode] for item in self])

HANDLE = "scn:" + "b" * 32 + ":11"
IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


class FakeMatrix:
    def __init__(self, rows=IDENTITY):
        self.rows = [[float(v) for v in row] for row in rows]

    def copy(self):
        return FakeMatrix(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __matmul__(self, vector):
        values = list(vector) + [1.0]
        return tuple(sum(self.rows[i][j] * values[j] for j in range(4)) for i in range(3))

    @property
    def translation(self):
        return tuple(row[3] for row in self.rows[:3])


class FakeShading:
    """studio_light reads/writes a different stored field per type and light, like RNA."""

    def __init__(self):
        object.__setattr__(self, "fields", {"studio": "rim.sl", "lookdev": "city.exr", "matcap": "basic_1.exr"})
        object.__setattr__(self, "writes", [])
        for key, value in dict(type="SOLID", light="MATCAP", color_type="OBJECT", show_shadows=False,
                               show_cavity=False, background_type="THEME", background_color=(.3, .3, .3),
                               show_xray=True, show_backface_culling=True, show_object_outline=True,
                               show_specular_highlight=True, use_dof=True, wireframe_color_type="THEME",
                               show_xray_wireframe=True, use_scene_lights=True, use_scene_world=True,
                               render_pass="NORMAL", use_compositor="ALWAYS", studiolight_rotate_z=1.0,
                               studiolight_intensity=2.0, studiolight_background_alpha=.5,
                               studiolight_background_blur=.1).items():
            object.__setattr__(self, key, value)

    def _field(self):
        if self.type == "MATERIAL":
            return "lookdev"
        return "matcap" if self.light == "MATCAP" else "studio"

    def __getattr__(self, name):
        if name == "studio_light":
            return self.fields[self._field()]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        self.writes.append(name)
        if name == "studio_light":
            if value == "missing.exr":
                raise TypeError("enum not found")
            self.fields[self._field()] = value
        else:
            object.__setattr__(self, name, value)

    def snapshot(self):
        return dict(vars(self), fields=dict(self.fields), writes=None)


class FakeOffScreen:
    draws = []
    fail = False

    def __init__(self, width, height):
        self.size = (width, height)
        self.texture_color = SimpleNamespace(read=lambda: FakeBuffer(width * height * 4))

    def draw_view3d(self, scene, layer, space, region, view, projection, do_color_management=False):
        if FakeOffScreen.fail:
            raise RuntimeError("injected draw failure")
        FakeOffScreen.draws.append({"type": space.shading.type, "studio": space.shading.studio_light,
                                    "overlays": space.overlay.show_overlays, "view": view.rows,
                                    "projection": projection.rows, "size": self.size,
                                    "lights": space.shading.use_scene_lights, "world": space.shading.use_scene_world})

    def free(self):
        pass


class FakeBuffer(bytearray):
    dimensions = None


class FakeQueryError(RuntimeError):
    pass


def environment():
    state = {"compiling": False, "revision": "r" * 64, "epoch": "c" * 32}
    bpy = ModuleType("bpy")
    bpy.context = SimpleNamespace(temp_override=lambda **_: contextlib.nullcontext())
    bpy.app = SimpleNamespace(is_job_running=lambda _job: state["compiling"])
    items = {name: SimpleNamespace(value=value) for name, value in MODES.items()}
    bpy.types = SimpleNamespace(Object=SimpleNamespace(bl_rna=SimpleNamespace(properties={
        "mode": SimpleNamespace(enum_items=items)})))
    target = SimpleNamespace(name="Hero", type="MESH", session_uid=11, matrix_world=FakeMatrix(), hidden=False,
                             mode="OBJECT")
    bpy.data = SimpleNamespace(objects=Objects([target]))
    msgbus = []  # bpy.msgbus subscribers, as the application lists them (_bpy._angelo_jobs).
    native = ModuleType("_bpy")
    native._angelo_jobs = SimpleNamespace(msgbus_subscribers=msgbus_listing(msgbus))
    target.visible_get = lambda view_layer=None: not target.hidden
    corners = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (0, 2)]
    evaluated = SimpleNamespace(type="MESH", bound_box=corners, original=target)
    instance = SimpleNamespace(is_instance=False, object=evaluated, parent=None, show_self=True, matrix_world=FakeMatrix())
    space = SimpleNamespace(shading=FakeShading(), local_view=None, use_local_collections=False,
                            overlay=SimpleNamespace(show_overlays=True, wireframe_threshold=.5, wireframe_opacity=.7),
                            region_3d=SimpleNamespace(use_clip_planes=True, view_perspective="PERSP", view_matrix=IDENTITY),
                            region_quadviews=[], show_object_viewport_mesh=True,
                            bl_rna=SimpleNamespace(properties=[SimpleNamespace(identifier="show_object_viewport_mesh")]))
    area = SimpleNamespace(type="VIEW_3D", spaces=SimpleNamespace(active=space), regions=[SimpleNamespace(type="WINDOW")])
    layer = SimpleNamespace(update=lambda: None, as_pointer=lambda: 99,
                            depsgraph=SimpleNamespace(object_instances=[instance]))
    scene = SimpleNamespace(session_uid=7, name="Scene", frame_current=1, camera=None,
                            render=SimpleNamespace(engine="CYCLES", film_transparent=False))
    window = SimpleNamespace(screen=SimpleNamespace(areas=[area]), scene=scene, view_layer=layer, modal_operators=[])
    queries = SimpleNamespace(SceneQueryError=FakeQueryError, document_epoch=lambda: state["epoch"],
                              resolve_object=lambda _window, handle: target)

    def revision(_window, _obj):
        if state["revision"] is None:
            raise FakeQueryError("Exact program revision unavailable: live_sculpt_surface")
        return state["revision"]
    queries.object_revision = revision
    gpu = ModuleType("gpu")
    gpu.types = SimpleNamespace(GPUOffScreen=FakeOffScreen)
    mathutils = ModuleType("mathutils")
    mathutils.Matrix, mathutils.Vector = FakeMatrix, tuple
    return SimpleNamespace(state=state, bpy=bpy, gpu=gpu, mathutils=mathutils, window=window, space=space,
                           target=target, queries=queries, native=native, msgbus=msgbus)


class ViewCaptureTests(unittest.TestCase):
    def setUp(self):
        self.env = environment()
        self.modules = patch.dict(sys.modules, {"bpy": self.env.bpy, "gpu": self.env.gpu,
                                                "mathutils": self.env.mathutils, "_bpy": self.env.native})
        self.modules.start()
        sys.modules.pop("angelo_agent.view_capture", None)
        self.capture = importlib.import_module("angelo_agent.view_capture")
        self.queries = patch.object(self.capture, "scene_queries", self.env.queries)
        self.queries.start()
        self.directory = tempfile.TemporaryDirectory()
        self.references = ReferenceStore(self.directory.name, "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        FakeOffScreen.draws, FakeOffScreen.fail = [], False
        view_cache.clear()

    def tearDown(self):
        view_cache.clear()
        self.queries.stop()
        self.modules.stop()
        self.directory.cleanup()

    def run_view(self, **args):
        return self.capture.execute(self.env.window, dict({"targets": [HANDLE], "views": ["FRONT"]}, **args),
                                    self.references)

    def test_identical_requests_coalesce_and_revision_changes_miss(self):
        first = self.run_view()
        self.assertEqual(len(FakeOffScreen.draws), 1)
        view = first["views"][0]
        self.assertFalse(view["cache_hit"])
        self.assertEqual(Path(view["path"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(first["targets"][0]["revision"], "r" * 64)
        self.assertEqual((first["read_only"], first["scene_changed"], first["uncertain"]), (True, False, False))
        self.assertEqual(first["document_epoch"], "c" * 32)
        self.assertEqual(self.references.tool_images(first), first["views"])
        second = self.run_view()
        self.assertEqual(len(FakeOffScreen.draws), 1)
        self.assertTrue(second["views"][0]["cache_hit"])
        self.assertNotEqual(second["views"][0]["view_id"], view["view_id"])
        self.assertEqual(Path(second["views"][0]["path"]).read_bytes(), Path(view["path"]).read_bytes())
        self.env.state["revision"] = "s" * 64
        self.assertFalse(self.run_view()["views"][0]["cache_hit"])
        view_cache.note_scene_change(7)
        self.assertFalse(self.run_view()["views"][0]["cache_hit"])
        self.assertEqual(len(FakeOffScreen.draws), 3)

    def test_reuse_draws_the_identical_camera_and_expires_with_the_document(self):
        first = self.run_view(views=["ISO"])
        self.env.target.matrix_world = FakeMatrix([[1, 0, 0, 5], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        reused = self.run_view(views=[first["views"][0]["view_id"]], shading="WIREFRAME", resolution=256)
        self.assertEqual(FakeOffScreen.draws[0]["view"], FakeOffScreen.draws[1]["view"])
        self.assertEqual(FakeOffScreen.draws[0]["projection"], FakeOffScreen.draws[1]["projection"])
        self.assertEqual(FakeOffScreen.draws[1]["size"], (256, 256))
        camera = reused["views"][0]["camera"]
        self.assertEqual(camera["matrix_world"], first["views"][0]["camera"]["matrix_world"])
        self.assertEqual((camera["reused_from"], camera["source"]), (first["views"][0]["view_id"], "AXIS:ISO"))
        self.assertEqual(reused["views"][0]["framing"], "REUSE")
        view_cache.clear()
        with self.assertRaisesRegex(ValueError, "expired"):
            self.run_view(views=[first["views"][0]["view_id"]])

    def test_display_is_normalized_only_during_drawing_and_restored_exactly(self):
        shading = self.env.space.shading
        before = shading.snapshot()
        overlay, region = dict(vars(self.env.space.overlay)), dict(vars(self.env.space.region_3d))
        self.run_view(shading="MATERIAL")
        draw = FakeOffScreen.draws[0]
        self.assertEqual((draw["type"], draw["studio"], draw["overlays"]), ("MATERIAL", "forest.exr", False))
        self.assertEqual(shading.snapshot(), before)
        self.assertEqual((dict(vars(self.env.space.overlay)), dict(vars(self.env.space.region_3d))), (overlay, region))
        self.run_view(shading="SOLID")
        self.assertEqual(FakeOffScreen.draws[1]["studio"], "Default")
        self.assertEqual(shading.snapshot(), before)
        self.run_view(shading="WIREFRAME")
        self.assertEqual(shading.snapshot(), before)
        self.assertEqual(dict(vars(self.env.space.overlay)), overlay)
        object.__setattr__(shading, "type", "MATERIAL")
        shading.writes.clear()
        self.run_view(shading="MATERIAL", resolution=384)
        self.assertNotIn("type", shading.writes, "An equal shading type is not written again")

    def test_lit_draws_with_the_scenes_own_lights_and_world_and_is_never_cached(self):
        shading = self.env.space.shading
        object.__setattr__(shading, "use_scene_lights", False)
        object.__setattr__(shading, "use_scene_world", False)
        before = shading.snapshot()
        result = self.run_view(shading="LIT")
        draw = FakeOffScreen.draws[0]
        self.assertEqual((draw["type"], draw["lights"], draw["world"], draw["overlays"]), ("MATERIAL", True, True, False))
        self.assertEqual(shading.snapshot(), before, "the artist's own viewport lighting is put back")
        self.assertEqual(result["lit_preview"], {"engine": "EEVEE", "lighting": "SCENE_LIGHTS_AND_WORLD",
                                                 "scene_engine": "CYCLES", "approximation": True})
        self.assertIn("an approximation of this CYCLES scene", result["note"])
        self.assertNotIn("material_preview", result)
        self.run_view(shading="MATERIAL")
        self.assertEqual((FakeOffScreen.draws[1]["lights"], FakeOffScreen.draws[1]["world"]), (False, False))
        # Colour management and film settings change the picture without any depsgraph update: drawn every time.
        self.assertFalse(self.run_view(shading="LIT")["views"][0]["cache_hit"])
        self.assertEqual(len(FakeOffScreen.draws), 3)
        self.env.window.scene.render.engine = "BLENDER_EEVEE"
        exact = self.run_view(shading="LIT")
        self.assertFalse(exact["lit_preview"]["approximation"])
        self.assertNotIn("approximation", exact["note"])
        self.assertNotIn("transparent", exact["note"])

    def test_lit_says_when_the_film_is_transparent_because_its_background_is_drawn_black(self):
        self.env.window.scene.render.film_transparent = True
        result = self.run_view(shading="LIT")
        self.assertTrue(result["lit_preview"]["transparent_film"])
        self.assertIn("The scene's film is transparent: its world is not drawn, and the black background here is "
                      "transparent in a render (RENDERED shows it over a checkerboard)", result["note"])
        self.assertNotIn("transparent", self.run_view(shading="MATERIAL")["note"])

    def test_rendered_is_not_drawn_here(self):
        with patch("angelo_agent.view_render.begin", create=True, return_value="pending") as begin:
            self.env.window.modal_operators = [SimpleNamespace(bl_idname="RENDER_OT_render")]
            result = self.capture.execute(self.env.window, {"views": ["SCENE_CAMERA"], "shading": "RENDERED"},
                                          self.references, "controller")
        self.assertEqual(result, "pending")
        self.assertEqual(begin.call_args.args[2:], (self.references, "controller"))
        self.assertEqual(FakeOffScreen.draws, [])

    def test_uncertain_and_unproven_captures_are_reported_and_never_cached(self):
        self.env.state["compiling"] = True
        result = self.run_view(shading="MATERIAL")
        self.assertTrue(result["uncertain"])
        self.assertEqual(result["uncertainty"][0]["code"], "shaders_compiling")
        self.assertEqual(result["material_preview"]["shader_compilation"], "SYNCHRONOUS_VIEWPORT_IMAGE_RENDER")
        self.assertFalse(self.run_view(shading="MATERIAL")["views"][0]["cache_hit"])
        self.env.state["compiling"] = False
        self.env.state["revision"] = None
        unproven = self.run_view()
        self.assertEqual((unproven["targets"][0]["revision"], unproven["targets"][0]["revision_exact"]), (None, False))
        self.assertIn("live_sculpt_surface", unproven["targets"][0]["revision_limitation"])
        self.assertFalse(self.run_view()["views"][0]["cache_hit"])
        self.env.state["revision"] = "r" * 64
        visible = self.run_view(views=["TOP"])
        self.env.target.hidden = True
        with self.assertRaisesRegex(RuntimeError, "nothing to frame"):
            self.run_view()
        hidden = self.run_view(views=[visible["views"][0]["view_id"]])
        self.assertIn("target_hidden", [note["code"] for note in hidden["uncertainty"]])

    def test_failures_restore_display_and_leave_no_reference_files(self):
        before = self.env.space.shading.snapshot()
        FakeOffScreen.fail = True
        with self.assertRaisesRegex(RuntimeError, "injected draw failure"):
            self.run_view(shading="MATERIAL")
        self.assertEqual(self.env.space.shading.snapshot(), before)
        self.assertFalse(list(self.references.directory.glob("*.png")) if self.references.directory.exists() else [])
        FakeOffScreen.fail = False
        self.env.window.modal_operators = [SimpleNamespace(bl_idname="TRANSFORM_OT_translate")]
        with self.assertRaisesRegex(RuntimeError, "Finish the current Blender interaction"):
            self.run_view()
        self.env.window.modal_operators = []
        with self.assertRaisesRegex(RuntimeError, "reference store"):
            self.capture.execute(self.env.window, {"views": ["FRONT"]}, None)
        self.env.state["epoch"] = "d" * 32
        original = self.env.queries.object_revision

        def rotate_epoch(window, obj):
            self.env.state["epoch"] = "e" * 32  # ID replacement while observing
            return original(window, obj)
        self.env.queries.object_revision = rotate_epoch
        with self.assertRaisesRegex(RuntimeError, "replaced during capture"):
            self.run_view()
        self.assertEqual(len(list(self.references.directory.glob("*.png"))), 0)

    def test_prefers_a_viewport_without_side_effects_and_reports_unavoidable_ones(self):
        shading = self.env.space.shading
        object.__setattr__(shading, "type", "RENDERED")
        result = self.run_view()
        self.assertEqual(result["side_effects"][0]["code"], "rendered_viewport_progress_reset")
        self.assertEqual(shading.type, "RENDERED")
        rendered_area = self.env.window.screen.areas[0]
        quiet = environment().space
        quiet_area = SimpleNamespace(type="VIEW_3D", spaces=SimpleNamespace(active=quiet), regions=rendered_area.regions)
        self.env.window.screen.areas = [rendered_area, quiet_area]
        shading.writes.clear()
        result = self.run_view(views=["TOP"])
        self.assertEqual(result["side_effects"], [])
        self.assertEqual(FakeOffScreen.draws[-1]["type"], "SOLID")
        self.assertEqual(shading.writes, [], "The Rendered viewport is not touched when another one can draw")

    def test_missing_best_effort_studio_light_is_reported_not_fatal(self):
        with patch.dict(self.capture.SHADING, {"MATERIAL": tuple(
                ("studio_light", "missing.exr") if key == "studio_light" else (key, value)
                for key, value in self.capture.SHADING["MATERIAL"])}):
            result = self.run_view(shading="MATERIAL")
        self.assertIn("studio_light_unavailable", [note["code"] for note in result["uncertainty"]])
        self.assertEqual(self.env.space.shading.fields["lookdev"], "city.exr")


if __name__ == "__main__":
    unittest.main()
