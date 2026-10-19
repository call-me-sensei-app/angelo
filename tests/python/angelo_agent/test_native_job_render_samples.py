# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Production admission measures effective layer samples, summed work and explicit overrides."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_view_render_admission as admission
from rendered_view_case import LookCase


class ProductionSamplesTests(LookCase):
    configure = admission.LayerAdmissionTests.configure
    layer_state = admission.LayerAdmissionTests.layer_state

    def measure(self, **arguments):
        from angelo_agent.job_runners import RenderRunner
        runner = RenderRunner.__new__(RenderRunner)
        runner.scene, runner.window, runner.plan = self.blender.scene, self.blender.window, None
        runner.request = dict(kind="RENDER_STILL", format="PNG", output="a.png", resolution=[256, 144], **arguments)
        return runner._measure()

    def test_inherited_over_limit_refuses_and_explicit_count_really_caps_it(self):
        from angelo_agent import job_render_samples, job_settings
        self.configure("USE", 1, [20000])
        with self.assertRaisesRegex(ValueError, "20,000"):
            self.measure()
        self.assertEqual(self.measure(samples=1)["samples"], 1)
        before = self.layer_state()
        overrides = job_settings.Overrides(self.blender.scene, "test")
        values = {"cycles.samples": 1, **job_render_samples.settings(self.blender.window, 1)}
        overrides.apply(values)
        self.assertEqual(job_render_samples.scene_samples(self.blender.window), 20000,
                         "queued admission reads original values, not the running job's overrides")
        scene = self.blender.scene
        self.assertEqual((scene.cycles.samples, scene.cycles.use_layer_samples), (1, "BOUNDED"))
        self.assertFalse(scene.cycles.use_denoising or scene.view_layers[0].cycles.use_denoising)
        self.assertEqual(overrides.restore(), [])
        self.assertEqual(self.layer_state(), before)

    def test_explicit_count_can_raise_quality_and_preserves_lower_nonzero_overrides(self):
        from angelo_agent import job_render_samples, job_settings
        for mode, expected in (("USE", [32, 8, 32]), ("BOUNDED", [32, 8, 32]), ("IGNORE", [32, 32, 32])):
            with self.subTest(mode=mode):
                scene = self.configure(mode, 4, [128, 8, 0])
                facts = self.measure(samples=32)
                self.assertEqual([row["samples"] for row in facts["layer_samples"]], expected)
                before = self.layer_state()
                overrides = job_settings.Overrides(scene, "test")
                overrides.apply({"cycles.samples": 32, **job_render_samples.settings(self.blender.window, 32)})
                actual = [scene.cycles.samples if scene.cycles.use_layer_samples == "IGNORE" else
                          min(layer.samples or scene.cycles.samples, scene.cycles.samples) for layer in scene.view_layers]
                self.assertEqual(actual, expected)
                self.assertEqual(overrides.restore(), [])
                self.assertEqual(self.layer_state(), before)

    def test_work_limit_sums_layers_and_single_layer_selection_includes_disabled_active(self):
        from angelo_agent.job_runners import RenderRunner
        self.configure("IGNORE", 16384, [0, 0])
        runner = RenderRunner.__new__(RenderRunner)
        runner.scene, runner.window, runner.plan = self.blender.scene, self.blender.window, None
        runner.request = dict(kind="RENDER_ANIMATION", format="PNG", output="frames", resolution=[4096, 4096],
                              frame_start=1, frame_end=60)
        with self.assertRaisesRegex(ValueError, "too large"):
            runner._measure()
        self.blender.scene.view_layers[1].use = False
        self.assertEqual(runner._measure()["samples"], 16384)
        self.blender.scene.view_layers[0].use = False
        self.blender.scene.view_layers[1].use = True
        self.blender.scene.render.use_single_layer = True
        facts = runner._measure()
        self.assertEqual(len(facts["layer_samples"]), 1)
        self.assertEqual(facts["layer_samples"][0]["name"], self.blender.window.view_layer.name)

    def test_queued_admission_uses_original_mode_samples_and_engine(self):
        from angelo_agent import job_settings
        scene = self.configure("USE", 50000, [0])
        overrides = job_settings.Overrides(scene, "running-look")
        overrides.apply({"cycles.samples": 32, "cycles.use_layer_samples": "BOUNDED"})
        with self.assertRaisesRegex(ValueError, "50,000"):
            self.measure()
        self.assertEqual(overrides.restore(), [])
        scene.render.engine = "BLENDER_EEVEE"
        overrides.apply({"render.engine": "CYCLES"})
        self.assertEqual(self.measure()["samples"], 64)
        self.assertEqual(overrides.restore(), [])

    def test_approval_and_details_show_each_rendered_layer_without_new_schema(self):
        from angelo_agent import job_approvals, job_chat
        self.configure("USE", 64, [8, 32])
        facts = self.measure()
        args = {"kind": "RENDER_STILL", "output": "a.png", "_facts": facts}
        body = job_approvals.message("angelo_job_start", args)["body"]
        detail = job_chat.details_text({"id": "j1", "request": args, "detail": facts, "receipt": {}})
        for row in facts["layer_samples"]:
            for text in (body, detail):
                self.assertIn(row["name"], text)
                self.assertIn("%d samples" % row["samples"], text)

    def test_normal_single_layer_receipt_keeps_existing_shape(self):
        self.configure("IGNORE", 64, [0])
        self.assertEqual(self.measure(), {"engine": "CYCLES", "resolution": [256, 144], "samples": 64,
                                          "frames": [12, 12]})


if __name__ == "__main__":
    unittest.main()
