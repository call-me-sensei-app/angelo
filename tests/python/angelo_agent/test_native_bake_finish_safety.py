# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mixed texture-bake results and verified recovery, with planted pixel buffers.

The runner, evidence and restoration code are real; no bake job or renderer runs.
``test_blender_bake_output_safety.py`` repeats the core cases with native images.
"""

from array import array
import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

BEFORE = array("f", [0.0, 0.0, 0.0, 1.0])
AFTER = array("f", [1.0, 0.5, 0.0, 1.0])


class Pixels(list):
    def foreach_get(self, values):
        values[:] = array("f", self)

    def foreach_set(self, values):
        self[:] = values


class Image:
    def __init__(self, name, changed=False):
        self.name, self.is_dirty = name, changed
        self.pixels = Pixels(AFTER if changed else BEFORE)
        self.updates = 0

    def update(self):
        self.updates += 1


class BakeFinishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(sys.modules, {"bpy": ModuleType("bpy")}):
            cls.bakes = importlib.import_module("angelo_agent.job_bakes")
            cls.targets = importlib.import_module("angelo_agent.job_bake_targets")

    def runner(self, changed, *, clean=True, restorable=True):
        runner = self.bakes.TextureBakeRunner.__new__(self.bakes.TextureBakeRunner)
        runner.images = [Image("Material%d" % index, wrote) for index, wrote in enumerate(changed)]
        runner.clean = [clean] * len(changed)
        runner.snapshots = [(image, BEFORE[:]) for image in runner.images] if restorable else []
        runner.created = runner.staging = runner.output = None
        runner.outcome, runner.restorable, runner.reload = "complete", restorable, []
        runner._outputs = lambda: []  # File naming is independent of the pixels under test.
        return runner

    def test_mixed_result_restores_every_image_before_discarding_backups(self):
        for clean in (True, False):
            with self.subTest(clean=clean):
                runner = self.runner([True, False], clean=clean)
                self.assertEqual(runner._evidence(), [True, False])
                status, reason, receipt = runner._finish(False)
                self.assertEqual((status, reason, receipt["note"]),
                                 ("failed", self.bakes.NOT_ALL_BAKED, self.bakes.PUT_BACK))
                self.assertTrue(all(image.pixels == list(BEFORE) for image in runner.images))
                self.assertEqual(runner.snapshots, [])

    def test_mixed_result_without_backups_reports_partial(self):
        runner = self.runner([True, False], restorable=False)
        self.assertEqual(runner._finish(False),
                         ("failed", self.bakes.NOT_ALL_BAKED, {"note": self.bakes.PARTIAL}))
        self.assertEqual(runner.images[0].pixels, list(AFTER))

    def test_unknown_and_untouched_result_does_not_claim_recovery(self):
        runner = self.runner([True, False], restorable=False)
        runner.clean[0] = False
        self.assertEqual(runner._evidence(), [None, False])
        self.assertEqual(runner._finish(False)[2]["note"], self.bakes.PARTIAL)

    def test_wholly_untouched_images_are_not_written_back(self):
        for clean, reason in ((True, self.bakes.NOT_BAKED), (False, self.bakes.UNCHANGED)):
            with self.subTest(clean=clean):
                runner = self.runner([False, False], clean=clean)
                self.assertEqual(runner._finish(False), ("failed", reason, {"note": self.bakes.PUT_BACK}))
                self.assertEqual([image.updates for image in runner.images], [0, 0])

    def test_success_retains_baked_pixels_and_releases_backups(self):
        runner = self.runner([True, True])
        self.assertEqual(runner._finish(False)[0], "done")
        self.assertTrue(all(image.pixels == list(AFTER) for image in runner.images))
        self.assertEqual(runner.snapshots, [])

    def test_cancel_and_native_failure_restore_pixels(self):
        for stopped, outcome, status in ((True, "complete", "cancelled"), (False, "cancel", "cancelled"),
                                         (False, None, "failed")):
            with self.subTest(stopped=stopped, outcome=outcome):
                runner = self.runner([True, False])
                runner.outcome = outcome
                result = runner._finish(stopped)
                self.assertEqual((result[0], result[2]["note"]), (status, self.bakes.PUT_BACK))
                self.assertTrue(all(image.pixels == list(BEFORE) for image in runner.images))

    def test_a_silent_write_failure_is_partial_on_failure_and_cancel(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                runner = self.runner([True, False])
                runner.images[0].pixels.foreach_set = lambda values: None
                self.assertEqual(runner._finish(stopped)[2]["note"], self.bakes.PARTIAL)
                self.assertEqual(runner.images[0].pixels, list(AFTER))

    def test_one_image_restore_exception_does_not_abandon_other_images(self):
        for error in (ReferenceError, RuntimeError, ValueError):
            with self.subTest(error=error):
                runner = self.runner([True, True, False])

                def refuse(values):
                    raise error("image no longer accepts its pixels")

                runner.images[0].pixels.foreach_set = refuse
                self.assertEqual(runner._finish(False)[2]["note"], self.bakes.PARTIAL)
                self.assertEqual(runner.images[1].pixels, list(BEFORE))

    def test_resized_image_cannot_claim_to_restore_its_original_pixels(self):
        runner = self.runner([True, False])
        runner.images[0].pixels.extend(AFTER)
        self.assertEqual(runner._finish(False)[2]["note"], self.bakes.PARTIAL)
        self.assertEqual(len(runner.images[0].pixels), 8)


if __name__ == "__main__":
    unittest.main()
