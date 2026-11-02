# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a finished tool hands its waiters is theirs: adding to it never changes the chat's journaled state in place.

The journal finds a changed result record by its identity and a changed row by its mark (``session_writer``), so a
record or a row must never share an object with code that may add to it later. Before the fix
``tool_finalization.complete_result`` kept the very result it returned, and ``Controller.finish_tool`` emitted that
object into the call's activity row too: a worker that noted ``part_saved_again`` on its answer after a script saved
the open file as another file (``worker_script.contain``, the lanes path ``worker_host``) changed the record unseen.
The release candidate's ``test_blender_worker_python@lanes`` (case save_as_and_unrestorable_raise) failed there:
"$.results.<id>.result.part_saved_again: missing on disk". Here a real ``Controller`` and ``SessionStore`` run with
both journal checks on, which a host test has, and as since B1 review 2 a worker Blender in a native suite has too.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_worker_python_route as route  # noqa: E402  (a module, so its tests are not collected twice)
from angelo_agent import activity_rows, session_writer, sessions, worker_host, worker_script  # noqa: E402
from angelo_agent.owned_values import owned  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

READ = {"success": True, "read_only": True, "scene_changed": False, "objects": {"names": ["Cube"]}}


class JournalCase(unittest.TestCase):
    """A real chat and its journal with both checks on."""

    def setUp(self):
        for module, name in ((session_writer, "CHECK_TOUCHES"), (sessions, "VERIFY_JOURNAL")):
            check = mock.patch.object(module, name, True, create=True)
            check.start()
            self.addCleanup(check.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        chat = self.chat = Controller(self.directory)
        self.addCleanup(chat.close)
        chat.busy = True
        chat.set_document(("synthetic-document",))
        chat.save()

    def finished(self):
        """A tool call finished on the real path: ``(what finish_tool returned, what its waiter got)``."""
        answers = []
        self.chat.request_tool("inspect", "angelo_inspect_scene", {}, answers.append, enqueue=False)
        call = next(iter(self.chat.pending.values()))
        returned = self.chat.finish_tool(call, dict(READ, objects={"names": ["Cube"]}))
        self.chat.pump_events()   # The activity event reaches its row.
        self.assertEqual(len(answers), 1)
        return returned, answers[0]

    def on_disk(self):
        return SessionStore(self.directory).load()

    def assert_disk_is_memory(self):
        saved = self.on_disk()
        self.assertEqual(saved["results"], self.chat.state["results"])
        self.assertEqual(saved["messages"], self.chat.state["messages"])


class ResultOwnership(JournalCase):
    def test_a_worker_note_on_its_answer_changes_no_journaled_record(self):
        returned, answer = self.finished()
        # The lanes path: the worker's answer, after a script saved the open file as another file.
        host = SimpleNamespace(bpy=SimpleNamespace(data=SimpleNamespace(filepath="/elsewhere/copy.blend")),
                               part="/parts/part.blend", ui=SimpleNamespace(_document_serial=1))
        noted, reloaded = worker_script.contain(host, answer, 1)
        self.assertFalse(reloaded)
        self.assertIn("copy.blend", noted["part_saved_again"])   # The note still reaches the lead.
        self.chat.save()   # Before the fix: AssertionError, the record changed in place, missing on disk.
        self.assert_disk_is_memory()
        record = next(iter(self.chat.state["results"].values()))
        self.assertNotIn("part_saved_again", record["result"])

    def test_adding_to_what_finish_tool_returned_or_delivered_changes_nothing_journaled(self):
        returned, answer = self.finished()
        self.chat.save()
        returned["noted_by_the_caller"] = True
        answer["noted_by_the_waiter"] = True
        self.chat.save()   # Before the fix: the record (and the activity row) changed in place.
        self.assert_disk_is_memory()
        record = next(iter(self.chat.state["results"].values()))
        row = next(row for row in self.chat.state["messages"] if row.get("role") == "activity")
        for held in (record["result"], row["result"]):
            self.assertNotIn("noted_by_the_caller", held)
            self.assertNotIn("noted_by_the_waiter", held)

    def test_a_waiter_adding_inside_its_answer_changes_no_journaled_record(self):
        # Ownership review: a top-level copy still shared every nested mapping and list with the record and the row.
        returned, answer = self.finished()
        self.chat.save()
        answer["objects"]["names"].append("Added by the waiter")
        returned["objects"]["names"].append("Added by the caller")
        self.chat.save()
        self.assert_disk_is_memory()
        record = next(iter(self.chat.state["results"].values()))
        self.assertEqual(record["result"]["objects"]["names"], ["Cube"])

    def test_a_replayed_answer_shares_nothing_with_its_record(self):
        self.finished()
        self.chat.save()
        replays = []
        self.chat.request_tool("inspect", "angelo_inspect_scene", {}, replays.append, enqueue=False)
        self.assertTrue(replays and replays[0].get("replayed"))
        replays[0]["objects"]["names"].append("Added by the replay's waiter")
        self.chat.save()
        self.assert_disk_is_memory()

    def test_an_activity_row_holds_its_own_copy_of_each_mapping_it_stores(self):
        result, arguments = {"success": True}, {"name": "Cube"}
        activity_rows.store(self.chat, {"id": "c1", "tool": "angelo_inspect_scene", "arguments": arguments,
                                        "status": "completed", "result": result})
        self.chat.save()
        result["added_later"] = arguments["added_later"] = True   # The sender adds to what it sent.
        self.chat.save()   # Before the fix: a row changed without its mark (the touch check names it).
        self.assert_disk_is_memory()


class OwnedValues(unittest.TestCase):
    def test_no_mapping_or_list_is_shared_at_any_depth_and_leaves_are_kept(self):
        leaf = "Object.001"
        value = {"a": [{"b": (1, [2.5, leaf])}, None, True], "c": {"d": {}}}
        copy = owned(value)
        self.assertEqual(copy, value)
        stack = [(value, copy)]
        while stack:
            original, copied = stack.pop()
            if type(original) in (dict, list):
                self.assertIsNot(original, copied)
            if type(original) is dict:
                stack.extend((original[key], copied[key]) for key in original)
            elif type(original) in (list, tuple):
                self.assertIs(type(copied), type(original))
                stack.extend(zip(original, copied))
            else:
                self.assertIs(copied, original)


class JournaledShadow(route.ScriptShadow):
    """The route tests' worker shadow whose calls finish in a real ``Controller``: its waiter (``WorkerHost.edit``'s)
    is the real one's, so the lanes path runs on a real journal with both checks on."""

    def __init__(self, precondition, chat):
        super().__init__(precondition)
        self.chat = chat

    def request_tool(self, call_id, name, arguments, callback):
        super().request_tool(call_id, name, arguments, callback)
        self.chat.request_tool(call_id, name, arguments, callback, enqueue=False)

    def finish_tool(self, call, result):
        self.pending.pop(call.id, None)
        real = next(iter(self.chat.pending.values()))
        self.chat.finish_tool(real, result)
        self.chat.pump_events()


class LanesPathOnAJournal(JournalCase):
    def test_a_script_that_saved_as_another_file_on_the_lanes_path_keeps_the_journal_whole(self):
        """``WorkerHost.edit`` as the worker runs it, the script saving the open file as another file: the lead's
        answer carries the note, the worker's journal holds exactly memory's state."""
        part = os.path.join(os.sep, "w", "part.blend")
        route.ui._document_serial = "serial"
        self.chat.state["options"]["approval"] = "ASK"
        shadow = JournaledShadow((1, "bound"), self.chat)
        host = worker_host.WorkerHost(route.ui, None, endpoint=None, part=part)

        def saved_elsewhere(blender):
            blender.save_as("/elsewhere/copy.blend")
            return {"success": True}
        host.bpy = blender = route.FakeBlender(part, shadow, saved_elsewhere)
        host.shadows["chat-1"] = shadow
        host.layout = lambda: {"revision": host.revision}
        request = {"name": route.SCRIPT, "arguments": route.WorkerHostScriptTests.CODE,
                   "approved": route.values.encode((1, "bound")),
                   "digest": route.digest(route.SCRIPT, route.WorkerHostScriptTests.CODE)}
        with mock.patch("angelo_agent.controller.pump_controller_tools"):
            answer = host.edit(shadow, None, request)
        self.assertIn("copy.blend", answer["result"]["part_saved_again"])   # The lead is told.
        self.assertEqual(blender.saved, ["/elsewhere/copy.blend", part])
        record = next(iter(self.chat.state["results"].values()))
        self.assertEqual(record["result"]["success"], True)   # The worker's chat recorded the script's result.
        self.chat.save()   # Before the fix: the record changed in place ("part_saved_again: missing on disk").
        self.assert_disk_is_memory()


if __name__ == "__main__":
    unittest.main()
