# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writing text files into a chat's working folder (``context_folder_write``; agent_chat.rst, "Folder as context"):
every refusal (outside the folder by a link, ``..``, a spelling or a race; a reference, hidden, repository or private
folder; delete, move and rename; too large; a file that was there before, without the person's approval), and what
is allowed (a new file, Angelo's own file, atomic, path-free). No Blender here."""

import suite_isolation  # noqa: F401

import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import context_folder_host as host  # noqa: E402
from angelo_agent import context_folder_write as cw  # noqa: E402
from angelo_agent import context_folder_working as cwk  # noqa: E402
from angelo_agent import files, private_roots  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
import angelo_guard_twin  # noqa: E402
angelo_guard_twin.stand_in_for_the_person(globals())   # Its tests approve as the person does, unless they say not.

POSIX = os.name == "posix"


class Chat:
    def __init__(self, state):
        self.state = {"id": "chat-1", "options": {"approval": "AUTO_BOUNDED"}}
        self.store = SimpleNamespace(directory=state)
        self.delegate_jobs, self.events, self.files = {}, [], None
        self.version = 0

    def emit(self, kind, data):
        self.events.append((kind, data))


class Working(unittest.TestCase):
    def setUp(self):
        angelo_guard_twin.strict_for(self)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.base = Path(os.path.realpath(scratch.name))
        self.work, self.ref, self.outside = self.base / "Game", self.base / "Refs", self.base / "elsewhere"
        for folder in (self.work, self.ref, self.outside):
            folder.mkdir()
        (self.work / "README.md").write_text("The person's own notes\n", encoding="utf-8")
        (self.ref / "style.md").write_text("Reference\n", encoding="utf-8")
        (self.work / ".git").mkdir()
        host._grants.clear()
        host._manifests.clear()
        self.addCleanup(host._grants.clear)
        self.chat = Chat(self.base / "state")
        self.work_id = host.attach(self.chat, self.work)
        self.ref_id = host.attach(self.chat, self.ref)

    def call(self, automatic=False, **arguments):
        call = SimpleNamespace(id="call-1", name=cw.TOOL, arguments=cw.validate(arguments), automatic=automatic)
        call.precondition = cw.precondition(self.chat, call)
        return call

    def write(self, automatic=False, **arguments):
        return cw.run(self.chat, self.call(automatic=automatic, **arguments))

    def refused(self, error=ValueError, **arguments):
        """The call is refused before or at its run, and nothing outside what was there changed."""
        before = self.snapshot()
        try:
            result = self.write(**arguments)
        except error as refused:   # Refused as it was bound (Controller._precondition): the same sentence.
            result = {"error": str(refused), "written": False}
        self.assertFalse(result.get("written"), result)
        self.assertIn("error", result)
        self.assertEqual(self.snapshot(), before)
        return result

    def snapshot(self):
        found = {}
        for root in (self.work, self.ref, self.outside):
            for path in sorted(root.rglob("*")):
                found[str(path)] = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        return found


class AllowedTests(Working):
    def test_a_new_checklist_is_written_atomically_and_named_without_a_path(self):
        result = self.write(action="CREATE", path="plans/checklist.md", content="- [ ] walls\n- [ ] gate\n")
        self.assertEqual((result["written"], result["folder"], result["path"]), (True, "Game", "plans/checklist.md"))
        self.assertEqual((self.work / "plans" / "checklist.md").read_text(encoding="utf-8"),
                         "- [ ] walls\n- [ ] gate\n")
        self.assertNotIn(str(self.base), repr(result))
        self.assertEqual(sorted(path.name for path in (self.work / "plans").iterdir()), ["checklist.md"],
                         "No temporary file is left beside it")

    def test_creating_and_updating_its_own_file_follow_auto(self):
        create = self.call(action="CREATE", path="notes.md", content="one")
        self.assertIsNone(cw.auto_reason(self.chat, create), "A new file: a bounded typed edit in Auto")
        self.assertTrue(cw.run(self.chat, create)["written"])
        update = self.call(automatic=True, action="UPDATE", path="notes.md", content="two")
        self.assertIsNone(cw.auto_reason(self.chat, update), "Angelo's own file, unchanged since: no question")
        self.assertTrue(cw.run(self.chat, update)["written"])
        self.assertEqual((self.work / "notes.md").read_text(encoding="utf-8"), "two")

    def test_its_own_file_survives_a_restart_as_its_own(self):
        self.write(action="CREATE", path="notes.md", content="one")
        angelo_guard_twin.reset()
        host._grants.clear()
        self.assertIsNone(cw.auto_reason(self.chat, self.call(action="UPDATE", path="notes.md", content="two")))

    @unittest.skipUnless(POSIX, environment_skip("file modes are POSIX permissions"))
    def test_an_update_keeps_the_persons_file_mode(self):
        os.chmod(self.work / "README.md", 0o644)
        self.write(action="UPDATE", path="README.md", content="new")   # The person's approval (the harness scope).
        self.assertEqual(stat.S_IMODE((self.work / "README.md").stat().st_mode), 0o644)

    def test_auto_reasons_reads_the_rule(self):
        from angelo_agent import auto_reasons, grant_authority
        grant_authority.record(grant_authority.APPROVAL_MODES, "chat-1", "AUTO_BOUNDED")   # Auto, as C holds it.
        self.addCleanup(grant_authority.forget, grant_authority.APPROVAL_MODES, "chat-1")
        create = self.call(action="CREATE", path="a.md", content="x")
        update = self.call(action="UPDATE", path="README.md", content="x")
        self.assertIsNone(auto_reasons.bounded_reason(self.chat, create, "AUTO_BOUNDED"))
        self.assertEqual(auto_reasons.bounded_reason(self.chat, update, "AUTO_BOUNDED"), auto_reasons.PREEXISTING)
        self.assertEqual(auto_reasons.bounded_reason(self.chat, create, "ASK"), auto_reasons.NOT_AUTO,
                         "Ask asks for every write")
        self.assertIn(auto_reasons.PREEXISTING, auto_reasons.LINES)


class PreexistingTests(Working):
    def test_a_file_that_was_there_always_asks_and_auto_never_runs_it(self):
        call = self.call(automatic=True, action="UPDATE", path="README.md", content="replaced")
        from angelo_agent import auto_reasons
        self.assertEqual(cw.auto_reason(self.chat, call), auto_reasons.PREEXISTING)
        self.assertEqual(cw.run(self.chat, call)["error"], cw.ASKS)
        self.assertEqual((self.work / "README.md").read_text(encoding="utf-8"), "The person's own notes\n")

    def test_without_the_persons_own_approval_it_is_never_written(self):
        """Its run outside a grant scope (no click of the person's) is refused, whatever the call's flags say."""
        call, results = self.call(action="UPDATE", path="README.md", content="replaced"), []
        thread = threading.Thread(target=lambda: results.append(cw.run(self.chat, call)))
        thread.start()
        thread.join()
        self.assertEqual(results[0]["error"], cw.ASKS)
        self.assertEqual((self.work / "README.md").read_text(encoding="utf-8"), "The person's own notes\n")

    def test_its_own_file_changed_by_the_person_since_asks_again(self):
        self.write(action="CREATE", path="notes.md", content="one")
        (self.work / "notes.md").write_text("the person's edit, longer", encoding="utf-8")
        from angelo_agent import auto_reasons
        self.assertEqual(cw.auto_reason(self.chat, self.call(action="UPDATE", path="notes.md", content="x")),
                         auto_reasons.PREEXISTING)

    def test_a_create_over_a_file_that_is_there_is_refused(self):
        self.assertEqual(self.refused(action="CREATE", path="README.md", content="x")["error"], cw.EXISTS)
        self.assertEqual(self.refused(action="UPDATE", path="missing.md", content="x")["error"], cw.MISSING_FILE)


class OutsideTests(Working):
    def test_dot_dot_and_absolute_paths_are_refused(self):
        for path in ("../elsewhere/x.md", "notes/../../x.md", "/tmp/x.md", str(self.outside / "x.md"), "..",
                     "a/./b.md"):
            with self.subTest(path=path):
                self.refused(action="CREATE", path=path, content="x")

    @unittest.skipUnless(POSIX, environment_skip("symbolic links need POSIX permissions to make in a host test"))
    def test_links_out_are_refused_at_every_name(self):
        (self.work / "out").symlink_to(self.outside, target_is_directory=True)
        (self.work / "linked.md").symlink_to(self.outside / "target.md")
        for path in ("out/x.md", "linked.md"):
            for action in ("CREATE", "UPDATE"):
                with self.subTest(path=path, action=action):
                    self.refused(action=action, path=path, content="x")
        self.assertFalse((self.outside / "target.md").exists())

    def test_a_junction_is_refused_as_a_link(self):
        """Windows junctions are links to the file tools (``Path.is_junction``): stood in for on any platform."""
        (self.work / "junction").mkdir()
        real = Path.is_junction
        with patch.object(Path, "is_junction", lambda self: self.name == "junction" or real(self)):
            self.refused(action="CREATE", path="junction/x.md", content="x")

    @unittest.skipUnless(POSIX, environment_skip("hard links to another folder need POSIX link semantics here"))
    def test_a_hard_link_to_a_file_outside_is_never_updated(self):
        (self.outside / "shared.md").write_text("outside", encoding="utf-8")
        os.link(self.outside / "shared.md", self.work / "shared.md")
        self.refused(action="UPDATE", path="shared.md", content="x")
        self.assertEqual((self.outside / "shared.md").read_text(encoding="utf-8"), "outside")

    def test_case_and_unicode_spellings_of_hidden_and_repository_names_are_refused(self):
        for path in (".git/x.md", ".GIT/config.md", ".Git/x.md", "．git/x.md", "．．/x.md",
                     "sub/.ssh/x.md", ".env.md", ".hidden/x.md", "․hidden.md".replace("․", "．")):
            with self.subTest(path=path):
                self.refused(action="CREATE", path=path, content="x")
        self.assertEqual(list((self.work / ".git").iterdir()), [])

    def test_names_windows_reads_otherwise_are_refused(self):
        for path in ("notes.md:stream", "notes.md.", "notes.md ", " notes.md", "con.md", "LPT1.txt", "a\\b.md",
                     "a//b.md", "a?.md"):
            with self.subTest(path=path):
                self.refused(action="CREATE", path=path, content="x")

    @unittest.skipUnless(POSIX, environment_skip("swapping a folder for a symbolic link mid-call needs POSIX links"))
    def test_a_folder_swapped_for_a_link_between_check_and_write_is_refused(self):
        (self.work / "notes").mkdir()
        call = self.call(action="CREATE", path="notes/x.md", content="x")
        real_path = cwk.WorkingFolder.path

        def then_swap(workspace, relative):
            checked = real_path(workspace, relative)
            (self.work / "notes").rmdir()
            (self.work / "notes").symlink_to(self.outside, target_is_directory=True)   # After the check.
            return checked
        with patch.object(cwk.WorkingFolder, "path", then_swap):
            result = cw.run(self.chat, call)
        self.assertFalse(result.get("written"))
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_file_that_appears_or_changes_between_check_and_write_is_never_replaced(self):
        call = self.call(action="CREATE", path="race.md", content="agent")
        real_open = cwk._open_file

        def appears(folder, name, flags, mode=0o600):
            (self.work / "race.md").write_text("the person's", encoding="utf-8")   # Meanwhile.
            return real_open(folder, name, flags, mode)
        with patch.object(cwk, "_open_file", appears):
            self.assertFalse(cw.run(self.chat, call).get("written"))
        self.assertEqual((self.work / "race.md").read_text(encoding="utf-8"), "the person's")
        update = self.call(action="UPDATE", path="race.md", content="agent")
        (self.work / "race.md").write_text("edited again, longer", encoding="utf-8")
        self.assertEqual(cw.run(self.chat, update)["error"], cw.CHANGED)
        self.assertEqual((self.work / "race.md").read_text(encoding="utf-8"), "edited again, longer")
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), [".git", "README.md", "race.md"])


