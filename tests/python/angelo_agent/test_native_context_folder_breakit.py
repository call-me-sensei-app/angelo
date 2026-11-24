# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The break-it review of 565f5df (context folders), each finding pinned: who owns a file (M1, M2), which folder the
working folder moves on to (M3), the Approve route (S1), worker chats (S2), publishing (S3), change time (S4), data and
settings files (S5) and its notes. The writes themselves: test_native_context_folder_write.py. No Blender here."""

import suite_isolation  # noqa: F401

import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_folder as cf  # noqa: E402
from angelo_agent import context_folder_grants as cg  # noqa: E402
from angelo_agent import context_folder_host as host  # noqa: E402
from angelo_agent import context_folder_working as cwk  # noqa: E402
from angelo_agent import context_folder_write as cw  # noqa: E402
from angelo_agent import auto_reasons, grant_authority as ga, grant_guard  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
from test_native_context_folder_write import Chat, Working  # noqa: E402  (its fixture: two folders, one working)
import angelo_guard_twin  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())

POSIX = os.name == "posix"


class OwnershipTests(Working):
    def test_an_approved_update_of_the_persons_file_never_makes_it_angelos(self):
        """M1: one approved UPDATE of a file that was there made it the agent's own, and every later UPDATE ran in Auto
        with no card. It stays the person's file: the next change asks again, and Auto's run is refused."""
        self.assertTrue(self.write(action="UPDATE", path="README.md", content="approved once")["written"])
        again = self.call(automatic=True, action="UPDATE", path="README.md", content="twice")
        self.assertEqual(cw.auto_reason(self.chat, again), auto_reasons.PREEXISTING)
        self.assertEqual(cw.run(self.chat, again)["error"], cw.ASKS)
        self.assertEqual((self.work / "README.md").read_text(encoding="utf-8"), "approved once")

    def test_angelos_own_file_stays_its_own_through_its_updates(self):
        self.write(action="CREATE", path="plan.md", content="1")
        for text in ("2", "3"):
            call = self.call(automatic=True, action="UPDATE", path="plan.md", content=text)
            self.assertIsNone(cw.auto_reason(self.chat, call))
            self.assertTrue(cw.run(self.chat, call)["written"])

    def test_what_angelo_created_is_held_in_c_never_in_python_state(self):
        """M2: the set lived in Python containers an agent script could change in place. C holds it, and a token
        written into C's own state is the only way a file becomes the agent's."""
        store = host.grants(self.chat.store.directory)
        self.write(action="CREATE", path="mine.md", content="x")
        mine = self.call(action="UPDATE", path="mine.md", content="y").precondition[1]
        self.assertEqual(store.state("chat-1", self.work_id).owned, [cg.token(mine)])
        self.assertTrue(store.owns("chat-1", self.work_id, mine))
        held = ga.value(ga.CONTEXT_FOLDERS, cg.own_key("chat-1", self.work_id))
        self.assertIn(cg.token(mine), held.split())

    def test_agent_code_never_marks_a_file_as_angelos(self):
        store = host.grants(self.chat.store.directory)
        signature = self.call(action="UPDATE", path="README.md", content="x").precondition[1]
        with grant_guard.agent_script(), self.assertRaises(PermissionError):
            store.note_written("chat-1", self.work_id, signature, created=True)
        with angelo_guard_twin.confine(), self.assertRaises(PermissionError):   # C itself refuses a script.
            ga.automatic(ga.record, ga.CONTEXT_FOLDERS, cg.own_key("chat-1", self.work_id), cg.token(signature))
        self.assertFalse(store.owns("chat-1", self.work_id, signature))

    def test_angelos_files_survive_a_restart_from_its_own_file_only(self):
        self.write(action="CREATE", path="mine.md", content="x")
        angelo_guard_twin.reset()
        host._grants.clear()
        self.assertIsNone(cw.auto_reason(self.chat, self.call(action="UPDATE", path="mine.md", content="y")))
        saved = json.loads((self.chat.store.directory / cf.NAME).read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], cg.VERSION)

    @unittest.skipUnless(POSIX, environment_skip("a change time moved without content needs POSIX chmod"))
    def test_a_change_only_its_change_time_shows_is_still_a_change(self):
        """S4: the signature held size and modification time only; a file changed and restored to both (or its
        permissions changed) was still the agent's own. Its change time is part of it."""
        self.write(action="CREATE", path="mine.md", content="x")
        before = self.call(action="UPDATE", path="mine.md", content="y").precondition[1]
        os.chmod(self.work / "mine.md", 0o640)
        after = self.call(action="UPDATE", path="mine.md", content="y").precondition[1]
        self.assertEqual(before[:2], after[:2])
        self.assertNotEqual(before, after)
        self.assertEqual(cw.auto_reason(self.chat, self.call(action="UPDATE", path="mine.md", content="y")),
                         auto_reasons.PREEXISTING)


