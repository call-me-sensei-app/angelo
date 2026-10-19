# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Animated render settings can neither override a job's own values nor slip past its bounds.

Review blocker (PR #64): a keyed ``cycles.samples`` (64 at frame 1, 128 at
frame 2) rendered 64 and 128 samples against an approved 8, because Blender
evaluates the scene's animation on every frame it renders. The same applies to
a look's 32-sample cap and to a layer count or the layer-sample policy. A job
now mutes the animation of every setting it sets for its run (and puts it back
with the settings, also from a saved file), and admission bounds an inherited
animated value by its greatest value on the job's frames. EEVEE layer counts,
which replace the scene's count, are capped as well.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_view_render_admission as admission  # noqa: E402
from animation_fakes import Curve, animate, driver  # noqa: E402
from rendered_view_case import LookCase  # noqa: E402
from angelo_agent import job_animated_settings as animated  # noqa: E402


def keyed_scene(**animation):
    scene = SimpleNamespace(name="Scene")
    animate(scene, **animation)
    return scene


class HoldTests(unittest.TestCase):
    def test_hold_mutes_every_source_of_a_setting_and_release_puts_them_back(self):
        keyed, other = Curve("cycles.samples", {1: 64, 2: 128}), Curve("render.fps", {1: 24})
        already = Curve("cycles.samples", {1: 1}, mute=True)
        driven = Curve("cycles.samples", driver=driver())
        on_strip = Curve("cycles.samples", {1: 4096})
        scene = keyed_scene(keyed=[keyed, other], drivers=[driven], nla=[[on_strip, already]])
        held = animated.hold(scene, ["cycles.samples"])
        self.assertTrue(keyed.mute and driven.mute and on_strip.mute)
        self.assertFalse(other.mute)
        self.assertEqual(len(held), 3, "an already muted channel is not the job's to unmute")
        self.assertEqual(animated.release(scene, held), [])
        self.assertFalse(keyed.mute or driven.mute or on_strip.mute)
        self.assertTrue(already.mute)

    def test_a_channel_gone_before_release_is_reported(self):
        keyed = Curve("cycles.samples", {1: 64})
        scene = keyed_scene(keyed=[keyed])
        held = animated.hold(scene, ["cycles.samples"])
        scene.animation_data.action.layers[0].strips[0].bag.fcurves.clear()
        self.assertEqual(animated.release(scene, held), ["animation of cycles.samples"])

    def test_bound_is_the_greatest_keyed_value_on_the_frames(self):
        scene = keyed_scene(keyed=[Curve("cycles.samples", {1: 64, 2: 128, 50: 20000})])
        self.assertEqual(animated.bound(scene, "cycles.samples", 64, (1, 2)), 128)
        self.assertEqual(animated.bound(scene, "cycles.samples", 64, (1, 60)), 20000)
        self.assertEqual(animated.bound(scene, "render.fps", 24, (1, 60)), 24, "not animated: the stored value")
        scene.animation_data.action.layers[0].strips[0].bag.fcurves[0].mute = True
        self.assertEqual(animated.bound(scene, "cycles.samples", 64, (1, 60)), 64, "a muted channel does not run")

    def test_values_known_only_while_rendering_refuse(self):
        for animation in ({"drivers": [Curve("cycles.samples", driver=driver())]},
                          {"nla": [[Curve("cycles.samples", {1: 8})]]},
                          {"keyed": [Curve("cycles.samples", {1: 8})], "blend": "ADD"}):
            with self.subTest(animation=sorted(animation)), self.assertRaises(animated.AnimationUnbounded) as caught:
                animated.bound(keyed_scene(**animation), "cycles.samples", 8, (1, 2))
            self.assertEqual(caught.exception.code, "UNSUPPORTED")
            self.assertIn("Give that value in the request", str(caught.exception))


class OverrideTests(LookCase):
    configure = admission.LayerAdmissionTests.configure

    def test_a_jobs_settings_hold_their_animation_and_a_saved_file_gets_it_back(self):
        from angelo_agent import job_settings
        scene = self.configure("IGNORE", 64, [0])
        keyed = Curve("cycles.samples", {1: 64, 2: 128})
        animate(scene, keyed=[keyed])
        overrides = job_settings.Overrides(scene, "j1")
        overrides.apply({"cycles.samples": 8})
        self.assertTrue(keyed.mute)
        self.assertIn('"held_channels"', scene[job_settings.TAG])
        self.blender.bpy.data.scenes = [scene]  # The file was saved meanwhile and opened again.
        self.assertEqual(job_settings.restore_tags(), ["Scene"])
        self.assertFalse(keyed.mute)
        self.assertEqual(scene.cycles.samples, 64)
        overrides.apply({"cycles.samples": 8})
        self.assertEqual(overrides.restore(), [])
        self.assertFalse(keyed.mute)
        self.assertNotIn(job_settings.TAG, scene)