class FolderRuleTests(Working):
    def test_a_reference_folder_is_never_written(self):
        result = self.refused(action="CREATE", path="x.md", content="x", folder="Refs")
        self.assertEqual(result["error"], cw.REFERENCE_ONLY)
        self.assertEqual(self.refused(action="CREATE", path="x.md", content="x", folder="Nope")["written"], False)

    def test_a_refusal_says_nothing_ran_so_no_other_edit_is_held(self):
        """A write refused as it is bound reads as a classified refusal that changed nothing (``error_result``): in the
        app an unclassified one held the request's other scene edits for recovery (GUI proof, 2.1.0)."""
        from angelo_agent.refusal_codes import error_result
        call = SimpleNamespace(id="c", name=cw.TOOL, automatic=False, arguments=cw.validate(
            {"action": "CREATE", "path": "x.md", "content": "x", "folder": "Refs"}))
        with self.assertRaises(ValueError) as caught:
            cw.precondition(self.chat, call)
        result = error_result(caught.exception)
        self.assertEqual((result["error_code"], result.get("scene_changed")), ("OWNERSHIP", False))
        self.assertNotIn("partial_changes_possible", result)
        for path in ("../x.md", ".git/x.md"):
            call.arguments = cw.validate({"action": "CREATE", "path": "a.md", "content": "x"})
            call.arguments["path"] = path   # Past validation, as a stale or altered call would be.
            with self.assertRaises(ValueError) as caught:
                cw.precondition(self.chat, call)
            self.assertEqual(error_result(caught.exception).get("scene_changed"), False)
        self.assertEqual(self.refused(action="UPDATE", path="missing.md", content="x")["error"], cw.MISSING_FILE)
        refused = cw.run(self.chat, SimpleNamespace(arguments={"action": "CREATE", "path": "README.md", "content": "x"},
                                                    automatic=False, precondition=(self.work_id, cw.MISSING)))
        self.assertEqual((refused["written"], refused["scene_changed"]), (False, False))
        self.assertIn(refused["error_code"], ("STALE_CONTEXT", "VALIDATION"))

    def test_a_write_whose_last_check_fails_is_still_reported_written(self):
        """Published, then the folder's fsync fails: the result says it was written (never "nothing changed"), and the
        file is not remembered as Angelo's own, so changing it again asks."""
        call = self.call(action="CREATE", path="late.md", content="x")
        with patch.object(cwk, "sync_directory", side_effect=OSError(5, "I/O error")):
            result = cw.run(self.chat, call)
        self.assertTrue(result["written"])
        self.assertTrue((self.work / "late.md").exists())
        from angelo_agent import auto_reasons
        self.assertEqual(cw.auto_reason(self.chat, self.call(action="UPDATE", path="late.md", content="y")),
                         auto_reasons.PREEXISTING)

    def test_a_reference_folder_moved_inside_the_working_folder_is_never_written(self):
        self.ref.rename(self.work / "Refs")
        for path in ("Refs/x.md", "Refs/deeper/x.md"):
            with self.subTest(path=path):
                result = self.refused(action="CREATE", path=path, content="x")
                self.assertEqual(result["error"], cw.INSIDE_REFERENCE)

    def test_a_private_folder_inside_the_working_folder_is_refused(self):
        """Angelo's own state folders (an owner file inside: ``private_roots``) are refused wherever they are."""
        state = self.work / "state-copy"
        state.mkdir()
        (state / private_roots.OWNER).write_text("token", encoding="utf-8")
        self.refused(action="CREATE", path="state-copy/x.md", content="x")

    def test_after_the_person_switches_only_the_new_working_folder_is_written(self):
        host.make_working(self.chat, self.ref_id)
        self.assertEqual(self.refused(action="CREATE", path="x.md", content="x", folder="Game")["error"],
                         cw.REFERENCE_ONLY)
        self.assertTrue(self.write(action="CREATE", path="x.md", content="x")["written"])
        self.assertTrue((self.ref / "x.md").exists())
        self.assertFalse((self.work / "x.md").exists())

    def test_a_write_bound_to_the_old_working_folder_is_refused_after_a_switch(self):
        call = self.call(action="CREATE", path="x.md", content="x")
        host.make_working(self.chat, self.ref_id)
        self.assertEqual(cw.run(self.chat, call)["error"], cw.SWITCHED)

    def test_no_working_folder_no_write(self):
        host.detach(self.chat)
        with self.assertRaises(ValueError):
            self.call(action="CREATE", path="x.md", content="x")
        self.chat.remote_executor = object()   # A worker's chat has none either.
        with self.assertRaises(ValueError):
            self.call(action="CREATE", path="x.md", content="x")

    def test_the_working_folder_replaced_at_its_path_is_never_written(self):
        self.work.rename(self.base / "moved")
        self.work.mkdir()
        with self.assertRaises(ValueError):
            self.call(action="CREATE", path="x.md", content="x")
        self.assertEqual(list(self.work.iterdir()), [])


