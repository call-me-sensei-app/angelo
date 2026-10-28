# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat keeps its own scene (``document_work.document_changed``, ``document_scene``; break-it E2E-20 R5-M-1).

Another scene of the same file shown in the window never invalidates a chat that holds work; another file (a load:
a new serial) does, as before; a chat with nothing follows the shown key quietly, as in 1.0.5.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import document_scene, document_work  # noqa: E402

SCENE, KITCHEN, LOADED = (7, 101), (7, 202), (8, 303)   # (load serial, scene session uid)


class Chat:
    def __init__(self, document=SCENE, work=True):
        self.document, self.document_invalid, self.busy, self.pending = document, False, False, {}
        self._native_preview_cards, self.rows, self.stopped, self.stale = {}, [], 0, []
        self.state = {"id": "chat", "messages": [{"role": "user", "text": "Build a tea set"}] if work else []}

    def mark_memory_stale(self, why):
        self.stale.append(why)

    def set_document(self, document):
        if document != self.document:
            document_work.document_changed(self, document)

    def invalidate_native_stages(self, why):
        pass

    def cancel_tools(self, why):
        pass

    def stop(self):
        self.stopped += 1

    def emit(self, kind, data):
        self.rows.append(data)


class DocumentTests(unittest.TestCase):
    def test_another_scene_of_its_file_never_invalidates_a_chat_that_holds_work(self):
        chat = Chat()
        document_work.document_changed(chat, KITCHEN)
        self.assertEqual((chat.document, chat.document_invalid, chat.stopped, chat.rows), (SCENE, False, 0, []))
        self.assertTrue(document_scene.away(chat, KITCHEN))
        self.assertFalse(document_scene.away(chat, SCENE))   # Its scene shown again: nothing to undo.

    def test_another_file_invalidates_it_as_before(self):
        chat = Chat()
        document_work.document_changed(chat, LOADED)
        self.assertTrue(chat.document_invalid and chat.stopped == 1 and len(chat.rows) == 1)
        self.assertFalse(document_scene.away(chat, LOADED))   # Invalid, not away.

    def test_a_chat_with_nothing_follows_the_shown_scene_quietly(self):
        chat = Chat(work=False)
        document_work.document_changed(chat, KITCHEN)
        self.assertEqual((chat.document, chat.document_invalid, chat.rows), (KITCHEN, False, []))

    def test_same_file(self):
        self.assertTrue(document_scene.same_file(SCENE, KITCHEN))
        self.assertFalse(document_scene.same_file(SCENE, LOADED))
        self.assertFalse(document_scene.same_file(None, SCENE))


