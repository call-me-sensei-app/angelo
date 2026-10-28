# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c on the checkpoint scene map: provenance, bounds, the save boundary and file-tool privacy.

* F1: a scene made in a Restore's load after the Restore is in no capture of the loads it descends from, so a chat on
  it is told its scene is missing from such a capture; it is never bound to a namesake (R4-L-7). Unknown provenance
  still rebinds by name.
* F5: the links a Restore keeps are bounded by scenes, chats and the loads a kept map names, not by Restore count.
* F4: a capture's map is read where the file is written, after every other ``save_pre`` handler.
* F2: application-state folders are refused to every file tool even when the workspace lies inside one; only the
  chat's own default workspace is exempt.
"""

from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_capture, checkpoint_scene_map as scene_map, document_checkpoint  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.controller_recovery import restored_document  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402


def scenes(**uids):
    return [SimpleNamespace(name_full=name, session_uid=uid) for name, uid in uids.items()]


def reset_links():
    for name in ("_parents", "_absent", "_loads", "_written"):
        getattr(scene_map, name, {}).clear()


class Profile(unittest.TestCase):
    def setUp(self):
        reset_links()
        self.addCleanup(reset_links)
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-scene-provenance-")
        self.addCleanup(self.temp.cleanup)
        self.store = CheckpointStore(Path(self.temp.name).resolve() / "state", str(uuid.uuid4()))

    def keep(self, value):
        """A checkpoint's map, kept as its capture keeps it; returns it as Restore reads it."""
        identity = str(uuid.uuid4())
        self.assertTrue(scene_map.save(self.store, identity, value))
        return identity, scene_map.read(self.store, identity)


class NewSceneAfterRestoreTests(Profile):
    def test_a_scene_made_after_a_restore_never_binds_to_an_older_namesake(self):
        """Review P1/F1: C0 (L0: Atelier=11) restored into L1 (Atelier=21); 21 renamed, a new scene 22 named Atelier,
        a chat works on it; C0 restored again into L2."""
        _, c0 = self.keep({"load": "L0", "names": [[11, "Atelier"]]})
        scene_map.remember_restored(c0, "L1", scenes(Atelier=21), [])
        self.assertEqual(restored_document(("Atelier", ("L1", 22)), ("L2", 31), scenes(Atelier=31),
                                           chat_id="new-scene", captured=c0), ("L2", 22), "missing, not C0's Atelier")
        # The restored scene, renamed since, still finds C0's scene by its captured name.
        self.assertEqual(restored_document(("Original", ("L1", 21)), ("L2", 31), scenes(Atelier=31),
                                           chat_id="restored-scene", captured=c0), ("L2", 31))

    def test_unknown_provenance_still_rebinds_by_name(self):
        _, c0 = self.keep({"load": "L0", "names": [[11, "Atelier"]]})
        scene_map.remember_restored(None, "L1", scenes(Atelier=21), [])   # An earlier build's checkpoint: no map.
        for document in (("L1", 22), ("L9", 5)):   # No provenance for L1; L9: a load no Restore made.
            with self.subTest(document=document):
                self.assertEqual(scene_map.captured_name(c0, document), (False, None))
                self.assertEqual(restored_document(("Atelier", document), ("L2", 31), scenes(Atelier=31),
                                                   chat_id="unknown", captured=c0), ("L2", 31))

    def test_a_scene_restored_without_a_link_is_unknown_not_missing(self):
        _, c0 = self.keep({"load": "L0", "names": [[11, "Atelier"]]})
        scene_map.remember_restored(c0, "L1", scenes(Atelier=21, Loft=23), [])   # Loft: no captured namesake.
        self.assertEqual(scene_map.captured_name(c0, ("L1", 23)), (False, None))

    def test_provenance_follows_restores_of_later_loads(self):
        """C0 into L1; a new scene 22 there; C1 (L1) into L2 (22 restored as 42); a new scene 43 in L2. Neither is in
        C0, and a chat on either is told so; C1 still finds 42's scene."""
        _, c0 = self.keep({"load": "L0", "names": [[11, "Atelier"]]})
        scene_map.remember_restored(c0, "L1", scenes(Atelier=21), [])   # Then 21 is renamed Original, 22 made.
        _, c1 = self.keep({"load": "L1", "names": [[21, "Original"], [22, "Atelier"]]})
        scene_map.remember_restored(c1, "L2", scenes(Original=41, Atelier=42), [])
        for document in (("L2", 42), ("L2", 43)):
            with self.subTest(document=document):
                self.assertEqual(scene_map.captured_name(c0, document), (True, None))
        self.assertEqual(scene_map.captured_name(c1, ("L2", 42)), (True, "Atelier"))
        self.assertEqual(scene_map.captured_name(c0, ("L2", 41)), (True, "Atelier"))

    def test_two_captured_scenes_in_the_ancestry_stay_ambiguous(self):
        _, c0 = self.keep({"load": "L0", "names": [[11, "Atelier"], [13, "Harbor"]]})
        scene_map.remember_restored(c0, "L1", scenes(Atelier=21, Harbor=23), [])
        scene_map.continues(("L2", 31), ("L1", 21))
        scene_map.continues(("L2", 31), ("L1", 23))
        self.assertEqual(scene_map.captured_name(c0, ("L2", 31)), (False, None))


