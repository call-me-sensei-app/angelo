# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Several folders per chat (``context_folder_grants``, ``context_folder_host``; agent_chat.rst, "Folder as context"):
the cap and why, which folder is the working one, switching and removing it, the manifests, and a chat resumed with
its folders after Angelo restarts. Writing is test_native_context_folder_write.py. No Blender here."""

import suite_isolation  # noqa: F401

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import context_folder as cf  # noqa: E402
from angelo_agent import context_folder_grants as cg  # noqa: E402
from angelo_agent import context_folder_host as host  # noqa: E402
from angelo_agent import grant_authority as ga  # noqa: E402
from angelo_agent import grant_guard  # noqa: E402
from host_waits import CAP_S  # noqa: E402
import angelo_guard_twin  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())   # Its tests attach folders as the person does.


class Chat:
    def __init__(self, state, chat_id="chat-1"):
        self.state = {"id": chat_id}
        self.store = SimpleNamespace(directory=state)
        self.references = SimpleNamespace(directory=state.parent / "references")
        self.delegate_jobs, self.events, self.files = {}, [], None
        self.version = 0

    def emit(self, kind, data):
        self.events.append((kind, data))


class Folders(unittest.TestCase):
    def setUp(self):
        angelo_guard_twin.strict_for(self)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(os.path.realpath(scratch.name))
        self.state = self.base / "state"
        self.folders = []
        for name in ("game", "refs", "docs", "audio", "levels", "extra"):
            folder = self.base / name
            folder.mkdir()
            (folder / ("%s.md" % name)).write_text(name, encoding="utf-8")
            self.folders.append(folder)
        host._grants.clear()
        host._manifests.clear()
        host._generations.clear()
        self.addCleanup(host._grants.clear)
        self.addCleanup(host._manifests.clear)
        self.chat = Chat(self.state)

    def labels(self):
        return [(record["label"], record["working"]) for record in host.labels(self.chat)]


class WorkingFolderTests(Folders):
    def test_the_first_folder_is_the_working_one_and_the_rest_are_references(self):
        for folder in self.folders[:3]:
            host.attach(self.chat, folder)
        self.assertEqual(self.labels(), [("game", True), ("refs", False), ("docs", False)])
        records = host.labels(self.chat)
        store = host.grants(self.state)
        self.assertEqual(store.writable("chat-1", records[0]["id"]), self.folders[0])
        for record in records[1:]:
            self.assertIsNone(store.writable("chat-1", record["id"]), "A reference folder is never writable")
            self.assertIsNotNone(store.readable("chat-1", record["id"]))
        self.assertEqual(host.working(self.chat)["label"], "game")

    def test_the_person_switches_the_working_folder_from_its_card(self):
        first, second = host.attach(self.chat, self.folders[0]), host.attach(self.chat, self.folders[1])
        self.assertTrue(host.make_working(self.chat, second))
        self.assertEqual(self.labels(), [("game", False), ("refs", True)])
        store = host.grants(self.state)
        self.assertIsNone(store.writable("chat-1", first), "The old working folder narrowed at once")
        self.assertEqual(store.writable("chat-1", second), self.folders[1])
        self.assertEqual(ga.value(ga.CONTEXT_FOLDERS, "chat-1/working"), cg.folder_digest(self.folders[1]))
        notice = [data for kind, data in self.chat.events if kind == "message"][-1]
        self.assertEqual(notice["i18n"][0], host.NOW_WORKING)

    def test_only_the_persons_own_click_switches_it(self):
        host.attach(self.chat, self.folders[0])
        second = host.attach(self.chat, self.folders[1])
        with grant_guard.agent_script(), self.assertRaises(PermissionError):
            host.make_working(self.chat, second)
        errors = []

        def elsewhere():   # Another thread holds no grant scope: no click of the person's.
            try:
                host.grants(self.state).make_working("chat-1", second)
            except PermissionError as error:
                errors.append(error)
        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.labels(), [("game", False), ("refs", False)],
                         "Refused in C after narrowing: no working folder, never the reference one")
        host.make_working(self.chat, second)
        self.assertEqual(self.labels(), [("game", False), ("refs", True)])

    def test_removing_the_working_folder_makes_the_next_one_working(self):
        ids = [host.attach(self.chat, folder) for folder in self.folders[:3]]
        self.assertTrue(host.remove(self.chat, ids[0]))
        self.assertEqual(self.labels(), [("refs", True), ("docs", False)])
        self.assertTrue(host.remove(self.chat, ids[2]))   # A reference folder: the working one stays.
        self.assertEqual(self.labels(), [("refs", True)])
        self.assertTrue(host.remove(self.chat, ids[1]))
        self.assertEqual(self.labels(), [])
        self.assertIsNone(host.working(self.chat))
        self.assertIsNone(ga.value(ga.CONTEXT_FOLDERS, "chat-1/working"), "No folder left: no working folder")
        self.assertEqual(json.loads((self.state / cf.NAME).read_text(encoding="utf-8"))["chats"], {})

    def test_agent_code_may_remove_a_folder_but_never_moves_the_working_folder_on(self):
        first = host.attach(self.chat, self.folders[0])
        host.attach(self.chat, self.folders[1])
        with grant_guard.agent_script():
            host.detach(self.chat, first)
        self.assertEqual(self.labels(), [("refs", False)], "Narrowed; the next folder is not made writable by it")

    def test_a_reference_folder_moved_away_is_skipped_when_the_working_one_moves_on(self):
        first = host.attach(self.chat, self.folders[0])
        host.attach(self.chat, self.folders[1])
        host.attach(self.chat, self.folders[2])
        self.folders[1].rename(self.base / "moved")
        host.remove(self.chat, first)
        working = host.working(self.chat)
        self.assertEqual(working["label"], "docs", "Only a folder still there takes over")


class AttachRuleTests(Folders):
    def test_a_chat_has_at_most_max_folders(self):
        for folder in self.folders[:cg.MAX_FOLDERS]:
            host.attach(self.chat, folder)
        with self.assertRaises(cf.TooMany):
            host.attach(self.chat, self.folders[cg.MAX_FOLDERS])
        self.assertEqual(len(host.labels(self.chat)), cg.MAX_FOLDERS)
        other = Chat(self.state, "chat-2")
        host.attach(other, self.folders[cg.MAX_FOLDERS])   # The cap is per chat.

    def test_the_cap_keeps_the_folders_manifests_within_their_budget(self):
        """Why five: each manifest is bounded (50 names cut to NAME_CHARS, 24 kinds), so five stay within about 30 KB
        of the turn's context even when every folder is at that bound."""
        manifests = []
        for index, folder in enumerate(self.folders[:cg.MAX_FOLDERS]):
            for number in range(60):   # Past both bounds: 60 long names, 30 kinds.
                (folder / ("%03d-%s.k%02d" % (number, "n" * 240, number % 30))).write_text("x", encoding="utf-8")
            manifests.append(cf.manifest(cf.ReadOnlyFolder(folder), folder.name, working=index == 0))
        for manifest in manifests:
            self.assertLessEqual(len(manifest["top_level"]), 50)
            self.assertLessEqual(len(manifest["kinds"]), 24)
            self.assertLessEqual(len(json.dumps(manifest).encode("utf-8")), cf.MANIFEST_BYTES)
        self.assertLessEqual(len(json.dumps(manifests).encode("utf-8")), 30 * 1024)

    def test_the_same_folder_twice_and_nested_folders_are_refused(self):
        host.attach(self.chat, self.folders[0])
        with self.assertRaises(cf.AlreadyAttached):
            host.attach(self.chat, self.folders[0])
        inside = self.folders[0] / "sub"
        inside.mkdir()
        with self.assertRaises(cf.Overlaps):
            host.attach(self.chat, inside)
        holder = self.base / "holder"
        (holder / "project").mkdir(parents=True)
        host.attach(self.chat, holder / "project")
        with self.assertRaises(cf.Overlaps):
            host.attach(self.chat, holder)
        self.assertEqual(len(host.labels(self.chat)), 2)

    def test_every_folder_goes_through_the_same_refusals(self):
        with unittest.mock.patch.object(cg, "vet_root", side_effect=cf.FolderRefused(cf.PRIVATE)):
            for folder in self.folders[:2]:
                with self.assertRaises(cf.FolderRefused):
                    host.attach(self.chat, folder)
        self.assertEqual(host.labels(self.chat), [])

    def test_folders_of_one_name_are_told_apart_by_number(self):
        for parent in ("a", "b"):
            (self.base / parent / "Game").mkdir(parents=True)
            host.attach(self.chat, self.base / parent / "Game")
        self.assertEqual(self.labels(), [("Game", True), ("Game (2)", False)])

    def test_a_workers_chat_has_none(self):
        host.attach(self.chat, self.folders[0])
        self.chat.remote_executor = object()
        self.assertEqual((host.labels(self.chat), host.observation(self.chat), host.working(self.chat)),
                         ([], None, None))


