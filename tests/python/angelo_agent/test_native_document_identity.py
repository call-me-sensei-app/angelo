# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat keeps working on its own file across save, reload, quit and reopen; any other file still invalidates it.

Dependency-free: real ``Controller`` objects and ``document_identity`` driven through a stand-in for ``ui`` and a tiny
model of Blender's files (a file read gives every scene a new session uid; custom properties travel with the file).
No Blender, provider process or model. The exposing case (1.0.6 GUI test, 2026-10-29): Save As stool.blend, quit,
Open Recent stool.blend, and the chat said "Document changed. Start New Chat before issuing scene commands."
"""

import copy
import itertools
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import document_identity as identity  # noqa: E402
from angelo_agent import document_scene  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.display_text import Text  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

CHANGED = "Document changed"
_uids = itertools.count(1)


class Scene(dict):
    """A scene: its custom properties are the dict; ``get`` and item assignment as ID properties offer them."""

    def __init__(self, name, props=None, library=None):
        super().__init__(props or {})
        self.name_full, self.session_uid, self.library, self.override_library = name, next(_uids), library, None


class Blender:
    """Angelo's window over a disk of .blend files: {path: [(scene name, its custom properties), ...]}."""

    def __init__(self, directory):
        self.directory, self.disk = Path(directory), {}
        self._files = []   # (a disk entry, its file number): kept, so a number is never reused for another entry.
        self.ui = SimpleNamespace(_document_serial="startup", _restoring_snapshot=False,
                                  _capturing_checkpoint=False, _controllers={},
                                  _controller=object(),   # Angelo's window has its chat (ui.get_controller).
                                  state_directory=lambda: str(self.directory / "state"),
                                  bpy=SimpleNamespace(data=SimpleNamespace(filepath="", scenes=[Scene("Scene")])))
        self.shown = self.ui.bpy.data.scenes[0]
        self.ui.document_key = lambda: (self.ui._document_serial, self.shown.session_uid)
        # As the add-on registers them (ui.register, document_identity.register): the load's end decides deferred chats.
        self.ui.document_load_post = lambda *_: None
        self.ui.bpy.app = SimpleNamespace(handlers=SimpleNamespace(load_post=[self.ui.document_load_post],
                                                                   load_post_fail=[identity._load_failed]))
        self.loads = 0

    def file_identity(self, path):
        """The model's ``file_identity`` (macOS-like: device, number, creation stamp): a save or a copy writes a new
        entry (a new file, a new stamp); a move keeps it."""
        entry = self.disk.get(path)
        if entry is None:
            return None
        number = next((known for item, known in self._files if item is entry), None)
        if number is None:
            number = len(self._files) + 1
            self._files.append((entry, number))
        return [1, number, number]

    def path(self, name):
        return str(self.directory / name)

    def scene(self, name):
        return next(scene for scene in self.ui.bpy.data.scenes if scene.name_full == name)

    def save(self, name):
        path = self.path(name)
        identity.stamp(self.ui, path)   # save_pre
        self.disk[path] = [(scene.name_full, copy.deepcopy(dict(scene))) for scene in self.ui.bpy.data.scenes]
        self.ui.bpy.data.filepath = path
        identity.after_save(self.ui)    # ui.document_save_post

    def _load(self, path, scenes):
        left = identity.leaving(self.ui)   # ui.document_load_pre
        self.loads += 1
        self.ui._document_serial = "load-%d" % self.loads
        if not self.ui._restoring_snapshot:   # As ui.document_load_pre: a Restore rebinds every chat itself.
            identity.follow(self.ui, left)
        self.ui.bpy.data.filepath, self.ui.bpy.data.scenes = path, scenes
        self.shown = scenes[0]
        identity.after_load(self.ui)       # ui.document_load_post

    def open(self, name):
        path = self.path(name)
        self._load(path, [Scene(scene, props) for scene, props in copy.deepcopy(self.disk[path])])

    def new(self):   # File > New: the home file, whatever it carries.
        self._load("", [Scene("Scene", {identity.PROPERTY: "f" * 32})])

    def copy(self, source, target):   # A Finder copy: the same bytes at another path.
        self.disk[self.path(target)] = copy.deepcopy(self.disk[self.path(source)])

    def chat(self, state_directory, *, work=True):
        chat = Controller(state_directory)
        chat.set_document(self.ui.document_key())
        if work:
            chat.state["messages"].append({"role": "user", "text": "Make a stool"})
            identity.remember(chat, self.ui)   # ui.prepare_controller, before the request
        self.ui._controllers[chat.state["id"]] = chat
        return chat

    def quit(self, chat):   # Saved and closed; the next launch starts on the home file.
        chat.save()
        chat.close()
        self.ui._controllers.clear()
        self.ui._document_serial, self.ui.bpy.data.filepath = "relaunch", ""
        self.ui.bpy.data.scenes = [Scene("Scene")]
        self.shown = self.ui.bpy.data.scenes[0]