class RestartTests(Working):
    """M2 across a restart (re-read of 121b533): the file was written from Python's state, and the next start mirrored
    it into C, so anything a script changed in place there came back as a grant. Each grant-bearing value is forged in
    place, an unrelated save follows, Angelo restarts, and the forgery has no effect."""

    def restart_after_an_unrelated_save(self, store):
        other = Chat(self.base / "state")
        other.state["id"] = "chat-2"
        outside = self.base / "unrelated"
        outside.mkdir()
        host.attach(other, outside)   # Any save of the grant file.
        angelo_guard_twin.reset()
        host._grants.clear()
        return host.grants(self.chat.store.directory)

    def test_a_token_added_to_pythons_copy_never_makes_the_persons_file_angelos(self):
        store = host.grants(self.chat.store.directory)
        readme = self.call(action="UPDATE", path="README.md", content="x").precondition[1]
        self.chat_record(store)["owned"] = [cg.token(readme)]
        self.chat_record(store)["state"] = cg.State(1, 0, False, [cg.token(readme)]).text()
        self.restart_after_an_unrelated_save(store)
        self.assertEqual(cw.auto_reason(self.chat, self.call(automatic=True, action="UPDATE", path="README.md",
                                                             content="x")), auto_reasons.PREEXISTING)

    def test_a_legacy_mark_cleared_in_python_never_lets_the_working_folder_move_on_to_it(self):
        host.detach(self.chat)
        real, device, inode = cg.identity(self.ref)
        (self.chat.store.directory / cf.NAME).write_text(json.dumps({"version": 1, "chats": {"chat-1": {
            "path": str(self.ref), "real": real, "device": device, "inode": inode, "at": 1.0}}}), encoding="utf-8")
        host._grants.clear()
        angelo_guard_twin.reset()
        work = host.attach(self.chat, self.work)
        store = host.grants(self.chat.store.directory)
        for record in store._grants["chat-1"]["folders"]:
            record["legacy"] = False   # What a script could do in place.
            record["state"] = "s1 c0"
        store = self.restart_after_an_unrelated_save(store)
        host.remove(self.chat, work)
        self.assertIsNone(host.working(self.chat), "Still legacy: never made writable by itself")

    def test_a_created_count_lowered_in_python_still_counts(self):
        with patch.object(cw, "MAX_CREATED", 1):
            self.write(action="CREATE", path="a.md", content="x")
            store = host.grants(self.chat.store.directory)
            self.chat_record(store)["created"] = 0
            self.chat_record(store)["state"] = "s1 c0"
            self.restart_after_an_unrelated_save(store)
            self.assertEqual(self.refused(action="CREATE", path="b.md", content="x")["error"], cw.TOO_MANY)

    def test_a_working_folder_switched_in_python_stays_the_one_the_person_chose(self):
        store = host.grants(self.chat.store.directory)
        store._grants["chat-1"]["working"] = self.ref_id   # What a script could do in place.
        self.restart_after_an_unrelated_save(store)
        self.assertEqual(host.working(self.chat)["id"], self.work_id)
        self.assertEqual(self.refused(action="CREATE", path="x.md", content="x", folder="Refs")["error"],
                         cw.REFERENCE_ONLY)

    def test_a_folder_added_in_python_is_never_saved_as_a_grant(self):
        store = host.grants(self.chat.store.directory)
        secret = self.base / "secret"
        secret.mkdir()
        real, device, inode = cg.identity(secret)
        store._grants["chat-1"]["folders"].append({"id": "f" * 16, "path": str(secret), "real": real,
                                                   "device": device, "inode": inode, "at": 1.0})
        self.restart_after_an_unrelated_save(store)
        self.assertEqual([record["path"] for record in host.labels(self.chat)], [self.work, self.ref])

    def test_a_start_whose_grants_couldnt_reach_c_never_saves_over_them_and_tries_again(self):
        """SHOULD 2 (third read): a refused start-up mirror latched for the session; Remove and a chat's deletion then
        narrowed C but left the grant in the file. It is tried again at the next call that isn't agent code."""
        angelo_guard_twin.reset()
        host._grants.clear()
        with angelo_guard_twin.confine():   # Read while an agent script runs: the mirror is refused.
            store = host.grants(self.chat.store.directory)
            with self.assertRaises(PermissionError):
                store._save()   # Never over grants C doesn't hold.
        self.assertEqual(len(host.labels(self.chat)), 2, "Read into C at the next call")
        host.remove(self.chat, self.ref_id)
        angelo_guard_twin.reset()
        host._grants.clear()
        self.assertEqual([record["id"] for record in host.labels(self.chat)], [self.work_id],
                         "The removed folder is gone from the file too")

    def chat_record(self, store):
        return next(record for record in store._grants["chat-1"]["folders"] if record["id"] == self.work_id)


