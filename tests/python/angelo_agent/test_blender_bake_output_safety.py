# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recovery over native image buffers and suffix admission over native compositor nodes.

Run through ``run_native_suites.py --include test_blender_bake_output_safety``
or an isolated compiled Angelo using --background --factory-startup
--python-exit-code 1 --python <this file> -- <receipt.json>. The standard runner
uses ANGELO_QA_DIR/bake-output-safety.json. No render or bake job runs: image
writes plant the mixed completion state directly. Actual job completion and
GUI controls remain the production bake/render suites' remit.
"""

from array import array
import json
import os
from pathlib import Path
import sys
import unittest

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import job_bakes, job_bake_targets, job_render_outputs, job_render_scene  # noqa: E402
from angelo_agent.job_spec import JobRequestError  # noqa: E402


class NativeSafetyTests(unittest.TestCase):
    def image(self, name, floating):
        image = bpy.data.images.new(name, 2, 2, alpha=True, float_buffer=floating)
        image.pixels.foreach_set(array("f", [0.1, 0.2, 0.3, 1.0]) * 4)
        image.update()
        self.addCleanup(bpy.data.images.remove, image)
        return image

    def runner(self, floating, changed):
        runner = job_bakes.TextureBakeRunner.__new__(job_bakes.TextureBakeRunner)
        runner.images = [self.image("BakeEvidence%d" % index, floating) for index in range(len(changed))]
        runner.clean = [False] * len(changed)  # Real unsaved pixels are compared with the bounded snapshots.
        runner.snapshots = job_bake_targets.snapshot(runner.images)
        before = [values[:] for _image, values in runner.snapshots]
        for image, wrote in zip(runner.images, changed):
            if wrote:
                image.pixels.foreach_set(array("f", [1.0, 0.5, 0.0, 1.0]) * 4)
                image.update()
        runner.created = runner.staging = runner.output = None
        runner.outcome, runner.restorable, runner.reload = "complete", True, []
        runner._outputs = lambda: []
        return runner, before

    def test_mixed_and_cancelled_results_restore_float_and_byte_pixels(self):
        for floating in (False, True):
            for stopped in (False, True):
                with self.subTest(floating=floating, stopped=stopped):
                    runner, before = self.runner(floating, [True, False])
                    self.assertEqual(runner._evidence(), [True, False])
                    result = runner._finish(stopped)
                    self.assertEqual((result[0], result[2]["note"]),
                                     ("cancelled" if stopped else "failed", job_bakes.PUT_BACK))
                    self.assertEqual([values for _image, values in job_bake_targets.snapshot(runner.images)], before)
                    self.assertEqual(runner.snapshots, [])

    def test_success_and_untouched_controls_preserve_pixels(self):
        for changed in ([True, True], [False, False]):
            with self.subTest(changed=changed):
                runner, _before = self.runner(True, changed)
                expected = job_bake_targets.snapshot(runner.images)
                result = runner._finish(False)
                self.assertEqual(result[0], "done" if all(changed) else "failed")
                self.assertEqual(job_bake_targets.snapshot(runner.images), expected)

    def test_resized_native_image_reports_partial_and_restores_other_image(self):
        runner, before = self.runner(True, [True, True, False])
        runner.images[0].scale(4, 4)
        result = runner._finish(False)
        self.assertEqual((result[0], result[2]["note"]), ("failed", job_bakes.PARTIAL))
        self.assertEqual(job_bake_targets.snapshot([runner.images[1]])[0][1], before[1])
        self.assertEqual(tuple(runner.images[0].size), (4, 4))

    def test_native_compositor_individual_formats_validate_suffixes(self):
        scene = bpy.data.scenes.new("SuffixAdmission")
        self.addCleanup(bpy.data.scenes.remove, scene)
        scene.render.use_multiview = True
        scene.render.views_format = "STEREO_3D"
        scene.render.image_settings.file_format = "OPEN_EXR"
        scene.render.image_settings.views_format = "MULTIVIEW"
        tree = bpy.data.node_groups.new("SuffixAdmission", "CompositorNodeTree")
        self.addCleanup(bpy.data.node_groups.remove, tree)
        scene.compositing_node_group = tree
        source = tree.nodes.new("CompositorNodeRLayers")
        output = tree.nodes.new("CompositorNodeOutputFile")
        output.name = "Passes"
        output.file_output_items.new("RGBA", "Beauty")
        tree.links.new(source.outputs["Image"], output.inputs["Beauty"])
        for file_format in ("PNG", "OPEN_EXR_MULTILAYER"):
            output.format.media_type = "MULTI_LAYER_IMAGE" if file_format == "OPEN_EXR_MULTILAYER" else "IMAGE"
            output.format.file_format = file_format
            output.format.views_format = "INDIVIDUAL"
            for left, right, message in (("/../../../escaped", "_R", "own file suffix"),
                                         ("_same", "_same", "own file suffix"),
                                         ("_L", "_l", "same file ignoring case"),
                                         ("_Straße", "_STRASSE", "same file ignoring case")):
                with self.subTest(format=file_format, suffixes=(left, right)):
                    scene.render.views["left"].file_suffix = left
                    scene.render.views["right"].file_suffix = right
                    with self.assertRaisesRegex(JobRequestError, message):
                        job_render_scene.Plan(scene, "OPEN_EXR")
            scene.render.views["left"].file_suffix = "_L"
            scene.render.views["right"].file_suffix = "_R"
            plan = job_render_scene.Plan(scene, "OPEN_EXR")
            self.assertEqual(len(plan.outputs), 3)
            names = [job_render_outputs.file_name(item, 1) for item in plan.outputs]
            self.assertEqual(names[0], "frame_0001.exr")
            self.assertTrue(names[1].startswith("Passes/"))


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "bake-output-safety.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeSafetyTests))
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": len(result.failures), "errors": len(result.errors),
                                   "scope": "native images and compositor admission; no renderer or bake job",
                                   "blender": bpy.app.version_string}, indent=2) + "\n")
    if not result.wasSuccessful():
        raise AssertionError("Native bake/output safety checks failed; see the receipt and process log")
