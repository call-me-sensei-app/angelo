# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's work waits while the person's operation is in progress (``edit_tool_hold``, ``input_policy``).

Evidence: learn-l4 (2026-11-13) reproduced on stock Blender 5.3.0 that a read-only ``bmesh.from_edit_mesh`` read of
an object inside a running modal edit drops the person's edit (0/20 kept); a lesson tick that held its reads while
such a tool ran kept it 20/20. The break-it review of the first fix (2026-11-13) found its Undo-flag rule missed sculpt
and paint strokes, counted Angelo's own bake as the person's, and left snapshots and job starts ungated.

Pinned here: the predicate is Blender's own "operation in progress" rule (a modal operator without MODAL_PASSIVE in
any window), never Angelo's own or one Angelo started, an ended render excluded, unreadable windows failing closed
(``test_blender_editing_tool_flags`` reads the real flags of common tools); the hold waits and never cancels; a call
stopped at its run keeps its click and resumes through its approval route; the model's fact is true; the live row
names the operation only while work is held; a voice snapshot event waits in order; a job-driven modal is nudged.
Every path is pinned to a gate in ``test_native_edit_tool_paths``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import edit_tool_hold, input_policy, live_progress, review_again  # noqa: E402

# Blender's real flags (source/blender/editors: transform_ops.cc, sculpt.cc:6014, paint_image_ops_paint.cc:915,
# the lasso gestures; the native suite reads them from the build).
TRANSFORM = {"REGISTER", "UNDO", "BLOCKING", "GRAB_CURSOR_XY"}
STROKE = {"BLOCKING"}          # A sculpt or image paint stroke: manages its own undo, no UNDO flag.
GESTURE = {"REGISTER"}         # A mask lasso.
NAVIGATION = {"BLOCKING", "GRAB_CURSOR_XY"}


def op(idname, options, label=""):
    return NS(bl_idname=idname, bl_options=set(options), bl_label=label or idname)


def manager(*windows):
    return NS(windows=[NS(modal_operators=list(ops), screen=None) for ops in windows])


class AddonModal:
    """An add-on's Python modal operator: its class names its own ``bl_idname``."""
    bl_idname, bl_label = "SCREENCAST_OT_keys", "Screencast Keys"

    def __init__(self, options):
        self.bl_options = set(options)


class Predicate(unittest.TestCase):
    def tearDown(self):
        input_policy._OWN.clear()

    def test_every_family_of_the_persons_operation_holds(self):
        for case in (op("TRANSFORM_OT_edge_crease", TRANSFORM, "Edge Crease"), op("SCULPT_OT_brush_stroke", STROKE),
                     op("PAINT_OT_image_paint", STROKE), op("PAINT_OT_mask_lasso_gesture", GESTURE),
                     op("VIEW3D_OT_rotate", NAVIGATION)):
            self.assertEqual(input_policy.editing_tool(manager([], [case])), case.bl_label, case.bl_idname)

    def test_nothing_or_only_passive_modals_never_hold(self):
        self.assertIsNone(input_policy.editing_tool(manager([], [])))
        self.assertIsNone(input_policy.editing_tool(manager([op("UI_OT_watch", {"MODAL_PASSIVE"})])))
        self.assertIsNone(input_policy.editing_tool(NS(windows=[])))   # Background mode: no windows.
        self.assertIsNone(input_policy.editing_tool(None))

    def test_angelos_own_modals_never_hold(self):
        own = [op("ANGELO_OT_chat_input", {"MODAL_PASSIVE"}), op("ANGELO_OT_accept_native_preview", set()),
               op("ANGELO_OT_render_region_draw", TRANSFORM)]
        self.assertIsNone(input_policy.editing_tool(manager(own)))

    def test_a_modal_angelo_started_is_angelos_until_it_ends(self):
        window = NS(modal_operators=[], screen=None)
        wm = NS(windows=[window])
        bake = op("PTCACHE_OT_bake_all", {"REGISTER", "UNDO"}, "Bake All Physics")
        with input_policy.angelo_started(wm):
            window.modal_operators.append(bake)
        self.assertIsNone(input_policy.editing_tool(wm))
        person = op("TRANSFORM_OT_translate", TRANSFORM, "Move")
        window.modal_operators.append(person)
        self.assertEqual(input_policy.editing_tool(wm), "Move")
        window.modal_operators[:] = []
        self.assertIsNone(input_policy.editing_tool(wm))
        self.assertEqual(input_policy._OWN, {})   # Forgotten once it ended: a later operator may reuse its address.

    def test_a_render_holds_only_while_its_job_runs(self):
        render = op("RENDER_OT_render", set(), "Render")
        with patch.object(input_policy, "_render_ended", return_value=False):
            self.assertEqual(input_policy.editing_tool(manager([render])), "Render")
        with patch.object(input_policy, "_render_ended", return_value=True):
            self.assertIsNone(input_policy.editing_tool(manager([render])))

    def test_an_unreadable_window_manager_or_operator_fails_closed(self):
        class Broken:
            @property
            def windows(self):
                raise RuntimeError("freed")

        class Unreadable:
            bl_idname = "MESH_OT_knife_tool"

            @property
            def bl_options(self):
                raise ReferenceError("freed")
        self.assertEqual(input_policy.editing_tool(Broken()), input_policy.UNREADABLE_TOOL)
        self.assertTrue(input_policy.editing_tool_running(manager([Unreadable()])))
        self.assertEqual(edit_tool_hold.UNREADABLE, input_policy.UNREADABLE_TOOL)


