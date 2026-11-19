# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's-operation hold, continued (``test_native_edit_tool_hold`` kept under 500 lines): one read per guarded
call, the status line (break-it reviews 4-6), a fresh add-on modal, held work never an approval card alone."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import edit_tool_hold, input_policy  # noqa: E402
from test_native_edit_tool_hold import TRANSFORM, AddonModal, manager, op  # noqa: E402  Helpers only, no test case.


class Walks(unittest.TestCase):
    """Break-it review 3, S8: a guarded call walks the windows once (its read), plus a length check after."""

    def tearDown(self):
        input_policy._OWN.clear()

    def test_a_guarded_call_reads_the_windows_once(self):
        walks = []
        window = NS(modal_operators=[op("ANGELO_OT_chat_input", {"MODAL_PASSIVE"})], screen=None, as_pointer=lambda: 7)
        wm = NS(windows=[window])
        real = input_policy._interactions   # The one read of a window's modal operators (operations, angelo_started).

        def counted(*args, **kwargs):
            walks.append(1)
            return real(*args, **kwargs)
        ran = []
        with patch.object(edit_tool_hold, "_window_manager", return_value=wm), \
                patch.object(input_policy, "_interactions", side_effect=counted):
            edit_tool_hold.guarded(lambda: ran.append(1))()
        self.assertEqual((len(walks), ran), (1, [1]))   # The predicate was read, once, and the call ran.

    def test_one_unreadable_window_never_hides_a_readable_one(self):   # Review 4, S-C
        class Broken:
            @property
            def modal_operators(self):
                raise ReferenceError("freed")
        wm = NS(windows=[Broken(), NS(modal_operators=[op("TRANSFORM_OT_translate", TRANSFORM, "Move")], screen=None)])
        names = [name for _window, name, _own in input_policy.operations(wm)]
        self.assertEqual(names, [input_policy.UNREADABLE_TOOL, "Move"])
        with patch.object(edit_tool_hold, "_window_manager", return_value=wm), \
                patch("angelo_agent.pump_guard.report_defect", lambda *args, **kwargs: None):
            self.assertEqual(edit_tool_hold._scan(), "Move")   # The named one, never "the tool" (N2).


class Review4(unittest.TestCase):
    """Break-it review 4, 2026-11-13: M2 (the person is never refused), M4 (a hold is never silent)."""

    def tearDown(self):
        edit_tool_hold._waiting.update(since=None, work=False, shown=None)
        edit_tool_hold._seen["tool"] = None   # N2

    def test_the_last_line_refuses_only_angelos_own_work(self):
        with patch.object(edit_tool_hold, "_tool", return_value="Render"):
            edit_tool_hold.refuse_under("An import")   # The person's own click: never refused here.
            with edit_tool_hold.angelo_work(), self.assertRaises(RuntimeError) as refused:
                edit_tool_hold.refuse_under("An import")
        self.assertNotIn("Blender", str(refused.exception))

    def test_the_status_line_names_a_hold_with_waiting_work_after_a_moment(self):
        now = [0.0]
        with patch.object(edit_tool_hold.time, "monotonic", lambda: now[0]), \
                patch("angelo_agent.live_windows.windows", return_value=[]):
            edit_tool_hold._seen["tool"] = "Render"
            edit_tool_hold._status("Render", NS(windows=[]))
            self.assertIsNone(edit_tool_hold.status_tool())   # No waiting work: nothing to say.
            edit_tool_hold.note_waiting()
            self.assertIsNone(edit_tool_hold.status_tool())   # A quick orbit never flashes it.
            now[0] = 2.0
            self.assertEqual(edit_tool_hold.status_tool(), "Render")
            edit_tool_hold._status(None, NS(windows=[]))
            self.assertIsNone(edit_tool_hold.status_tool())

    def test_a_message_in_the_tray_is_held_work(self):
        due = NS(has_due=lambda: True)
        paused = NS(has_due=lambda: False, __len__=lambda self=None: 1)   # Paused by a refusal: not held work (F7).
        self.assertTrue(edit_tool_hold._has_work(NS(pending={}, tools=None, inbox=due)))
        self.assertFalse(edit_tool_hold._has_work(NS(pending={}, tools=None, inbox=paused)))