class ProductionTests(LookCase):
    configure = admission.LayerAdmissionTests.configure

    def measure_job(self, **arguments):
        from angelo_agent.job_runners import RenderRunner
        runner = RenderRunner.__new__(RenderRunner)
        runner.scene, runner.window, runner.plan = self.blender.scene, self.blender.window, None
        request = dict(kind="RENDER_ANIMATION", format="PNG", output="frames", frame_start=1, frame_end=2)
        runner.request = dict(request, **arguments)
        return runner._measure()

    def test_inherited_animated_samples_and_resolution_count_at_their_greatest(self):
        scene = self.configure("IGNORE", 64, [0])
        animate(scene, keyed=[Curve("cycles.samples", {1: 64, 2: 128}), Curve("render.resolution_x", {1: 1920, 2: 3840})])
        facts = self.measure_job()
        self.assertEqual(facts["samples"], 128)
        self.assertEqual(facts["resolution"], [1920, 540], "the greatest width at the scene's 50%")
        self.assertEqual(self.measure_job(resolution=[64, 64])["resolution"], [64, 64])

    def test_an_inherited_value_over_the_bound_on_a_later_frame_refuses(self):
        scene = self.configure("IGNORE", 64, [0])
        animate(scene, keyed=[Curve("cycles.samples", {1: 64, 2: 20000})])
        with self.assertRaisesRegex(ValueError, "20,000"):
            self.measure_job()
        self.assertEqual(self.measure_job(samples=8)["samples"], 8, "an explicit count is held, so it is the bound")

    def test_driven_inherited_samples_or_policy_ask_for_an_explicit_count(self):
        scene = self.configure("USE", 64, [0])
        animate(scene, drivers=[Curve("cycles.samples", driver=driver())])
        with self.assertRaisesRegex(ValueError, "Give that value in the request"):
            self.measure_job()
        animate(scene, keyed=[Curve("cycles.use_layer_samples", {1: 0, 2: 1})])
        with self.assertRaisesRegex(ValueError, "Give samples in the request"):
            self.measure_job()
        self.assertEqual(self.measure_job(samples=16)["samples"], 16)

    def test_explicit_samples_hold_the_scene_count_policy_and_every_layer(self):
        from angelo_agent import job_render_samples, job_settings
        scene = self.configure("USE", 64, [128, 0])
        layer = scene.view_layers[0].path_from_id() + ".samples"
        curves = [Curve("cycles.samples", {1: 64}), Curve("cycles.use_layer_samples", {1: 0}), Curve(layer, {1: 4096})]
        animate(scene, keyed=curves)
        overrides = job_settings.Overrides(scene, "j1")
        overrides.apply({"cycles.samples": 8, **job_render_samples.settings(self.blender.window, 8)})
        self.assertTrue(all(curve.mute for curve in curves))
        self.assertEqual((scene.cycles.samples, scene.cycles.use_layer_samples, scene.view_layers[0].samples),
                         (8, "BOUNDED", 128))
        self.assertEqual(overrides.restore(), [])
        self.assertFalse(any(curve.mute for curve in curves))


class EeveeLayerTests(LookCase):
    engine = "BLENDER_EEVEE"
    image = dict(media_type="IMAGE", file_format="PNG", color_mode="RGBA", color_depth="16")

    def test_a_look_caps_an_eevee_layer_count_and_holds_it(self):
        scene = self.blender.scene
        scene.view_layers[0].values["samples"] = 4096
        keyed = Curve('view_layers["ViewLayer"].samples', {1: 4096, 12: 4096})
        animate(scene, keyed=[keyed])
        pending = self.begin()
        self.assertEqual((scene.eevee.taa_render_samples, scene.view_layers[0].samples), (32, 32))
        self.assertTrue(keyed.mute)
        self.blender.progress = 1.0
        self.manager.poll()
        self.blender.end()
        self.manager.poll()
        result = pending.poll()
        self.assertEqual((result["rendered"]["samples"], result["rendered"]["scene_samples"]), (32, 4096))
        self.assertEqual((scene.eevee.taa_render_samples, scene.view_layers[0].samples), (64, 4096))
        self.assertFalse(keyed.mute)

    def test_production_bounds_and_caps_an_eevee_layer_count(self):
        from angelo_agent import job_render_samples
        from angelo_agent.job_runners import RenderRunner
        scene = self.blender.scene
        scene.view_layers[0].values["samples"] = 20000
        runner = RenderRunner.__new__(RenderRunner)
        runner.scene, runner.window, runner.plan = scene, self.blender.window, None
        runner.request = dict(kind="RENDER_STILL", format="PNG", output="a.png", resolution=[64, 64])
        with self.assertRaisesRegex(ValueError, "20,000"):
            runner._measure()
        runner.request["samples"] = 8
        self.assertEqual(runner._measure()["samples"], 8)
        self.assertEqual(job_render_samples.settings(self.blender.window, 8),
                         {'view_layers["ViewLayer"].samples': 8})


class LookTests(LookCase):
    def test_a_keyed_scene_count_cannot_lift_a_looks_cap(self):
        scene = self.blender.scene
        keyed = Curve("cycles.samples", {1: 4096})
        animate(scene, keyed=[keyed])
        before = scene.snapshot()
        pending = self.begin()
        self.assertTrue(keyed.mute, "evaluating the animation while it renders would put 4096 back")
        self.assertEqual(scene.cycles.samples, 32)
        pending.cancel()
        self.blender.end(written=False)
        self.manager.poll()
        self.assertFalse(keyed.mute)
        self.assertEqual(scene.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