class Chat:
    """A stand-in controller: pending calls with lifetimes, a queue, and the execute_tool a resumed call runs
    through."""

    def __init__(self, *calls):
        self.pending = {call.id: call for call in calls}
        self.tools, self.events, self.ran = queue.Queue(), queue.Queue(), []

    def execute_tool(self, call, executor, document):
        self.ran.append(("direct", call.id))

    def _tool_available(self, call, document):
        return self.pending.get(call.id) is call


def call(name, expires=100.0, **fields):
    defaults = dict(approval=False, executing=False, authorized=False, precondition=None)
    return NS(id=name, name=name, expires=expires, **{**defaults, **fields})


class Hold(unittest.TestCase):
    def setUp(self):
        self.tool, self.now = None, [0.0]
        for target, name, value in ((edit_tool_hold, "_tool", lambda: self.tool),
                                    (edit_tool_hold.time, "monotonic", lambda: self.now[0])):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_it_waits_while_an_operation_runs_and_never_cancels(self):
        first = call("a")
        chat = Chat(first)
        self.tool = "Edge Crease"
        self.assertTrue(edit_tool_hold.hold(chat, False))
        self.now[0] = 30.0
        self.assertTrue(edit_tool_hold.hold(chat, False))
        self.assertEqual(first.expires, 130.0)   # Its lifetime does not run while it waits.
        self.assertIn("a", chat.pending)
        self.assertEqual((chat._tool_held, chat._tool_waiting, first.waited_for_tool), ("Edge Crease",) * 3)
        self.tool = None
        self.assertFalse(edit_tool_hold.hold(chat, False))
        self.assertIsNone(chat._tool_held)

    def test_the_scene_hold_shifts_lifetimes_alone(self):
        first = call("a")
        chat = Chat(first)
        self.tool = "Move"
        self.assertTrue(edit_tool_hold.hold(chat, True))
        self.now[0] = 10.0
        self.assertTrue(edit_tool_hold.hold(chat, True))
        self.assertEqual(first.expires, 100.0)   # document_scene.hold already moved it: never twice.

    def test_only_calls_held_before_their_first_read_waited(self):
        queued, card = call("q"), call("card", approval=True, precondition=("bound",))
        staged, running = call("s", authorized=True), call("r", executing=True)
        chat = Chat(queued, card, staged, running)
        self.tool = "Move"
        edit_tool_hold.hold(chat, False)
        self.assertEqual([getattr(c, "waited_for_tool", "") for c in (queued, card, staged, running)],
                         ["Move", "", "", ""])

    def test_the_live_row_names_the_operation_only_while_work_is_held(self):
        chat = Chat()
        self.tool = "Move"
        self.assertTrue(edit_tool_hold.hold(chat, False))
        self.assertIsNone(chat._tool_waiting)   # Thinking or streaming: its own phase, and a stall stays a stall.
        chat.tools.put(call("q"))
        edit_tool_hold.hold(chat, False)
        self.assertEqual(chat._tool_waiting, "Move")

    def test_a_call_stopped_at_its_run_keeps_its_click_and_resumes_by_its_route(self):
        clicked, read = call("c", approval=True), call("r")
        clicked.person_click = 3
        chat = Chat(clicked, read)
        budget, applied = NS(admissions=0, executions=0), []
        self.tool = "Knife"
        self.assertTrue(edit_tool_hold.wait(chat, clicked) and edit_tool_hold.wait(chat, read))
        self.assertEqual(vars(clicked)["person_click"], 3)   # Read by its resume, never consumed by the stop.
        self.assertTrue(edit_tool_hold.hold(chat, False))
        self.assertFalse(edit_tool_hold.resume(chat, clicked, "executor", "document", applied.append, budget))
        self.tool = None
        self.assertFalse(edit_tool_hold.hold(chat, False))
        self.assertTrue(edit_tool_hold.resume(chat, clicked, "executor", "document", applied.append, budget))
        self.assertTrue(edit_tool_hold.resume(chat, read, "executor", "document", applied.append, budget))
        self.assertEqual((applied, chat.ran), ([clicked], [("direct", "r")]))   # An approval through its Undo route.
        self.assertFalse(edit_tool_hold.resume(chat, clicked, "executor", "document", applied.append, budget))
        self.assertFalse(edit_tool_hold.wait(chat, call("d")))   # Nothing in progress: it runs at once.

    def test_a_worker_chat_reads_its_own_document_and_never_waits_here(self):
        worker = Chat(call("w"))
        worker.remote_executor = object()
        self.tool = "Move"
        self.assertFalse(edit_tool_hold.hold(worker, False))
        self.assertFalse(edit_tool_hold.wait(worker, call("x")))

    def test_the_models_fact_only_for_a_call_that_waited_and_ran(self):
        ran, never = call("a", waited_for_tool="Move", executing=True), call("b", waited_for_tool="Move")
        self.assertEqual(edit_tool_hold.noted(ran, {"success": True})["waited"],
                         edit_tool_hold.WAITED.format(tool="Move"))
        self.assertEqual(edit_tool_hold.noted(never, {"error_code": "EXPIRED"}), {"error_code": "EXPIRED"})
        self.assertEqual(edit_tool_hold.noted(call("c", executing=True), {"ok": 1}), {"ok": 1})

    def test_a_voice_snapshot_event_waits_with_every_event_after_it(self):
        chat = Chat()
        chat.checkpoint_factory, chat.voice = object(), object()
        for event in (("voice_request", {}), ("delta", {"text": "x"})):
            chat.events.put(event)
        self.tool = "Move"
        kind, data = edit_tool_hold.next_event(chat)
        self.assertTrue(edit_tool_hold.defers(chat, kind, data))
        self.assertRaises(queue.Empty, edit_tool_hold.next_event, chat)   # Nothing after it is read meanwhile.
        self.assertFalse(edit_tool_hold.defers(chat, "delta", {}))   # Only a snapshot waits for itself.
        self.tool = None
        self.assertEqual(edit_tool_hold.next_event(chat), ("voice_request", {}))
        self.assertEqual(edit_tool_hold.next_event(chat), ("delta", {"text": "x"}))

    def test_a_deferred_delivery_is_retried_once_the_operation_ends(self):
        retried = []
        with patch.object(edit_tool_hold, "_later", retried.append):
            self.tool = "Move"
            self.assertTrue(edit_tool_hold.deferred(lambda: retried.append("sent")))
            self.assertEqual(retried[0](), edit_tool_hold.RETRY_SECONDS)   # Still in progress: asks again.
            self.tool = None
            self.assertIsNone(retried[0]())
            self.assertEqual(retried[-1], "sent")
            self.assertFalse(edit_tool_hold.deferred(lambda: None))