class BoundedLinksTests(Profile):
    def test_links_stay_bounded_however_many_restores_run(self):
        """Review P1/F5: a chat's key continues through many Restores of map-less checkpoints; the C0 answer stays."""
        _, c0 = self.keep({"load": "L0", "names": [[1, "Atelier"]]})
        previous = ("L0", 1)
        for index in range(1, 301):
            current = ("L%d" % index, index + 1)
            scene_map.continues(current, previous)
            scene_map.remember_restored(None, current[0], scenes(Atelier=current[1]), [current])
            previous = current
        self.assertLessEqual(len(scene_map._parents) + len(getattr(scene_map, "_loads", {})), 2,
                             "bounded by scenes and chats")
        self.assertEqual(scene_map.captured_name(c0, previous), (True, "Atelier"))

    def test_a_removed_map_releases_its_loads_links(self):
        identity, c0 = self.keep({"load": "L0", "names": [[1, "Atelier"]]})
        scene_map.remember_restored(c0, "L1", scenes(Atelier=21), [])
        scene_map.remove(self.store.root, self.store.session_id, identity)
        scene_map.remember_restored(None, "L2", scenes(Atelier=31), [("L1", 21)])
        self.assertEqual(scene_map._parents, {})
        self.assertEqual(scene_map._loads, {})


class SaveBoundaryTests(unittest.TestCase):
    """The capture's map names scenes as the written file does: a save_pre handler may rename them."""

    def setUp(self):
        reset_links()
        self.addCleanup(reset_links)
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-scene-map-save-")
        self.addCleanup(self.temp.cleanup)
        self.store = CheckpointStore(Path(self.temp.name).resolve() / "state", str(uuid.uuid4()))
        self.scenes = scenes(Atelier=11, Harbor=12)
        prefs = SimpleNamespace(filepaths=SimpleNamespace(file_preview_type="AUTO", save_modified_images="ASK"),
                                is_dirty=False, bl_rna=SimpleNamespace(properties={"is_dirty": SimpleNamespace(is_readonly=True)}))
        self.bpy = SimpleNamespace(context=SimpleNamespace(preferences=prefs, temp_override=lambda **_: nullcontext()),
                                   data=SimpleNamespace(use_autopack=False, scenes=self.scenes),
                                   app=SimpleNamespace(handlers=SimpleNamespace(save_pre=[], save_post=[])))

    def capture(self, saves=1, fail=False):
        def native(store, original, **arguments):
            record, path = store.reserve(original, capture_mode=arguments["capture_mode"], purpose=arguments["purpose"])
            for _ in range(saves):   # Blender runs the handlers in list order, one appended meanwhile too (P3 late).
                self.dispatch(self.bpy.app.handlers.save_pre, path)
                if fail:
                    raise RuntimeError("Injected capture failure")
                path.write_bytes(b"synthetic scene document")
                self.dispatch(self.bpy.app.handlers.save_post, path)
            return store.complete(record["id"])
        with patch.object(checkpoint_capture, "capture_checkpoint", side_effect=native):
            return document_checkpoint.capture(self.bpy, self.store, "window", "", capture_mode="EVERY_REQUEST",
                                               purpose="request", load="load-a")

    @staticmethod
    def dispatch(handlers, path):
        index = 0
        while index < len(handlers):
            handlers[index](str(path))
            index += 1

    def swap(self, *_arguments):
        first, second = self.scenes
        first.name_full, second.name_full = second.name_full, first.name_full

    def test_a_save_pre_rename_by_angelos_own_code_is_in_the_map(self):
        self.bpy.app.handlers.save_pre.append(self.swap)
        with patch("angelo_agent.handler_owner.bundled", return_value=True):   # As if Blender's or Angelo's own.
            record = self.capture()
        self.assertEqual(scene_map.read(self.store, record["id"]), {"load": "load-a", "names": [[11, "Harbor"], [12, "Atelier"]]})
        self.assertEqual(self.bpy.app.handlers.save_pre, [self.swap], "the capture's own handler is gone")

    def test_an_add_ons_save_handler_keeps_no_map(self):
        """Round 3 M-3: an add-on can rename around the write and reorder handlers, so both reads can agree on names
        the file does not hold (a late swap before the write, a restore inserted first in save_post). Any add-on save
        handler, even one that renames nothing, keeps no map; Restore rebinds by name."""
        def restore(*_arguments):
            self.swap()

        def register(*_arguments):
            if self.swap not in self.bpy.app.handlers.save_pre:
                self.bpy.app.handlers.save_pre.append(self.swap)
                self.bpy.app.handlers.save_post.insert(0, restore)
        for handlers, handler in ((self.bpy.app.handlers.save_pre, register), (self.bpy.app.handlers.save_post, len),
                                  (self.bpy.app.handlers.save_pre, len)):
            with self.subTest(handler=handler):
                self.bpy.app.handlers.save_pre.clear()
                self.bpy.app.handlers.save_post.clear()
                self.scenes[:] = scenes(Atelier=11, Harbor=12)
                handlers.append(handler if handler is register else (lambda *_arguments: None))
                record = self.capture()
                self.assertIsNone(scene_map.read(self.store, record["id"]))

    def test_saves_that_disagree_keep_no_map_and_no_handler_runs_reads_before(self):
        self.bpy.app.handlers.save_pre.append(self.swap)
        record = self.capture(saves=2)   # The swap runs twice: the two reads differ.
        self.assertIsNone(scene_map.read(self.store, record["id"]))
        self.bpy.app.handlers.save_pre.clear()
        with patch.object(checkpoint_capture, "capture_checkpoint",
                          side_effect=lambda store, original, **arguments: store.complete(
                              (lambda r: (r[1].write_bytes(b"document"), r[0])[1])(
                                  store.reserve(original, capture_mode=arguments["capture_mode"],
                                                purpose=arguments["purpose"]))["id"])):
            record = document_checkpoint.capture(self.bpy, self.store, "window", "", capture_mode="EVERY_REQUEST",
                                                 purpose="request", load="load-a")   # Runs no handler.
        self.assertEqual(scene_map.read(self.store, record["id"]), {"load": "load-a", "names": [[11, "Atelier"], [12, "Harbor"]]})

    def test_a_rename_by_a_handler_registered_during_the_dispatch_keeps_no_map(self):
        """Round 2 P2: a save_pre handler registers the swap during the dispatch; it runs after Angelo's read."""
        def register(*_arguments):
            if self.swap not in self.bpy.app.handlers.save_pre:
                self.bpy.app.handlers.save_pre.append(self.swap)
        self.bpy.app.handlers.save_pre.append(register)
        record = self.capture()
        self.assertIsNone(scene_map.read(self.store, record["id"]), "Restore rebinds by name, never by a wrong map")
        self.assertEqual(self.bpy.app.handlers.save_post, [])

    def test_a_save_post_rename_never_changes_the_map(self):
        self.bpy.app.handlers.save_post.append(self.swap)
        with patch("angelo_agent.handler_owner.bundled", return_value=True):
            record = self.capture()
        self.assertEqual(scene_map.read(self.store, record["id"]), {"load": "load-a", "names": [[11, "Atelier"], [12, "Harbor"]]})

    def test_a_failed_capture_removes_its_handler(self):
        with self.assertRaisesRegex(RuntimeError, "Injected"):
            self.capture(fail=True)
        self.assertEqual(self.bpy.app.handlers.save_pre, [])
        self.assertEqual(self.bpy.app.handlers.save_post, [])


