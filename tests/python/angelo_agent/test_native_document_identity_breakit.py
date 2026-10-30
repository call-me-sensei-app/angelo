# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The independent break-it review of the document identity (2026-10-29, on 0312b0633f6), one regression per finding.

Dependency-free, on ``test_native_document_identity``'s model of Angelo's window over a disk of .blend files.
"""

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from test_native_document_identity import Base, Scene, identity, notices  # noqa: E402
from angelo_agent import document_offer, document_scene, worker_ending, worker_resume  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402


class Launch(Base):
    """A chat that holds work, saved and closed, read again at the next launch on the home file."""

    def quit_and_restore(self, chat, *, restore=True):
        self.chats.remove(chat)
        self.blender.quit(chat)
        restored = Controller(chat.store.directory, session_id=chat.store.session_id)
        self.chats.append(restored)
        if restore:
            identity.restore(restored, self.blender.ui)   # ui.get_controller
            self.blender.ui._controllers[restored.state["id"]] = restored
        return restored

    def session_chat(self):
        """A chat with its own session file in one profile, as ``ui.get_controller`` and MCP apps make them."""
        chat = Controller(str(Path(self.root.name) / "profile"), session_id=str(uuid.uuid4()))
        self.chats.append(chat)
        chat.set_document(self.blender.ui.document_key())
        chat.state["messages"].append({"role": "user", "text": "Make a stool"})
        identity.remember(chat, self.blender.ui)
        self.blender.ui._controllers[chat.state["id"]] = chat
        return chat


class WorkersEndAtAReloadTests(Base):
    """Finding 1: a reload of the lead's own file ends its worker batch and stops its workers, as another file does."""

    def setUp(self):
        super().setUp()
        self.blender.save("stool.blend")
        self.lead = self.chat()
        worker_resume._batches.clear()
        worker_resume._batches[self.lead.state["id"]] = {1}
        self.addCleanup(worker_resume._batches.clear)

    def step(self):
        rows = [{"worker": 1, "name": "Leg", "state": "idle", "turn": "completed", "mergeable": True}]
        with mock.patch.object(worker_resume, "_idle", return_value=True), \
                mock.patch.object(worker_resume, "_ended", return_value=True), \
                mock.patch.object(worker_resume, "_unmerged", return_value=rows), \
                mock.patch.object(worker_resume, "_fire") as fire:
            worker_resume.step(self.blender.ui)
        return fire

    def test_another_file_ends_the_batch(self):
        self.blender.ui.bpy.data.scenes = [Scene("Scene")]
        self.blender.save("other.blend")
        self.blender.open("other.blend")
        self.blender.open("stool.blend")
        self.assertFalse(self.step().called)

    def test_a_revert_of_the_same_file_ends_the_batch(self):
        self.blender.open("stool.blend")
        self.assertFalse(self.lead.document_invalid)
        self.assertFalse(self.step().called, "the worker-done turn would merge pre-reload parts on the reloaded file")

    def test_a_revert_stops_the_leads_workers_with_its_reason(self):
        lead = self.lead.state["id"]
        with mock.patch.object(worker_ending, "_holding", return_value={lead}), \
                mock.patch.object(worker_ending, "document_replaced",
                                  side_effect=lambda: self.assertIn(lead, worker_ending._reloaded)) as replaced:
            self.blender.open("stool.blend")
        replaced.assert_called_once_with()
        worker_ending._reloaded.clear()


class NamesTests(Launch):
    """Finding 2: any name a file system or Blender allows is kept; a recorded chat never binds as a legacy one."""

    def check(self, file_name, scene_name="Scene"):
        self.blender.ui.bpy.data.scenes[0].name_full = scene_name
        self.blender.save(file_name)
        chat = self.chat()
        self.assertEqual(chat.state["document_file"]["scene"], scene_name)
        restored = self.quit_and_restore(chat)
        self.assertIsNone(restored.document, "bound to the startup document after the relaunch")
        self.assertIn(Path(file_name).name, document_scene.refusal(restored))
        self.blender.open(file_name)
        self.assert_bound(restored, scene_name)

    def test_full_width_space_in_a_file_name(self):
        self.check("椅子　モデル.blend")

    def test_no_break_space_in_a_file_name(self):
        self.check("stool v2.blend")

    def test_zero_width_joiner_in_a_scene_name(self):
        self.check("stool.blend", scene_name="Family \U0001F468‍\U0001F469‍\U0001F467")

    def test_a_control_character_shows_as_a_replacement_only(self):
        self.assertEqual(identity._display("/a/b\x07c.blend"), "b�c.blend")


class ExternalConversationTests(Launch):
    """Finding 3: an MCP app's conversation, restored when its app reconnects, waits for its file too."""

    def test_it_waits_for_its_file_and_keeps_its_record(self):
        from angelo_agent.external_host import UiEnvironment
        self.blender.save("stool.blend")
        chat = self.session_chat()
        record = dict(chat.state["document_file"])
        self.chats.remove(chat)
        self.blender.quit(chat)
        ui = self.blender.ui
        ui.Controller, ui.state_directory = Controller, lambda: chat.store.directory
        ui.get_controller, ui._sessions = (lambda: None), None
        restored = UiEnvironment(ui).make_controller(chat.state["id"])
        self.chats.append(restored)
        self.assertIsNone(restored.document, "bound to the startup document %r" % (restored.document,))
        identity.remember(restored, ui)   # ui.prepare_controller before an app's task
        self.assertEqual(restored.state["document_file"], record)