class Nudge(unittest.TestCase):
    def test_a_window_with_an_operation_gets_one_slow_timer_removed_when_it_ends(self):
        added, removed = [], []
        window, other = NS(as_pointer=lambda: 1), NS(as_pointer=lambda: 2)
        wm = NS(windows=[window, other], event_timer_remove=removed.append,
                event_timer_add=lambda seconds, window: added.append((seconds, window)) or len(added))
        self.addCleanup(edit_tool_hold._nudges.clear)
        edit_tool_hold._nudge(wm, [window])
        edit_tool_hold._nudge(wm, [window])
        self.assertEqual(added, [(edit_tool_hold.NUDGE_SECONDS, window)])   # One per window, kept while it runs.
        edit_tool_hold._nudge(wm, [])
        self.assertEqual(removed, [1])
        edit_tool_hold._nudge(wm, [other])
        wm.windows = [window]   # Its window closed: its timer went with it, never removed again.
        edit_tool_hold._nudge(wm, [])
        self.assertEqual(removed, [1])

    def test_a_click_mark_is_kept_for_a_stop_before_anything_ran(self):
        stopped = call("m")
        self.assertEqual(review_again.keep_click(stopped, 7, "waiting"), "waiting")
        self.assertEqual(review_again.take_click(stopped), 7)
        self.assertIsNone(review_again.keep_click(stopped, None))


