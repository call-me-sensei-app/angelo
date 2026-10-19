# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What bounds a RENDERED look on the host: its budget, the request's total, Cancel, Stop and every other ending."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rendered_view_fakes as fakes  # noqa: E402
from rendered_view_case import LookCase  # noqa: E402


class BoundTests(LookCase):
    def test_the_budget_stops_a_render_and_a_partial_image_says_so(self):
        pending = self.begin()
        self.blender.progress = 0.25
        self.manager.poll()
        self.clock.now += 121.0
        self.manager.poll()
        self.assertEqual(self.blender.stops, 1, "past its budget with samples to show, the look stops itself")
        self.assertIsNone(pending.poll())
        self.blender.end(written=True)  # A stopped render still writes the samples that finished.
        self.manager.poll()
        result = pending.poll()
        self.assertTrue(result["success"] and result["uncertain"])
        self.assertEqual(result["uncertainty"][0]["code"], "render_partial")
        self.assertIn("Stopped at its 120 s budget after about 8 of 32 samples", result["uncertainty"][0]["detail"])
        self.assertEqual((result["rendered"]["complete"], result["rendered"]["samples_finished"]), (False, 8))
        self.assertNotIn("denoised", result["rendered"], "whether a stopped Cycles render was denoised is not known")
        self.assertIn("may not be denoised", result["note"])
        self.assertIn("partial", result["note"])
        self.assertEqual(len(result["views"]), 1)
        self.assertEqual(self.manager.store.records, {})

    def test_a_scene_slower_to_prepare_than_the_budget_still_returns_its_first_samples(self):
        pending = self.begin()
        self.manager.poll()
        self.clock.now += 150.0
        self.manager.poll()
        self.assertEqual(self.blender.stops, 0, "nothing to show yet: stopping now would have rendered for nothing")
        self.blender.progress = 0.04
        self.manager.poll()
        self.assertEqual(self.blender.stops, 0, "one noisy sample is not a picture: it samples on for a while")
        self.clock.now += 19.0
        self.blender.progress = 0.5
        self.manager.poll()
        self.assertEqual(self.blender.stops, 0)
        self.clock.now += 2.0
        self.manager.poll()
        self.assertEqual(self.blender.stops, 1, "%d s after its first sample" % self.render.SAMPLE_SECONDS)
        self.blender.end(written=True)
        self.manager.poll()
        result = pending.poll()
        self.assertEqual(len(result["views"]), 1)
        self.assertIn("Stopped after 171 s, 20 s into its samples (preparing the scene took 150 s of its 120 s budget) "
                      "after about 16 of 32 samples", result["uncertainty"][0]["detail"])
        self.assertEqual((result["rendered"]["prepare_seconds"], result["rendered"]["sampling_seconds"]), (150.0, 21.0))
        self.assertIn("It took 171.0 s: 150.0 s preparing the scene for rendering and 21.0 s sampling. Preparation does "
                      "not depend on the picture's size or samples, so every look of this scene costs about as much: "
                      "iterate with LIT (seconds) and render at checkpoints.", result["note"])

    def test_a_first_sample_just_before_the_budget_still_gets_its_seconds_of_sampling(self):
        pending = self.begin()
        self.manager.poll()
        self.clock.now += 110.0
        self.blender.progress = 0.04
        self.manager.poll()
        self.clock.now += 15.0  # 125 s: past the budget, 15 s into its samples.
        self.manager.poll()
        self.assertEqual(self.blender.stops, 0)
        self.clock.now += 6.0
        self.manager.poll()
        self.assertEqual(self.blender.stops, 1)
        pending.cancel()

    def test_the_requests_total_ends_a_look_that_never_reached_a_sample_without_an_error(self):
        before = self.blender.scene.snapshot()
        pending = self.begin()
        self.manager.poll()
        self.clock.now += 361.0
        self.manager.poll()
        self.assertEqual((self.blender.stops, self.manager.active()["status"]), (1, "stopping"))
        self.blender.end(written=True)  # Whatever Blender wrote before its first sample is not a picture.
        self.manager.poll()
        result = pending.poll()
        self.assertNotIn("error", result)
        self.assertNotIn("success", result)
        self.assertEqual((result["views"], result["rendered"]["image"], result["rendered"]["ended"]), ([], False, "time_budget"))
        self.assertIn("still preparing the scene", result["note"])
        self.assertIn("this request's 360 s of looks ran out", result["note"])
        self.assertIn("angelo_job_start RENDER_STILL", result["note"])
        self.assertEqual(self.reference_files(), [])
        self.assertEqual(self.blender.scene.snapshot(), before)
        with self.assertRaisesRegex(RuntimeError, "used its 360 s of RENDERED looks"):
            self.begin()

    def test_a_request_has_a_total_of_looks_and_the_next_request_starts_again(self):
        for _look in range(2):
            self.run_look(seconds=110.0)
        pending = self.begin()
        self.assertEqual(round(self.manager.active()["request"]["minutes"] * 60), 140, "the request's total is the hard bound")
        self.blender.progress = 1.0
        self.manager.poll()
        self.clock.now += 118.0
        self.blender.end()
        self.manager.poll()
        self.assertEqual(pending.poll()["rendered"]["request_seconds_left"], 22)
        self.run_look(seconds=10.0)
        with self.assertRaisesRegex(RuntimeError, "used its 360 s of RENDERED looks"):
            self.begin()
        self.assertEqual(self.blender.invoked.__len__(), 4, "nothing started")
        self.controller._request_generation += 1
        _pending, result = self.run_look(seconds=5.0)
        self.assertEqual(result["rendered"]["request_seconds_left"], 355)

    def test_the_person_cancelling_from_the_card_ends_it_without_an_image(self):
        before = self.blender.scene.snapshot()
        pending = self.begin()
        self.blender.progress = 0.5
        self.manager.poll()
        self.manager.cancel(self.manager.active()["id"], by="user")
        self.blender.end(written=True)
        self.manager.poll()
        result = pending.poll()
        self.assertEqual((result["views"], result["rendered"]["ended"]), ([], "user"))
        self.assertIn("the person cancelled this look", result["note"])
        self.assertNotIn("error", result)
        self.assertEqual(self.reference_files(), [])
        self.assertEqual(self.blender.scene.snapshot(), before)

    def test_stopping_the_turn_stops_the_render_and_discards_its_image(self):
        before = self.blender.scene.snapshot()
        pending = self.begin()
        self.blender.progress = 0.5
        self.manager.poll()
        pending.cancel()  # Controller.cancel_tools, expire_tool.
        self.assertEqual(pending.stopped_result["scene_changed"], False)
        self.assertEqual((self.blender.stops, self.manager.active()["reason"]), (1, "stopped"))
        self.blender.end(written=True)
        self.manager.poll()
        self.assertEqual(self.reference_files(), [])
        self.assertEqual(self.manager.store.records, {})
        self.assertEqual(self.blender.scene.snapshot(), before)

    def test_blender_ending_without_an_image_is_an_error_with_everything_restored(self):
        before = self.blender.scene.snapshot()
        _pending, result = self.run_look(written=False)
        self.assertIn("without writing an image", result["error"])
        self.assertEqual((result["read_only"], result["scene_changed"]), (True, False))
        self.assertEqual(self.blender.scene.snapshot(), before)
        self.assertEqual(self.reference_files(), [])

    def test_a_document_closed_under_the_look_is_an_error(self):
        pending = self.begin()
        self.manager.document_closed()
        self.assertIn("closed or reloaded during the look", pending.poll()["error"])
        self.assertEqual(self.reference_files(), [])

    def test_a_document_replaced_during_the_look_returns_no_stale_picture(self):
        pending = self.begin()
        self.blender.progress = 1.0
        self.manager.poll()
        self.epoch = "f" * 32
        self.blender.end()
        self.manager.poll()
        self.assertIn("Document IDs were replaced", pending.poll()["error"])
        self.assertEqual(self.reference_files(), [])

    def test_a_setting_blender_would_not_take_back_is_reported(self):
        pending = self.begin()
        self.blender.progress = 1.0
        self.manager.poll()
        self.blender.scene.render.image_settings.readonly.add("exr_codec")
        self.blender.scene.render.image_settings.values["exr_codec"] = "ZIP"  # Something else changed it meanwhile.
        original = fakes.ImageSettings.assign

        def refuse(struct, name, value):
            if name == "exr_codec":
                raise TypeError("read-only")
            original(struct, name, value)
        with patch.object(fakes.ImageSettings, "assign", refuse):
            self.blender.end()
            self.manager.poll()
        result = pending.poll()
        self.assertEqual(result["side_effects"][0]["code"], "render_settings_not_restored")
        self.assertIn("render.image_settings.exr_codec", result["side_effects"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