class Review5(unittest.TestCase):
    """Break-it review 5, 2026-11-13: F1 (only real work shows the line), F2 (it is redrawn), F4 (a fresh modal)."""

    def tearDown(self):
        edit_tool_hold._waiting.update(since=None, work=False, shown=None)
        edit_tool_hold._seen["tool"] = None   # N2

    def test_an_idle_guarded_tick_never_says_angelo_waits(self):   # F1
        with patch.object(edit_tool_hold, "_scan", return_value="Render"):
            self.assertEqual(edit_tool_hold.guarded(lambda: None)(), edit_tool_hold.RETRY_SECONDS)
            self.assertFalse(edit_tool_hold._waiting["work"])   # An always-registered tick with nothing to do.
            edit_tool_hold.guarded(lambda: None, waiting=lambda: False)()
            self.assertFalse(edit_tool_hold._waiting["work"])
            edit_tool_hold.guarded(waiting=lambda: True)(lambda: None)()   # Decorator form, real work waiting.
            self.assertTrue(edit_tool_hold._waiting["work"])

    def test_the_line_is_redrawn_when_it_shows_and_when_it_clears(self):   # F2
        now, redraws = [0.0], []
        with patch.object(edit_tool_hold.time, "monotonic", lambda: now[0]), \
                patch.object(edit_tool_hold, "_redraw_status_bars", lambda: redraws.append(1)):
            edit_tool_hold._seen["tool"] = "Render"
            edit_tool_hold._status("Render", None)
            edit_tool_hold.note_waiting()
            now[0] = 2.0
            edit_tool_hold._status("Render", None)
            edit_tool_hold._status(None, None)
        self.assertEqual(len(redraws), 2)   # Shown once, cleared once.

    def test_a_fresh_add_on_modal_holds_until_it_answers_the_person(self):   # F4
        fresh, observer, editor = AddonModal({"REGISTER"}), AddonModal({"REGISTER"}), AddonModal({"REGISTER"})
        fresh.has_consumed_input, fresh.has_answered_input = False, False
        observer.has_consumed_input, observer.has_answered_input = False, True
        editor.has_consumed_input, editor.has_answered_input = True, True
        self.assertEqual(input_policy.editing_tool(manager([fresh])), "Screencast Keys")
        self.assertIsNone(input_policy.editing_tool(manager([observer])))
        self.assertEqual(input_policy.editing_tool(manager([editor])), "Screencast Keys")

    def test_an_app_reads_that_the_person_s_tool_holds_its_turn(self):   # F6
        source = (ROOT / "scripts/addons_core/angelo_agent/external_agent.py").read_text()   # N3: the state it sends.
        self.assertIn('turn = "waiting_for_person"', source)
        from angelo_agent import external_mcp_agent
        self.assertIn("waiting_for_person", external_mcp_agent.SETTLED)


class Review6(unittest.TestCase):
    """Break-it review 6, 2026-11-13: S5 (an approval card is no work), N4 (a raising predicate). S3, a synthetic
    pointer move to a fresh modal, was removed in review 7 (``Review7``)."""

    def tearDown(self):
        edit_tool_hold._seen["tool"] = None
        edit_tool_hold._waiting.update(since=None, clear=None, work=False, shown=None)

    def test_a_card_waiting_only_for_the_persons_approve_is_not_held_work(self):
        card = NS(approval=True, tool_waits=False, automatic=False)
        queued = NS(approval=False)
        self.assertFalse(edit_tool_hold._has_work(NS(pending={"c": card}, tools=None, inbox=None)))
        self.assertTrue(edit_tool_hold._has_work(NS(pending={"c": card, "q": queued}, tools=None, inbox=None)))

    def test_a_raising_waiting_predicate_never_drops_the_timer(self):   # N4
        def broken():
            raise KeyError("gone")
        with patch.object(edit_tool_hold, "_scan", return_value="Move"):
            self.assertEqual(edit_tool_hold.guarded(lambda: None, waiting=broken)(), edit_tool_hold.RETRY_SECONDS)