class LiveRow(unittest.TestCase):
    def test_the_person_sees_that_angelo_waits_for_their_operation_never_a_stall(self):
        facts = live_progress.Facts("Thinking", 0.0, True, 0.0, 0.0, False, False, None, True, None, tool="Edge Crease")
        self.assertEqual(live_progress.phase(facts), edit_tool_hold.shown("Edge Crease"))
        self.assertIn("Edge Crease", live_progress.phase(facts))
        self.assertIsNone(live_progress.note(facts, 10_000.0))
        self.assertIsNone(live_progress.Facts("Thinking", 0.0, True, 0.0, 0.0, False, False, None, True, None).tool)


class Review2(unittest.TestCase):
    """Break-it re-review 2026-11-13: F3, S1, S2, S3 and the one choke point."""

    def tearDown(self):
        input_policy._OWN.clear()
        edit_tool_hold._nudges.clear()

    def test_a_reused_address_is_never_angelos_own(self):   # F3
        window = NS(modal_operators=[], screen=None, as_pointer=lambda: 7)
        wm = NS(windows=[window])
        bake = op("OBJECT_OT_bake", {"REGISTER", "UNDO"}, "Bake")
        bake.as_pointer = lambda: 42
        with input_policy.angelo_started(wm):
            window.modal_operators.append(bake)
        self.assertIsNone(input_policy.editing_tool(wm))
        stroke = op("SCULPT_OT_brush_stroke", STROKE, "Brush Stroke")
        stroke.as_pointer = lambda: 42   # Freed and reused by the person's next tool, with no read in between.
        window.modal_operators[:] = [stroke]
        self.assertEqual(input_policy.editing_tool(wm), "Brush Stroke")

    def test_angelos_own_job_modal_is_nudged_until_its_job_ends(self):   # S4
        window = NS(modal_operators=[], screen=None, as_pointer=lambda: 7)
        added = []
        wm = NS(windows=[window], event_timer_add=lambda seconds, window: added.append(window) or 1,
                event_timer_remove=lambda timer: None)
        with input_policy.angelo_started(wm):
            window.modal_operators.append(op("PTCACHE_OT_bake_all", {"REGISTER", "UNDO"}, "Bake All"))
        with patch.object(edit_tool_hold, "_window_manager", return_value=wm):
            self.assertIsNone(edit_tool_hold._scan())   # Never the person's operation: no hold.
        self.assertEqual(added, [window])   # Yet its window gets the nudge that ends it once its job has.

    def test_a_file_browser_never_holds_and_an_add_on_modal_does(self):   # S1, review 3: flags do not say "edits"
        browsing = op("WM_OT_open_mainfile", {"REGISTER"}, "Open")
        area = NS(type="FILE_BROWSER", spaces=NS(active=NS(active_operator=browsing)))
        window = NS(modal_operators=[browsing], screen=NS(areas=[area]))
        self.assertIsNone(input_policy.editing_tool(NS(windows=[window])))
        self.assertEqual(input_policy.editing_tool(manager([AddonModal({"REGISTER"})])), "Screencast Keys")
        self.assertIsNone(input_policy.editing_tool(manager([AddonModal({"REGISTER", "MODAL_PASSIVE"})])))

    def test_an_add_on_modal_holds_once_it_has_consumed_the_persons_input(self):   # Maintainer ruling, 1.1.0
        observer, editing, fresh = AddonModal({"REGISTER", "UNDO"}), AddonModal({"REGISTER"}), AddonModal({"REGISTER"})
        observer.has_consumed_input, observer.has_answered_input = False, True   # Answered by passing input on.
        editing.has_consumed_input, fresh.has_consumed_input, fresh.has_answered_input = True, False, False
        self.assertIsNone(input_policy.editing_tool(manager([observer])))   # It only passes input on: never holds.
        self.assertEqual(input_policy.editing_tool(manager([fresh])), "Screencast Keys")   # Not answered yet: holds.
        self.assertEqual(input_policy.editing_tool(manager([editing])), "Screencast Keys")   # Flags say nothing.
        self.assertEqual(input_policy.editing_tool(manager([AddonModal({"REGISTER"})])), "Screencast Keys")   # An older
        # build without the mark: fail closed.
        c_operator = op("TRANSFORM_OT_translate", TRANSFORM, "Move")
        c_operator.has_consumed_input = False   # Blender's own operators keep autosave's rule.
        self.assertEqual(input_policy.editing_tool(manager([c_operator])), "Move")
        self.assertEqual(input_policy.editing_tool(manager([op("OBJECT_OT_modal_c", {"REGISTER"})])),
                         "OBJECT_OT_modal_c")   # Blender's own C modals keep autosave's rule.

    def test_one_read_per_tick(self):   # S2
        reads = []
        with patch.object(edit_tool_hold, "_scan", side_effect=lambda: reads.append(1)):
            with edit_tool_hold.tick():
                for _ in range(5):
                    edit_tool_hold.running()
        self.assertEqual(len(reads), 1)

    def test_a_load_removes_the_nudges_and_forgets_angelos_operators(self):   # S3
        removed = []
        wm = NS(windows=[], event_timer_remove=removed.append)
        edit_tool_hold._nudges[1] = "timer"
        input_policy._OWN[2] = ("object.bake", 7)
        with patch.object(edit_tool_hold, "_window_manager", return_value=wm):
            edit_tool_hold.clear()
        self.assertEqual((removed, edit_tool_hold._nudges, input_policy._OWN), (["timer"], {}, {}))

    def test_the_choke_point_waits_then_runs_and_owns_what_it_starts(self):   # F2 and the class
        window = NS(modal_operators=[], screen=None, as_pointer=lambda: 7)
        wm = NS(windows=[window])
        ran = []

        def step():
            ran.append(1)
            window.modal_operators.append(op("OBJECT_OT_bake", {"REGISTER", "UNDO"}, "Bake"))
            return None
        guarded = edit_tool_hold.guarded(step)
        with patch.object(edit_tool_hold, "_window_manager", return_value=wm), \
                patch.object(edit_tool_hold, "_scan", return_value="Move"):
            self.assertEqual(guarded(), edit_tool_hold.RETRY_SECONDS)   # Held: nothing ran, it asks again.
        self.assertEqual(ran, [])
        with patch.object(edit_tool_hold, "_window_manager", return_value=wm):
            self.assertIsNone(guarded())
        self.assertEqual(ran, [1])
        self.assertIsNone(input_policy.editing_tool(wm))   # The bake it started is Angelo's own.


