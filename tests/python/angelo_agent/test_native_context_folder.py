# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A folder attached to a chat as context (``context_folder``, ``context_folder_host``): read-only, path-free, off the
main thread, and nothing outside it is ever reached (links, ``..``, absolute paths, credential and state folders,
huge files). Real folders in a temporary directory; no Blender.
"""
import suite_isolation  # noqa: F401
from host_waits import CAP_S  # noqa: E402

import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import context_folder as cf, context_folder_host as host  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
import angelo_guard_twin  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())   # Its tests attach folders as the person does (grant_authority).

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
POSIX = os.name == "posix"


class Tree(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name)
        self.root = self.base / "Castle Game"
        (self.root / "levels").mkdir(parents=True)
        (self.root / "levels" / "keep.tscn").write_text("[node name=\"Keep\"]\nscale = 2\n", encoding="utf-8")
        (self.root / "README.md").write_text("Castle game\nWalls are 4 m tall.\n", encoding="utf-8")
        (self.root / "art").mkdir()
        (self.root / "art" / "wall.png").write_bytes(PNG)
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("secret", encoding="utf-8")
        (self.root / ".env").write_text("KEY=1", encoding="utf-8")
        self.outside = self.base / "private"
        self.outside.mkdir()
        (self.outside / "notes.txt").write_text("outside words", encoding="utf-8")
        self.folder = cf.ReadOnlyFolder(self.root)

    def run_action(self, **arguments):
        staged = []

        def stage(data, suffix):
            staged.append((data, suffix))
            return {"id": "picture", "bytes": len(data)}
        return cf.run(self.folder, cf.validate(arguments), stage), staged


class ActionTests(Tree):
    def test_list_shows_the_folder_without_hidden_or_credential_entries(self):
        result, _ = self.run_action(action="LIST")
        names = [entry["name"] for entry in result["entries"]]
        self.assertEqual(sorted(names), ["README.md", "art", "levels"])
        self.assertEqual(self.run_action(action="LIST", path="levels")[0]["entries"][0]["name"], "keep.tscn")

    def test_read_gives_an_untrusted_excerpt(self):
        result, _ = self.run_action(action="READ", path="README.md", start=0, length=11)
        self.assertEqual((result["text"], result["untrusted"]), ("Castle game", True))

    def test_search_finds_names_and_text_with_the_line(self):
        result, _ = self.run_action(action="SEARCH", query="WALLS ARE")
        self.assertEqual(result["hits"], [{"path": "README.md", "match": "text", "line": 2,
                                           "excerpt": "Walls are 4 m tall."}])
        named, _ = self.run_action(action="SEARCH", query="keep")
        self.assertIn({"path": "levels/keep.tscn", "match": "name"}, named["hits"])
        self.assertTrue(named["complete"])
        self.assertEqual(self.run_action(action="SEARCH", query="secret")[0]["hits"], [], "Never inside .git")

    def test_view_stages_a_picture(self):
        result, staged = self.run_action(action="VIEW", path="art/wall.png")
        self.assertEqual(result["views"], [{"id": "picture", "bytes": len(PNG), "name": "wall.png"}])
        self.assertEqual(staged, [(PNG, ".png")])
        with self.assertRaises(ValueError):
            self.run_action(action="VIEW", path="README.md")

    def test_a_drive_mounted_inside_the_folder_later_is_never_read(self):
        """Re-read of ccdfea52edb, S-1: vetting looks once, at the pick; a drive mounted inside the folder later (under
        /Volumes or /media) was read as part of it. A read never crosses from the attached folder's device."""
        from angelo_agent import files
        real_info = files.directory_info
        mounted = self.root / "levels"

        def info(folder):
            found = real_info(folder)
            if files._canonical_path(folder) is not None and Path(files._canonical_path(folder)).name == mounted.name:
                return os.stat_result((found.st_mode, found.st_ino, found.st_dev + 1) + tuple(found)[3:])
            return found
        with patch.object(files, "directory_info", info):
            with self.assertRaises((ValueError, OSError)):
                self.run_action(action="READ", path="levels/keep.tscn", start=0, length=10)
            self.assertIn("Castle game", self.run_action(action="READ", path="README.md", start=0, length=40)[0]["text"])

    def test_a_search_says_a_folder_on_another_drive_was_left_out_never_that_it_was_too_large(self):
        """Re-read of dd87b2d5aa3, N-1: the walk recorded the device refusal as ``folder_size``, so the model was
        told the folder was too large."""
        from angelo_agent import files
        real_info = files.directory_info

        def info(folder):
            found = real_info(folder)
            if files._canonical_path(folder) is not None and Path(files._canonical_path(folder)).name == "levels":
                return os.stat_result((found.st_mode, found.st_ino, found.st_dev + 1) + tuple(found)[3:])
            return found
        with patch.object(files, "directory_info", info):
            result = self.run_action(action="SEARCH", query="Castle")[0]
        self.assertIn("other_drive", result["limits"])
        self.assertNotIn("folder_size", result["limits"])

    def test_a_search_says_a_file_on_another_drive_was_left_out(self):
        """Re-read of 70142a371dd, N-3: SEARCH skipped a file on another drive silently, so its result read as
        complete. The file is left out and the result says other_drive."""
        from angelo_agent import files
        real_admit = cf.ReadOnlyFolder._admit

        def admit(workspace, info):
            if info is not None and info.st_size == (self.root / "README.md").stat().st_size:
                raise cf.OtherDrive("That file is on another drive than the attached folder")
            return real_admit(workspace, info)
        with patch.object(cf.ReadOnlyFolder, "_admit", admit):
            result = self.run_action(action="SEARCH", query="Castle")[0]
        self.assertIn("other_drive", result["limits"])
        self.assertFalse(result["complete"])

    def test_a_file_mounted_over_a_name_in_the_folder_is_never_read(self):
        """Re-read of dd87b2d5aa3, N-1: the device was checked per folder, so a file bind-mounted over a name in the
        folder (its own device) was read. Each file's own stat is admitted before it opens, for READ and VIEW."""
        import ast
        from angelo_agent import files
        workspace = cf.ReadOnlyFolder(str(self.root))
        here = os.stat(self.root / "README.md")
        workspace._admit(here)   # The attached folder's own device: read.
        elsewhere = os.stat_result((here.st_mode, here.st_ino, here.st_dev + 1) + tuple(here)[3:])
        with self.assertRaises(cf.OtherDrive):
            workspace._admit(elsewhere)
        for module, name in ((files, "_read"), (cf, "read_image")):
            with self.subTest(reader=name):
                tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
                code = ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name))
                self.assertLess(code.index("self._admit("), code.index("_open_file("), "before it opens")

    def test_view_judges_a_picture_by_its_bytes_never_its_name(self):
        """Fable review 1, F-6: View checked only the suffix, so any file up to 5 MiB named .png was copied into the
        chat's pictures and offered to the model as one."""
        (self.root / "art" / "notes.png").write_text("API_KEY=not a picture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_action(action="VIEW", path="art/notes.png")
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        (self.root / "art" / "photo.png").write_bytes(jpeg)
        _result, staged = self.run_action(action="VIEW", path="art/photo.png")
        self.assertEqual(staged, [(jpeg, ".jpg")], "kept as what its bytes are")

    def test_nothing_outside_the_folder(self):
        for path in ("../private/notes.txt", str(self.outside / "notes.txt"), ".git/config", ".env", "levels/../../private"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.run_action(action="READ", path=path, start=0, length=10)

    def test_links_out_are_refused(self):
        if not POSIX:
            self.skipTest(environment_skip("a link made without Windows' symlink privilege (POSIX only)"))
        (self.root / "notes-link.txt").symlink_to(self.outside / "notes.txt")
        (self.root / "dir-link").symlink_to(self.outside)
        for path in ("notes-link.txt", "dir-link/notes.txt"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.run_action(action="READ", path=path, start=0, length=10)
        names = [entry["name"] for entry in self.run_action(action="LIST")[0]["entries"]]
        self.assertNotIn("dir-link", names)
        self.assertEqual(self.run_action(action="SEARCH", query="outside")[0]["hits"], [], "A walk never follows a link")

    def test_huge_files_are_refused(self):
        huge = self.root / "huge.txt"
        with open(huge, "wb") as handle:
            handle.truncate(cf.files.MAX_FILE + 1)
        with self.assertRaises(ValueError):
            self.run_action(action="READ", path="huge.txt", start=0, length=10)
        picture = self.root / "art" / "big.png"
        with open(picture, "wb") as handle:
            handle.truncate(cf.MAX_IMAGE + 1)
        with self.assertRaises(ValueError):
            self.run_action(action="VIEW", path="art/big.png")

    def test_it_never_writes(self):
        for name in ("angelo_write_file", "angelo_delete_file"):
            with self.assertRaises(ValueError):
                self.folder.execute(name, {"path": "README.md", "content": "x"})
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "Castle game\nWalls are 4 m tall.\n")

    def test_angelo_state_folders_are_refused_inside_the_folder(self):
        state = self.root / "angelo-state"
        state.mkdir()
        (state / "chat.json").write_text("{}", encoding="utf-8")
        folder = cf.ReadOnlyFolder(self.root, excluded=[state])
        with self.assertRaises((ValueError, OSError)):
            cf.run(folder, cf.validate({"action": "READ", "path": "angelo-state/chat.json"}), None)
        listed = cf.run(folder, cf.validate({"action": "LIST"}), None)
        self.assertNotIn("angelo-state", [entry["name"] for entry in listed["entries"]])

    def test_search_is_bounded(self):
        many = self.root / "many"
        many.mkdir()
        for index in range(cf.MAX_HITS + 10):
            (many / ("hit%03d.txt" % index)).write_text("x", encoding="utf-8")
        result, _ = self.run_action(action="SEARCH", query="hit")
        self.assertEqual(len(result["hits"]), cf.MAX_HITS)
        self.assertFalse(result["complete"])

    def test_arguments_are_checked(self):
        for bad in ({"action": "WRITE"}, {"action": "READ"}, {"action": "SEARCH"}, {"action": "SEARCH", "query": " "},
                    {"action": "READ", "path": "a", "length": cf.READ_LIMIT + 1}, {"action": "LIST", "path": "a\x00"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cf.validate(bad)


class PathFreeTests(Tree):
    def test_the_manifest_names_the_folder_never_its_path(self):
        manifest = cf.manifest(self.folder, self.root.name)
        text = json.dumps(manifest)
        self.assertNotIn(str(self.base), text)
        self.assertNotIn(os.path.realpath(self.base), text)
        self.assertEqual(manifest["folder"], "Castle Game")
        self.assertEqual(manifest["top_level"], ["art/", "levels/", "README.md"])
        self.assertEqual(manifest["kinds"][".tscn"], 1)
        self.assertTrue(manifest["complete"])

    def test_errors_never_carry_the_path(self):
        self.assertEqual(cf.path_free("no such %s/x" % self.root, self.root), "no such <Castle Game>/x")
        call = SimpleNamespace(id="c1", arguments={"action": "READ", "path": "missing.txt", "start": 0, "length": 5})
        events = []
        job = cf.FolderJob(SimpleNamespace(emit=lambda kind, data: events.append(data)), call, self.folder, None)
        job._run()
        self.assertNotIn(str(self.base), json.dumps(events[0]["result"]))


class Chat:
    def __init__(self, state, references):
        self.state = {"id": "chat-1"}
        self.store = SimpleNamespace(directory=state)
        self.references = SimpleNamespace(directory=references)
        self.delegate_jobs, self.events, self.files = {}, [], None
        self.version = 0   # As Controller.__init__: Remove bumps it to redraw the card.
        self.done = threading.Event()

    def emit(self, kind, data):
        self.events.append((kind, data, threading.current_thread().name))
        self.done.set()


class HostTests(Tree):
    def setUp(self):
        super().setUp()
        host._grants.clear()
        host._manifests.clear()
        self.addCleanup(host._grants.clear)
        self.addCleanup(host._manifests.clear)
        self.chat = Chat(self.base / "state", self.base / "references")

    def call(self, **arguments):
        return SimpleNamespace(id="call-1", arguments=cf.validate(arguments), approval=True)

    def test_the_grant_store_itself_refuses_agent_code_and_a_failed_write_grants_nothing(self):
        """r2 S-c: the lowest function that changes the state is guarded, and writes the file before memory."""
        from unittest.mock import patch
        from angelo_agent import grant_guard
        store = cf.Grants(self.base / "grants-probe")
        with grant_guard.agent_script(), self.assertRaises(PermissionError):
            store.attach("chat-1", self.root)
        self.assertIsNone(store.get("chat-1"))
        with patch.object(cf.Grants, "_save", side_effect=OSError("refused")), self.assertRaises(OSError):
            store.attach("chat-1", self.root)
        self.assertIsNone(store.get("chat-1"))
        store.attach("chat-1", self.root)
        self.assertEqual(store.get("chat-1"), self.root)
        # r3 S-4: removing narrows at once; a failed write is reported, never undone.
        with patch.object(cf.Grants, "_save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            store.revoke("chat-1")
        self.assertIsNone(store.get("chat-1"))

    def test_agent_code_never_attaches_a_folder(self):
        """Attaching grants the agent a read of the folder: the person's pick only (the share-link break-it class,
        2026-11-15: the function behind the grant refuses agent code, not only its operator). Removing narrows."""
        from angelo_agent import grant_guard
        with grant_guard.agent_script():
            with self.assertRaises(PermissionError):
                host.attach(self.chat, self.root)
        self.assertIsNone(host.folder_of(self.chat))
        host.attach(self.chat, self.root)
        with grant_guard.agent_script():
            host.detach(self.chat)
        self.assertIsNone(host.folder_of(self.chat))

    def test_no_folder_answers_at_once(self):
        call = self.call(action="LIST")
        host.start(self.chat, call)
        kind, data, _ = self.chat.events[0]
        self.assertEqual((kind, data["result"]["error"], data["_call"]), ("delegate_finished", cf.NO_FOLDER, call))
        self.assertIsNone(host.observation(self.chat))

    def test_a_call_runs_on_its_own_thread_and_finishes_as_an_event(self):
        host.attach(self.chat, self.root)
        call = self.call(action="READ", path="README.md", start=0, length=6)
        host.start(self.chat, call)
        self.assertFalse(call.approval, "Read-only inside the grant: never asked")
        self.assertIs(self.chat.delegate_jobs["call-1"].call, call)
        self.assertTrue(self.chat.done.wait(CAP_S))
        kind, data, thread = self.chat.events[0]
        self.assertEqual((kind, data["result"]["text"]), ("delegate_finished", "Castle"))
        self.assertEqual(thread, "AngeloContextFolder")

    def test_a_viewed_picture_is_kept_in_the_chats_own_folder(self):
        host.attach(self.chat, self.root)
        host.start(self.chat, self.call(action="VIEW", path="art/wall.png"))
        self.assertTrue(self.chat.done.wait(CAP_S))
        record = self.chat.events[0][1]["result"]["views"][0]
        kept = Path(record["path"])
        self.assertEqual((kept.parent, kept.suffix, record["mime"]), (self.base / "references", ".png", "image/png"))
        self.assertEqual(kept.read_bytes(), PNG)
        self.assertEqual(record["name"], "wall.png", "named by the file's own name, never a translated label")

    def slow(self):
        """``cf.run`` held until the test lets it go: ``(entered, release)``."""
        entered, release, real = threading.Event(), threading.Event(), cf.run

        def held(workspace, arguments, stage_image):
            entered.set()
            release.wait(CAP_S)
            return real(workspace, arguments, stage_image)
        patcher = patch.object(cf, "run", held)
        patcher.start()
        self.addCleanup(patcher.stop)
        return entered, release

    def finished(self):
        return [data["result"] for kind, data, _ in self.chat.events if kind == "delegate_finished"]

    def test_remove_stops_an_outstanding_read_and_nothing_is_delivered_after(self):
        """Audit 2026-11-14 F2: Remove cancels the reads under way; a read that ends later delivers nothing."""
        host.attach(self.chat, self.root)
        entered, release = self.slow()
        host.start(self.chat, self.call(action="READ", path="README.md"))
        self.assertTrue(entered.wait(CAP_S))
        self.assertTrue(host.remove(self.chat))
        release.set()
        self.chat.delegate_jobs["call-1"].thread.join(CAP_S)
        finished = self.finished()
        self.assertEqual(len(finished), 1)
        self.assertIn("error", finished[0])
        self.assertNotIn("Castle game", repr(self.chat.events))

    def test_removing_one_folder_stops_its_reads_and_never_another_folders(self):
        """Several folders: Remove ends the reads of its own folder only; a read of the chat's other folder goes on."""
        first = host.attach(self.chat, self.root)
        host.attach(self.chat, self.outside)
        entered, release = self.slow()
        host.start(self.chat, self.call(action="READ", path="README.md"))   # The working folder (the first).
        self.assertTrue(entered.wait(CAP_S))
        host.detach(self.chat, first)
        release.set()
        self.chat.delegate_jobs["call-1"].thread.join(CAP_S)
        self.assertNotIn("Castle game", repr(self.chat.events))
        self.assertIn("error", self.finished()[0])
        self.chat.events.clear()
        self.chat.done.clear()
        host.start(self.chat, SimpleNamespace(id="call-2", approval=True, arguments=cf.validate(
            {"action": "READ", "path": "notes.txt", "folder": "private"})))
        self.assertTrue(self.chat.done.wait(CAP_S))
        self.chat.delegate_jobs["call-2"].thread.join(CAP_S)
        self.assertEqual(self.finished()[0]["text"], "outside words")

    def test_a_picture_viewed_as_the_folder_is_removed_is_not_kept(self):
        host.attach(self.chat, self.root)
        entered, release = self.slow()
        host.start(self.chat, self.call(action="VIEW", path="art/wall.png"))
        self.assertTrue(entered.wait(CAP_S))
        host.detach(self.chat)
        release.set()
        self.chat.delegate_jobs["call-1"].thread.join(CAP_S)
        references = self.base / "references"
        self.assertEqual(sorted(references.iterdir()) if references.exists() else [], [])
        self.assertNotIn("views", self.finished()[0])

    def test_cancel_ends_the_call_and_a_late_result_changes_nothing(self):
        job = cf.FolderJob(self.chat, SimpleNamespace(id="c", arguments={"action": "LIST"}), self.folder, None)
        job.cancel("Stopped")
        job._run()
        self.assertEqual([data["result"] for _, data, _ in self.chat.events], [{"error": "Stopped"}])

    def test_the_manifest_is_built_off_the_main_thread_and_dropped_on_remove(self):
        folder_id = host.attach(self.chat, self.root)
        first = host.observation(self.chat)
        self.assertEqual((first[0]["folder"], first[0]["role"]), ("Castle Game", "working"))
        self.assertTrue(host._manifests[("chat-1", folder_id)]["ready"].wait(CAP_S))
        self.assertEqual(host.observation(self.chat)[0]["top_level"], ["art/", "levels/", "README.md"])
        host.detach(self.chat)
        self.assertIsNone(host.observation(self.chat))

    def test_the_cards_remove_is_drawn_without_touching_the_disk(self):
        """Fable review 1, F-5 (and round 2's N-7): the card asked ``attached_stands`` at each redraw, which resolved
        and stat'ed the folder on the main thread, so a folder on a slow or unreachable share stalled the chat."""
        host.attach(self.chat, self.root)

        def no_disk(*args, **kwargs):
            raise AssertionError("the disk was touched while drawing the card")
        with patch.object(os.path, "realpath", no_disk), patch.object(os, "stat", no_disk), \
                patch.object(os, "lstat", no_disk):
            self.assertTrue(host.attached_stands(self.chat))
        host.detach(self.chat)
        self.assertFalse(host.attached_stands(self.chat))

    def test_a_folder_moved_or_deleted_since_is_still_removed_from_its_card(self):
        """F-4 / F-5: Remove asked whether the folder still stood where it was picked, so a grant whose folder had
        moved or been deleted could not be removed from its card, and stayed saved."""
        host.attach(self.chat, self.root)
        shutil.rmtree(self.root)
        self.assertIsNone(host.folder_of(self.chat), "never read: the folder is gone")
        self.assertTrue(host.attached_stands(self.chat), "its card still offers Remove")
        self.assertTrue(host.remove(self.chat))
        self.assertFalse(host.attached_stands(self.chat))
        self.assertEqual(json.loads((self.base / "state" / cf.NAME).read_text(encoding="utf-8"))["chats"], {})

    def test_a_deleted_chats_folder_grant_ends_with_it(self):
        """Fable review 1, F-4: a grant outlived its chat; deleting the chat left the folder readable to a chat id that
        could come back. Deleting a chat (to Recently Deleted) revokes its folder; restoring it asks for the folder
        again."""
        from angelo_agent import conversations_delete
        host.attach(self.chat, self.root)
        directory = str(self.chat.store.directory)
        records = {"chat-1": {"id": "chat-1"}, "chat-2": {"id": "chat-2"}}
        sessions = SimpleNamespace(records=records, active="chat-2", save=lambda: None,
                                   ordered=lambda: list(records.values()), update=lambda state: None)
        ui = SimpleNamespace(_sessions=sessions, _controllers={}, _drafts={}, state_directory=lambda: directory)
        with patch.object(conversations_delete.conversations, "delete", return_value={"id": "chat-1"}), \
                patch.object(conversations_delete, "search_index",
                             return_value=SimpleNamespace(forget=lambda session_id: None)):
            conversations_delete.delete(ui, "chat-1", switch=lambda session_id: None)
        self.assertFalse(host.attached_stands(self.chat))
        self.assertIsNone(host.folder_of(self.chat))
        self.assertEqual(json.loads((self.base / "state" / cf.NAME).read_text(encoding="utf-8"))["chats"], {})

    def test_a_worker_chat_has_no_folder(self):
        host.attach(self.chat, self.root)
        self.chat.remote_executor = object()
        self.assertIsNone(host.folder_of(self.chat))


class RegistrationTests(unittest.TestCase):
    def test_one_read_only_tool_deferred_on_both_runtimes_and_never_by_voice(self):
        from angelo_agent import plan_kinds, tool_specs
        spec = tool_specs.TOOL_MAP[cf.TOOL]
        self.assertTrue(spec["annotations"]["readOnlyHint"])
        self.assertIn(cf.TOOL, plan_kinds.DEFERRED_TOOLS)
        self.assertNotIn(cf.TOOL, [tool["name"] for tool in tool_specs.declared_dynamic()])
        self.assertNotIn(cf.TOOL, tool_specs.COLLABORATOR_TOOLS)
        self.assertEqual(tool_specs.validate_call(cf.TOOL, {"action": "LIST"}), {"action": "LIST"})
        self.assertLess(len(json.dumps(spec)), 1200, "A compact declaration")

    def test_the_controller_routes_it_to_its_thread(self):
        source = (Path(cf.__file__).with_name("controller.py")).read_text(encoding="utf-8")
        self.assertIn("elif call.name == context_folder_host.TOOL:", source)
        self.assertIn("return context_folder_host.start(self, call)", source)


if __name__ == "__main__":
    unittest.main()