class AnchorTests(Working):
    """SHOULD 1 (third read): the anchored walk asked C's may_write about the folder, which refuses a person's project
    folder once any agent script has run, so reads and writes failed for the rest of the session. A context folder is
    judged by its own grant in C instead, at the very folder opened, on every platform's walk (fourth read, MUST 2),
    and only the working folder's grant opens a folder to write in."""

    def refuse_may_write(self):
        from angelo_agent import checkpoint_filesystem
        def refused(path, within=None):
            raise PermissionError("may_write refuses it, as after a script")
        return patch.object(checkpoint_filesystem, "require_anchorable", refused)

    def test_reads_and_writes_never_ask_may_write_for_a_granted_folder(self):
        with self.refuse_may_write():
            self.assertTrue(self.write(action="CREATE", path="after.md", content="x")["written"])
            record = next(r for r in host.labels(self.chat) if r["id"] == self.ref_id)
            reader = host._workspace(self.chat, record["path"], self.ref_id)
            listed = cf.run(reader, cf.validate({"action": "LIST"}), None)
            self.assertEqual([entry["name"] for entry in listed["entries"]], ["style.md"])
        with self.refuse_may_write(), self.assertRaises(PermissionError):   # Without a grant: as before.
            cf.ReadOnlyFolder(self.ref).list_page(".")

    def test_a_folder_c_doesnt_hold_under_its_grant_is_never_opened(self):
        record = next(r for r in host.labels(self.chat) if r["id"] == self.ref_id)
        reader = host._workspace(self.chat, record["path"], self.ref_id)
        other = cf.ReadOnlyFolder(self.outside, grant=cg.key("chat-1", self.ref_id))   # Another folder, that grant.
        with self.assertRaises(ValueError) as caught:
            other.list_page(".")
        self.assertEqual(str(caught.exception), cf.NOT_GRANTED)
        host.detach(self.chat, self.ref_id)
        with self.assertRaises(ValueError):
            reader.list_page(".")
        writer = cwk.WorkingFolder(self.ref, grant=cg.key("chat-1", cg.WORKING))   # A reference folder as "working".
        with self.assertRaises(ValueError):
            writer.write_note("x.md", "x", cwk.MISSING)
        self.assertFalse((self.ref / "x.md").exists())
        for key in (cg.key("chat-1", self.ref_id), None):   # The reference folder's own (reading) grant, or none.
            with self.assertRaises(ValueError) as caught:
                cwk.WorkingFolder(self.ref, grant=key).write_note("x.md", "x", cwk.MISSING)
            self.assertEqual(str(caught.exception), cf.NOT_GRANTED)
        self.assertFalse((self.ref / "x.md").exists())

    def writes_after_a_script(self):
        """CREATE in a new folder, UPDATE of that file, with may_write refusing as after a script."""
        with self.refuse_may_write():
            self.assertTrue(self.write(action="CREATE", path="notes/after.md", content="one")["written"])
            updated = self.write(automatic=True, action="UPDATE", path="notes/after.md", content="two")
        self.assertTrue(updated["written"], updated)
        self.assertEqual((self.work / "notes" / "after.md").read_text(encoding="utf-8"), "two")

    @unittest.skipUnless(POSIX, environment_skip("POSIX descriptor walks; Windows runs its own backend below"))
    def test_writes_after_a_script_on_the_walk_without_macos_one_open(self):
        """The fourth read's MUST 2: write_note's identity re-check went through the shared walk wherever macOS'
        search-only open (``_SEARCH``) is absent, so every write after a script failed on Linux and Windows."""
        from angelo_agent import checkpoint_filesystem
        with patch.object(checkpoint_filesystem, "_SEARCH", 0), patch.object(checkpoint_filesystem, "_NOFOLLOW_ANY", 0):
            self.writes_after_a_script()

    def test_writes_after_a_script_through_the_windows_backend(self):
        from angelo_agent import checkpoint_filesystem
        if os.name != "nt":   # Off Windows the stand-in; on Windows the real backend runs it.
            from fake_nt_files import FakeNtApi
            stand_in = patch.object(checkpoint_filesystem, "_windows_api", return_value=FakeNtApi())
            stand_in.start()
            self.addCleanup(stand_in.stop)
        self.writes_after_a_script()

    def test_a_place_c_refuses_is_said_without_its_path(self):
        """A ``PermissionError`` from C (its message holds the path) read "(error)" in the transcript."""
        refused = PermissionError("Angelo writes only where it made something: %s" % self.work)
        with patch.object(cwk.WorkingFolder, "write_note", side_effect=refused):
            result = self.write(action="CREATE", path="x.md", content="x")
        self.assertEqual((result["error"], result["error_code"], result["written"]),
                         (cw.NOT_ALLOWED, "READ_ONLY", False))
        with patch.object(cwk.WorkingFolder, "signature", side_effect=refused), self.assertRaises(Exception) as caught:
            self.call(action="CREATE", path="x.md", content="x")
        self.assertEqual(str(caught.exception), cw.NOT_ALLOWED)


