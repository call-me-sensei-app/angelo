# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Continue after Angelo closed mid-request (``crash_resume``, ``crash_resume_ui``, ``RecoveryBarrier.arm_continue``).

What a cut-off request is, the journal-only summary (done / may have applied / didn't run), the offer and Continue,
and the hold that makes the model read the recovered scene before any edit. Nothing is ever replayed. The persistence
of the open mark goes through the real Controller and SessionStore (``ControllerFixture``).
"""
import suite_isolation  # noqa: F401

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import crash_resume as cr, crash_resume_ui as cui, request_rows  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402
from test_native_cache_diet import ControllerFixture  # noqa: E402

DEAD = {"pid": 2 ** 22 + 12345, "run": "earlier-run"}
MOVE = {"object": "scn:3:41", "location": [1, 0, 0]}


def activity(call, tool, status="completed", arguments=None, result=None):
    return {"role": "activity", "call_id": call, "tool": tool, "status": status, "arguments": arguments or {},
            "result": result or {}}


def record(phase, result):
    return {"document": "doc", "invocation_digest": "0" * 64, "phase": phase, "result": result, "approved": True,
            "automatic": False}


INTERRUPTED = {"error_code": "OWNERSHIP", "error": "ended", "interrupted": True}


class CutOffTests(unittest.TestCase):
    def rows(self, marker):
        return [{"role": "user", "text": "first"}, {"role": "assistant", "text": "ok"},
                {"role": "user", "text": "Make a castle", cr.OPEN: marker},
                {"role": "user", "text": "also a moat", "steer": True}]

    def test_the_newest_request_open_from_an_ended_process(self):
        self.assertEqual(cr.cut_off(self.rows(DEAD), alive=lambda pid: False), 2)

    def test_not_while_this_process_or_a_live_one_runs_it(self):
        self.assertIsNone(cr.cut_off(self.rows(dict(cr.TOKEN)), alive=lambda pid: False))
        self.assertIsNone(cr.cut_off(self.rows(DEAD), alive=lambda pid: True), "Another Angelo may be running it")

    def test_only_the_newest_request_and_only_with_a_mark(self):
        rows = self.rows(DEAD)
        rows.append({"role": "user", "text": "next"})
        self.assertIsNone(cr.cut_off(rows, alive=lambda pid: False))
        self.assertIsNone(cr.cut_off([{"role": "user", "text": "x"}], alive=lambda pid: False))
        self.assertIsNone(cr.cut_off([], alive=lambda pid: False))
        self.assertIsNone(cr.cut_off(self.rows("not a dict"), alive=lambda pid: False))

    def test_close_open_drops_every_mark_and_says_which_rows_changed(self):
        rows = self.rows(DEAD)
        rows[0][cr.OPEN] = DEAD
        self.assertEqual(cr.close_open(rows), [0, 2])   # The callers tell the journal (store.touch, called directly).
        self.assertFalse(any(cr.OPEN in row for row in rows))

    def test_an_ended_process_is_found_dead(self):
        self.assertFalse(cr._alive(None))
        self.assertFalse(cr._alive(-1))
        self.assertTrue(cr._alive(cr.TOKEN["pid"]))
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(cr._alive(child.pid), "A process that ended")

    def test_liveness_never_signals_a_process(self):
        source = Path(cr.__file__).read_text(encoding="utf-8")
        self.assertNotIn("os.kill(", source, "On Windows os.kill(pid, 0) ends the process")

    def test_liveness_is_asked_without_signalling_the_process(self):
        """Audit 2026-11-14 F1: on Windows os.kill(pid, 0) is TerminateProcess, so asking whether the process that
        left a turn open is alive could end a live Angelo, or whatever process reused its id. The check is the engine
        link's platform-aware one (OpenProcess + GetExitCodeProcess there), never a signal sent from here."""
        from unittest.mock import patch
        from angelo_agent import engine_link_protocol
        asked = []
        with patch.object(engine_link_protocol, "process_alive", lambda pid: asked.append(pid) or True), \
                patch.object(cr.os, "kill", side_effect=AssertionError("a signal was sent")):
            self.assertTrue(cr._alive(4242))
        self.assertEqual(asked, [4242])


class SummaryTests(unittest.TestCase):
    def state(self):
        rows = [{"role": "user", "text": "Build it", "checkpoint": "cp-1", cr.OPEN: DEAD},
                activity("c1", "angelo_inspect_scene", result={"objects": []}),
                activity("c2", "angelo_set_transform", arguments=MOVE, result={"ok": True}),
                activity("c3", "angelo_execute_python", "running", {"description": "Add ten trees", "code": "..."}),
                activity("c4", "angelo_set_transform", "approval", MOVE),
                activity("c5", "angelo_set_transform", "queued", MOVE),
                activity("c6", "angelo_apply_batch", "running", {"operations": []}),
                activity("c7", "angelo_set_transform", "completed", MOVE, {"ok": True}),
                activity("c8", "angelo_set_transform", "failed", MOVE, {"error": "x"}),
                activity("c9", "angelo_set_transform", "failed", MOVE),
                {"role": "user", "text": "steer", "steer": True},
                activity("c10", "angelo_set_transform", "running", MOVE)]
        results = {"c2": record("completed", {"ok": True}),
                   "c3": record("execution_started", dict(INTERRUPTED, partial_changes_possible=True)),
                   "c4": record("admitted", dict(INTERRUPTED, scene_changed=False)),
                   "c8": record("completed", {"error": "refused", "scene_changed": False}),
                   "c9": record("completed", {"error": "failed late", "partial_changes_possible": True})}
        return {"messages": rows, "results": results}

    def test_each_call_in_its_group_from_the_journal_with_row_fallback(self):
        summary = cr.summarize(self.state(), 0, lambda tool: tool == "angelo_inspect_scene")
        names = {group: [entry[1].get("description") or entry[0] for entry in summary[group]]
                 for group in ("done", "maybe", "not_run", "looked")}
        self.assertEqual(len(summary["done"]), 2, "c2 by its record, c7 by its row (record evicted)")
        self.assertEqual(len(summary["maybe"]), 4, "c3 started, c6 and c10 running without a record, c9 partial")
        self.assertIn("Add ten trees", names["maybe"])
        self.assertEqual(len(summary["not_run"]), 3, "c4 admitted only, c5 queued, c8 refused with nothing applied")
        self.assertEqual(names["looked"], ["angelo_inspect_scene"])
        self.assertEqual(summary["checkpoint"], "cp-1")
        self.assertEqual(cr.parts(summary), [2, 4, 3])

    def test_the_model_text_is_the_journal_and_bounded(self):
        summary = cr.summarize(self.state(), 0, lambda tool: False)
        text = cr.model_text(summary)
        self.assertIn("Nothing was run again", text)
        self.assertIn("Before any scene edit, read the current scene", text)
        self.assertIn('angelo_execute_python {"code":"...","description":"Add ten trees"}', text)
        many = {"messages": [{"role": "user", "text": "x"}] + [activity("m%d" % i, "angelo_set_transform", "running", MOVE)
                                                              for i in range(40)], "results": {}}
        text = cr.model_text(cr.summarize(many, 0, lambda tool: False))
        self.assertIn("and %d more" % (40 - cr.MAX_LISTED), text)
        self.assertLess(len(text), 6000)

    def test_a_failed_row_the_journal_no_longer_holds_may_have_applied(self):
        """The 2.1.0 break-it review, C-3: a script that failed can have changed the scene before it failed."""
        state = {"messages": [{"role": "user", "text": "Build it", cr.OPEN: DEAD},
                              activity("f1", "angelo_execute_python", "failed", {"description": "Add trees", "code": "x"},
                                       {"error": "boom"})], "results": {}}
        summary = cr.summarize(state, 0, lambda tool: False)
        self.assertEqual((len(summary["maybe"]), len(summary["not_run"])), (1, 0))

    def test_the_model_is_told_done_steps_may_be_missing_from_the_reopened_scene(self):
        """C-2: the scene is the file as last saved, so the journal's done steps may be absent from it."""
        text = cr.model_text(cr.summarize(self.state(), 0, lambda tool: False))
        self.assertIn("a done step may be missing from it", text)
        self.assertNotIn("Never repeat a done step.", text)


class RequestRowTests(unittest.TestCase):
    def controller(self, rows, **extra):
        touched = []
        controller = SimpleNamespace(state={"messages": rows}, store=SimpleNamespace(touch=touched.append), **extra)
        return controller, touched

    def test_every_request_row_is_marked_and_earlier_marks_close(self):
        rows = [{"role": "user", "text": "old", cr.OPEN: DEAD}]
        controller, touched = self.controller(rows)
        fields = {**request_rows.request_fields(controller), **cr.opened(controller)}
        self.assertEqual(fields, {cr.OPEN: cr.TOKEN}, "The person's request: no kind, only the mark")
        self.assertNotIn(cr.OPEN, rows[0])
        self.assertEqual(touched, [0])
        self.assertEqual(request_rows.kept_fields(fields)[cr.OPEN], cr.TOKEN, "The row pump keeps the mark")
        source = (Path(cr.__file__).with_name("controller.py")).read_text(encoding="utf-8")
        self.assertIn("**crash_resume.opened(self)}", source, "Every request row the controller emits is marked")

    def test_a_turn_end_closes_and_saves(self):
        rows = [{"role": "user", "text": "now", cr.OPEN: dict(cr.TOKEN)}]
        controller, touched = self.controller(rows)
        saved = []
        controller.save = lambda **options: saved.append(options)
        cr.closed(controller)
        self.assertEqual((touched, len(saved)), ([0], 1))
        self.assertNotIn(cr.OPEN, rows[0])
        cr.closed(controller)
        self.assertEqual(len(saved), 1, "Nothing open: no save")

    def test_a_continuation_is_angelos_own_request(self):
        controller, _ = self.controller([], _request_kind=request_rows.CONTINUE, _host_request=[1, 2, 0])
        fields = {**request_rows.request_fields(controller), **cr.opened(controller)}
        self.assertEqual((fields["request_kind"], fields["parts"]), (request_rows.CONTINUE, [1, 2, 0]))
        kept = request_rows.kept_fields(fields)
        self.assertEqual((kept["request_kind"], kept["parts"]), (request_rows.CONTINUE, [1, 2, 0]))
        self.assertFalse(request_rows.person_request({"role": "user", "request_kind": request_rows.CONTINUE}))
        self.assertEqual(request_rows.request_kind(request_rows.CONTINUE, lead=True), request_rows.CONTINUE)
        with self.assertRaises(ValueError):   # It never crosses to a worker's chat.
            request_rows.request_kind(request_rows.CONTINUE)
        with self.assertRaises(ValueError):
            request_rows.sender(SimpleNamespace(attachments=[], remote_executor=object()), [0, 1, 0], request_rows.CONTINUE)
        with self.assertRaises(ValueError):
            request_rows.sender(SimpleNamespace(attachments=[]), None, request_rows.CONTINUE)


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.barrier = RecoveryBarrier()

    def test_no_hold_unless_armed(self):
        self.barrier.clear()
        self.assertFalse(self.barrier.check("angelo_set_transform", MOVE))
        self.barrier.arm_continue("x")
        self.barrier.disarm()
        self.barrier.clear()
        self.assertFalse(self.barrier.check("angelo_set_transform", MOVE))

    def test_edits_wait_for_a_scene_read_then_for_the_next_step(self):
        self.barrier.arm_continue("angelo_execute_python {...}")
        self.barrier.clear()   # The continuation's request starts.
        self.barrier.step()    # Its first model step.
        self.assertFalse(self.barrier.check("angelo_inspect_scene", {}), "Reads always run")
        with self.assertRaises(RefusalError) as refused:
            self.barrier.check("angelo_set_transform", MOVE)
        self.assertEqual(refused.exception.code, "RECOVERY_REQUIRED")
        self.assertIn("Angelo closed while this request ran", str(refused.exception))
        self.assertIn("may_have_applied", self.barrier.description())
        self.barrier.read("angelo_read_file", {}, {"content": "x"})
        with self.assertRaises(RefusalError):
            self.barrier.check("angelo_set_transform", MOVE)   # A file read is not a look at the scene.
        self.barrier.read("angelo_inspect_scene", {}, {"objects": []})
        with self.assertRaises(RefusalError):
            self.barrier.check("angelo_set_transform", MOVE)   # Written in the step that read: never blind.
        self.barrier.step()
        self.assertFalse(self.barrier.check("angelo_set_transform", MOVE))

    def test_only_a_read_of_the_scene_counts(self):
        """C-1: an allow-list of scene reads, so a read of something else (a library, a goal, a web page, a job) or a
        read-only tool added later never lifts the hold."""
        from angelo_agent import request_recovery, tool_specs
        self.assertTrue(request_recovery.SCENE_READS <= tool_specs.READ_ONLY_TOOLS, "Every name is a real read")
        for name in ("angelo_inspect_scene", "angelo_scene_object", "angelo_read_data", "angelo_view"):
            self.assertTrue(request_recovery.continue_read(name), name)
        for name in ("angelo_library_search", "angelo_goal", "angelo_web_check", "angelo_studio_list",
                     "angelo_agent_capabilities", "angelo_read_file", "angelo_memory_read", "angelo_context_folder",
                     "angelo_a_read_only_tool_added_later"):
            self.assertFalse(request_recovery.continue_read(name), name)
        self.barrier.arm_continue("x")
        self.barrier.clear()
        self.barrier.read("angelo_library_search", {}, {"results": []})
        self.barrier.step()
        with self.assertRaises(RefusalError):
            self.barrier.check("angelo_set_transform", MOVE)

    def test_a_failed_read_does_not_count(self):
        self.barrier.arm_continue("x")
        self.barrier.clear()
        self.barrier.read("angelo_inspect_scene", {}, {"error": "failed"})
        self.barrier.step()
        with self.assertRaises(RefusalError):
            self.barrier.check("angelo_set_transform", MOVE)

    def test_the_arm_is_spent_by_one_request(self):
        self.barrier.arm_continue("x")
        self.barrier.clear()
        self.barrier.clear()   # The next request.
        self.assertFalse(self.barrier.check("angelo_set_transform", MOVE))


class Chat:
    """A stand-in chat for the offer and Continue."""

    def __init__(self, rows, results=None):
        self.state = {"messages": rows, "results": results or {}, "id": "chat-1"}
        self.rows, self.sent, self.busy = [], [], False
        self.recovery_barrier = RecoveryBarrier()
        self.store = SimpleNamespace(touch=lambda index: None)

    def emit(self, kind, row):
        self.rows.append(row)

    def send(self, text, **options):
        self.sent.append((text, options))


class OfferTests(unittest.TestCase):
    def setUp(self):
        self.alive = cr._alive
        cr._alive = lambda pid: False   # Every other process has ended.
        self.addCleanup(setattr, cr, "_alive", self.alive)
        rows = [{"role": "user", "text": "Build it", cr.OPEN: DEAD},
                activity("c3", "angelo_execute_python", "running", {"description": "Add ten trees", "code": "..."})]
        self.chat = Chat(rows, {"c3": record("execution_started", dict(INTERRUPTED, partial_changes_possible=True))})
        self.ui = SimpleNamespace(prepare_controller=lambda chat: None, main_window=lambda: None)
        import angelo_agent.worker_routing as routing
        self.deliver = routing.deliver
        routing.deliver = lambda chat, window, text, send, failed: send(SimpleNamespace(observation={}, recovery_evidence={}))
        self.addCleanup(setattr, routing, "deliver", self.deliver)

    def test_the_card_says_the_counts_and_continue_sends_angelos_request_with_the_hold(self):
        cui.offer(self.chat)
        card = self.chat.rows[-1]
        self.assertEqual((card["role"], card["notice"], card["i18n"][0]), ("system", True, cui.CARD))
        self.assertEqual(card["i18n"][1], {"done": 0, "maybe": 1, "not_run": 0})
        self.assertTrue(cui.stands(self.chat))
        self.assertTrue(cui.continue_request(self.chat, self.ui))
        text, options = self.chat.sent[0]
        self.assertIn("Add ten trees", text)
        self.assertEqual((options["host_kind"], options["host_request"]), (request_rows.CONTINUE, [0, 1, 0]))
        self.assertIsNotNone(self.chat.recovery_barrier._continue, "Armed for the request the send starts")
        self.assertFalse(cui.continue_request(self.chat, self.ui), "One Continue per offer")

    def test_nothing_may_have_applied_means_no_hold(self):
        self.chat.state["results"] = {"c3": record("admitted", dict(INTERRUPTED, scene_changed=False))}
        self.chat.state["messages"][1]["status"] = "approval"
        cui.offer(self.chat)
        cui.continue_request(self.chat, self.ui)
        self.assertIsNone(self.chat.recovery_barrier._continue)

    def test_a_done_scene_edit_alone_still_holds_edits_for_a_read(self):
        """C-2: a done step may be missing from the reopened file, so a listed scene edit of any kind arms the hold."""
        self.chat.state["messages"][1] = activity("c7", "angelo_set_transform", "completed", MOVE, {"ok": True})
        self.chat.state["results"] = {"c7": record("completed", {"ok": True})}
        cui.offer(self.chat)
        self.assertTrue(cui.continue_request(self.chat, self.ui))
        self.assertIsNotNone(self.chat.recovery_barrier._continue)

    def test_a_refused_send_disarms_and_says_why(self):
        def refuse(text, **options):
            raise RuntimeError("busy")
        self.chat.send = refuse
        cui.offer(self.chat)
        self.assertFalse(cui.continue_request(self.chat, self.ui))
        self.assertIsNone(self.chat.recovery_barrier._continue)
        self.assertEqual(self.chat.rows[-1]["i18n"][0], cui.FAILED)

    def test_no_offer_for_a_worker_chat_or_a_chat_on_another_document(self):
        self.chat.remote_executor = object()
        self.assertIsNone(cui.offer(self.chat))
        del self.chat.remote_executor
        cui.offer(self.chat)
        self.chat.awaited_file = {"path": "x"}
        self.assertFalse(cui.stands(self.chat))

    def test_the_cut_off_requests_live_steps_read_stopped_not_waiting(self):
        """A reloaded step still saved as waiting, queued or running read "Needs you" / "Running" beside the card
        though nothing waits (Director GUI proof 2026-11-15): the offer closes them as the host's own end (call_ended),
        once, and saves them; finished steps and earlier requests keep theirs."""
        from angelo_agent import activity_text, call_ended
        earlier = activity("c1", "angelo_inspect_scene", "approval")   # An older request's row: never touched.
        rows = [{"role": "user", "text": "Look"}, earlier, {"role": "user", "text": "Build it", cr.OPEN: DEAD},
                activity("c2", "angelo_scene_program", "completed"), activity("c3", "angelo_execute_python", "approval"),
                activity("c4", "angelo_set_transform", "queued"), activity("c5", "angelo_inspect_scene", "running")]
        touched = []
        self.chat = Chat(rows)
        self.chat.store = SimpleNamespace(touch=touched.append)
        cui.offer(self.chat)
        self.assertEqual([activity_text.step_status(row) for row in rows[3:]], ["done", "stopped", "stopped", "stopped"])
        self.assertEqual([row.get("ended") for row in rows[4:]], [call_ended.CLOSED] * 3)
        self.assertEqual((earlier["status"], earlier.get("ended")), ("approval", None))
        self.assertEqual(touched, [4, 5, 6])
        cui.offer(self.chat)
        self.assertEqual(touched, [4, 5, 6], "Closed once")

    def test_the_offer_withdraws_once_a_new_request_starts(self):
        cui.offer(self.chat)
        self.chat.state["messages"].append({"role": "user", "text": "Something else", cr.OPEN: dict(cr.TOKEN)})
        self.assertFalse(cui.stands(self.chat))


class PersistenceTests(ControllerFixture):
    def test_the_mark_is_saved_while_open_and_gone_after_the_turn(self):
        self.turn("How many objects are there?")
        rows = self.c.state["messages"]
        request = next(row for row in rows if row.get("role") == "user")
        self.assertNotIn(cr.OPEN, request, "A turn that ended closes its request")

    def test_a_cut_off_request_survives_a_reload(self):
        self.turn("How many objects are there?")
        rows = self.c.state["messages"]
        index = max(i for i, row in enumerate(rows) if row.get("role") == "user")
        rows[index][cr.OPEN] = dict(DEAD)
        self.c.store.touch(index)
        self.c.save()
        self.c.close()
        self.c = self.controller(session_id=self.session)   # A later launch loads the chat (tearDown closes it).
        self.assertEqual(cr.cut_off(self.c.state["messages"], alive=lambda pid: False), index)


if __name__ == "__main__":
    unittest.main()
