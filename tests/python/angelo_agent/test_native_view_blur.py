# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Viewport looks of a scene that renders with motion blur draw none; a render says it has it.

EEVEE blurs a viewport image render by the motion since the previous draw on
the same offscreen (``view_blur``). Recorded one-shot bridge trials with an
animated car (friction scan G08: Sol 3 of 8, Astra 2 of 4) saw the second and
third LIT views of one call smeared, the first clean. These pin, over the
stand-ins of ``test_native_view_capture``, that every MATERIAL or LIT draw then
gets an offscreen of its own (one alive at a time), that Workbench looks and
scenes without blur keep reusing one, and the facts the results state.
``test_blender_view_motion_blur.py`` measures the pixels in the compiled app.
"""

import unittest
from types import SimpleNamespace

import test_native_view_capture as capture_tests  # noqa: E402
from test_native_view_capture import FakeOffScreen  # noqa: E402
from test_native_view_motion import StripCase  # noqa: E402
from rendered_view_case import LookCase  # noqa: E402
from angelo_agent import view_blur  # noqa: E402


class CountingOffScreen(FakeOffScreen):
    """Records which offscreen drew each view and how many were alive at once."""
    made, alive, peak, drawn_by = 0, 0, 0, []

    def __init__(self, width, height):
        super().__init__(width, height)
        CountingOffScreen.made += 1
        self.number = CountingOffScreen.made
        CountingOffScreen.alive += 1
        CountingOffScreen.peak = max(CountingOffScreen.peak, CountingOffScreen.alive)
        self.freed = False

    def draw_view3d(self, *args, **kwargs):
        CountingOffScreen.drawn_by.append(self.number)
        return super().draw_view3d(*args, **kwargs)

    def free(self):
        assert not self.freed, "freed twice"
        self.freed = True
        CountingOffScreen.alive -= 1

    @classmethod
    def reset(cls):
        cls.made, cls.alive, cls.peak, cls.drawn_by = 0, 0, 0, []


def blurring(env, scene_on=True, layer_on=True):
    env.window.scene.render.use_motion_blur = scene_on
    env.window.scene.render.motion_blur_shutter = 0.5
    env.window.scene.render.motion_blur_position = "CENTER"
    env.window.view_layer.use_motion_blur = layer_on


class ViewBlurTests(unittest.TestCase):
    # The capture stand-ins' fixture, without inheriting (and rerunning) their tests.
    _capture_setup, tearDown = capture_tests.ViewCaptureTests.setUp, capture_tests.ViewCaptureTests.tearDown
    run_view = capture_tests.ViewCaptureTests.run_view

    def setUp(self):
        self._capture_setup()
        self.env.gpu.types.GPUOffScreen = CountingOffScreen
        CountingOffScreen.reset()

    def three(self, shading):
        return self.run_view(views=["FRONT", "TOP", "ISO"], shading=shading)

    def test_each_eevee_view_of_a_blurring_scene_draws_on_its_own_offscreen(self):
        blurring(self.env)
        for shading, key in (("LIT", "lit_preview"), ("MATERIAL", "material_preview")):
            with self.subTest(shading=shading):
                CountingOffScreen.reset()
                result = self.three(shading)
                self.assertEqual(len(set(CountingOffScreen.drawn_by)), 3, "no view draws where another drew")
                self.assertEqual((CountingOffScreen.peak, CountingOffScreen.alive), (1, 0), "one at a time, all freed")
                self.assertEqual(result[key]["motion_blur"], view_blur.NOT_DRAWN)

    def test_workbench_looks_and_scenes_without_blur_keep_one_offscreen(self):
        cases = [("SOLID", True, True), ("WIREFRAME", True, True), ("LIT", False, True), ("LIT", True, False)]
        for shading, scene_on, layer_on in cases:
            with self.subTest(shading=shading, scene=scene_on, layer=layer_on):
                CountingOffScreen.reset()
                blurring(self.env, scene_on, layer_on)
                result = self.three(shading)
                self.assertEqual(CountingOffScreen.drawn_by, [1, 1, 1])
                self.assertNotIn("motion_blur", result.get("lit_preview", {}))

    def test_without_the_setting_nothing_changes(self):
        """The stand-in render has no use_motion_blur at all: today's single offscreen and result."""
        result = self.three("LIT")
        self.assertEqual(CountingOffScreen.drawn_by, [1, 1, 1])
        self.assertNotIn("motion_blur", result["lit_preview"])

    def test_the_persons_settings_are_never_written(self):
        blurring(self.env)
        render = vars(self.env.window.scene.render).copy()
        self.three("LIT")
        self.assertEqual(vars(self.env.window.scene.render), render)
        self.assertTrue(self.env.window.view_layer.use_motion_blur)


class StripBlurTests(StripCase):
    def setUp(self):
        super().setUp()
        self.env.gpu.types.GPUOffScreen = CountingOffScreen
        CountingOffScreen.reset()

    def test_each_tile_of_a_blurring_eevee_strip_draws_on_a_new_offscreen(self):
        blurring(self.env)
        result = self.strip(shading="LIT")
        self.assertEqual(CountingOffScreen.drawn_by, [1, 2, 3])
        self.assertEqual((CountingOffScreen.peak, CountingOffScreen.alive), (1, 0))
        self.assertEqual(result["views"][0]["motion_blur"], view_blur.NOT_DRAWN)

    def test_a_solid_strip_keeps_one_offscreen(self):
        blurring(self.env)
        result = self.strip()
        self.assertEqual(CountingOffScreen.drawn_by, [1, 1, 1])
        self.assertNotIn("motion_blur", result["views"][0])


class RenderedBlurTests(unittest.TestCase):
    def scene(self, on=True, shutter=0.35):
        render = SimpleNamespace(use_motion_blur=on, motion_blur_shutter=shutter, motion_blur_position="START")
        return SimpleNamespace(render=render), SimpleNamespace(use_motion_blur=True)

    def test_a_render_states_the_blur_its_engine_applies(self):
        scene, layer = self.scene()
        for engine in ("BLENDER_EEVEE", "CYCLES"):
            self.assertEqual(view_blur.rendered_blur(scene, layer, engine), {"shutter": 0.35, "position": "START"})
        self.assertIsNone(view_blur.rendered_blur(scene, layer, "BLENDER_WORKBENCH"))
        self.assertIsNone(view_blur.rendered_blur(*self.scene(on=False), "CYCLES"))
        layer.use_motion_blur = False
        self.assertIsNone(view_blur.rendered_blur(scene, layer, "CYCLES"))


class RenderedLookBlurTests(LookCase):
    def test_a_rendered_look_of_a_blurring_scene_says_it_is_blurred(self):
        self.blender.scene.render.values.update(use_motion_blur=True, motion_blur_shutter=0.5,
                                                motion_blur_position="CENTER")
        before = self.blender.scene.snapshot()
        _, result = self.run_look()
        self.assertEqual(result["rendered"]["motion_blur"], {"shutter": 0.5, "position": "CENTER"})
        self.assertIn("renders with motion blur", result["note"])
        self.assertEqual(self.blender.scene.snapshot(), before, "the blur setting is the person's and stays")

    def test_without_blur_the_rendered_result_is_unchanged(self):
        _, result = self.run_look()
        self.assertNotIn("motion_blur", result["rendered"])
        self.assertNotIn("motion blur", result["note"])


if __name__ == "__main__":
    unittest.main()