class PromotionTests(Working):
    def test_removing_the_working_folder_says_which_folder_is_working_now(self):
        """M3: the next folder became writable without a word. The chat says so after Remove."""
        self.chat.events.clear()
        host.remove(self.chat, self.work_id)
        notices = [data["i18n"][0] for kind, data in self.chat.events if kind == "message"]
        self.assertEqual(notices, [host.REMOVED, host.NOW_WORKING])
        self.assertEqual(host.working(self.chat)["label"], "Refs")

    def test_a_folder_attached_read_only_before_never_becomes_writable_by_itself(self):
        """M3: a grant from 2.1.0 before several folders was attached as read-only; removing the working folder
        made it writable. It never is, without the person's click."""
        host.detach(self.chat)
        real, device, inode = cg.identity(self.ref)
        state = self.chat.store.directory
        (state / cf.NAME).write_text(json.dumps({"version": 1, "chats": {"chat-1": {
            "path": str(self.ref), "real": real, "device": device, "inode": inode, "at": 1.0}}}), encoding="utf-8")
        host._grants.clear()
        angelo_guard_twin.reset()
        work = host.attach(self.chat, self.work)   # The legacy folder isn't working: the new one is.
        self.assertEqual([(r["label"], r["working"]) for r in host.labels(self.chat)], [("Refs", False), ("Game", True)])
        self.chat.events.clear()
        host.remove(self.chat, work)
        self.assertIsNone(host.working(self.chat), "Never moved on to the legacy folder")
        self.assertNotIn(host.NOW_WORKING, [data.get("i18n", [None])[0] for _, data in self.chat.events])
        legacy = host.labels(self.chat)[0]["id"]
        host.make_working(self.chat, legacy)   # The person's own click does.
        self.assertEqual(host.working(self.chat)["label"], "Refs")
        self.assertFalse(host.grants(state).state("chat-1", legacy).legacy)