class Review7(unittest.TestCase):
    """Break-it reviews 7 and 8, 2026-11-13: no synthetic input (M1, a Known gap instead), only work queued before the
    person's operation waits (S2, S3; an editor's acknowledgement is no request), a raising predicate is work (S4)."""

    def tearDown(self):
        edit_tool_hold._seen["tool"] = None
        edit_tool_hold._waiting.update(since=None, clear=None, work=False, shown=None)

    def test_a_fresh_add_on_modal_holds_and_is_named_with_nothing_sent_to_it(self):   # M1
        moves = []
        window = NS(modal_operators=[], screen=None, as_pointer=lambda: 7, event_add_mousemove=lambda: moves.append(1))
        fresh = AddonModal({"REGISTER"})
        fresh.has_consumed_input, fresh.has_answered_input = False, False
        window.modal_operators.append(fresh)
        now = [0.0]
        with patch.object(edit_tool_hold, "_window_manager", return_value=NS(windows=[window])), \
                patch.object(edit_tool_hold.time, "monotonic", lambda: now[0]), \
                patch.object(edit_tool_hold, "_redraw_status_bars", lambda: None):
            self.assertEqual(edit_tool_hold._scan(), "Screencast Keys")   # Holds until input reaches it or it ends.
            edit_tool_hold.note_waiting()
            now[0] = edit_tool_hold.SHOW_AFTER + 1.0
            self.assertEqual(edit_tool_hold._scan(), "Screencast Keys")
            self.assertEqual(edit_tool_hold.status_tool(), "Screencast Keys")   # The status bar names it.
        self.assertEqual(moves, [])   # Never an event of Angelo's: it could reach the person's own modal first.
        self.assertFalse(hasattr(edit_tool_hold, "_poke_fresh") or hasattr(input_policy, "LAST_FRESH"))

    def test_a_raising_waiting_predicate_counts_as_work(self):   # S4
        def broken():
            raise KeyError("gone")
        with patch.object(edit_tool_hold, "_scan", return_value="Move"):
            self.assertEqual(edit_tool_hold.guarded(lambda: None, waiting=broken)(), edit_tool_hold.RETRY_SECONDS)
        self.assertTrue(edit_tool_hold._waiting["work"])   # Unknown work is shown, never hidden.

    def test_only_work_queued_before_the_operation_began_waits_on_it(self):   # S3
        now = [1.0]
        with patch.object(edit_tool_hold.time, "monotonic", lambda: now[0]), \
                patch.object(edit_tool_hold, "_redraw_status_bars", lambda: None):
            self.assertFalse(edit_tool_hold.before_hold(0.5))   # No operation: nothing waits on one.
            edit_tool_hold._status(None, None)   # The last read that found no operation: t=1.
            now[0] = 2.0
            edit_tool_hold._status("Move", None)   # The first that found one: t=2.
            self.assertTrue(edit_tool_hold.before_hold(0.5))   # Queued before it.
            self.assertTrue(edit_tool_hold.before_hold(1.0))
            self.assertFalse(edit_tool_hold.before_hold(1.5))   # Between the reads: the operation's own change.
            self.assertFalse(edit_tool_hold.before_hold(2.5))   # A change the drag makes.
            self.assertFalse(edit_tool_hold.before_hold(None))
            now[0] = 3.0
            edit_tool_hold._status(None, None)
            self.assertFalse(edit_tool_hold.before_hold(0.5))

    def test_the_engine_link_waits_on_editors_and_an_earlier_export_never_the_persons_card(self):   # S2
        import threading
        from collections import deque
        from angelo_agent import engine_link_reads, engine_link_server
        server = NS(_lock=threading.Lock(), _inbox=deque())
        server.unread = lambda: engine_link_server.LinkServer.unread(server)
        state = {"server": server, "pending": {"client": "c"}, "session": {"asset": "a"}, "dirty_at": None}
        with patch.object(edit_tool_hold, "before_hold", lambda at: at is not None and at < 1.0):
            self.assertFalse(engine_link_reads.waiting(state))   # The person's Open / Not now card alone: no work.
            for notice in ({"type": "status", "code": "previewed"}, {"type": "connected"}, {"type": "disconnected"}):
                server._inbox.append(("c", notice))   # An acknowledgement or a connection notice: recorded, no work.
                self.assertFalse(engine_link_reads.waiting(state), notice)   # Review 8, finding 1.
            for request in ({"type": "open"}, {"type": "close"}, {"type": "status", "code": "resync"}):
                server._inbox.append(("c", request))
                self.assertTrue(engine_link_reads.waiting(state), request)   # An editor's request not yet read.
                server._inbox.pop()
            server._inbox.clear()
            reads = engine_link_reads.Inbox()   # Read from the server, not yet handled: the same rule.
            reads._server = server
            reads._queue.append(["c", {"type": "status", "code": "previewed"}, {}, None, False, 1])
            self.assertFalse(reads.unread(server))
            reads._queue.append(["c", {"type": "open"}, {}, None, False, 2])
            self.assertTrue(reads.unread(server))
            reads._queue[-1][4] = True   # Handled.
            self.assertFalse(reads.unread(server))
            state["dirty_at"] = 2.0
            self.assertFalse(engine_link_reads.waiting(state))   # The operation's own change.
            state["dirty_at"] = 0.5
            self.assertTrue(engine_link_reads.waiting(state))   # An export of changes made before it.
            state["session"] = None
            self.assertFalse(engine_link_reads.waiting(state))
            self.assertFalse(engine_link_reads.waiting(dict(state, server=None)))


class Review8(unittest.TestCase):
    """Break-it review 8, 2026-11-13, nit 3: the web preview keeps its oldest unsent change, as the engine link does,
    so an update queued before the person's operation still counts once the drag changes the scene."""

    def test_the_web_preview_keeps_its_oldest_unsent_change(self):
        import ast
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/web_preview.py").read_text(encoding="utf-8"))
        noted = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_dirty")
        namespace = {"_state": {"dirty_at": None, "queued_at": None}}
        exec(compile(ast.Module([noted], []), "web_preview.py", "exec"), namespace)  # noqa: S102 - our own source.
        namespace["_dirty"](1.0)
        namespace["_dirty"](2.0)   # The drag changes the scene: the quiet wait restarts, the earlier update stays.
        self.assertEqual(namespace["_state"], {"dirty_at": 2.0, "queued_at": 1.0})
        inside = set(ast.walk(noted))
        names = ("_state['dirty_at']", "_state['queued_at']")   # As ast.unparse writes them (single quotes).
        writes = [node for node in ast.walk(tree) if isinstance(node, ast.Assign) and node not in inside
                  and any(ast.unparse(target) in names for target in node.targets)]
        self.assertTrue(writes and all(ast.unparse(node.value) == "None" for node in writes))   # Elsewhere: cleared.


if __name__ == "__main__":
    unittest.main()