class ManifestTests(Folders):
    def test_each_folder_has_a_path_free_manifest_with_its_role(self):
        ids = [host.attach(self.chat, folder) for folder in self.folders[:2]]
        for folder_id in ids:
            host.observation(self.chat)
            self.assertTrue(host._manifests[("chat-1", folder_id)]["ready"].wait(CAP_S))
        sent = host.observation(self.chat)
        self.assertEqual([(item["folder"], item["role"]) for item in sent],
                         [("game", "working"), ("refs", "reference")])
        self.assertEqual(sent[0]["top_level"], ["game.md"])
        text = json.dumps(sent)
        self.assertNotIn(str(self.base), text)
        host.make_working(self.chat, ids[1])
        self.assertEqual([item["role"] for item in host.observation(self.chat)], ["reference", "working"],
                         "The role is as it is now, without waiting for a new manifest")

    def test_the_tool_reads_the_named_folder_and_defaults_to_the_working_one(self):
        host.attach(self.chat, self.folders[0])
        host.attach(self.chat, self.folders[1])
        self.assertEqual(host.resolve(self.chat, None)[1], self.folders[0])
        self.assertEqual(host.resolve(self.chat, "refs")[1], self.folders[1])
        self.assertEqual(host.resolve(self.chat, "nothing"), (None, cf.UNKNOWN_FOLDER))
        self.assertEqual(cf.validate({"action": "LIST", "folder": "refs"})["folder"], "refs")
        with self.assertRaises(ValueError):
            cf.validate({"action": "LIST", "folder": "a\nb"})