class RouteTests(Working):
    def test_the_persons_approve_runs_it_without_an_undo_step(self):
        """S1: the Approve went through the Undo operator (refused with Global Undo off, an empty undo step per
        note). It runs as task memory's notes do."""
        source = (Path(cw.__file__).with_name("ui.py")).read_text(encoding="utf-8")
        self.assertIn('{"angelo_memory_update", "angelo_discard_preview", "angelo_context_folder_write"}', source)

    def test_a_workers_chat_has_no_folders_on_either_path(self):
        """S2: a chat its worker hosts (design A′) has no remote executor and its store is the lead's state folder:
        it read the lead's folders. Both tools are refused there as every tool a worker can't run is."""
        self.chat.chat_host = object()
        self.assertEqual((host.labels(self.chat), host.observation(self.chat)), ([], None))
        from angelo_agent.refusal_codes import RefusalError, error_result
        call = SimpleNamespace(id="c", name=cw.TOOL, automatic=False,
                               arguments=cw.validate({"action": "CREATE", "path": "x.md", "content": "x"}))
        with self.assertRaises(RefusalError) as caught:
            cw.precondition(self.chat, call)
        self.assertEqual((error_result(caught.exception)["error_code"], error_result(caught.exception).get(
            "scene_changed")), ("UNSUPPORTED", False))
        read = SimpleNamespace(id="r", arguments=cf.validate({"action": "LIST"}), approval=True)
        host.start(self.chat, read)
        self.assertEqual(self.chat.events[-1][1]["result"]["error_code"], "UNSUPPORTED")
        self.assertFalse((self.work / "x.md").exists())


class PublishTests(Working):
    def test_a_new_file_takes_its_name_by_an_exclusive_rename_never_a_link_first(self):
        """S3: publishing by hard link failed on exFAT, FAT and many shares."""
        with patch.object(os, "link", side_effect=AssertionError("linked")):
            result = self.write(action="CREATE", path="n.md", content="x")
        if cwk.exclusive_rename.available():
            self.assertTrue(result["written"])

    @unittest.skipUnless(POSIX, environment_skip("Windows publishes by its own rename without replace on the held "
                                                 "folder's handle (WindowsDirectory.rename_new), never these two"))
    def test_where_neither_an_exclusive_rename_nor_a_link_works_nothing_is_written(self):
        with patch.object(cwk.exclusive_rename, "rename", side_effect=cwk.exclusive_rename.Unsupported()), \
                patch.object(os, "link", side_effect=OSError(45, "Operation not supported")):
            result = self.refused(action="CREATE", path="deep/n.md", content="x")
        self.assertEqual(result["error"], cwk.NO_PUBLISH)
        self.assertFalse((self.work / "deep").exists(), "The folder made for it is gone too")
        self.assertEqual([p.name for p in self.work.iterdir() if p.name.startswith(".angelo-")], [])

    @unittest.skipUnless(POSIX, environment_skip("the umask is POSIX"))
    def test_new_files_and_folders_get_the_persons_own_permissions(self):
        mask = os.umask(0o022)
        self.addCleanup(os.umask, mask)
        self.write(action="CREATE", path="a/b.md", content="x")
        self.assertEqual(stat.S_IMODE((self.work / "a" / "b.md").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.work / "a").stat().st_mode), 0o755)

    def test_a_temporary_file_a_stopped_write_left_is_swept_later(self):
        stale = self.work / (".angelo-" + "a" * 32 + ".tmp")
        stale.write_text("half", encoding="utf-8")
        old = time.time() - cwk.STALE_TEMPORARY_SECONDS - 60
        os.utime(stale, (old, old))
        fresh = self.work / (".angelo-" + "b" * 32 + ".tmp")   # Another write's, under way: kept.
        fresh.write_text("busy", encoding="utf-8")
        self.write(action="CREATE", path="n.md", content="x")
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())