class GateTests(unittest.TestCase):
    def test_its_work_waits_while_away_and_its_turn_is_never_ended_for_it(self):
        chat = Chat()
        chat.busy, chat.voice = True, None
        self.assertFalse(document_scene.hold(chat, SCENE))   # Shown: its work runs.
        for _ in range(1000):   # However long the person looks elsewhere (break-it R5M1-L1 M-1).
            self.assertTrue(document_scene.hold(chat, KITCHEN))
        self.assertEqual((chat.stopped, chat.rows), (0, []))   # A thinking turn, a lead coordinating: they go on.
        self.assertFalse(document_scene.hold(chat, SCENE))   # Back: its work runs again.

    def test_a_held_chat_never_counts_as_work_to_dispatch(self):
        from angelo_agent.controller import Controller
        held = SimpleNamespace(_held=True, _native_cleanup=[])   # Nothing else is read: no fast re-arm, no spin (H-1).
        self.assertFalse(Controller.has_dispatchable_tools.fget(held))
        held._native_cleanup.append(object())   # A stopped stage still retires while held (break-it R5-M-1).
        self.assertTrue(Controller.has_dispatchable_tools.fget(held))

    def test_a_voice_session_ends_once_with_a_notice_while_away(self):
        stopped = []
        chat = Chat()
        chat.voice = SimpleNamespace(stop=lambda: stopped.append(1))
        for _ in range(3):
            self.assertTrue(document_scene.hold(chat, KITCHEN))
        import time
        deadline = time.monotonic() + 5
        while not stopped and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual((stopped, len(chat.rows)), ([1], 1))
        self.assertEqual((chat.rows[0]["role"], chat.rows[0]["text"]), ("system", document_scene.VOICE_AWAY))

    def test_an_invalid_chat_is_not_away(self):
        chat = Chat()
        chat.document_invalid = True
        self.assertFalse(document_scene.hold(chat, KITCHEN))

    def test_the_persons_input_and_window_actions_refuse_while_away_and_say_why(self):
        from unittest import mock
        chat, reports = Chat(), []
        operator = SimpleNamespace(report=lambda kind, text: reports.append(text))
        window = SimpleNamespace(scene=SimpleNamespace(name="Kitchen"))
        ui = SimpleNamespace(document_key=lambda: KITCHEN, main_window=lambda: window)
        own = SimpleNamespace(name="Scene")
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": ui}), mock.patch.object(
                document_scene, "scene_of", lambda chat: own):
            self.assertTrue(document_scene.refused(operator, chat))
            self.assertEqual(reports[0], document_scene.AWAY.format(scene="Scene"))   # rpt_: no catalog.
            with self.assertRaises(RuntimeError):
                document_scene.refuse(chat)   # A request while away never observes or edits it.
            self.assertEqual(chat.document, SCENE)
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": ui}), mock.patch.object(
                document_scene, "scene_of", lambda chat: None):
            self.assertEqual(str(document_scene.refusal(chat)), document_scene.GONE)   # Its scene is gone: said so.
        shown = SimpleNamespace(document_key=lambda: SCENE, main_window=lambda: window)
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": shown}):
            self.assertFalse(document_scene.refused(operator, chat))
            document_scene.refuse(chat)   # Its own scene shown: nothing refused.

    def test_selecting_an_away_chat_brings_its_scene_back_a_workers_never_moves_the_window(self):
        from unittest import mock
        from angelo_agent import parallel_sessions
        chat, window = Chat(), SimpleNamespace(scene="Kitchen")
        with mock.patch.object(document_scene, "scene_of", lambda chat: "Scene"):
            with mock.patch.object(parallel_sessions, "is_worker", lambda session: True):
                document_scene.select(chat, window, KITCHEN)
            self.assertEqual(window.scene, "Kitchen")   # A worker's chat: only what is displayed changes.
            with mock.patch.object(parallel_sessions, "is_worker", lambda session: False):
                document_scene.select(chat, window, SCENE)   # Shown: nothing changes.
                self.assertEqual(window.scene, "Kitchen")
                document_scene.select(chat, window, KITCHEN)
            self.assertEqual(window.scene, "Scene")

    def test_its_own_call_that_changes_the_shown_scene_leaves_it_on_its_scene_with_every_key(self):
        from unittest import mock
        own = SimpleNamespace(session_uid=SCENE[1], name="Scene")

        def executor(uid):
            return SimpleNamespace(window=SimpleNamespace(scene=SimpleNamespace(session_uid=uid, name="Shot")))
        chat, moved = Chat(), executor(KITCHEN[1])
        with mock.patch.object(document_scene, "scene_of", lambda chat: own):
            document_scene.keep_shown(chat, moved, SCENE[1])   # A script's scene.new switched the window.
        self.assertEqual((moved.window.scene, chat.document), (own, SCENE))   # Its calls, memory, previews: its key.
        other, untouched = Chat(document=(7, 303)), executor(KITCHEN[1])
        document_scene.keep_shown(other, untouched, SCENE[1])   # Bound elsewhere: not its move.
        self.assertEqual(untouched.window.scene.session_uid, KITCHEN[1])
        with mock.patch.object(document_scene, "scene_of", lambda chat: None):
            gone = executor(KITCHEN[1])
            document_scene.keep_shown(chat, gone, SCENE[1])   # Its own scene removed: GONE, the window as it is.
        self.assertEqual(gone.window.scene.session_uid, KITCHEN[1])
        document_scene.keep_shown(chat, SimpleNamespace(), SCENE[1])   # No window (a lane): nothing to do.

    def test_a_background_angelo_invalidates_on_another_scene_as_in_1_0_5(self):
        from unittest import mock
        chat = Chat()
        with mock.patch.object(document_scene, "unattended", lambda: True):
            document_work.document_changed(chat, KITCHEN)   # A worker or headless host: no person to show it back.
        self.assertEqual((chat.document_invalid, chat.stopped), (True, 1))

    def test_no_lifetime_of_a_held_chat_runs(self):
        from unittest import mock
        import threading
        from angelo_agent import tool_deadlines
        chat = Chat()
        chat.voice, call = None, SimpleNamespace(expires=1000.0)
        chat.pending = {"chat/call": call}
        clock = iter([5000.0, 5060.0])
        with mock.patch("time.monotonic", lambda: next(clock)):
            document_scene.hold(chat, KITCHEN)
            document_scene.hold(chat, KITCHEN)   # A minute held: its expiry moved a minute on (R2-M-1).
        self.assertEqual(call.expires, 1060.0)
        held, done = SimpleNamespace(_held=True), threading.Event()
        threading.Timer(0.3, done.set).start()
        with mock.patch.object(tool_deadlines, "POLL_SECONDS", 0.01):
            self.assertTrue(tool_deadlines.await_host(done, SimpleNamespace(host=held), 0.05))   # Not late: held.
            self.assertFalse(tool_deadlines.await_host(threading.Event(), SimpleNamespace(_held=False), 0.05))

    def test_a_held_chat_is_named_wherever_angelo_waits_for_every_chat(self):
        from unittest import mock
        held, idle = Chat(), Chat(document=KITCHEN)
        held.busy, held._held, idle.busy = True, True, True
        fill = lambda text, **f: text.format(**f)   # noqa: E731
        with mock.patch.object(document_scene, "scene_of", lambda chat: SimpleNamespace(name="Scene")):
            self.assertEqual(document_scene.busy_message([held, idle], fill, "Stop first"),
                             document_scene.WAITING.format(scenes="'Scene'"))   # R3-M-1: names what it waits for.
        self.assertEqual(document_scene.busy_message([idle], fill, "Stop first"), "Stop first")

    def test_the_model_is_told_when_its_own_call_left_another_scene_shown(self):
        from unittest import mock
        own = SimpleNamespace(session_uid=SCENE[1], name="Scene")
        executor = SimpleNamespace(window=SimpleNamespace(scene=SimpleNamespace(session_uid=KITCHEN[1], name="Shot")))
        with mock.patch.object(document_scene, "scene_of", lambda chat: own):
            told = document_scene.keep_shown(Chat(), executor, SCENE[1])
        self.assertEqual(told, document_scene.REVERTED.format(scene="Scene", other="Shot"))   # R3-M-2.
        self.assertIsNone(document_scene.keep_shown(Chat(), executor, KITCHEN[1]))   # Nothing moved: nothing said.

    def test_a_held_chats_writer_lease_stops_no_other_chats_clock(self):
        from unittest import mock
        holder = SimpleNamespace(backend=SimpleNamespace(controller=SimpleNamespace(_held=True)))
        call = SimpleNamespace(expires=500.0)
        clock = iter([100.0, 130.0, 140.0])
        with mock.patch("time.monotonic", lambda: next(clock)):
            document_scene.wait_on_lease(call, holder)
            document_scene.wait_on_lease(call, holder)   # 30 s waiting on a held chat's lease: not spent (R3-M-3).
            holder.backend.controller._held = False
            document_scene.wait_on_lease(call, holder)   # Its holder shown again: the wait counts as before.
        self.assertEqual(call.expires, 530.0)

    def test_a_wait_on_another_holder_starts_its_own_count(self):
        from unittest import mock
        held = lambda: SimpleNamespace(backend=SimpleNamespace(controller=SimpleNamespace(_held=True)))  # noqa: E731
        first, second, call = held(), held(), SimpleNamespace(expires=500.0)
        clock = iter([100.0, 200.0, 210.0])
        with mock.patch("time.monotonic", lambda: next(clock)):
            document_scene.wait_on_lease(call, first)
            document_scene.wait_on_lease(call, second)   # 100 s later, another holder: nothing credited (R4-M-1).
            document_scene.wait_on_lease(call, second)
        self.assertEqual(call.expires, 510.0)

    def test_a_wait_asks_only_a_held_holder_to_give_the_writer_up(self):
        shown, held = SimpleNamespace(_held=False), SimpleNamespace(_held=True)
        for chat in (shown, held):
            document_scene.wait_on_lease(SimpleNamespace(expires=None), SimpleNamespace(
                backend=SimpleNamespace(controller=chat)))
        self.assertEqual((getattr(shown, "_writer_wanted", None), held._writer_wanted), (None, True))
        chat = Chat()
        chat._writer_wanted = True
        document_scene.hold(chat, SCENE)   # Shown again before its pump yielded: nothing left to give up.
        self.assertFalse(chat._writer_wanted)

    def test_a_held_chat_gives_up_a_stage_another_chat_waits_for_and_stages_it_again(self):
        from unittest import mock
        from angelo_agent import program_jobs

        def held(**changes):
            call = SimpleNamespace(id="c", native_preparation=None, mutation_started=False, committing=False,
                                   commit_requested=False, executing=True)
            job = SimpleNamespace(accepting_preview=False)
            for name, value in changes.items():
                setattr(job if name == "accepting_preview" else call, name, value)
            chat = SimpleNamespace(_writer_wanted=True, native_jobs={"c": job}, pending={"c": call}, discarded=[])
            chat._discard_native_job = lambda c: (chat.discarded.append(c), chat.native_jobs.pop(c.id), {})[2]
            return chat, call, job

        chat, call, job = held()
        with mock.patch.object(program_jobs.WRITER, "owner", job):
            document_scene.yield_writer(chat)
        self.assertEqual((chat.discarded, call.executing, chat.native_jobs, chat._writer_wanted),
                         ([call], False, {}, False))
        for kept in ({"committing": True}, {"commit_requested": True},
                     {"mutation_started": True}, {"native_preparation": object()}):
            chat, call, job = held(**kept)   # A commit, another thread's preparation, a live write: kept.
            with mock.patch.object(program_jobs.WRITER, "owner", job):
                document_scene.yield_writer(chat)
            self.assertEqual((chat.discarded, call.executing), ([], True), kept)
        chat, call, job = held()
        with mock.patch.object(program_jobs.WRITER, "owner", object()):   # Its job keeps no writer: nothing to give.
            document_scene.yield_writer(chat)
        chat._writer_wanted, chat.native_jobs["c"] = False, job
        with mock.patch.object(program_jobs.WRITER, "owner", job):   # Nobody waits: its stage stays.
            document_scene.yield_writer(chat)
        self.assertEqual(chat.discarded, [])

    def test_a_held_chat_retires_a_stage_stopped_off_the_main_thread(self):
        """Break-it R5-M-1: a voice session's end stops the held chat's stage on its own thread, so the stage waits in
        ``_native_cleanup`` with the writer lease; the held pump retires it, and the lease is free for the shown chat."""
        import threading
        from unittest import mock
        from agent_runtime_fixtures import Backend, program
        from angelo_agent import program_jobs
        from angelo_agent.controller import Controller, ToolDispatchBudget
        writer = program_jobs.WriterLease()
        job = program_jobs.ProgramJob(Backend(), program(), owner="a", writer=writer, previews=program_jobs.PreviewStore())
        job.poll()   # Staged: it keeps the lease.
        call = SimpleNamespace(id="c", native_preparation=None, finished_preparation=None)
        chat = SimpleNamespace(native_jobs={"c": job}, _native_cleanup=[], pending={}, _writer_wanted=True,
                               set_document=lambda document: None)
        stopper = threading.Thread(target=Controller._discard_native_job, args=(chat, call))   # As cancel_tools does.
        stopper.start()
        stopper.join()
        self.assertEqual((chat._native_cleanup, chat.native_jobs, writer.owner), ([job], {}, job))
        with mock.patch.object(document_scene, "hold", lambda chat, document: True), \
                mock.patch.object(program_jobs, "WRITER", writer):
            Controller._pump_tools(chat, SimpleNamespace(), KITCHEN, None, ToolDispatchBudget(), None)
        self.assertEqual((job.phase, chat._native_cleanup, writer.owner), ("CANCELLED", [], None))

    def test_a_held_chats_accepted_preview_waits_without_the_writer_and_is_checked_again(self):
        """Break-it R5-M-2: a consumed preview is never staged again, so its exact stage waits without the lease while
        its chat is held; another chat's program stages and commits meanwhile; shown again, it takes the lease back
        and commits through REVALIDATE, or fails when the live scene changed under it."""
        from unittest import mock
        from agent_runtime_fixtures import Backend, program
        from angelo_agent import program_jobs

        def accepted(backend, writer, previews):
            staging = program_jobs.ProgramJob(backend, program("PREVIEW"), owner="a", writer=writer, previews=previews)
            while not staging.closed:
                staging.poll()
            job = program_jobs.ProgramJob.accept(staging.result["preview_id"], "a", previews=previews, writer=writer)
            job.poll()   # REVALIDATE, then mid-VALIDATE: it keeps the lease.
            call = SimpleNamespace(id="c", native_preparation=None, mutation_started=False, committing=False,
                                   commit_requested=False, executing=True)
            chat = SimpleNamespace(_writer_wanted=True, native_jobs={"c": job}, pending={"c": call}, discarded=[])
            chat._discard_native_job = lambda c: chat.discarded.append(c)
            with mock.patch.object(program_jobs, "WRITER", writer):
                document_scene.yield_writer(chat)
            return job, call, chat

        for changed in (False, True):
            backend, writer, previews = Backend(), program_jobs.WriterLease(), program_jobs.PreviewStore()
            job, call, chat = accepted(backend, writer, previews)
            self.assertEqual((writer.owner, job.closed, job.phase, getattr(job, "writer_suspended", None), chat.discarded),
                             (None, False, "REVALIDATE", True, []))
            calls = list(backend.calls)
            self.assertFalse(job.poll()["final"])   # Without the lease it does no stage work.
            self.assertEqual(backend.calls, calls)
            other = program_jobs.ProgramJob(Backend(), program(location=[4, 5, 6]), owner="b", writer=writer,
                                            previews=previews)   # The shown chat's edit takes the lease.
            self.assertFalse(document_scene.resume_writer(call, job))
            self.assertIs(call._lease_wait_at[1], other)   # It waits on the other program, as a new stage does.
            while not other.ready_to_commit:
                other.poll()
            self.assertEqual(other.commit()["status"], "committed")
            if changed:
                backend.revision += 1   # The live object changed under the accepted stage.
            self.assertTrue(document_scene.resume_writer(call, job))
            self.assertIs(writer.owner, job)
            while not job.ready_to_commit and not job.closed:
                job.poll()
            if changed:
                self.assertEqual((job.result["status"], backend.commits), ("failed", 0))   # Never a different result.
            else:
                self.assertEqual((job.commit()["status"], backend.source), ("committed", [1, 2, 3]))
            self.assertIsNone(writer.owner)

    def test_the_gate_and_the_refusals_are_wired(self):
        root = ROOT / "scripts/addons_core/angelo_agent"
        read = lambda name: (root / name).read_text()   # noqa: E731
        controller, ui = read("controller.py"), read("ui.py")
        self.assertIn("self._held = document_scene.hold(self, document)", controller)
        self.assertIn('return bool(self._native_cleanup) or not getattr(self, "_held", False) and (', controller)
        self.assertIn("moved = document_scene.keep_shown(self, executor, shown)", controller)
        self.assertIn('result["window_scene"] = moved', controller)
        self.assertIn("document_scene.wait_on_lease(call, WRITER.owner)", controller)
        self.assertIn("            return document_scene.yield_writer(self)   # retires, whatever scene", controller)
        self.assertIn("            pending_delivery.retire_native_cleanup(self, budget)   # chat waits for", controller)
        self.assertIn('if getattr(job, "writer_suspended", False) and not document_scene.resume_writer(call, job):',
                      controller)
        self.assertEqual(ui.count("document_scene.busy_message(_controllers.values(), rpt_, rpt_("), 2)
        self.assertIn("controller.set_document(document_key())   # A request to a chat", ui)
        self.assertIn("document_scene.select(show_session(self.session_id), main_window(), document_key())", ui)
        self.assertNotIn("document_scene.select(active", ui)   # Never on a rebuild of the chat's dock (R2-M-2).
        self.assertIn("if call is None or not call.approval or document_scene.refused(self, controller):", ui)
        self.assertIn("document_scene.refused(self, controller) or controller.expire_tool(call)", ui)
        self.assertEqual(read("console.py").count("document_scene.refuse("), 2)   # A typed request and voice text.
        self.assertEqual(read("voice_ui.py").count("document_scene.refuse(controller)"), 1)
        self.assertEqual(read("agent_workspace_ui.py").count("refused(self, controller, ui.main_window())"), 4)


