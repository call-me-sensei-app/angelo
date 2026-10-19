# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Motion strips of ``angelo_view`` (``frames``) on the host (doc/angelo/view_sources.rst, "Motion strips").

``view_capture`` and ``view_motion`` run over the stand-ins of
``test_native_view_capture`` with a scene whose target moves with its frame.
These pin the held camera fitted over every frame, the frame and display put
back after success, failure and a time-out, the refusals and the strip's
layout. ``test_blender_view_sources`` draws a real animated strip.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_fakes  # noqa: E402
from angelo_agent import view_cache, view_motion, view_spec  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from test_native_view_capture import HANDLE, FakeMatrix, FakeOffScreen, environment  # noqa: E402
from rendered_view_fakes import Collection, msgbus_listing  # noqa: E402


class MovingScene:
    """The stand-in scene of ``environment`` whose target moves one unit along X a frame."""

    def __init__(self, base, instance):
        self.__dict__.update(vars(base))
        self.frame_subframe, self.instance, self.sets = 0.25, instance, []
        self.render = SimpleNamespace(engine="CYCLES", film_transparent=False, resolution_x=1920, resolution_y=1080,
                                      pixel_aspect_x=1.0, pixel_aspect_y=1.0)
        self.objects, self.rigidbody_world = [], None

    def frame_set(self, frame, subframe=0.0):
        self.sets.append((frame, subframe))
        self.frame_current, self.frame_subframe = frame, subframe
        self.instance.matrix_world = FakeMatrix([[1, 0, 0, frame], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


class StripCase(unittest.TestCase):
    def setUp(self):
        self.env = environment()
        instance = self.env.window.view_layer.depsgraph.object_instances[0]
        self.scene = MovingScene(self.env.window.scene, instance)
        self.scene.frame_current = 7
        self.env.window.scene = self.scene
        self.env.window.screen.is_animation_playing = False
        self.handlers = SimpleNamespace(frame_change_pre=[], frame_change_post=[])
        self.env.bpy.app.handlers = self.handlers
        self.active_job = None
        job_ui = ModuleType("angelo_agent.job_ui")
        job_ui.manager = lambda create=True: SimpleNamespace(active=lambda: self.active_job)
        self.msgbus = []  # bpy.msgbus subscribers, as the application lists them (_bpy._angelo_jobs).
        native = ModuleType("_bpy")
        native._angelo_jobs = SimpleNamespace(msgbus_subscribers=msgbus_listing(self.msgbus))
        self.modules = patch.dict(sys.modules, {"bpy": self.env.bpy, "gpu": self.env.gpu, "mathutils": self.env.mathutils,
                                                "angelo_agent.job_ui": job_ui, "_bpy": native})
        self.modules.start()
        sys.modules.pop("angelo_agent.view_capture", None)
        self.capture = importlib.import_module("angelo_agent.view_capture")
        self.patches = [patch.object(self.capture, "scene_queries", self.env.queries),
                        patch("angelo_agent.job_ui", job_ui, create=True),
                        patch("angelo_agent.scene_queries", self.env.queries, create=True)]
        for item in self.patches:
            item.start()
        self.directory = tempfile.TemporaryDirectory()
        self.references = ReferenceStore(self.directory.name, "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        FakeOffScreen.draws, FakeOffScreen.fail = [], False
        view_cache.clear()

    def tearDown(self):
        view_cache.clear()
        for item in reversed(self.patches):
            item.stop()
        self.modules.stop()
        self.directory.cleanup()

    def strip(self, **args):
        return self.capture.execute(self.env.window, dict({"targets": [HANDLE], "views": ["FRONT"], "frames": [1, 5, 9]},
                                                          **args), self.references)


class StripTests(StripCase):
    def test_a_held_camera_is_fitted_over_every_frame_and_the_frame_is_put_back(self):
        shading = self.env.space.shading
        before = shading.snapshot()
        result = self.strip(resolution=512)
        self.assertEqual([draw["view"] for draw in FakeOffScreen.draws], [FakeOffScreen.draws[0]["view"]] * 3,
                         "one camera for every frame")
        self.assertEqual(self.scene.sets[-1], (7, 0.25), "frame and subframe put back")
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (7, 0.25))
        self.assertEqual(shading.snapshot(), before)
        view = result["views"][0]
        self.assertEqual((view["framing"], view["frames"], view["grid"], view["shading"]), ("STRIP", [1, 5, 9], [3, 1], "SOLID"))
        centre = view["camera"]["center_world"][0]
        self.assertAlmostEqual(centre, 5.0, places=5, msg="the middle of the union of the target's positions")
        self.assertEqual((result["frame"], result["missing_frames"], result["uncertain"]), (7, [], False))
        self.assertIn("Frames 1, 5, 9 drawn as a SOLID viewport look", result["note"])
        self.assertIn("The scene is back at frame 7", result["note"])
        picture = picture_fakes.read_png(Path(view["path"]).read_bytes())
        self.assertEqual(picture.shape[:2], (169, 3 * 169 + 2 * 2), "three square tiles and their gaps")
        self.assertEqual(picture[4, 6, :3].tolist(), [255, 255, 255], "the first tile's label: the 1's stem")
        reused = self.capture.execute(self.env.window, {"targets": [HANDLE], "views": [view["view_id"]]}, self.references)
        self.assertEqual(reused["views"][0]["camera"]["matrix_world"], view["camera"]["matrix_world"])

    def test_the_scene_camera_follows_each_frame(self):
        frames_seen = []

        def scene_camera(window, epoch, resolution):
            frames_seen.append(window.scene.frame_current)
            return {"view_matrix": [[1, 0, 0, -window.scene.frame_current], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                    "projection_matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "aspect": 16 / 9}
        with patch.object(self.capture, "_scene_camera", scene_camera):
            result = self.strip(views=["SCENE_CAMERA"], frames=[2, 4])
        self.assertEqual(frames_seen, [2, 4])
        self.assertEqual(len({str(draw["view"]) for draw in FakeOffScreen.draws}), 2)
        self.assertEqual(result["views"][0]["camera"], {"source": "SCENE_CAMERA", "per_frame": True})
        self.assertEqual(result["views"][0]["grid"], [2, 1])

    def test_a_failed_draw_and_a_time_out_still_put_the_frame_and_display_back(self):
        before = self.env.space.shading.snapshot()
        FakeOffScreen.fail = True
        with self.assertRaisesRegex(RuntimeError, "injected draw failure"):
            self.strip()
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe, self.env.space.shading.snapshot()),
                         (7, 0.25, before))
        FakeOffScreen.fail = False
        with patch.object(view_motion, "STRIP_SECONDS", 0.0):
            result = self.strip(frames=[1, 2, 3, 4])
        self.assertEqual(result["missing_frames"], [2, 3, 4])
        self.assertEqual([entry["code"] for entry in result["uncertainty"]], ["strip_truncated"])
        self.assertEqual(self.scene.frame_current, 7)

    def test_what_scrubbing_runs_is_reported(self):
        from angelo_agent.render_extensions import host_handler
        self.handlers.frame_change_post.append(host_handler(lambda *args: None))  # Not extension code.
        self.scene.objects = [SimpleNamespace(modifiers=[SimpleNamespace(type="CLOTH")])]
        result = self.strip()
        self.assertEqual([entry["code"] for entry in result["side_effects"]], ["frame_change_handlers_ran"])
        self.assertEqual([entry["code"] for entry in result["uncertainty"]], ["simulation_frames"])

    def test_playback_an_interaction_and_a_running_job_are_refused_before_any_frame_changes(self):
        cases = ((self.env.window.screen, "is_animation_playing", True, "is playing"),
                 (self.env.window, "modal_operators", [SimpleNamespace(bl_idname="TRANSFORM_OT_translate")],
                  "interaction"),
                 (self, "active_job", {"id": "j2"}, "job is running"))
        for owner, name, value, pattern in cases:
            with self.subTest(pattern=pattern):
                old = getattr(owner, name)
                setattr(owner, name, value)
                try:
                    with self.assertRaisesRegex(RuntimeError, pattern):
                        self.strip()
                finally:
                    setattr(owner, name, old)
                self.assertEqual(self.scene.sets, [])


def code_from(filename, name="on_frame"):
    """A handler whose code lives in ``filename``, as an add-on module's would."""
    namespace = {"__name__": "addon_module"}
    exec(compile("def %s(*args):\n    return None\n" % name, filename, "exec"), namespace)
    return namespace[name]


class ExtensionTests(StripCase):
    """A strip changes the frame without approval: add-on or script Python that would run refuses it, as for looks."""

    def report(self, extension):
        return SimpleNamespace(any=extension, summary=lambda: "1 handler(s): addon_module.on_frame (frame_change_post)",
                               blocking=lambda mode: extension, depsgraph_handlers=[], view_python=[], subscribers=[])

    def test_python_that_would_run_refuses_the_strip_before_any_frame_changes(self):
        with patch("angelo_agent.render_extensions.inspect", return_value=self.report(True)) as inspect:
            with self.assertRaisesRegex(RuntimeError, "Changing this scene's frame, or the viewport display a "
                                                      r"strip sets, runs Python from add-ons or scripts \(1 handler"):
                self.strip()
        self.assertEqual(inspect.call_args[0][1], "FRAME_BAKE", "the frame-change handlers and the drivers")
        self.assertEqual((self.scene.sets, FakeOffScreen.draws), ([], []))

    def test_the_policy_is_checked_again_just_before_the_first_frame_change(self):
        answers = iter([self.report(False), self.report(True)])
        with patch("angelo_agent.render_extensions.inspect", side_effect=lambda scene, kind, written: next(answers)):
            with self.assertRaisesRegex(RuntimeError, "angelo_job_start RENDER_ANIMATION over those frames") as caught:
                self.strip()
        self.assertIn("angelo_view with shading RENDERED, image set to its job ID and frame", str(caught.exception))
        self.assertEqual(self.scene.sets, [], "refused at start: no frame was changed")

    def test_only_code_that_does_not_ship_refuses_and_shipped_handlers_are_reported(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.env.bpy.utils = SimpleNamespace(system_resource=lambda kind: str(Path(temp.name) / "shipped"))
        user = code_from(str(Path(temp.name) / "user" / "addon" / "__init__.py"))
        shipped = code_from(str(Path(temp.name) / "shipped" / "addons_core" / "tool" / "__init__.py"), "shipped")
        self.handlers.frame_change_post.append(user)
        with self.assertRaisesRegex(RuntimeError, r"addon_module\.on_frame \(frame_change_post\)"):
            self.strip()
        self.handlers.frame_change_post[:] = [shipped]
        result = self.strip()
        self.assertEqual([entry["code"] for entry in result["side_effects"]], ["frame_change_handlers_ran"])
        self.assertIn("ship with the application", result["side_effects"][0]["detail"])


class DepsgraphHandlerTests(StripCase):
    """Frame changes run add-on depsgraph handlers: refused in Read-only and Ask, named and checked in Auto."""

    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.handlers.depsgraph_update_pre, self.handlers.depsgraph_update_post = [], [
            code_from(str(Path(temp.name) / "community_addon.py"), "on_update")]
        self.env.bpy.data = SimpleNamespace(images=Collection())

    def auto_strip(self):
        controller = SimpleNamespace(state={"id": "chat", "options": {"approval": "AUTO_BOUNDED"}})
        return self.capture.execute(self.env.window, {"targets": [HANDLE], "views": ["FRONT"], "frames": [1, 5, 9]},
                                    self.references, controller)

    def test_read_only_and_ask_refuse_before_any_frame_changes(self):
        with self.assertRaisesRegex(RuntimeError, r"addon_module\.on_update \(depsgraph_update_post\)"):
            self.strip()  # No controller: Ask.
        controller = SimpleNamespace(state={"id": "chat", "options": {"approval": "READ_ONLY"}})
        with self.assertRaisesRegex(RuntimeError, "This chat is Read-only"):
            self.capture.execute(self.env.window, {"targets": [HANDLE], "views": ["FRONT"], "frames": [1, 5, 9]},
                                 self.references, controller)
        self.assertEqual((self.scene.sets, FakeOffScreen.draws), ([], []))

    def test_auto_draws_the_strip_names_the_handler_and_never_claims_the_scene_untouched(self):
        result = self.auto_strip()
        self.assertEqual(self.scene.frame_current, 7)
        self.assertIsNone(result["scene_changed"])
        self.assertIn("extension_handlers_ran", [entry["code"] for entry in result["side_effects"]])
        self.assertIn("scene_change_unverified", [entry["code"] for entry in result["uncertainty"]])

    def test_auto_reports_what_the_handler_changed(self):
        frame_set = self.scene.frame_set

        def handler_adds_an_image(frame, subframe=0.0):  # As the add-on's handler does on each frame change.
            frame_set(frame, subframe)
            if not self.env.bpy.data.images:
                self.env.bpy.data.images.append(SimpleNamespace(name="Planted", session_uid=40, type="IMAGE"))
        self.scene.frame_set = handler_adds_an_image
        result = self.auto_strip()
        self.assertIs(result["scene_changed"], True)
        self.assertEqual(result["changed_during_view"], ["added image Planted"])


class MessageBusTests(StripCase):
    """A strip's viewport settings notify ``bpy.msgbus`` subscribers after it ends: refused or named, by mode."""

    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.msgbus.append(("View3DShading", "type", code_from(str(Path(temp.name) / "addon.py"), "on_shading")))
        self.env.bpy.data = SimpleNamespace(images=Collection())

    def strip_in(self, approval):
        controller = SimpleNamespace(state={"id": "chat", "options": {"approval": approval}})
        return self.capture.execute(self.env.window, {"targets": [HANDLE], "views": ["FRONT"], "frames": [1, 5, 9]},
                                    self.references, controller)

    def test_read_only_and_ask_refuse_before_any_frame_changes(self):
        for approval in ("READ_ONLY", "ASK"):
            with self.subTest(approval=approval):
                with self.assertRaisesRegex(RuntimeError, r"on_shading \(message bus: View3DShading.type\)"):
                    self.strip_in(approval)
                self.assertEqual((self.scene.sets, FakeOffScreen.draws), ([], []))

    def test_auto_draws_the_strip_names_them_and_never_says_unchanged(self):
        result = self.strip_in("AUTO_BOUNDED")
        self.assertIsNone(result["scene_changed"], "they run after the strip ends")
        self.assertIn("message bus: View3DShading.type", result["side_effects"][-1]["detail"])

    def test_a_subscriber_to_a_setting_the_strip_leaves_alone_does_not_count(self):
        self.msgbus[:] = [("RenderSettings", "resolution_x", self.msgbus[0][2])]
        self.assertIs(self.strip_in("READ_ONLY")["scene_changed"], False)


class LayoutTests(unittest.TestCase):
    def test_tiles_fill_the_strip_in_reading_order_with_labels(self):
        size = view_motion.tile_size(512, 1.0, 3, 2)
        self.assertEqual(size, (169, 169))
        tiles = {frame: numpy.full((169, 169, 4), (frame * 20, 0, 0, 255), numpy.uint8) for frame in (1, 2, 3, 4)}
        sheet = view_motion.compose(tiles, [1, 2, 3, 4, 5], 3, 2, size)
        self.assertEqual(sheet.shape[:2], (169 * 2 + 2, 169 * 3 + 4))
        self.assertEqual(sheet[100, 100 + 171, 0], 40, "frame 2 is the second tile of the first row")
        self.assertEqual(sheet[100, 100, 0], 20)
        self.assertEqual(sheet[171 + 100, 171 + 100, :3].tolist(), [24, 24, 24], "frame 5 was not drawn")
        self.assertEqual(sheet[4, 6, :3].tolist(), [255, 255, 255], "label ink: the 1's stem")
        self.assertEqual(sheet[4, 4, :3].tolist(), [20, 20, 20], "the label's dark box")


class ValidationTests(unittest.TestCase):
    def validate(self, **arguments):
        return view_spec.validate_view("angelo_view", dict({"views": ["FRONT"]}, **arguments))

    def test_frames_are_bounded_unique_and_for_one_viewport_look(self):
        checked = self.validate(frames=[1, 12, -3], shading="LIT")
        self.assertEqual(checked["frames"], [1, 12, -3])
        self.assertEqual(view_spec.validate_view("angelo_view", checked), checked)
        for arguments, pattern in (({"frames": [1]}, "2 to 8 frame numbers"),
                                   ({"frames": list(range(9))}, "2 to 8 frame numbers"),
                                   ({"frames": [1, 1]}, "Repeated frame"),
                                   ({"frames": [1, 2.5]}, "2 to 8 frame numbers"),
                                   ({"frames": [1, 2], "views": ["FRONT", "TOP"]}, "one views item"),
                                   ({"frames": [1, 2], "shading": "WEIGHT", "targets": [HANDLE]}, "viewport look"),
                                   ({"frames": [1, 2], "shading": "RENDERED", "views": ["SCENE_CAMERA"]}, "viewport look")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, pattern):
                self.validate(**arguments)


if __name__ == "__main__":
    unittest.main()
