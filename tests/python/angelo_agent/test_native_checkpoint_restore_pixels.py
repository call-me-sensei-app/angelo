# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restore and Undo restore say plainly which unsaved image pixel edits no snapshot holds.

Snapshots are private saves and keep image pixels only as a save does, so
unsaved pixel edits are in no snapshot (the rule: report
partial image-pixel recovery honestly, no full pixel backups). The dialog
states the rule and names the images at stake before anything happens; the
chat says afterwards which edits the replaced document took with it, and an
Undo restore says which ones its safety snapshot never held.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_checkpoint_restore_ui as restore_case  # noqa: E402
from angelo_agent import checkpoint_images as images  # noqa: E402
from angelo_agent import checkpoint_restore_ui as flow  # noqa: E402


def image(name, dirty=True, kind="IMAGE"):
    return SimpleNamespace(name=name, is_dirty=dirty, type=kind)


class RestorePixelTests(unittest.TestCase):
    setUp = restore_case.CheckpointRestoreUITests.setUp
    open_scene = restore_case.CheckpointRestoreUITests.open_scene

    def dirty(self, *names):
        self.ui.bpy.data.images = [image(name) for name in names] + [
            image("Clean", dirty=False), image("Render Result", kind="RENDER_RESULT"), image("Viewer Node", kind="COMPOSITING")]

    def test_a_restore_records_and_reports_the_pixel_edits_it_discards(self):
        self.dirty("Paint B", "Paint A")
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        restore, pixels = self.active.state['messages'][-2:]
        self.assertEqual((restore['restore_state'], restore['unsaved_images']), ('complete', ['Paint A', 'Paint B']))
        self.assertEqual(pixels['unsaved_images_discarded'], ['Paint A', 'Paint B'])
        self.assertIn("Unsaved pixel edits in Paint A, Paint B were in no snapshot and were discarded; Undo restore "
                      "can't bring them back.", pixels['text'])
        self.assertEqual(pixels['i18n'][0], images.DISCARDED, "the chat translates it as it draws")
        self.assertEqual(self.active.saved['messages'][-1], pixels, "saved with the outcome")
        self.assertEqual(self.operator.report.call_args.args[0], {'WARNING'})

    def test_nothing_is_said_without_unsaved_pixels(self):
        self.dirty()
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        restore = self.active.state['messages'][-1]
        self.assertEqual(restore['restore_state'], 'complete')
        self.assertNotIn('unsaved_images', restore)
        self.operator.report.assert_not_called()

    def test_an_undo_restore_says_its_safety_snapshot_never_held_them(self):
        self.record.update(purpose='safety')
        self.active.state['messages'].append({'role': 'system', 'restore_checkpoint': 'selected',
                                              'unsaved_images': ['Texture']})
        self.dirty()
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        said = self.active.state['messages'][-1]
        self.assertEqual(said['unsaved_images_not_restored'], ['Texture'])
        self.assertIn("When this safety snapshot was taken, Texture had unsaved pixel edits; it doesn't hold them.",
                      said['text'])

    def test_a_failure_after_the_scene_opened_still_says_what_was_discarded(self):
        self.dirty("Paint")
        with patch.object(flow, "restore_outcome", side_effect=RuntimeError("Injected outcome failure")):
            self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        restore, pixels = self.active.state['messages'][-2:]
        self.assertEqual(restore['restore_state'], 'partial')
        self.assertEqual(pixels['unsaved_images_discarded'], ['Paint'])

    def test_a_restore_that_never_opened_discarded_nothing(self):
        self.dirty("Paint")
        self.active.fail_save = 1  # The pre-open safety link cannot be saved: no scene is opened.
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'CANCELLED'})
        self.assertFalse(any('unsaved_images_discarded' in message for message in self.active.state['messages']))


class Layout:
    def __init__(self):
        self.labels = []

    def label(self, text="", translate=True, icon='NONE'):
        self.labels.append((text, icon))


class DialogTests(unittest.TestCase):
    def setUp(self):
        chat_view = SimpleNamespace(wrap=lambda text, width, size, font: [(text, 0)], _font=0)
        self.patches = [patch.dict(sys.modules, {"angelo_agent.chat_view": chat_view}),
                        patch("angelo_agent.chat_view", chat_view, create=True)]
        for item in self.patches:
            item.start()
        self.messages = [{'role': 'user', 'checkpoint': 'request', 'checkpoint_scope': 'before_request'},
                         {'role': 'system', 'restore_checkpoint': 'safety', 'unsaved_images': ['Old paint']}]
        records = {'request': {'id': 'request', 'capture_scope': 'before_request'},
                   'safety': {'id': 'safety', 'purpose': 'safety'}}
        self.controller = SimpleNamespace(state={'messages': self.messages},
                                          checkpoints=SimpleNamespace(load=records.__getitem__))
        self.bpy = SimpleNamespace(data=SimpleNamespace(images=[image("Paint"), image("Saved", dirty=False)]))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def draw(self, checkpoint):
        review = images.restore_review(self.bpy, self.controller, checkpoint)
        layout = Layout()
        images.draw_restore(layout, review)
        return review, layout.labels

    def test_the_dialog_states_the_rule_and_names_the_images_that_would_lose_edits(self):
        review, labels = self.draw('request')
        self.assertEqual((review['unsaved_now'], review['unsaved_then']), (['Paint'], []))
        self.assertIn((images.SNAPSHOT_RULE, 'NONE'), labels)
        self.assertIn((images.LOSE_NOW.format(images="Paint"), 'ERROR'), labels)

    def test_undo_restore_names_what_its_safety_snapshot_never_held(self):
        review, labels = self.draw('safety')
        self.assertEqual(review['scope'], 'before_restore')
        self.assertIn((images.NEVER_HELD.format(images="Old paint"), 'INFO'), labels)

    def test_a_clean_document_shows_only_the_rule(self):
        self.bpy.data.images = [image("Saved", dirty=False)]
        _review, labels = self.draw('request')
        self.assertIn((images.SNAPSHOT_RULE, 'NONE'), labels)
        self.assertFalse([text for text, icon in labels if icon == 'ERROR'])

    def test_long_lists_are_counted(self):
        self.assertEqual(images.names(["I%d" % n for n in range(8)]), "I0, I1, I2, I3, I4, I5, … +2")


class CapturePackingTests(unittest.TestCase):
    def test_only_builds_that_say_they_never_pack_skip_the_autopack_guard(self):
        # ui.capture_checkpoint holds Automatically Pack Resources off unless the native capture never packs.
        native = SimpleNamespace(_angelo_checkpoint=SimpleNamespace(capture_never_packs=True))
        older = SimpleNamespace(_angelo_checkpoint=SimpleNamespace())
        with patch.dict(sys.modules, {"_bpy": native}):
            self.assertFalse(images.capture_packs())
        for module in (older, SimpleNamespace()):
            with self.subTest(module=module), patch.dict(sys.modules, {"_bpy": module}):
                self.assertTrue(images.capture_packs())
        with patch.dict(sys.modules, {"_bpy": None}):  # No Blender: assume the older behavior.
            self.assertTrue(images.capture_packs())


if __name__ == "__main__":
    unittest.main()