class ResumeTests(Folders):
    def test_a_resumed_chat_has_its_folders_in_order_and_its_working_folder(self):
        """Continue-after-crash and session resume read the chat's grants from the state folder: a new process (C's
        ledger empty, mirrored again from Angelo's own file) has the same folders, order and working folder."""
        ids = [host.attach(self.chat, folder) for folder in self.folders[:3]]
        host.make_working(self.chat, ids[2])
        before = self.labels()
        angelo_guard_twin.reset()   # Angelo restarted: nothing held in C.
        host._grants.clear()
        self.assertEqual(self.labels(), before)
        self.assertEqual(host.working(self.chat)["id"], ids[2])
        self.assertEqual(ga.value(ga.CONTEXT_FOLDERS, "chat-1/working"), cg.folder_digest(self.folders[2]))
        other = Chat(self.state, "chat-2")
        self.assertEqual(host.labels(other), [], "Per chat: another chat resumed has none of them")

    def test_a_folder_replaced_while_angelo_was_closed_is_never_read_or_written(self):
        host.attach(self.chat, self.folders[0])
        angelo_guard_twin.reset()
        host._grants.clear()
        self.folders[0].rename(self.base / "away")
        self.folders[0].mkdir()   # Another folder at its path.
        self.assertIsNone(host.working(self.chat))
        self.assertEqual(host.resolve(self.chat, None)[0], None)

    def test_a_single_folder_grant_from_before_is_kept_read_only(self):
        """2.1.0 before several folders saved one read-only folder per chat (version 1): it stays attached as a
        reference folder, never made writable without the person's click."""
        real, device, inode = cg.identity(self.folders[0])
        self.state.mkdir()
        (self.state / cf.NAME).write_text(json.dumps({"version": 1, "chats": {"chat-1": {
            "path": str(self.folders[0]), "real": real, "device": device, "inode": inode, "at": 1.0}}}),
            encoding="utf-8")
        self.assertEqual(self.labels(), [("game", False)])
        self.assertIsNone(host.working(self.chat))
        self.assertEqual(host.resolve(self.chat, None)[1], self.folders[0], "The only folder is read by default")

    def test_a_card_from_before_is_still_its_folders_card(self):
        host.attach(self.chat, self.folders[0])
        legacy = {"role": "system", "notice": True, "i18n": [host.ATTACHED_BEFORE, {"name": "game"}]}   # As stored.
        self.assertEqual(host.card_folder(self.chat, legacy)["label"], "game")