class PrivateStateTests(unittest.TestCase):
    """Review P1/F2: a project saved in the profile (or in a folder inside it) never exposes application state."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-private-state-")
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name).resolve()
        session, checkpoint = str(uuid.uuid4()), str(uuid.uuid4())
        self.map = "%s/%s/%s.json" % (scene_map.FOLDER, session, checkpoint)
        (self.profile / scene_map.FOLDER / session).mkdir(parents=True)
        (self.profile / self.map).write_text('{"load":"synthetic","names":[[1,"PRIVATE-SCENE"]]}')
        (self.profile / "sessions").mkdir()
        (self.profile / "sessions" / "chat.json").write_text("{}")
        (self.profile / "workspace").mkdir()
        (self.profile / "workspace" / "notes.txt").write_text("notes")
        (self.profile / "projects" / "boat").mkdir(parents=True)

    def refused(self, files, name, arguments):
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            files.execute(name, arguments)

    def test_a_workspace_in_the_profile_refuses_every_file_tool(self):
        own = self.profile / "workspace"
        for root in (self.profile, self.profile / "projects" / "boat"):
            for exception in ({}, {"own": own}):
                with self.subTest(root=root, exception=exception):
                    files = FileWorkspace(root, excluded=[self.profile], **exception)
                    self.refused(files, "angelo_list_files", {"path": "."})
                    if root == self.profile:
                        self.refused(files, "angelo_list_files", {"path": scene_map.FOLDER})
                        self.refused(files, "angelo_read_file", {"path": self.map, "start": 0, "length": 4096})
                        self.refused(files, "angelo_read_file", {"path": "sessions/chat.json", "start": 0, "length": 64})
                        self.refused(files, "angelo_write_file", {"path": "sessions/chat.json", "content": "x"})

    def test_the_chats_own_default_workspace_is_the_one_exception(self):
        own = self.profile / "workspace"
        files = FileWorkspace(own, excluded=[self.profile], own=own)
        self.assertEqual([entry["name"] for entry in files.execute("angelo_list_files", {"path": "."})["entries"]],
                         ["notes.txt"])
        self.assertEqual(files.execute("angelo_read_file", {"path": "notes.txt", "start": 0, "length": 64})["text"],
                         "notes")
        outside = FileWorkspace(self.profile / "projects", excluded=[self.profile], own=own)
        self.refused(outside, "angelo_list_files", {"path": "."})

    def test_the_window_passes_the_chats_default_workspace_as_the_exception(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/ui.py").read_text()
        self.assertIn("own=controller.workspace", source)


if __name__ == "__main__":
    unittest.main()