class UnattendedIsTheProcessesOwnTests(unittest.TestCase):
    """``unattended()`` reads only its own process's ``bpy.app.background``: no environment or test flag, and nothing
    in the add-on replaces it. The scene-switch suite stands for a person's window by replacing it inside its own run
    only (its process); the worker Blenders it starts are new processes that import the add-on afresh."""

    ADDON = ROOT / "scripts/addons_core/angelo_agent"
    SUITE = ROOT / "tests/python/angelo_agent/test_blender_parallel_scene_switch.py"

    def test_it_reads_only_background(self):
        import ast
        tree = ast.parse((self.ADDON / "document_scene.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "unattended")
        self.assertLessEqual({node.id for node in ast.walk(function) if isinstance(node, ast.Name)},
                             {"bpy", "bool", "ImportError", "AttributeError"})
        self.assertEqual({node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)},
                         {"app", "background"})
        source = (self.ADDON / "document_scene.py").read_text(encoding="utf-8")
        self.assertNotIn("environ", source)
        self.assertNotIn("getenv", source)
        from unittest.mock import patch
        for background in (True, False):
            with patch.dict(sys.modules, {"bpy": SimpleNamespace(app=SimpleNamespace(background=background))}):
                self.assertIs(document_scene.unattended(), background)

    def test_nothing_in_the_addon_replaces_it(self):
        import ast
        for path in sorted(self.ADDON.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
                    node, (ast.AugAssign, ast.AnnAssign)) else []
                for target in targets:
                    with self.subTest(module=path.name):
                        self.assertFalse(isinstance(target, ast.Attribute) and target.attr == "unattended")

    def test_the_suite_replaces_it_only_inside_its_own_run(self):
        import ast
        tree = ast.parse(self.SUITE.read_text(encoding="utf-8"))
        owners = [function.name for function in ast.walk(tree) if isinstance(function, ast.FunctionDef)
                  for node in ast.walk(function) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Attribute) and target.attr == "unattended" for target in node.targets)]
        self.assertEqual(owners, ["run_checks"])
        self.assertFalse(any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Attribute)
                         and target.attr == "unattended" for target in node.targets) for node in tree.body),
                         "never at import")


if __name__ == "__main__":
    unittest.main()