def notices(chat):
    chat.pump_events()
    return [m for m in chat.state["messages"] if CHANGED in str(m.get("text"))]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.blender = Blender(self.root.name)
        files = mock.patch.object(identity, "file_identity", self.blender.file_identity)   # The model's file system.
        files.start()
        self.addCleanup(files.stop)
        self.states = itertools.count()
        self.chats = []

    def tearDown(self):
        for chat in self.chats:
            chat.close()
        self.root.cleanup()

    def chat(self, **options):
        chat = self.blender.chat(str(Path(self.root.name) / ("state-%d" % next(self.states))), **options)
        self.chats.append(chat)
        return chat

    def assert_bound(self, chat, scene="Scene"):
        self.assertFalse(chat.document_invalid)
        self.assertIsNone(chat.awaited_file)
        self.assertEqual(chat.document, (self.blender.ui._document_serial, self.blender.scene(scene).session_uid))
        self.assertEqual(notices(chat), [])

    def assert_invalidated(self, chat):
        self.assertTrue(chat.document_invalid)
        self.assertEqual(len(notices(chat)), 1)


class ReloadTests(Base):
    """In one session: the same document rebinds, anything else invalidates as before."""

    def setUp(self):
        super().setUp()
        self.blender.save("stool.blend")
        self.stool = self.chat()

    def test_reopening_the_same_file_keeps_the_chat_with_its_memory_stale(self):
        generation = self.stool.state["task_memory"]["generation"]
        self.blender.open("stool.blend")
        self.assert_bound(self.stool)
        memory = self.stool.state["task_memory"]
        self.assertGreater(memory["generation"], generation, "the agent observes the scene again")
        self.stool.require_model_chat = lambda: None
        self.stool.apply_next_connection = lambda: None
        with mock.patch.object(self.stool, "_auto_route", side_effect=RuntimeError("past the document gate")):
            with self.assertRaisesRegex(RuntimeError, "past the document gate"):
                self.stool.send("Make it taller")

    def test_a_reload_revokes_what_the_earlier_load_staged(self):
        with mock.patch.object(self.stool, "invalidate_native_stages") as stages, \
                mock.patch.object(self.stool, "cancel_tools") as calls:
            self.blender.open("stool.blend")
        stages.assert_called_once_with(identity.LEFT_STAGES)
        calls.assert_called_once_with(identity.LEFT_CALLS)
        self.assert_bound(self.stool)

    def test_a_file_with_another_id_at_the_same_path_invalidates(self):
        self.blender.disk[self.blender.path("stool.blend")] = [("Scene", {identity.PROPERTY: "a" * 32})]
        self.blender.open("stool.blend")
        self.assert_invalidated(self.stool)

    def test_a_finder_copy_at_another_path_invalidates(self):
        self.blender.copy("stool.blend", "stool copy.blend")
        self.blender.open("stool copy.blend")
        self.assert_invalidated(self.stool)

    def test_a_file_without_a_stored_id_invalidates(self):
        self.blender.disk[self.blender.path("old.blend")] = [("Scene", {})]
        self.blender.open("old.blend")
        self.assert_invalidated(self.stool)

    def test_an_untitled_file_invalidates_even_if_the_home_file_carries_an_id(self):
        self.blender.new()
        self.assert_invalidated(self.stool)

    def test_save_as_moves_the_chat_and_the_old_path_is_another_document(self):
        self.blender.save("stool v2.blend")
        self.blender.open("stool v2.blend")
        self.assert_bound(self.stool)
        self.blender.open("stool.blend")   # The older copy, same id, at the path the chat left.
        self.assert_invalidated(self.stool)

    def test_a_failed_load_keeps_the_chat_on_the_document_still_open(self):
        left = identity.leaving(self.blender.ui)
        self.blender.ui._document_serial = "load-failed"
        identity.follow(self.blender.ui, left)
        identity.after_load(self.blender.ui, failed=True)   # load_post_fail: nothing was read.
        self.assert_bound(self.stool)

    def test_a_fresh_chat_follows_any_file_quietly_as_before(self):
        fresh = self.chat(work=False)
        self.blender.new()
        self.assertFalse(fresh.document_invalid)
        self.assertEqual(fresh.document[0], self.blender.ui._document_serial)