class PersistenceFailureTests(Launch):
    """Findings 4 and 5: a chat whose save fails still binds, and never stops another chat's binding or the load."""

    def test_restoring_a_chat_whose_save_fails_binds_it_without_raising(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        restored = self.quit_and_restore(chat, restore=False)
        self.blender.open("stool.blend")   # Launched on the chat's file.
        restored.store._unkept = True      # Every save raises OSError (read-only profile, an unkept file).
        try:
            identity.restore(restored, self.blender.ui)
        finally:
            restored.store._unkept = False
        self.blender.ui._controllers[restored.state["id"]] = restored
        self.assertTrue(restored.persistence_warning)
        self.assert_bound(restored)

    def test_one_chat_that_cannot_save_never_stops_the_others(self):
        self.blender.save("stool.blend")
        first, second = self.chat(), self.chat()
        first.store._unkept = True
        try:
            self.blender.open("stool.blend")   # Raises out of ui.document_load_post before the fix.
        finally:
            first.store._unkept = False
        self.assert_bound(first)
        self.assert_bound(second)

    def test_one_chat_that_raises_never_stops_the_others(self):
        self.blender.save("stool.blend")
        first, second = self.chat(), self.chat()
        with mock.patch.object(first, "cancel_tools", side_effect=RuntimeError("broken chat")):
            self.blender.open("stool.blend")
        self.assert_bound(second)


class RestoreTests(Launch):
    """Findings 6, 7 and 8: what a Restore leaves the document's identity."""

    def restore_snapshot(self, scenes, original):
        ui = self.blender.ui
        recovery = str(Path(self.root.name) / ("recovered-%s.blend" % uuid.uuid4()))
        ui._restoring_snapshot = True   # execute_restore: the recovery copy opens...
        self.blender._load(recovery, [Scene(name, dict(props)) for name, props in scenes])
        ui._restoring_snapshot = False
        ui.bpy.data.filepath = original   # ...then the native step makes it the document open at the Restore.

    def test_a_failed_load_after_a_restore_keeps_the_chat_and_its_path(self):
        ui = self.blender.ui
        self.blender.save("stool.blend")
        chat = self.chat()
        self.restore_snapshot(self.blender.disk[self.blender.path("stool.blend")], self.blender.path("stool.blend"))
        chat.document, chat.document_invalid = ui.document_key(), False   # rebind_after_restore
        left = identity.leaving(ui)
        ui._document_serial = "load-failed"   # ui.document_load_pre
        identity.follow(ui, left)
        identity.after_load(ui, failed=True)   # load_post_fail: nothing was read, the document's path stays
        self.assertFalse(chat.document_invalid)
        self.assertEqual(ui.bpy.data.filepath, self.blender.path("stool.blend"))
        self.assertEqual(identity.document_path(ui), self.blender.path("stool.blend"))

    def test_the_file_a_save_as_left_stays_another_document_after_a_restore(self):
        ui = self.blender.ui
        self.blender.save("stool.blend")
        chat = self.chat()
        original, snapshot = ui.bpy.data.filepath, [(s.name_full, dict(s)) for s in ui.bpy.data.scenes]
        self.blender.save("stool v2.blend")
        self.restore_snapshot(snapshot, original)   # A checkpoint captured before the Save As.
        chat.document, chat.document_invalid = ui.document_key(), False
        self.assertEqual(identity.document_path(ui), self.blender.path("stool v2.blend"))
        self.blender.open("stool.blend")
        self.assertTrue(chat.document_invalid)

    def test_a_chat_stays_bound_after_restore_and_reopens_its_file(self):
        """Under either Restore model (the recovery copy open, or the person's file kept as the open path) the
        document stays where the person's file lives, so the chat's file reopens as the same document."""
        ui = self.blender.ui
        self.blender.save("stool.blend")
        chat = self.chat()
        self.restore_snapshot([(s.name_full, dict(s)) for s in ui.bpy.data.scenes], self.blender.path("stool.blend"))
        chat.document, chat.document_invalid = ui.document_key(), False
        identity.remember(chat, ui)
        self.assertEqual(chat.state["document_file"]["path"], self.blender.path("stool.blend"))
        self.blender.open("stool.blend")
        self.assert_bound(chat)

    def test_a_snapshot_keeps_the_documents_id_unless_it_carries_another(self):
        """Round 2, E (reverses round 1's finding 8): a Restore reads only this document's own history, so a
        snapshot taken before the first Save (no id) stays this document; one carrying another id takes it."""
        ui = self.blender.ui
        self.blender.disk[self.blender.path("other.blend")] = [("Scene", {identity.PROPERTY: "c" * 32})]
        self.blender.open("other.blend")
        self.restore_snapshot([("Scene", {})], self.blender.path("other.blend"))
        self.assertEqual(identity.current(ui), "c" * 32)
        self.restore_snapshot([("Scene", {identity.PROPERTY: "d" * 32})], self.blender.path("other.blend"))
        self.assertEqual(identity.current(ui), "d" * 32)


class SaveCopyTests(Base):
    """Finding 9: a Save Copy leaves the document where it is, and its chats' records as they were."""

    def test_rename_then_save_copy_then_revert_keeps_the_chat_on_its_scene(self):
        data = self.blender.ui.bpy.data
        data.scenes.append(Scene("Shop"))
        self.blender.shown = self.blender.scene("Shop")
        chat = self.chat()
        self.blender.save("two.blend")
        self.blender.scene("Shop").name_full = "Workshop"
        copy_path = self.blender.path("backup.blend")
        identity.stamp(self.blender.ui, copy_path)   # save_pre gets the copy's path; the document's stays.
        self.blender.disk[copy_path] = [(s.name_full, dict(s)) for s in data.scenes]
        identity.after_save(self.blender.ui)
        self.assertEqual(identity.document_path(self.blender.ui), self.blender.path("two.blend"))
        self.blender.open("two.blend")
        self.assert_bound(chat, "Shop")


class JobWaitTests(Base):
    """Finding 10: a render wait cancelled by a reload of the same file says the document changed, as for another
    file (the job is interrupted by the load), never that the job goes on. No model-facing text changed."""

    def test_the_wait_says_the_document_changed(self):
        from angelo_agent.view_wait import JobWait
        self.blender.save("stool.blend")
        chat = self.chat()
        answers, real_leave = [], identity._leave

        def leave(target):
            wait = JobWait.__new__(JobWait)
            wait.manager = SimpleNamespace(record=lambda _i: {"id": "j1", "kind": "render", "status": "running",
                                                              "detail": {}})
            wait.identity, wait.controller, wait._current = "j1", target, None
            wait._wait_account, wait._terminal_waited, wait._terminal_left = None, 1.0, 10.0
            answers.append(wait._stopped_answer())
            real_leave(target)
        with mock.patch.object(identity, "_leave", leave):
            self.blender.open("stool.blend")
        self.assertEqual(answers[0]["waiting"]["ended"], "document_changed")
        self.assertNotIn("goes on", answers[0]["error"])
        self.assertFalse(chat.document_leaving, "bound again: a later wait is an ordinary one")


class SameIdElsewhereTests(Launch):
    """Round 1 finding 11, withdrawn in round 2 (A): the same id at another path is a copy or the file moved, and no
    handler can tell which, so a chat never binds there on its own; a waiting chat offers Continue, and only the
    person's click rebinds it. No file-system call decides it."""

    def move(self, name, folder):
        target = str(Path(self.root.name) / folder / name)
        self.blender.disk[target] = self.blender.disk.pop(self.blender.path(name))
        self.blender._load(target, [Scene(n, dict(p)) for n, p in self.blender.disk[target]])
        return target

    def offers(self, chat):
        chat.pump_events()
        return [m for m in chat.state["messages"] if (m.get("i18n") or [None])[0] == document_offer.OFFER]

    def test_a_moved_file_is_offered_and_continue_binds_it(self):
        self.blender.save("stool.blend")
        restored = self.quit_and_restore(self.chat())
        with mock.patch("os.path.exists", side_effect=AssertionError("stat")), \
                mock.patch("os.path.lexists", side_effect=AssertionError("stat")), \
                mock.patch("os.path.realpath", side_effect=AssertionError("stat")):
            moved = self.move("stool.blend", "moved")
        self.assertIsNotNone(restored.awaited_file, "never bound without the person")
        self.assertEqual(len(self.offers(restored)), 1)
        self.assertTrue(document_offer.offered(restored))
        self.assertTrue(document_offer.continue_with_open_file(restored, self.blender.ui))
        self.assert_bound(restored)
        self.assertEqual(restored.state["document_file"]["path"], moved)

    def test_a_copy_is_only_offered(self):
        self.blender.save("stool.blend")
        restored = self.quit_and_restore(self.chat())
        self.blender.copy("stool.blend", "stool copy.blend")
        self.blender.open("stool copy.blend")
        self.assertIsNotNone(restored.awaited_file)
        self.assertEqual(len(self.offers(restored)), 1)
        self.blender.disk[self.blender.path("table.blend")] = [("Scene", {identity.PROPERTY: "e" * 32})]
        self.blender.open("table.blend")   # Another document: the offer is withdrawn, Continue does nothing.
        self.assertFalse(document_offer.offered(restored))
        self.assertFalse(document_offer.continue_with_open_file(restored, self.blender.ui))

    def test_renamed_while_open_then_a_backup_copy_opened(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        self.blender.copy("stool.blend", "stool backup.blend")
        self.blender.disk[self.blender.path("stool final.blend")] = self.blender.disk.pop(self.blender.path("stool.blend"))
        self.blender.open("stool backup.blend")
        self.assertTrue(chat.document_invalid)

    def test_relaunch_with_the_original_on_an_unmounted_volume(self):
        self.blender.save("stool.blend")
        self.blender.copy("stool.blend", "stool local.blend")
        restored = self.quit_and_restore(self.chat())
        self.blender.disk.pop(self.blender.path("stool.blend"))
        self.blender.open("stool local.blend")
        self.assertIsNotNone(restored.awaited_file)

    def test_a_file_renamed_while_open_is_another_document_at_its_reopen(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        self.move("stool.blend", "renamed")
        self.assertTrue(chat.document_invalid)


class OfferCardTests(unittest.TestCase):
    """The offer is a card in the waiting chat with Continue, whose click is the only route that moves the chat
    (``chat_notices`` and ``chat_welcome`` need Blender's drawing modules, so their wiring is read from source)."""

    ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"

    def test_the_card_offers_continue_only_while_the_offer_stands(self):
        import ast
        source = (self.ADDON / "chat_notices.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "_offer")
        namespace = {"document_offer": document_offer}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "chat_notices.py", "exec"), namespace)
        self.assertTrue(namespace["_offer"]({"role": "error", "i18n": [document_offer.OFFER, {"old": "a", "new": "b"}]}))
        self.assertFalse(namespace["_offer"]({"role": "error", "i18n": [identity.AWAITING, {"file": "a"}]}))
        self.assertIn('elif _offer(message) and _offer_stands(message, controller):', source)
        self.assertIn('(iface_("Continue"), "welcome_continue_file", None)', source)
        welcome = (self.ADDON / "chat_welcome.py").read_text(encoding="utf-8")
        self.assertIn('elif action == "welcome_continue_file":', welcome)
        self.assertIn("document_offer.continue_with_open_file(controller, ui)", welcome)


class RoundTwoTests(Launch):
    """Round 2 (on 91e7f635f31): B, C, D, F, G."""

    def lead_with_batch(self):
        self.blender.save("stool.blend")
        lead = self.chat()
        worker_resume._batches[lead.state["id"]] = {1}
        self.addCleanup(worker_resume._batches.clear)
        return lead

    def test_a_restore_ends_the_leads_worker_batch(self):
        ui = self.blender.ui
        lead = self.lead_with_batch()
        recovery = str(Path(self.root.name) / "state" / "recoveries" / "recovered-1.blend")
        ui._restoring_snapshot = True
        self.blender._load(recovery, [Scene(n, dict(p)) for n, p in self.blender.disk[self.blender.path("stool.blend")]])
        ui._restoring_snapshot = False
        lead.rebind_after_restore(ui.document_key(), "checkpoint")   # The real host rebind.
        self.assertFalse(worker_resume.pending(lead.state["id"]))

    def test_a_failed_open_leaves_chats_batches_and_workers_as_they_were(self):
        ui = self.blender.ui
        lead = self.lead_with_batch()
        generation = lead.state["task_memory"]["generation"]
        with mock.patch.object(worker_ending, "_holding", return_value={lead.state["id"]}), \
                mock.patch.object(worker_ending, "document_replaced") as replaced:
            left = identity.leaving(ui)
            ui._document_serial = "load-failed"
            identity.follow(ui, left)
            identity.after_load(ui, failed=True)
        self.assertFalse(replaced.called)
        self.assertTrue(worker_resume.pending(lead.state["id"]))
        self.assertEqual(lead.state["task_memory"]["generation"], generation, "no memory stale")
        self.assert_bound(lead)

    def test_a_save_into_angelos_state_folder_never_moves_the_document(self):
        ui = self.blender.ui
        self.blender.save("stool.blend")
        chat = self.chat()
        original = ui.bpy.data.filepath
        recovery = str(Path(self.root.name) / "state" / "recoveries" / "recovered-2.blend")
        ui._restoring_snapshot = True
        self.blender._load(recovery, [Scene(n, dict(p)) for n, p in self.blender.disk[original]])
        ui._restoring_snapshot = False
        chat.document, chat.document_invalid = ui.document_key(), False
        identity.stamp(ui, recovery)   # Ctrl+S after the Restore writes the open recovery copy.
        identity.after_save(ui)
        self.assertEqual((identity.document_path(ui), chat.state["document_file"]["path"]), (original, original))
        self.blender.open("stool.blend")
        self.assertFalse(chat.document_invalid)

    def test_a_save_as_into_angelos_state_folder_never_moves_the_document(self):
        ui = self.blender.ui
        self.blender.save("stool.blend")
        chat = self.chat()
        inside = str(Path(self.root.name) / "state" / "checkpoints" / "scene.blend")   # A script's Save As, say.
        identity.stamp(ui, inside)
        ui.bpy.data.filepath = inside
        identity.after_save(ui)
        self.assertEqual(chat.state["document_file"]["path"], self.blender.path("stool.blend"))
        self.assertEqual(identity.document_path(ui), self.blender.path("stool.blend"))

    def test_a_save_onto_the_copy_a_restore_opened_elsewhere_never_moves_the_document(self):
        ui = self.blender.ui
        self.blender.save("stool.blend")
        self.chat()
        opened = str(Path(self.root.name) / "elsewhere" / "recovered-3.blend")   # Not under the state folder.
        ui._restoring_snapshot = True
        self.blender._load(opened, [Scene("Scene", {})])
        ui._restoring_snapshot = False
        ui.bpy.data.filepath = self.blender.path("stool.blend")   # The native step: the document open at the Restore.
        identity.stamp(ui, opened)   # A save naming the copy (a Save Copy onto it): never the document's own.
        identity.after_save(ui)
        self.assertEqual(identity.document_path(ui), self.blender.path("stool.blend"))

    def test_a_restore_of_a_snapshot_from_before_the_first_save_keeps_the_file_reopenable(self):
        ui = self.blender.ui
        chat = self.chat()
        snapshot = [(s.name_full, {k: v for k, v in s.items() if k != identity.PROPERTY}) for s in ui.bpy.data.scenes]
        self.blender.save("stool.blend")
        identity.remember(chat, ui)
        recovery = str(Path(self.root.name) / "state" / "recoveries" / "recovered-4.blend")
        ui._restoring_snapshot = True
        self.blender._load(recovery, [Scene(n, dict(p)) for n, p in snapshot])
        ui._restoring_snapshot = False
        ui.bpy.data.filepath = self.blender.path("stool.blend")   # The native step: the document open at the Restore.
        chat.document, chat.document_invalid = ui.document_key(), False
        identity.remember(chat, ui)
        restored = self.quit_and_restore(chat)
        self.blender.open("stool.blend")
        self.assertIsNone(restored.awaited_file)

    def test_save_startup_file_never_touches_the_record(self):
        data = self.blender.ui.bpy.data
        data.scenes.append(Scene("Shop"))
        self.blender.shown = self.blender.scene("Shop")
        chat = self.chat()
        self.blender.save("two.blend")
        record = dict(chat.state["document_file"])
        self.blender.scene("Shop").name_full = "Workshop"
        identity.stamp(self.blender.ui, "")   # wm_homefile_write: save_pre("") then save_post.
        identity.after_save(self.blender.ui)
        self.assertEqual(chat.state["document_file"], record)
        self.assertNotIn(identity.PROPERTY + "x", data.scenes[0])
        self.blender.open("two.blend")
        self.assert_bound(chat, "Shop")

    def test_memory_is_stale_whenever_the_chat_is_rebound(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        generation = chat.state["task_memory"]["generation"]
        with mock.patch.object(worker_ending, "reloaded", side_effect=RuntimeError("worker unbind failed")):
            self.blender.open("stool.blend")
        self.assertEqual(chat.document[0], self.blender.ui._document_serial)
        self.assertGreater(chat.state["task_memory"]["generation"], generation)

    def test_a_failed_save_while_binding_is_said_in_the_chat_once(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        chat.store._unkept = True
        try:
            self.blender.open("stool.blend")
            self.blender.open("stool.blend")
        finally:
            chat.store._unkept = False
        chat.pump_events()
        rows = [m for m in chat.state["messages"] if (m.get("i18n") or [None])[0] == identity.SAVE_FAILED]
        self.assertEqual(len(rows), 1)


class FileIdentityTests(unittest.TestCase):
    """Round 3, A: the as-written path names whatever file is there now, so the record also holds the file itself
    (st_dev, st_ino), read only from the open document's own file at its load and save. On the real file system."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_a_retargeted_symlink_names_another_file(self):
        for version in ("v1", "v2"):
            (self.root / version).mkdir()
            (self.root / version / "stool.blend").write_bytes(version.encode())
        link = self.root / "current"
        link.symlink_to(self.root / "v1")
        path = str(link / "stool.blend")
        recorded = identity.file_identity(path)   # Recorded while current -> v1.
        link.unlink()
        link.symlink_to(self.root / "v2")
        self.assertFalse(identity.same_document("a" * 32, path, "a" * 32, path, recorded, identity.file_identity(path)))
        self.assertFalse(identity.same_document("a" * 32, path, "a" * 32, path), "an unknown file never matches")

    def test_a_file_replaced_by_a_copy_is_another_file(self):
        path = self.root / "stool.blend"
        path.write_bytes(b"one")
        recorded = identity.file_identity(str(path))
        (self.root / "copy.blend").write_bytes(b"one")
        os.replace(self.root / "copy.blend", path)   # A sync or a checkout writing a new file there.
        self.assertFalse(identity.same_document("a" * 32, str(path), "a" * 32, str(path), recorded,
                                                identity.file_identity(str(path))))

    def test_a_save_then_reopen_is_the_same_file(self):
        path = self.root / "stool.blend"
        path.write_bytes(b"one")
        (self.root / "stool.blend@").write_bytes(b"two")
        os.replace(self.root / "stool.blend@", path)   # Blender's save: a temporary file renamed over the target...
        recorded = identity.file_identity(str(path))  # ...so after_save records the new file.
        self.assertTrue(identity.same_document("a" * 32, str(path), "a" * 32, str(path), recorded,
                                               identity.file_identity(str(path))))


class RoundThreeTests(Launch):
    """Round 3 (on 79552795bb0): B, C, D, E."""

    def offered_copy(self):
        self.blender.save("stool.blend")
        restored = self.quit_and_restore(self.chat())
        self.blender.copy("stool.blend", "stool copy.blend")
        self.blender.open("stool copy.blend")
        restored.pump_events()
        return restored

    def offer_rows(self, chat):
        chat.pump_events()
        return [m for m in chat.state["messages"] if (m.get("i18n") or [None])[0] == document_offer.OFFER]

    def test_a_refused_reply_shows_the_offer_again_and_a_reopen_offers_again(self):
        chat = self.offered_copy()
        try:
            document_scene.refuse(chat)   # console.execute: the person typed "yes, continue".
        except RuntimeError as error:
            from angelo_agent.display_text import error_notice
            chat.emit("message", {"role": "error", **error_notice(error)})
        rows = self.offer_rows(chat)
        self.assertEqual(len(rows), 2, "the refusal is the offer itself, with Continue")
        self.assertIs(chat.state["messages"][-1], rows[-1])
        self.blender.open("stool copy.blend")
        self.assertEqual(len(self.offer_rows(chat)), 3, "a reopen of the copy offers again")
        self.assertTrue(document_offer.offered(chat))

    def test_continue_stays_usable_on_an_earlier_card(self):
        chat = self.offered_copy()
        chat.emit("message", {"role": "error", "text": "Something else"})
        chat.pump_events()
        self.assertIsNotNone(document_offer.names(chat))   # chat_notices draws Continue on every card naming it.
        self.assertTrue(document_offer.continue_with_open_file(chat, self.blender.ui))
        self.assert_bound(chat)

    def test_a_stale_continue_is_withdrawn_and_says_why(self):
        chat = self.offered_copy()
        self.blender.save("stool v3.blend")   # Save As while the offer is shown.
        self.assertFalse(document_offer.offered(chat))
        self.assertIsNone(document_offer.names(chat), "no Continue is drawn")
        self.assertFalse(document_offer.continue_with_open_file(chat, self.blender.ui))
        chat.pump_events()
        self.assertEqual((chat.state["messages"][-1].get("i18n") or [None])[0], document_offer.STALE)
        self.assertIsNotNone(chat.awaited_file)

    def test_a_failed_open_keeps_a_chat_on_an_untitled_document(self):
        ui = self.blender.ui
        chat = self.chat()
        key = chat.document
        left = identity.leaving(ui)
        ui._document_serial = "load-failed"
        identity.follow(ui, left)
        identity.after_load(ui, failed=True)
        self.assertFalse(chat.document_invalid)
        self.assertEqual(chat.document, ("load-failed", key[1]))

    def test_an_untitled_document_replaced_by_a_load_still_invalidates(self):
        chat = self.chat()
        self.blender.disk[self.blender.path("other.blend")] = [("Scene", {identity.PROPERTY: "c" * 32})]
        self.blender.open("other.blend")
        self.assertTrue(chat.document_invalid)

    def test_a_reloads_worker_ending_failure_is_said_in_the_chat(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        with mock.patch.object(worker_ending, "document_replaced", side_effect=RuntimeError("unbind failed")), \
                mock.patch.object(worker_ending, "_holding", return_value={chat.state["id"]}):
            self.blender.open("stool.blend")
        chat.pump_events()
        worker_ending._reloaded.clear()
        self.assertTrue(any((m.get("i18n") or [None])[0] == worker_ending.WORKERS_LEFT for m in chat.state["messages"]))


class RoundFourTests(Launch):
    """Round 4 (on 813ec776f6a): 1-5."""

    def remount(self, shift=4):
        """macOS gives a volume its st_dev by mount order (repro_r4_remount.py: the same image, another st_dev)."""
        model = identity.file_identity
        patcher = mock.patch.object(identity, "file_identity",
                                    lambda path: None if model(path) is None else [model(path)[0] + shift] + model(path)[1:])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_remount_keeps_a_bound_chat_and_binds_a_restored_one(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        self.remount()
        self.blender.open("stool.blend")
        self.assert_bound(chat)
        restored = self.quit_and_restore(chat)
        self.remount(8)
        self.blender.open("stool.blend")
        self.assertIsNone(restored.awaited_file)

    def test_same_file_ignores_the_device_only_where_a_creation_stamp_was_read(self):
        self.assertTrue(identity.same_file([1, 5, 7], [9, 5, 7]), "a remount (macOS)")
        self.assertFalse(identity.same_file([1, 5, 7], [1, 5, 8]), "a copy that reused the number")
        self.assertFalse(identity.same_file([1, 5, 0], [2, 5, 0]), "another device (Windows, Linux)")
        self.assertTrue(identity.same_file([1, 5, 0], [1, 5, 0]))
        self.assertTrue(identity.same_file([1, 5, 7], [2, 5, 7]), "a block-level clone is the same file (documented)")
        self.assertFalse(identity.same_file(None, [1, 5, 7]))

    def test_an_unknown_file_at_the_same_path_offers_instead_of_invalidating(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        with mock.patch.object(identity, "file_identity", lambda path: None):   # st_ino 0 (some file systems)
            self.blender.open("stool.blend")
            self.assertFalse(chat.document_invalid)
            self.assertIsNotNone(chat.awaited_file)
            self.assertTrue(document_offer.offered(chat))
            self.assertTrue(document_offer.continue_with_open_file(chat, self.blender.ui))
        self.assertFalse(chat.document_invalid)
        self.assertIsNone(chat.awaited_file)

    def test_another_id_at_the_same_path_still_says_document_changed(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        self.blender.disk[self.blender.path("stool.blend")] = [("Scene", {identity.PROPERTY: "f" * 32})]
        self.blender.open("stool.blend")
        self.assertTrue(chat.document_invalid)

    def test_the_card_tells_two_files_of_one_name_apart(self):
        self.blender.save("stool.blend")
        restored = self.quit_and_restore(self.chat())
        backup = str(Path(self.root.name) / "Old backups" / "stool.blend")
        self.blender.disk[backup] = list(self.blender.disk[self.blender.path("stool.blend")])
        self.blender._load(backup, [Scene(n, dict(p)) for n, p in self.blender.disk[backup]])
        fields = document_offer.names(restored)
        self.assertNotEqual(fields["old"], fields["new"])
        self.assertTrue(fields["new"].endswith(os.path.join("Old backups", "stool.blend")))
        self.assertEqual(document_offer.labels("/a/b/c.blend", "/a/d.blend"), ("c.blend", "d.blend"))
        self.assertEqual(document_offer.labels("/r/x/p/q/c.blend", "/r/y/p/q/c.blend"),
                         ("\u2026" + os.sep + os.path.join("x", "p", "q", "c.blend"),
                          "\u2026" + os.sep + os.path.join("y", "p", "q", "c.blend")))
        self.assertEqual(document_offer.labels("/x/c.blend", "/y/c.blend"),
                         (os.path.normpath("/x/c.blend"), os.path.normpath("/y/c.blend")), "whole paths when that short")
        self.assertLessEqual(len(document_offer.labels("/" + "a" * 200 + "/c.blend", "/b/c.blend")[0]),
                             document_offer.LABEL)

    def test_a_card_is_matched_by_the_full_paths_not_the_names(self):
        self.blender.save("stool.blend")
        restored = self.quit_and_restore(self.chat())
        self.blender.copy("stool.blend", "stool copy.blend")
        self.blender.open("stool copy.blend")
        fields = document_offer.names(restored)
        self.assertEqual(fields["offer"], document_offer._token(self.blender.path("stool.blend"),
                                                                self.blender.path("stool copy.blend")))
        self.assertNotEqual(fields["offer"], document_offer._token("/elsewhere/stool.blend",
                                                                   self.blender.path("stool copy.blend")))
        restored.pump_events()
        card = [m for m in restored.state["messages"] if (m.get("i18n") or [None])[0] == document_offer.OFFER][-1]
        self.assertEqual(card["i18n"][1]["offer"], fields["offer"])

    def test_a_failed_ending_never_marks_the_lead_for_a_later_sweep(self):
        self.blender.save("stool.blend")
        lead = self.chat()
        with mock.patch.object(worker_ending, "_holding", return_value={lead.state["id"]}), \
                mock.patch("angelo_agent.worker_dispatch.reclaim", side_effect=RuntimeError("lane busy")):
            self.assertFalse(worker_ending.end_after_change(lead))
        self.assertNotIn(lead.state["id"], worker_ending._reloaded)

    def test_a_restore_reports_a_workers_failure_once(self):
        ui = self.blender.ui
        self.blender.save("stool.blend")
        lead = self.chat()
        with mock.patch.object(worker_ending, "_holding", return_value={lead.state["id"]}), \
                mock.patch.object(worker_ending, "document_replaced", side_effect=RuntimeError("unbind failed")):
            result = lead.rebind_after_restore(ui.document_key(), "checkpoint")
        lead.pump_events()
        rows = [m for m in lead.state["messages"] if (m.get("i18n") or [None])[0] == worker_ending.WORKERS_LEFT]
        self.assertEqual(len(rows), 1)
        self.assertFalse(any("workers" in str(warning) for warning in result["warnings"]))


class RoundFiveTests(Launch):
    """Round 5 (on b0a6dc2023f): A, every branch of a successful load ends the lead's workers once, before the chat
    is rebound, offered or invalidated; C, the offer's labels and its own wording for one changed file."""

    def lead(self):
        self.blender.save("stool.blend")
        lead = self.chat()
        worker_resume._batches[lead.state["id"]] = {1}
        self.addCleanup(worker_resume._batches.clear)
        return lead

    def load(self, lead, action):
        """``action`` under a lead that holds workers: (document_replaced calls, the reasons they ended with)."""
        reasons = []
        with mock.patch.object(worker_ending, "_holding", return_value={lead.state["id"]}), \
                mock.patch.object(worker_ending, "document_replaced",
                                  side_effect=lambda: reasons.append(worker_ending._reasons.get(lead.state["id"]))):
            action()
        return reasons

    def fires(self, lead):
        rows = [{"worker": 1, "name": "Leg", "state": "idle", "turn": "completed", "mergeable": True}]
        with mock.patch.object(worker_resume, "_idle", return_value=True), \
                mock.patch.object(worker_resume, "_ended", return_value=True), \
                mock.patch.object(worker_resume, "_unmerged", return_value=rows), \
                mock.patch.object(worker_resume, "_fire") as fire:
            worker_resume.step(self.blender.ui)
        return fire.called

    def rewritten(self):   # A sync or a checkout wrote a new file at the chat's path, same id.
        path = self.blender.path("stool.blend")
        self.blender.disk[path] = list(self.blender.disk[path])

    def test_a_rebind_ends_them(self):
        lead = self.lead()
        self.assertEqual(self.load(lead, lambda: self.blender.open("stool.blend")), [worker_ending.REOPENED])
        self.assert_bound(lead)
        self.assertFalse(self.fires(lead))

    def test_an_offer_then_continue_never_resumes_the_batch(self):
        lead = self.lead()
        self.rewritten()
        self.assertEqual(self.load(lead, lambda: self.blender.open("stool.blend")), [worker_ending.REOPENED])
        self.assertIsNotNone(lead.awaited_file)
        worker_resume._batches[lead.state["id"]] = {1}   # Even a batch that somehow survived the load.
        self.assertTrue(document_offer.continue_with_open_file(lead, self.blender.ui))
        self.assertFalse(self.fires(lead))

    def test_an_offer_then_another_file_ended_them_at_the_first_load(self):
        lead = self.lead()
        self.rewritten()
        self.assertEqual(self.load(lead, lambda: self.blender.open("stool.blend")), [worker_ending.REOPENED])
        self.blender.disk[self.blender.path("table.blend")] = [("Scene", {identity.PROPERTY: "e" * 32})]
        self.assertEqual(self.load(lead, lambda: self.blender.open("table.blend")), [])
        self.assertFalse(self.fires(lead))

    def test_an_invalidation_ends_them_as_another_file(self):
        lead = self.lead()
        self.blender.disk[self.blender.path("table.blend")] = [("Scene", {identity.PROPERTY: "e" * 32})]
        self.assertEqual(self.load(lead, lambda: self.blender.open("table.blend")), [worker_ending.REPLACED])
        self.assertTrue(lead.document_invalid)
        self.assertFalse(self.fires(lead))

    def test_a_failed_load_ends_nothing(self):
        ui = self.blender.ui
        lead = self.lead()

        def failed():
            left = identity.leaving(ui)
            ui._document_serial = "load-failed"
            identity.follow(ui, left)
            identity.after_load(ui, failed=True)
        self.assertEqual(self.load(lead, failed), [])
        self.assertTrue(worker_resume.pending(lead.state["id"]))

    def test_long_folders_keep_the_part_that_differs_and_the_name(self):
        old = "/Users/me/Clients/northwind-furniture-2026-autumn-catalogue/stool.blend"
        new = "/Volumes/Backup/Clients/northwind-furniture-2026-autumn-catalogue/stool.blend"
        first, second = document_offer.labels(old, new)
        self.assertNotEqual(first, second)
        for label, part in ((first, "me"), (second, "Backup")):
            self.assertLessEqual(len(label), document_offer.LABEL)
            self.assertIn(os.sep + part + os.sep, label)
            self.assertTrue(label.endswith("stool.blend"))
        long_old = "/p/" + "n" * 50 + "-autumn-2026-A/" + "m" * 40 + "/stool.blend"
        long_new = "/p/" + "n" * 50 + "-autumn-2026-B/" + "m" * 40 + "/stool.blend"
        first, second = document_offer.labels(long_old, long_new)
        self.assertNotEqual(first, second)
        self.assertIn("2026-A", first)
        self.assertIn("2026-B", second)
        self.assertTrue(first.endswith("stool.blend") and second.endswith("stool.blend"))
        self.assertLessEqual(max(len(first), len(second)), document_offer.LABEL)

    def test_one_changed_file_is_named_once_with_why(self):
        lead = self.lead()
        self.rewritten()
        self.load(lead, lambda: self.blender.open("stool.blend"))
        lead.pump_events()
        template, fields = document_offer.card(lead)
        self.assertEqual(template, document_offer.CHANGED)
        self.assertTrue(fields["file"].endswith("stool.blend"))
        self.assertLessEqual(len(fields["file"]), document_offer.LABEL)
        self.assertNotIn("old", fields)
        self.assertEqual((lead.state["messages"][-1].get("i18n") or [None])[0], document_offer.CHANGED)
        self.assertIn("changed outside Angelo", str(document_scene.refusal(lead)))


class ModelContextTests(Launch):
    """GUI proof of a4c8bf48bbf (Sol, 2026-10-29): after Continue the model asked the person to confirm again, because
    the offer card was a model-visible error row and nothing told the model the person had answered. The offer and
    its refusals are the person's UI only; Continue leaves the model one fact (``model_notice``)."""

    def offered(self):
        self.blender.save("stool.blend")
        restored = self.quit_and_restore(self.chat())
        self.blender.copy("stool.blend", "stool copy.blend")
        self.blender.open("stool copy.blend")
        restored.pump_events()
        return restored

    def model_sees(self, chat):
        from angelo_agent import routing
        chat.pump_events()
        return routing.handoff(chat.state["messages"])[0]

    def test_the_next_turn_reads_the_fact_and_never_the_question(self):
        chat = self.offered()
        try:
            document_scene.refuse(chat)   # A typed "yes" first: refused with the same question.
        except RuntimeError as error:
            from angelo_agent.display_text import error_notice
            chat.emit("message", {"role": "error", **error_notice(error)})
        self.assertNotIn("Continue it with", self.model_sees(chat))
        self.assertTrue(document_offer.continue_with_open_file(chat, self.blender.ui))
        seen = self.model_sees(chat)
        self.assertIn("The person continued this chat with", seen)
        self.assertIn("stool copy.blend", seen)
        self.assertNotIn("Continue it with", seen)
        row = chat.state["messages"][-1]
        self.assertEqual(row["i18n"][0], document_offer.CONTINUED, "the person reads a translated notice")

    def test_a_withdrawn_offer_gives_the_model_nothing(self):
        chat = self.offered()
        self.blender.save("stool v3.blend")
        self.assertFalse(document_offer.continue_with_open_file(chat, self.blender.ui))
        seen = self.model_sees(chat)
        self.assertNotIn("offer", seen.lower())
        self.assertNotIn("continued", seen.lower())

    def test_an_app_reading_task_progress_never_gets_the_question(self):
        from angelo_agent import external_apps
        chat = self.offered()
        chat.pump_events()
        offer = next(m for m in chat.state["messages"] if document_offer.person_only(m))
        self.assertIsNone(external_apps._row(offer))


class UnstoppedWorkersTests(Launch):
    """Round 6: when ending a lead's workers fails, the batch is cancelled and the chat says so, and no merge of a part
    from a worker left running is admitted (Auto included) until it is stopped or discarded."""

    def test_a_merge_of_a_part_from_a_worker_left_running_is_refused(self):
        from angelo_agent import worker_merge, worker_sessions
        from angelo_agent.refusal_codes import RefusalError
        self.blender.save("stool.blend")
        lead = self.chat()
        lead_id, session = lead.state["id"], "worker-1"
        worker_sessions._started[lead_id] = [session]
        self.addCleanup(worker_sessions._started.pop, lead_id, None)
        self.addCleanup(worker_ending._unstopped.pop, lead_id, None)
        with mock.patch.object(worker_ending, "_holding", return_value={lead_id}), \
                mock.patch("angelo_agent.worker_dispatch.reclaim", side_effect=RuntimeError("lane busy")):
            self.blender.open("stool.blend")
        worker = SimpleNamespace(state={"id": session})
        part = Path(self.root.name) / "part.blend"
        part.write_bytes(b"BLENDER")
        with self.assertRaises(RefusalError) as refused, \
                mock.patch.object(worker_merge, "refuse_away"):   # Its scene is shown (Blender's window: not here).
            worker_merge.precondition(lead, {"parts": [{"worker": 1}]}, {1: (worker, str(part))})
        self.assertEqual(refused.exception.code, "VALIDATION")
        self.assertIn("not stopped when this chat's file changed", str(refused.exception))
        worker_sessions._started[lead_id] = []   # The person discarded it.
        self.assertFalse(worker_ending.unstopped(lead_id, session))

    def test_a_later_successful_ending_clears_it(self):
        from angelo_agent import worker_sessions
        self.blender.save("stool.blend")
        lead = self.chat()
        lead_id = lead.state["id"]
        worker_sessions._started[lead_id] = ["worker-1"]
        self.addCleanup(worker_sessions._started.pop, lead_id, None)
        with mock.patch.object(worker_ending, "_holding", return_value={lead_id}), \
                mock.patch("angelo_agent.worker_dispatch.reclaim", side_effect=RuntimeError("lane busy")):
            self.assertFalse(worker_ending.end_after_change(lead))
        self.assertTrue(worker_ending.unstopped(lead_id, "worker-1"))
        with mock.patch.object(worker_ending, "_holding", return_value={lead_id}), \
                mock.patch.object(worker_ending, "document_replaced"):
            self.assertTrue(worker_ending.end_after_change(lead))
        self.assertFalse(worker_ending.unstopped(lead_id))


class LoadEndTests(Launch):
    """``test_blender_parallel_scene_switch`` S3 on main (8198530ffbd): File > New left the lead valid and its workers
    running. Its process registers only ``ui.document_load_pre`` (``headless_main._register``), so the chats
    ``leaving`` deferred to load_post were never decided. A chat is deferred only where the load's end runs."""

    def without_load_end(self):
        self.blender.ui.bpy.app.handlers.load_post.clear()   # As headless_main._register: load_pre alone.

    def test_a_host_without_the_load_end_tells_every_chat_at_load_pre(self):
        self.blender.save("stool.blend")
        lead = self.chat()
        self.without_load_end()
        self.assertEqual(identity.leaving(self.blender.ui), (), "nothing waits for a decision no handler makes")
        self.blender.new()
        self.assertTrue(lead.document_invalid)
        self.assertEqual(len(notices(lead)), 1)

    def test_file_new_invalidates_the_lead_and_ends_its_workers_with_replaced(self):
        self.blender.save("stool.blend")
        lead = self.chat()
        worker_resume._batches[lead.state["id"]] = {1}
        self.addCleanup(worker_resume._batches.clear)
        reasons = []
        with mock.patch.object(worker_ending, "_holding", return_value={lead.state["id"]}), \
                mock.patch.object(worker_ending, "document_replaced",
                                  side_effect=lambda: reasons.append(worker_ending._reasons.get(lead.state["id"]))):
            self.blender.new()
        self.assertTrue(lead.document_invalid)
        self.assertEqual(reasons, [worker_ending.REPLACED])
        self.assertFalse(worker_resume.pending(lead.state["id"]))

    def test_the_same_holds_for_a_failed_load_without_its_handler(self):
        self.blender.save("stool.blend")
        lead = self.chat()
        self.blender.ui.bpy.app.handlers.load_post_fail.clear()
        self.assertEqual(identity.leaving(self.blender.ui), ())


class HeadlessHostTests(Launch):
    """The 1.0.7 candidate 933cdffbcce: in the headless MCP child an approved script that loaded a file came back as
    "The open Blender file changed in Angelo" instead of ``DOCUMENT_REPLACED``. The child has no window chat (its chats
    come from ``headless_host.make_controller``), and up to 1.0.6 such a process told no chat of a load: the host
    loads its own file again and owns that decision. ``follow`` keeps that rule."""

    def test_a_process_without_a_window_chat_tells_no_chat_of_a_load(self):
        self.blender.save("scene.blend")
        chat = self.session_chat()   # As headless_host.make_controller: a chat that holds work, no window chat.
        self.blender.ui._controller = None
        self.blender.ui.bpy.app.handlers.load_post.clear()   # headless_main._register: load_pre alone.
        with mock.patch.object(identity, "unattended", return_value=True):   # A background Blender.
            self.blender.new()   # The script's load (read_homefile) ...
            self.blender.open("scene.blend")   # ... then the host loads its own file again.
        self.assertFalse(chat.document_invalid)
        self.assertEqual(notices(chat), [])

    def test_a_window_chat_still_tells_every_chat(self):
        self.blender.save("scene.blend")
        chat = self.session_chat()   # An app's chat beside the window's own.
        self.blender.new()
        self.assertTrue(chat.document_invalid)


class NotesKeepTheirScopeTests(Launch):
    """The 1.0.7 candidate (``test_blender_memory_project_resume``'s "changed" phase): after the person's Continue onto
    changed bytes at the project's path, the earlier brief bound itself to that file. ``_bind`` marked the memory stale
    with the new document, which moved the notes' scope there, so ``bind_saved_memory`` took any bytes. A rebind marks
    scene evidence stale only; notes follow a verified receipt (the same saved bytes) or the person's confirmation."""

    def brief(self, chat):
        from angelo_agent.task_memory import update_memory
        origin = {"kind": "approved_update", "id": "host:brief"}
        chat.state = update_memory(chat.state, {"expected_revision": chat.state["task_memory"]["revision"],
                                                "patch": {"objective": "A walnut stool"}},
                                   chat.document, provenance=origin, profile_id=str(chat.store.directory.resolve()))

    def read(self, chat):
        from angelo_agent.task_memory import read_memory
        return read_memory(chat.state, chat.document, profile_id=str(chat.store.directory.resolve()))

    def test_continue_onto_another_file_keeps_the_notes_unbound(self):
        self.blender.save("stool.blend")
        chat = self.session_chat()
        self.brief(chat)
        self.assertEqual(self.read(chat)["brief"]["objective"], "A walnut stool")
        self.blender.copy("stool.blend", "copy/stool.blend")   # The chat's id, another file.
        restored = self.quit_and_restore(chat, restore=False)
        self.blender.open("copy/stool.blend")
        identity.restore(restored, self.blender.ui)
        self.blender.ui._controllers[restored.state["id"]] = restored
        self.assertTrue(document_offer.continue_with_open_file(restored, self.blender.ui))
        memory = self.read(restored)
        self.assertTrue(memory["scope_mismatch"])
        self.assertFalse(memory["brief"]["objective"])

    def test_a_reopen_of_the_same_document_keeps_the_notes_scope_too(self):
        self.blender.save("stool.blend")
        chat = self.chat()
        self.brief(chat)
        scope = chat.state["task_memory"]["scope"]["document"]
        self.blender.open("stool.blend")   # Rebound (the same document); the saved receipt decides the notes.
        self.assertFalse(chat.document_invalid)
        self.assertEqual(chat.state["task_memory"]["scope"]["document"], scope)


if __name__ == "__main__":
    unittest.main()
