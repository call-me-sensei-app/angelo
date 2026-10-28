# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restore keeps each chat on its own scene (break-it E2E-20 R5-M-1; R5M1 round 2, R2-M-4).

A file read gives every scene a new session uid, so a chat holding work is rebound by its scene's name: never onto the
scene the window shows, where a lead's next merge would land. The name is the one its scene had when the restored
checkpoint was captured (its scene map, kept in the profile's ``checkpoint-scenes`` folder), so names changed since
(swapped, renamed, reused) never move it; a checkpoint without a map (an earlier build's, or its map lost), a
malformed map or one of another load rebinds by the name read before the load, as before. A chat whose scene is not in the restored file keeps a key no scene has (it reads away, its scene
missing); a chat with no work, or one bound to another file, takes the window's key, as in 1.0.5. The flow and its
fakes are ``test_native_checkpoint_restore_ui``'s.
"""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
import test_native_checkpoint_restore_ui as base  # noqa: E402  (a module, so its tests are not collected twice)
from angelo_agent import checkpoint_restore_ui as flow, checkpoint_scene_map, controller_recovery, document_scene  # noqa: E402
from angelo_agent.controller_recovery import restored_document  # noqa: E402
from test_native_checkpoint_scene_map import MALFORMED  # noqa: E402

ORIGINAL, RECOVERED, SAFETY = '/private/original.blend', '/private/recovered.blend', '/private/safety-working.blend'
AGAIN, BETWEEN = '/private/recovered-again.blend', '/private/between.blend'
SERIALS = {ORIGINAL: 'before', RECOVERED: 'restored', SAFETY: 'reloaded', AGAIN: 'again', BETWEEN: 'between'}
CAPTURED = {'Atelier': 11, 'Garden': 12, 'Harbor': 13}   # The original file's scenes when the checkpoint was taken.
READ = checkpoint_scene_map.read


def scene_map(load='before', **uids):
    """A checkpoint's scene map: each scene's name by its session uid in the load ``load`` (``checkpoint_scene_map``)."""
    return {'load': load, 'names': [[uid, name] for name, uid in (uids or CAPTURED).items()]}


class Files(SimpleNamespace):
    """``bpy.data`` over several files: {path: {scene name_full: session uid}}; each read gives new uids."""
    scenes = property(lambda self: [SimpleNamespace(name_full=name, session_uid=uid)
                                    for name, uid in self.files[self.filepath].items()])


class RestoreKeepsEachChatsSceneTests(unittest.TestCase):
    setUp_flow, open_scene = base.CheckpointRestoreUITests.setUp, base.CheckpointRestoreUITests.open_scene
    bundle = base.CheckpointRestoreUITests.bundle
    failed_after_replacement = base.CheckpointRestoreUITests.failed_after_replacement

    def setUp(self):
        self.setUp_flow()
        controller_recovery._missing.clear()
        self.addCleanup(controller_recovery._missing.clear)
        for links in (checkpoint_scene_map._parents, checkpoint_scene_map._absent, checkpoint_scene_map._loads,
                      checkpoint_scene_map._written):
            links.clear()
            self.addCleanup(links.clear)
        self.ui.bpy.data = Files(filepath=ORIGINAL, files={ORIGINAL: {'Atelier': 11, 'Garden': 12, 'Harbor': 13},
                                                           RECOVERED: {'Harbor': 31, 'Garden': 32, 'Atelier': 33},
                                                           SAFETY: {'Garden': 41, 'Atelier': 42, 'Harbor': 43},
                                                           AGAIN: {'Harbor': 51, 'Garden': 52, 'Atelier': 53},
                                                           BETWEEN: {'Atelier': 71, 'Garden': 72, 'Harbor': 73}})
        self.shown = 'Garden'   # The scene the main window shows, by name: a read keeps the window on it.
        self.ui.document_key = lambda: (SERIALS[self.ui.bpy.data.filepath], self.uid(self.shown))
        self.active.document, self.other.document = ('before', 11), ('before', 13)   # Atelier and Harbor: not shown.
        # Maps are real files of a temporary profile. The flow's fake store and checkpoint ids are not UUIDs (its
        # session id is the chat's), so the map store stands for the chat's and each id for one UUID.
        profile = tempfile.TemporaryDirectory(prefix='angelo-restore-scenes-')
        self.addCleanup(profile.cleanup)
        self.maps = SimpleNamespace(root=Path(profile.name).resolve(), session_id=str(uuid.uuid4()))
        self.ids = {name: str(uuid.uuid4()) for name in ('selected', 'safety', 'undo', 'between')}

        def read(store, identity):
            self.assertIs(store, self.active.checkpoints, "the map of the chat's own store")
            return READ(self.maps, self.ids.get(identity, identity))
        reading = patch.object(checkpoint_scene_map, 'read', side_effect=read)
        reading.start()
        self.addCleanup(reading.stop)

    def keep(self, value=None, identity='selected'):
        """The checkpoint's scene map, kept as its capture keeps it (``checkpoint_scene_map.save``)."""
        self.assertTrue(checkpoint_scene_map.save(self.maps, self.ids[identity], value or scene_map()))

    def map_file(self, identity='selected'):
        return self.maps.root / checkpoint_scene_map.FOLDER / self.maps.session_id / (self.ids[identity] + '.json')

    def uid(self, name):
        return next((s.session_uid for s in self.ui.bpy.data.scenes if s.name_full == name), 0)

    def restore(self):
        self.assertEqual(flow.execute_restore(self.ui, self.operator), {'FINISHED'})
        return self.ui.document_key()

    def test_each_chat_holding_work_is_rebound_to_its_own_scene_by_name(self):
        for shown in ('Garden', 'Harbor', 'Atelier'):
            with self.subTest(shown=shown):
                self.setUp()
                self.shown = shown
                key = self.restore()
                self.assertEqual(self.active.document, ('restored', self.uid('Atelier')))
                self.assertEqual(self.other.document, ('restored', self.uid('Harbor')))
                for target in (self.active, self.other):
                    self.assertFalse(target.document_invalid)
                    self.assertEqual(document_scene.away(target, key), target.document != key)
                self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'complete')

    def test_a_chat_whose_scene_is_not_in_the_restored_file_keeps_a_key_no_scene_has(self):
        for missing in ('Atelier', 'Harbor'):
            with self.subTest(missing=missing):
                self.setUp()
                self.ui.bpy.data.files[RECOVERED].pop(missing)
                key = self.restore()
                gone, kept = (self.active, self.other) if missing == 'Atelier' else (self.other, self.active)
                self.assertEqual(gone.document, ('restored', 11 if missing == 'Atelier' else 13), 'its old uid')
                self.assertNotIn(gone.document[1], [s.session_uid for s in self.ui.bpy.data.scenes])
                self.assertTrue(document_scene.away(gone, key) and not gone.document_invalid, 'away, never invalid')
                self.assertNotEqual(gone.document, key, 'never moved onto the shown scene')
                self.assertEqual(kept.document[1], self.uid('Atelier' if kept is self.active else 'Harbor'))

    def test_a_later_restore_that_brings_the_missing_scene_back_rebinds_the_chat_to_it(self):
        self.ui.bpy.data.files[RECOVERED].pop('Atelier')
        self.restore()
        self.assertEqual(self.active.document, ('restored', 11), 'missing')
        self.active.checkpoints.recovery_copy = Mock(return_value=(self.record, Path(SAFETY)))   # Undo restore.
        key = self.restore()
        self.assertEqual(self.active.document, ('reloaded', self.uid('Atelier')))
        self.assertEqual(document_scene.away(self.active, key), self.active.document != key)
        self.assertEqual(controller_recovery._missing, {})

    def test_a_chat_with_no_work_or_on_another_file_takes_the_shown_key(self):
        self.other.state.update(messages=[{'role': 'system', 'text': 'Angelo is ready.'}], native_id=None)
        third = base.Controller('third', self.timeline)   # Invalidated by an earlier load: bound to another file.
        third.document, third.document_invalid = ('older', 11), True
        self.ui._controllers['third'] = third
        key = self.restore()
        self.assertEqual(self.other.document, key)
        self.assertEqual(third.document, key)
        self.assertFalse(third.document_invalid)
        self.assertEqual(self.active.document, ('restored', self.uid('Atelier')), 'the chat with work keeps its own')

    def test_the_safety_reload_rebinds_by_name_too(self):
        owner, native = self.bundle()

        def reload(prepared):
            self.ui.bpy.data.filepath = SAFETY
            return True
        recovery, _safety_owner = self.failed_after_replacement(owner, native, reload)
        with recovery, patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)}):
            key = self.restore()
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'not_applied')
        self.assertEqual(key, ('reloaded', 41))
        self.assertEqual((self.active.document, self.other.document), (('reloaded', 42), ('reloaded', 43)))

    def rename(self, path, **uids):
        """The scenes of ``path`` after the person renamed (deleted, added) some: {name_full: session uid}."""
        self.ui.bpy.data.files[path] = uids

    def own(self, key, active, other):
        """Each chat's key is its own scene of the restored file, by name there: (active's, other's)."""
        self.assertEqual((self.active.document, self.other.document),
                         ((key[0], self.uid(active)), (key[0], self.uid(other))))

    def test_names_swapped_after_the_capture_rebind_each_chat_to_its_own_scene(self):
        self.keep()
        self.rename(ORIGINAL, Harbor=11, Garden=12, Atelier=13)   # Atelier and Harbor swapped their names.
        key = self.restore()
        self.own(key, 'Atelier', 'Harbor')   # What they were called in the checkpoint, where their merges belong.
        self.assertFalse(self.active.document_invalid or self.other.document_invalid)

    def test_a_scene_renamed_after_the_capture_is_found_not_gone(self):
        self.keep()
        self.rename(ORIGINAL, Loft=11, Garden=12, Harbor=13)
        self.own(self.restore(), 'Atelier', 'Harbor')
        self.assertEqual(controller_recovery._missing, {})

    def test_a_name_reused_by_a_scene_made_after_the_capture_is_not_the_chats(self):
        self.keep()
        self.rename(ORIGINAL, Garden=12, Harbor=13, Atelier=14)   # Atelier deleted; a new scene took its name.
        self.active.document = ('before', 14)
        key = self.restore()
        self.assertEqual(self.active.document, ('restored', 14), 'its scene is not in the checkpoint: a key no scene has')
        self.assertTrue(document_scene.away(self.active, key) and not self.active.document_invalid)
        self.assertEqual(self.other.document, ('restored', self.uid('Harbor')))

    def test_a_scene_deleted_after_the_capture_comes_back_with_its_chat(self):
        self.keep()
        self.rename(ORIGINAL, Garden=12, Harbor=13)   # The active chat's scene (Atelier, 11) was deleted.
        self.own(self.restore(), 'Atelier', 'Harbor')

    def test_a_checkpoint_without_a_map_rebinds_by_the_name_read_before_the_load_as_before(self):
        self.own(self.restore(), 'Atelier', 'Harbor')   # The names did not change: each its own scene.
        self.setUp()
        self.rename(ORIGINAL, Loft=11, Garden=12, Harbor=13)
        self.restore()
        self.assertEqual(self.active.document, ('restored', 11), 'renamed, no map: gone, as before')
        self.assertEqual(controller_recovery._missing, {'active': 'Loft'})

    def outcome(self, scenes=None, payload=None):
        """(active, other, missing names) after a Restore of a checkpoint whose map is ``scenes`` (or the file bytes
        ``payload``; neither: no map) once Atelier was renamed and Garden and Harbor swapped their names."""
        self.setUp()
        if scenes is not None or payload is not None:
            self.keep()
            self.map_file().write_bytes(payload if payload is not None else json.dumps(scenes).encode())
        self.rename(ORIGINAL, Loft=11, Harbor=12, Garden=13)
        self.restore()
        return self.active.document, self.other.document, dict(controller_recovery._missing)

    def test_a_malformed_map_or_one_of_another_load_rebinds_exactly_as_without_a_map(self):
        today = self.outcome()
        self.assertEqual(today[0], ('restored', 11), 'without a map the renamed scene reads as gone')
        for scenes in MALFORMED + (scene_map('another load'),):
            with self.subTest(scenes=scenes):
                self.assertEqual(self.outcome(scenes), today)
        for payload in (b'', b'{', b'\xff\xfe', b'null'):
            with self.subTest(payload=payload):
                self.assertEqual(self.outcome(payload=payload), today)
        self.assertEqual(self.outcome(scene_map())[:2], (('restored', 33), ('restored', 31)), 'a valid map: their own')

    def test_a_checkpoint_whose_map_is_gone_restores_by_name_as_before(self):
        self.keep()   # Written by this build, then lost (removed by hand, or by a cleanup of another build).
        self.map_file().unlink()
        self.rename(ORIGINAL, Loft=11, Garden=12, Harbor=13)
        self.restore()
        self.assertEqual((self.active.document, self.other.document), (('restored', 11), ('restored', 31)))
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'complete')

    def test_undo_restore_after_names_changed_rebinds_each_chat_to_its_own_scene(self):
        self.keep()
        self.own(self.restore(), 'Atelier', 'Harbor')
        self.rename(RECOVERED, Atelier=31, Garden=32, Harbor=33)   # Swapped after the Restore.
        # Undo restore: the safety capture taken before the Restore, in the original load, with the same names.
        self.active.checkpoints.recovery_copy = Mock(return_value=(self.record, Path(SAFETY)))
        self.own(self.restore(), 'Atelier', 'Harbor')

    def test_a_restore_of_the_load_between_two_restores_follows_each_chat_after_names_changed(self):
        """break-it R4-M-2: Restore C0, swap two names, Restore C0 again; then Undo restore (the safety taken between)
        or a request checkpoint taken between brings each chat back to the scene it was on then."""
        for later in ('undo', 'between'):
            with self.subTest(later=later):
                self.setUp()
                self.keep()   # C0, captured in the original load.
                self.own(self.restore(), 'Atelier', 'Harbor')
                self.rename(RECOVERED, Atelier=31, Garden=32, Harbor=33)   # Swapped in the restored load.
                # Taken now, in that load: the safety the next Restore captures, or a request checkpoint.
                self.keep(scene_map('restored', Atelier=31, Garden=32, Harbor=33), later)
                self.active.checkpoints.recovery_copy = Mock(return_value=(self.record, Path(AGAIN)))
                self.own(self.restore(), 'Atelier', 'Harbor')   # C0 again: by its own names.
                self.operator.checkpoint_id = later
                self.active.checkpoints.recovery_copy = Mock(return_value=(self.record, Path(BETWEEN)))
                # Their scenes then: the active chat's (33) was called Harbor, the other's (31) Atelier.
                self.own(self.restore(), 'Harbor', 'Atelier')

    def test_the_safety_reload_rebinds_by_its_own_capture_and_links_for_a_later_restore(self):
        self.keep()   # The selected checkpoint: taken before Atelier and Harbor swapped names.
        self.rename(ORIGINAL, Harbor=11, Garden=12, Atelier=13)
        self.rename(SAFETY, Garden=41, Harbor=42, Atelier=43)   # The safety capture: the names as they are now.
        owner, native = self.bundle()

        def reload(prepared):
            self.ui.bpy.data.filepath = SAFETY
            return True
        recovery, _safety_owner = self.failed_after_replacement(owner, native, reload)
        self.keep(scene_map(Harbor=11, Garden=12, Atelier=13), 'safety')   # The safety capture's own map.
        with recovery, patch.dict(sys.modules, {'_bpy': SimpleNamespace(_angelo_checkpoint=native)}):
            key = self.restore()
        self.assertEqual(self.active.state['messages'][-1]['restore_state'], 'not_applied')
        self.own(key, 'Harbor', 'Atelier')   # Their own scenes, by the safety capture's names (not the selected's).
        # Restore the selected checkpoint again, now through an ordinary copy: each chat finds its scene as it was then.
        self.record['version'] = 3
        self.ui.capture_checkpoint.side_effect, self.ui.capture_checkpoint.return_value = None, {'id': 'safety'}
        self.active.checkpoints.restore_since.side_effect = None
        self.own(self.restore(), 'Atelier', 'Harbor')

    def test_a_scene_still_holding_its_uid_is_the_chats_even_renamed(self):
        scenes = [SimpleNamespace(name_full='Studio', session_uid=7), SimpleNamespace(name_full='Atelier', session_uid=9)]
        self.assertEqual(restored_document(('Atelier', ('r', 7)), ('s', 9), scenes), ('s', 7), 'uids are never reused')
        self.assertEqual(restored_document(('Atelier', ('r', 5)), ('s', 7), scenes), ('s', 9), 'else by its name')
        self.assertEqual(restored_document(('Pier', ('r', 5)), ('s', 7), scenes), ('s', 5), 'else a key no scene has')
        self.assertEqual(restored_document(None, ('s', 7), scenes), ('s', 7), 'no work: the shown key')
        captured = scene_map('r', Studio=5, Pier=6)
        self.assertEqual(restored_document(('Pier', ('r', 5)), ('s', 7), scenes, captured=captured), ('s', 7),
                         'its name at capture, not the one read before the load')
        self.assertEqual(restored_document(('Atelier', ('r', 8)), ('s', 7), scenes, captured=captured), ('s', 8),
                         'not in the capture: its scene was made later, so the namesake is another scene')

    def test_the_names_are_read_before_the_load(self):
        names = Mock(side_effect=lambda *args: self.timeline.append(('names',)) or {})
        with patch('angelo_agent.controller_recovery.scene_names', names):
            self.restore()
        self.assertLess(self.timeline.index(('names',)), self.timeline.index(('open',)))
        self.assertEqual(self.active.document, self.ui.document_key(), 'no entry: the shown key')


if __name__ == '__main__':
    unittest.main()