class ScenesTests(Base):
    """A chat works on its own scene; the same document rebinds it there, by the name its file holds."""

    def test_each_chat_is_rebound_to_its_own_scene(self):
        data = self.blender.ui.bpy.data
        data.scenes.append(Scene("Shop"))
        on_scene = self.chat()
        self.blender.shown = self.blender.scene("Shop")
        on_shop = self.chat()
        self.blender.save("two.blend")
        self.blender.open("two.blend")   # The file shows its first scene.
        self.assert_bound(on_scene, "Scene")
        self.assert_bound(on_shop, "Shop")
        self.assertTrue(document_scene.away(on_shop, self.blender.ui.document_key()), "it waits for its scene, as before")

    def test_a_rename_after_the_last_save_finds_the_name_the_file_holds(self):
        self.blender.ui.bpy.data.scenes.append(Scene("Shop"))
        self.blender.shown = self.blender.scene("Shop")
        chat = self.chat()
        self.blender.save("two.blend")
        self.blender.scene("Shop").name_full = "Workshop"   # Not saved: the file still says "Shop".
        self.blender.open("two.blend")
        self.assert_bound(chat, "Shop")


class RecoveryScopeTests(Base):
    """1.0.6 GUI test: a chat stranded by the reopen made every later Restore of the file report "not everything came
    back" ("Cross-chat recovery scope requires the same current native document and profile"). After a reopen of the
    same document every chat of it is on the new load, so a Restore from any of them shares its scope with the rest."""

    def test_after_a_reopen_a_restore_from_a_new_chat_shares_the_scope_with_the_earlier_chat(self):
        import uuid
        shared = str(Path(self.root.name) / "profile")
        data = self.blender.ui.bpy.data
        data.scenes.append(Scene("Shop"))
        chats = []
        for scene in ("Scene", "Shop"):
            self.blender.shown = self.blender.scene(scene)
            chat = Controller(shared, session_id=str(uuid.uuid4()))
            self.chats.append(chat)
            chat.set_document(self.blender.ui.document_key())
            chat.state["messages"].append({"role": "user", "text": "Make a stool"})
            identity.remember(chat, self.blender.ui)
            self.blender.ui._controllers[chat.state["id"]] = chat
            chats.append(chat)
        earlier, other = chats
        self.blender.save("stool.blend")
        self.blender.open("stool.blend")
        newer = Controller(shared, session_id=str(uuid.uuid4()))
        self.chats.append(newer)
        newer.set_document(self.blender.ui.document_key())
        record, path = newer.checkpoints.reserve("")
        path.write_bytes(b"BLENDER-v502" + b"recovery scene")
        newer.checkpoints.complete(record["id"])
        for sibling in (earlier, other):
            with self.subTest(scene=sibling.document):
                self.assertFalse(sibling.document_invalid)
                scope = sibling.project_scope_for_restore(record["id"], source_controller=newer)
                self.assertEqual(scope.checkpoint_id, record["id"])