class RuleTests(Working):
    def test_a_new_data_or_settings_file_always_asks(self):
        """S5: a toolchain reads JSON and YAML as settings; creating one is never Auto's alone."""
        for path in ("package.json", "ci/pipeline.yml", "data.jsonl", "compose.yaml"):
            with self.subTest(path=path):
                call = self.call(action="CREATE", path=path, content="{}")
                self.assertEqual(cw.auto_reason(self.chat, call), auto_reasons.CONFIG_FILE)
        for path in ("notes.md", "list.csv", "plan.txt"):
            with self.subTest(path=path):
                self.assertIsNone(cw.auto_reason(self.chat, self.call(action="CREATE", path=path, content="x")))
        self.assertIn(auto_reasons.CONFIG_FILE, auto_reasons.LINES)

    def test_a_data_or_settings_file_is_never_created_without_the_persons_approval(self):
        """S5 at its run too (re-read NIT): Auto's run, or a run with no click of the person's, is refused."""
        call = self.call(automatic=True, action="CREATE", path="package.json", content="{}")
        self.assertEqual(cw.run(self.chat, call)["error"], cw.ASKS_CONFIG)
        results = []
        call = self.call(action="CREATE", path="package.json", content="{}")
        thread = threading.Thread(target=lambda: results.append(cw.run(self.chat, call)))
        thread.start()
        thread.join()
        self.assertEqual(results[0]["error"], cw.ASKS_CONFIG)
        self.assertFalse((self.work / "package.json").exists())
        self.assertTrue(self.write(action="CREATE", path="package.json", content="{}")["written"], "The person's own")

    def test_windows_short_names_are_never_written(self):
        for path in ("GIT~1/config.md", "PROGRA~1/x.md", "notes~2.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                cw.validate({"action": "CREATE", "path": path, "content": "x"})

    def test_a_lower_layers_refusal_is_said_as_one_sentence(self):
        """NIT: "Snapshot storage contains an invalid file" reached the model."""
        with patch.object(cwk.WorkingFolder, "write_note", side_effect=ValueError("Snapshot storage changed")):
            result = self.write(action="CREATE", path="n.md", content="x")
        self.assertEqual((result["error"], result["error_code"]), (cw.ELSEWHERE, "VALIDATION"))

    def test_a_failure_to_remember_a_written_file_still_says_it_was_written(self):
        with patch.object(cg.Grants, "_save", side_effect=OSError(28, "No space left")):
            result = self.write(action="CREATE", path="n.md", content="x")
        self.assertTrue(result["written"])
        self.assertTrue((self.work / "n.md").exists())


class ConcurrencyTests(Working):
    def test_ownership_is_never_given_off_a_grant_scope(self):
        store, results = host.grants(self.chat.store.directory), []
        signature = self.call(action="UPDATE", path="README.md", content="x").precondition[1]

        def elsewhere():
            try:
                ga.record(ga.CONTEXT_FOLDERS, cg.own_key("chat-1", self.work_id), cg.token(signature))
            except PermissionError as error:
                results.append(error)
        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join()
        self.assertEqual(len(results), 1)
        self.assertFalse(store.owns("chat-1", self.work_id, signature))


if __name__ == "__main__":
    unittest.main()