class LimitTests(Working):
    def test_delete_move_and_rename_are_not_offered(self):
        for action in ("DELETE", "MOVE", "RENAME", "delete", "Rename", "REMOVE", "APPEND", "", None):
            with self.subTest(action=action), self.assertRaises(ValueError):
                cw.validate({"action": action, "path": "README.md", "content": ""})
        self.assertEqual(cw.SPEC["inputSchema"]["properties"]["action"]["enum"], ["CREATE", "UPDATE"])
        self.assertNotIn("to", cw.SPEC["inputSchema"]["properties"])
        self.assertTrue((self.work / "README.md").exists())

    def test_only_text_files(self):
        for path in ("tool.py", "run.sh", "Makefile", "scene.blend", "x.md.exe", "level.tscn"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                cw.validate({"action": "CREATE", "path": path, "content": "x"})

    def test_oversize_text_is_refused(self):
        with self.assertRaises(ValueError):
            cw.validate({"action": "CREATE", "path": "big.md", "content": "x" * (cw.MAX_CHARS + 1)})
        with self.assertRaises(ValueError):
            cw.validate({"action": "CREATE", "path": "nul.md", "content": "a\x00b"})
        with self.assertRaises(ValueError):
            cw.validate({"action": "CREATE", "path": "/".join(["d"] * cw.MAX_DEPTH) + "/x.md", "content": "x"})
        with patch.object(cw, "MAX_BYTES", 8):
            self.assertFalse(cw.run(self.chat, SimpleNamespace(
                arguments={"action": "CREATE", "path": "big.md", "content": "é" * 5}, automatic=False,
                precondition=(self.work_id, cw.MISSING))).get("written"), "Bounded again after encoding")
        self.assertFalse((self.work / "big.md").exists())

    def test_a_chat_creates_a_bounded_number_of_files(self):
        with patch.object(cw, "MAX_CREATED", 2):
            for name in ("a.md", "b.md"):
                self.assertTrue(self.write(action="CREATE", path=name, content="x")["written"])
            self.assertEqual(self.refused(action="CREATE", path="c.md", content="x")["error"], cw.TOO_MANY)

    def test_errors_never_carry_a_path_on_the_disk(self):
        with patch.object(cwk.WorkingFolder, "write_note", side_effect=OSError(13, "Permission denied",
                                                                             str(self.work / "x.md"))):
            result = self.write(action="CREATE", path="x.md", content="x")
        self.assertNotIn(str(self.base), repr(result))


class RegistrationTests(unittest.TestCase):
    def test_a_deferred_writing_tool_never_for_voice_collaborators_apps_or_plans(self):
        from angelo_agent import codex_plan, external_clients, plan_kinds, request_path, tool_specs
        spec = tool_specs.TOOL_MAP[cw.TOOL]
        self.assertFalse(spec["annotations"]["readOnlyHint"])
        self.assertNotIn(cw.TOOL, tool_specs.READ_ONLY_TOOLS)
        self.assertIn(cw.TOOL, plan_kinds.DEFERRED_TOOLS)
        self.assertNotIn(cw.TOOL, [tool["name"] for tool in tool_specs.declared_dynamic()])
        self.assertNotIn(cw.TOOL, tool_specs.COLLABORATOR_TOOLS)
        self.assertIn(cw.TOOL, external_clients.NEVER_FOR_APPS)
        self.assertIn(cw.TOOL, codex_plan.IN_TURN_ONLY)
        self.assertIn(cw.TOOL, request_path.NOT_TYPED)
        self.assertEqual(tool_specs.validate_call(cw.TOOL, {"action": "CREATE", "path": "a.md", "content": ""}),
                         {"action": "CREATE", "path": "a.md", "content": ""})

    def test_the_controller_binds_and_runs_it_with_no_scene_snapshot(self):
        source = (Path(cw.__file__).with_name("controller.py")).read_text(encoding="utf-8")
        self.assertIn("elif call.name == context_folder_write.TOOL:", source)
        self.assertIn("context_folder_write.run(self, call)", source)
        self.assertIn("context_folder_write.precondition(self, call)", source)

    def test_the_transcript_names_the_file_never_a_path(self):
        from angelo_agent import activity_text, approvals
        arguments = {"action": "CREATE", "path": "plans/checklist.md", "content": "- [ ] a"}
        row = {"tool": cw.TOOL, "status": "completed", "arguments": arguments, "result": {"success": True}}
        self.assertEqual(activity_text.step_title(row), "Wrote “checklist.md”")
        message = approvals.approval_message(cw.TOOL, arguments)
        self.assertEqual(message["title"], "Write “checklist.md”")
        self.assertNotIn("/", message["title"] + message["body"])
        from angelo_agent import approval_families
        self.assertEqual(approval_families.approval_kind(cw.TOOL), "Working folder",
                         "Its card names its own kind: a note in the person's folder is no scene change")


if __name__ == "__main__":
    unittest.main()