class RelaunchTests(Base):
    """A restored chat waits for its own file and never binds to (or looks like it belongs to) the home file."""

    def relaunch(self, chat):
        self.chats.remove(chat)
        self.blender.quit(chat)
        restored = Controller(chat.store.directory)   # Its saved state, read at the next launch.
        self.chats.append(restored)
        identity.restore(restored, self.blender.ui)
        self.blender.ui._controllers[restored.state["id"]] = restored
        return restored

    def test_reopening_its_file_after_a_relaunch_continues_the_chat(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        restored = self.relaunch(chat)
        self.assertIsNone(restored.document, "never bound to the home file")
        self.assertIsNotNone(restored.awaited_file)
        why = document_scene.refusal(restored)
        self.assertIsInstance(why, Text)
        self.assertIn("stool.blend", why)
        restored.set_document(self.blender.ui.document_key())   # A request's set_document never binds it.
        self.assertIsNone(restored.document)
        with self.assertRaises(RuntimeError):
            restored.send("Make it taller")
        self.blender.open("stool.blend")
        self.assert_bound(restored)
        self.assertIsNone(document_scene.refusal(restored))

    def test_another_file_leaves_a_waiting_chat_waiting(self):
        self.blender.save("stool.blend")
        restored = self.relaunch(self.chat())
        self.blender.ui.bpy.data.scenes = [Scene("Scene")]
        self.blender.save("table.blend")
        self.blender.open("table.blend")
        self.assertIsNone(restored.document)
        self.assertFalse(restored.document_invalid)
        self.blender.open("stool.blend")
        self.assert_bound(restored)

    def test_a_chat_on_a_file_never_saved_says_so_and_never_binds(self):
        chat = self.chat()
        restored = self.relaunch(chat)
        self.assertEqual(str(document_scene.refusal(restored)), identity.UNSAVED)
        self.blender.save("later.blend")
        self.blender.open("later.blend")
        self.assertIsNone(restored.document)

    def test_a_chat_saved_before_this_build_binds_as_before(self):
        chat = self.chat()
        chat.state["document_file"] = None
        restored = self.relaunch(chat)
        self.assertEqual(restored.document, self.blender.ui.document_key())

    def test_a_restored_chat_on_the_open_file_binds_at_once(self):
        self.blender.save("stool.blend")
        restored = self.relaunch(self.chat())
        self.blender.open("stool.blend")
        restored.close()
        self.chats.remove(restored)
        again = Controller(restored.store.directory)
        self.chats.append(again)
        identity.restore(again, self.blender.ui)   # Launched on its file: get_controller after the load.
        self.assert_bound(again)


class StampTests(Base):
    def test_the_id_goes_into_local_scenes_only_at_a_real_save(self):
        data = self.blender.ui.bpy.data
        linked = Scene("Linked", library=object())
        data.scenes.append(linked)
        identity.stamp(self.blender.ui, "")   # The home file.
        self.assertNotIn(identity.PROPERTY, data.scenes[0])
        self.blender.ui._capturing_checkpoint = True   # A request snapshot never changes the document.
        identity.stamp(self.blender.ui, self.blender.path("a.blend"))
        self.assertNotIn(identity.PROPERTY, data.scenes[0])
        self.blender.ui._capturing_checkpoint = False
        identity.stamp(self.blender.ui, self.blender.path("a.blend"))
        self.assertEqual(data.scenes[0][identity.PROPERTY], identity.current(self.blender.ui))
        self.assertNotIn(identity.PROPERTY, linked)

    def test_scenes_that_disagree_carry_no_id(self):
        one, two = "1" * 32, "2" * 32
        self.assertEqual(identity.stored([Scene("A", {identity.PROPERTY: one}), Scene("B")]), one)
        self.assertIsNone(identity.stored([Scene("A", {identity.PROPERTY: one}), Scene("B", {identity.PROPERTY: two})]))
        self.assertIsNone(identity.stored([Scene("A", {identity.PROPERTY: "not an id"})]))

    def test_opening_a_file_without_an_id_writes_nothing(self):
        self.blender.disk[self.blender.path("old.blend")] = [("Scene", {})]
        self.blender.open("old.blend")
        self.assertNotIn(identity.PROPERTY, self.blender.ui.bpy.data.scenes[0])


class RecordPersistenceTests(unittest.TestCase):
    def test_the_record_round_trips_and_a_malformed_one_is_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory, "00000000-0000-4000-8000-000000000001")
            state = store.load()
            record = {"id": "a" * 32, "path": "/Users/me/椅子　モデル.blend", "scene": "Family 👨‍👩",
                      "file": [16777234, 2 ** 40, 1761700000123456789]}
            for saved, read in ((record, record), (dict(record, id="nope"), None), (dict(record, path="a\0b"), None),
                                (None, None)):
                with self.subTest(saved=saved):
                    state["document_file"] = saved
                    store.save(state)
                    self.assertEqual(SessionStore(directory, store.session_id).load()["document_file"], read)

    def test_clean_record_requires_exactly_its_fields(self):
        empty = {"id": None, "path": "", "scene": "", "file": None}
        self.assertIsNone(identity.clean_record(dict(empty, name="x")))
        self.assertIsNone(identity.clean_record({"id": None, "path": "", "scene": ""}), "no file field")
        self.assertEqual(identity.clean_record(empty), empty)
        self.assertIsNone(identity.clean_record(dict(empty, path="\udc80.blend")), "not UTF-8")
        self.assertIsNone(identity.clean_record(dict(empty, file=[1])))
        self.assertIsNone(identity.clean_record(dict(empty, file=[1, 2])), "device, number and stamp")


class RestoreTests(unittest.TestCase):
    def test_a_waiting_chat_refuses_restore_and_is_left_out_of_another_chats(self):
        from angelo_agent import checkpoint_restore_ui as flow
        waiting = SimpleNamespace(awaited_file={"id": "a" * 32, "path": "/x/stool.blend", "scene": "Scene",
                                                "file": None},
                                  state={"id": "waiting"}, rebind_after_restore=mock.Mock(), save=mock.Mock())
        operator = SimpleNamespace(report=mock.Mock())
        ui = SimpleNamespace(get_controller=lambda: waiting, _controllers={"waiting": waiting})
        self.assertEqual(flow.execute_restore(ui, operator), {'CANCELLED'})
        self.assertIn("stool.blend", operator.report.call_args.args[1])
        other = SimpleNamespace(checkpoints=None, state={"id": "other"}, save=mock.Mock(), document=None,
                                rebind_after_restore=mock.Mock(return_value={}))
        ui = SimpleNamespace(document_key=lambda: ("new", 1), _controllers={"waiting": waiting, "other": other},
                             bpy=SimpleNamespace(data=SimpleNamespace(scenes=[])))
        with mock.patch("angelo_agent.checkpoint_scene_map.read", return_value=None), \
                mock.patch.object(flow, "_mark_scene_history"):
            flow._finish_chats(ui, other, "checkpoint", {}, {}, 0, [], {})
        waiting.rebind_after_restore.assert_not_called()
        other.rebind_after_restore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