STUBS = ("bpy", "bpy.props", "bpy.types", "bpy.app", "bpy.app.handlers", "bpy.utils", "blf", "gpu", "gpu_extras",
         "gpu_extras.batch", "mathutils", "bpy_extras", "bpy_extras.io_utils", "bl_math")


class CardTests(Folders):
    """Each folder's card in the transcript (``chat_notices._folder_card``), with Blender's modules stood in as the
    notice suites do (test_native_notice_resend)."""

    @classmethod
    def setUpClass(cls):
        mock = unittest.mock
        cls._before = set(sys.modules)
        stubs = {name: mock.MagicMock(name=name) for name in STUBS}
        same = lambda message, context=None: message   # noqa: E731  English, as without Blender.
        stubs["bpy.app"].translations = SimpleNamespace(pgettext_iface=same, pgettext_tip=same, pgettext_rpt=same,
                                                        pgettext_data=same, locale="en_US")
        from angelo_agent import ui_text
        cls._translations = mock.patch.dict(ui_text._pgettext, clear=True)
        cls._translations.start()
        cls._modules = mock.patch.dict(sys.modules, stubs)
        cls._modules.start()
        from angelo_agent import chat_notices
        cls.notices = chat_notices

    @classmethod
    def tearDownClass(cls):
        added = [name for name in sys.modules if name not in cls._before and name.startswith("angelo_agent.")]
        cls._modules.stop()
        cls._translations.stop()
        package = sys.modules.get("angelo_agent")
        for name in added:
            if package is not None and name.count(".") == 1:
                package.__dict__.pop(name.split(".", 1)[1], None)

    def card(self, message):
        return self.notices._card(message, 0, False, SimpleNamespace(), self.chat, set())

    def message(self, folder_id, name):
        return {"role": "system", "notice": True, "context_folder": name, "context_folder_id": folder_id,
                "i18n": [host.ATTACHED, {"name": name}], "text": host.ATTACHED.format(name=name)}

    def test_each_card_names_its_role_with_make_working_and_remove(self):
        first, second = host.attach(self.chat, self.folders[0]), host.attach(self.chat, self.folders[1])
        working, reference = self.card(self.message(first, "game")), self.card(self.message(second, "refs"))
        self.assertEqual(working["body"], host.WORKING_LINE)
        self.assertEqual([action[:2] for action in working["actions"]], [(host.REMOVE_ACTION, first)])
        self.assertEqual(reference["body"], host.REFERENCE_LINE)
        self.assertEqual([action[:2] for action in reference["actions"]],
                         [(host.WORKING_ACTION, second), (host.REMOVE_ACTION, second)])
        host.remove(self.chat, second)
        self.assertEqual(self.card(self.message(second, "refs"))["actions"], (), "Removed: no pills")

    def test_the_controller_keeps_the_cards_folder_on_the_stored_row(self):
        """The chat stores only the message keys the controller copies (``Controller._pump_events``): a card whose
        folder id was dropped there drew no pills in the app, though a card built by hand here did (the GUI proof)."""
        source = (Path(host.__file__).with_name("controller.py")).read_text(encoding="utf-8")
        self.assertIn('for key in ("connection_id", "provider", "context_folder_id"):', source)
        ui = (Path(host.__file__).with_name("context_folder_ui.py")).read_text(encoding="utf-8")
        self.assertIn('"context_folder_id": folder_id', ui)

    def test_the_cards_pills_are_drawn_without_touching_the_disk(self):
        first = host.attach(self.chat, self.folders[0])

        def no_disk(*args, **kwargs):
            raise AssertionError("the disk was touched while drawing the card")
        with unittest.mock.patch.object(os.path, "realpath", no_disk), \
                unittest.mock.patch.object(os, "stat", no_disk), unittest.mock.patch.object(os, "lstat", no_disk):
            self.assertTrue(host.attached_stands(self.chat, self.message(first, "game")))

    def test_the_cards_pills_act_on_their_own_folder(self):
        chat_notices = self.notices
        first, second = host.attach(self.chat, self.folders[0]), host.attach(self.chat, self.folders[1])
        chat_notices.dispatch(None, self.chat, {}, host.WORKING_ACTION, second)
        self.assertEqual(self.labels(), [("game", False), ("refs", True)])
        chat_notices.dispatch(None, self.chat, {}, host.REMOVE_ACTION, first)
        self.assertEqual(self.labels(), [("refs", True)])


if __name__ == "__main__":
    unittest.main()