class Review3(unittest.TestCase):
    """Break-it review 3, 2026-11-13: S6 and S5."""

    def tearDown(self):
        edit_tool_hold._unreadable_since.update(at=None, reported=set())
        edit_tool_hold._nudges.clear()

    def test_an_unreadable_operator_holds_then_stops_holding_and_is_reported_once(self):   # S6
        class Unreadable:
            bl_idname = "ADDON_OT_broken"

            @property
            def bl_options(self):
                raise ReferenceError("freed")
        wm = manager([Unreadable()])
        now, reports = [0.0], []
        with patch.object(edit_tool_hold, "_window_manager", return_value=wm), \
                patch.object(edit_tool_hold.time, "monotonic", lambda: now[0]), \
                patch("angelo_agent.pump_guard.report_defect", lambda where, error, redact=str: reports.append(error)):
            self.assertEqual(edit_tool_hold._scan(), input_policy.UNREADABLE_TOOL)   # Fail closed at first.
            self.assertIsNone(edit_tool_hold.seen())   # An app's deadline is never paused for it.
            now[0] = edit_tool_hold.UNREADABLE_SECONDS + 1
            self.assertIsNone(edit_tool_hold._scan())   # Bounded: a broken add-on never holds forever.
        self.assertEqual(len(reports), 1)

    def test_a_texture_set_waiting_for_its_next_bake_keeps_its_budget(self):   # S5; review 4 M3: through poll
        from angelo_agent import job_manager
        moved = []
        waiting = NS(pause_budget=lambda seconds: moved.append(seconds) or True)
        manager_ = job_manager.JobManager.__new__(job_manager.JobManager)
        record = {"id": "j", "started": 100.0}
        now = [0.0]
        manager_.runners, manager_.active, manager_.has_work = {"j": waiting}, (lambda: record), (lambda: True)
        with patch.object(edit_tool_hold, "_tool", return_value="Render"), \
                patch.object(job_manager.time, "monotonic", lambda: now[0]):
            self.assertTrue(manager_.poll())   # The job tick (ticked) calls poll while held.
            now[0] = 60.0
            manager_.poll()
        self.assertEqual((moved, record["started"]), ([60.0], 160.0))   # The 60 s it waited is not spent.


if __name__ == "__main__":
    unittest.main()
