# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unchanged image metadata never proves that a reviewed script's pixels were restored."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from test_native_script_rollback import BEFORE, PARTIAL, Blender, image
from angelo_agent import script_errors


class PixelRecoveryTests(unittest.TestCase):
    def test_already_dirty_pixels_survive_main_restore_without_a_full_rollback_claim(self):
        texture = image(1, dirty=True)
        texture.pixel = 0.2
        blender = Blender(BEFORE, images=[texture])
        point = blender.start()
        texture.pixel = 0.8
        result = blender.stop(point, PARTIAL, code="getattr(im, 'pix' + 'els')[0] = 0.8")
        self.assertEqual((blender.state, texture.pixel), (BEFORE, 0.8))
        self.assertIs(result["main_data_restored"], True)
        self.assertIs(result["rolled_back"], False)
        self.assertIs(result["partial_changes_possible"], True)
        self.assertIs(result["image_pixels_restored"], False)
        self.assertNotIn("scene_changed", result)
        self.assertEqual(result["rollback_scope"], "MAIN_DATA")

    def test_equal_size_repacking_does_not_prove_recovery(self):
        texture = image(1, packed=1024)
        blender = Blender(BEFORE, images=[texture])
        point = blender.start()
        texture.content = b"replacement with the same encoded size"
        result = blender.stop(point, PARTIAL, code="getattr(im, 'pa' + 'ck')()")
        self.assertEqual(script_errors._image_facts(blender), point[2])
        self.assertIs(result["main_data_restored"], True)
        self.assertIs(result["rolled_back"], False)
        self.assertIs(result["partial_changes_possible"], True)
        self.assertNotIn("scene_changed", result)

    def test_image_free_main_restore_still_does_not_prove_external_effects(self):
        blender = Blender(BEFORE)
        result = blender.stop(blender.start(), PARTIAL)
        self.assertEqual(blender.state, BEFORE)
        self.assertIs(result["main_data_restored"], True)
        self.assertIs(result["external_effects_restored"], False)
        self.assertIs(result["rolled_back"], False)
        self.assertIs(result["partial_changes_possible"], True)
        self.assertNotIn("scene_changed", result)
        self.assertIn("sending a corrected script is safe", result["recovery"])
        self.assertIn("Image pixels and files it wrote are not restored", result["recovery"])

    def test_successful_script_with_any_existing_image_cannot_promise_full_scene_undo(self):
        for texture in (image(1), image(1, dirty=True), image(1, packed=1024)):
            with self.subTest(texture=texture):
                blender = Blender(BEFORE, images=[texture])
                facts = script_errors.undo_facts(blender, blender.start(), "OBJECT")
                self.assertIs(facts["undoable"], False)
                self.assertIs(facts["main_data_undoable"], False)
                self.assertIs(facts["image_pixels_undoable"], False)
                self.assertEqual(facts["undo_scope"], "MAIN_DATA")

    def test_images_added_or_removed_by_a_successful_script_are_unverified(self):
        for before in ([], [image(1, dirty=True)]):
            blender = Blender(BEFORE, images=before)
            point = blender.start()
            blender.data.images = [] if before else [image(2, dirty=True)]
            self.assertIs(script_errors.undo_facts(blender, point, "OBJECT")["undoable"], False)

    def test_success_without_images_cannot_prove_the_scripts_undo_history(self):
        blender = Blender(BEFORE)
        facts = script_errors.undo_facts(blender, blender.start(), "OBJECT")
        self.assertIs(facts["undoable"], False)
        self.assertIs(facts["main_data_undoable"], False)
        self.assertEqual(facts["undo_scope"], "MAIN_DATA")
        self.assertIs(facts["image_pixels_undoable"], False)

    def test_availability_is_not_proof_of_script_undo_coverage(self):
        for global_undo, steps, poll in ((True, 32, True), (False, 32, True), (True, 0, True), (True, 32, False)):
            with self.subTest(global_undo=global_undo, steps=steps, poll=poll):
                blender = Blender(BEFORE)
                point = blender.start()
                preferences = SimpleNamespace(use_global_undo=global_undo, undo_steps=steps)
                blender.context.preferences = SimpleNamespace(edit=preferences)
                blender.ops.ed.undo = Mock(side_effect=blender.undo)
                blender.ops.ed.undo.poll.return_value = poll
                facts = script_errors.undo_facts(blender, point, "OBJECT")
                self.assertIs(facts["undo_available"], bool(global_undo and steps > 0 and poll))
                self.assertIs(facts["main_data_undoable"], False)
                self.assertIs(facts["undoable"], False)
                self.assertIn("unverified", facts["undo"])
                self.assertEqual((preferences.use_global_undo, preferences.undo_steps), (global_undo, steps))
                blender.ops.ed.undo.assert_not_called()

    def test_undo_poll_failure_is_unavailable_without_failing_the_script(self):
        blender = Blender(BEFORE)
        point = blender.start()
        blender.context.preferences = SimpleNamespace(edit=SimpleNamespace(use_global_undo=True, undo_steps=32))
        blender.ops.ed.undo = Mock(side_effect=blender.undo)
        blender.ops.ed.undo.poll.side_effect = RuntimeError("context is incorrect")
        facts = script_errors.undo_facts(blender, point, "OBJECT")
        self.assertIs(facts["undo_available"], False)
        self.assertIs(facts["main_data_undoable"], False)

    def test_partial_recovery_notice_never_says_everything_was_undone(self):
        for restored in (True, False):
            row = script_errors.row({"main_data_restored": restored, "error": "KeyError: size"})
            self.assertIn("KeyError: size", row["text"])
            self.assertNotIn("changes were undone", row["text"])
            self.assertIn("not verified" if restored else "may remain", row["text"])
            self.assertIn("i18n", row)


if __name__ == "__main__":
    unittest.main()
