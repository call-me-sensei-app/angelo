# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One chat's failure in the tool step never stalls another's, and never leaves its own busy unseen (``tool_pump``,
``pump_guard``; the fifth break-it read, of 6068aab).

The tool step (``pump_controller_tools``, the window's and a worker's) ran every chat's ``pump_tools`` unguarded:
one that raised stopped the tick for every chat, and its cursor never moved past it, so it raised first again every
tick (SHOULD-1). Now its error is reported once per chat, place and kind (``pump_guard.failed``: bounded, through the
chat's own redaction, never raising), the next chat goes first next tick, and the failing chat's turn ends once with
an error row the person sees and a stop (SHOULD-2). The review digest binds any text, as the approval digest does.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import contextlib
import io
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from test_native_worker_pending_before_start import INSPECT, HeldProvider, PendingFixture  # noqa: E402

DAMAGE = " ".join(("one", "chat's", "damaged", "tool", "step"))   # In no source line a traceback shows.


class ToolStepEachChatTests(PendingFixture):
    hold = False

    def two_chats(self):
        self.provider.tools = (INSPECT,)
        self.assertTrue(self.open()["ok"])
        other = str(uuid.uuid4())
        self.assertTrue(self.ask("chat.open", session=other, store_directory=str(self.state),
                                 settings={"model": "", "reuse_codex_login": True})["ok"])
        return self.chats.hosted(self.session), self.chats.hosted(other), other

    def test_a_chat_whose_tool_step_raises_never_stalls_another_and_its_turn_ends_once_with_an_error(self):
        broken, working, other = self.two_chats()
        broken.pump_tools = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(DAMAGE))
        self.assertTrue(self.ask("chat.send", text="go", attachments=[])["ok"])
        self.assertTrue(self.ask("chat.send", session=other, text="go", attachments=[])["ok"])
        printed, deadline = io.StringIO(), time.monotonic() + CAP_S
        with contextlib.redirect_stderr(printed):
            while (broken.busy or working.busy) and time.monotonic() < deadline:
                self.chats.pump()
                time.sleep(0.002)
            for _ in range(5):   # More ticks: the failed chat is reported and ended once, not per tick.
                self.chats.pump()
        self.chats.outbox.flush()
        self.assertEqual(self.executor.calls, [INSPECT])   # The other chat's call ran.
        self.assertFalse(working.busy)
        self.assertFalse(broken.busy, "the failed chat stayed busy")
        errors = [event for event in self.endpoint.events("message")
                  if event["session"] == self.session and (event["data"] or {}).get("role") == "error"]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn(DAMAGE, errors[0]["data"]["text"])
        self.assertTrue(errors[0]["data"].get("i18n"))   # Shown in the person's language.
        self.assertEqual(printed.getvalue().count(DAMAGE), 1, printed.getvalue()[-800:])

    def test_the_next_chat_goes_first_after_a_failed_one(self):
        from angelo_agent.controller import ToolDispatchBudget
        from angelo_agent.tool_pump import pump_controller_tools
        broken = SimpleNamespace(pump_tools=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(DAMAGE)),
                                 busy=False, state={"id": "broken"})
        served = []
        working = SimpleNamespace(pump_tools=lambda *args, **kwargs: served.append(1), busy=False,
                                  state={"id": "working"})
        with contextlib.redirect_stderr(io.StringIO()):
            cursor = pump_controller_tools([broken, working], 0, object(), ("doc", 1), lambda *a: None,
                                           budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(served, [1])
        self.assertEqual(cursor, 1, "the failed chat would go first again")


class SpentTickTests(unittest.TestCase):
    """A chat whose step outlasts the tick (a loaded machine, a slow scene check) never starves the chats after it:
    the cursor moved only on an admission or a failure, so a step that spent the budget admitting nothing went first
    again every tick and no later chat ever ran (VM CI win-4 on 8855491: the other chat's call never dispatched in
    60 s). A chat that ran while the tick had time goes to the back; one reached with the tick spent goes first. Nor
    does a chat starve itself: its own setup admits nothing, so the tick's first chat is owed one admission."""

    TICKS = 3

    def setUp(self):
        self.served, self.order = [], []

    def chat(self, name, setup_spends=False):
        def step(*args, budget, **kwargs):   # Serves only with time left, as a real chat's admission checks do.
            self.order[-1].append(name)
            if setup_spends:
                self.spend(budget)   # Its scene and tool holds, before any admission, outlast the tick.
            if not budget.exhausted:
                budget.admissions += 1
                self.served.append(name)
        return SimpleNamespace(pump_tools=step, busy=False, state={"id": name})

    def ticks(self, chats):
        from angelo_agent.controller import ToolDispatchBudget
        from angelo_agent.tool_pump import pump_controller_tools
        cursor = 0
        for _ in range(self.TICKS):   # Each tick its own budget; ``spend`` ends its time, never the machine's load.
            self.order.append([])
            cursor = pump_controller_tools(chats, cursor, object(), ("doc", 1), lambda *a: None,
                                           budget=ToolDispatchBudget(seconds=60))
        return cursor

    @staticmethod
    def spend(budget):
        budget.started -= budget.seconds   # Its step took the whole tick, deterministically (no sleep).

    def idle_and_slow(self, name):
        def step(*args, budget, **kwargs):
            self.order[-1].append(name)
            self.spend(budget)
        return SimpleNamespace(pump_tools=step, busy=False, state={"id": name})

    def test_a_step_that_spends_the_tick_admitting_nothing_never_starves_the_next_chat(self):
        self.ticks([self.idle_and_slow("slow"), self.chat("working")])
        self.assertIn("working", self.served, "the slow chat went first every tick")
        self.assertEqual(self.order[1][0], "working")

    def test_a_chat_whose_own_setup_spends_the_tick_still_serves_its_work(self):
        self.ticks([self.chat("slow", setup_spends=True)])
        self.assertEqual(self.served, ["slow"] * self.TICKS, "its own setup refused its every admission")

    def test_slow_setups_on_both_chats_serve_each_in_turn(self):
        self.ticks([self.chat("a", setup_spends=True), self.chat("b", setup_spends=True)])
        self.assertEqual(self.served, ["a", "b", "a"])   # One past the deadline a tick, the first chat reached.

    def test_an_idle_chat_in_front_passes_the_late_admission_on_to_a_slow_chat_with_work(self):
        def idle(*args, budget, **kwargs):   # No work, no time: it neither admits nor reaches the deadline.
            self.order[-1].append("idle")
        self.ticks([SimpleNamespace(pump_tools=idle, busy=False, state={"id": "idle"}),
                    self.chat("slow", setup_spends=True)])
        self.assertEqual([tick[0] for tick in self.order], ["idle"] * self.TICKS)   # The idle chat stays first,
        self.assertEqual(self.served, ["slow"] * self.TICKS, "the idle chat took the tick's late admission")

    def test_a_chat_reached_after_a_lane_spent_the_tick_goes_first_next_tick(self):
        from angelo_agent import worker_dispatch
        lane = SimpleNamespace(busy=False, state={"id": "lane"})

        def lane_step(controller, budget):
            self.order[-1].append("lane")
            self.spend(budget)
            return False   # Admitted nothing on its lane.
        with patch.object(worker_dispatch, "owns", lambda controller: controller is lane), \
                patch.object(worker_dispatch, "pump", lane_step):
            self.ticks([lane, self.chat("working")])
        self.assertEqual(self.order[0], ["lane", "working"])   # Reached with the tick spent: it served nothing,
        self.assertEqual(self.order[1][0], "working", "the lane chat went first again")   # and goes first next tick.
        self.assertIn("working", self.served)


class ReportTests(unittest.TestCase):
    def setUp(self):
        from angelo_agent import pump_guard
        self.guard = pump_guard
        kept = dict(pump_guard._reported)
        self.addCleanup(lambda: (pump_guard._reported.clear(), pump_guard._reported.update(kept)))

    def test_each_chat_has_its_own_report_and_the_record_is_bounded(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(self.guard.failed("the tool step", RuntimeError("x"), str, "chat-a"))
            self.assertTrue(self.guard.failed("the tool step", RuntimeError("x"), str, "chat-b"))
            self.assertFalse(self.guard.failed("the tool step", RuntimeError("x"), str, "chat-a"))
            for index in range(self.guard.REPORTED_LIMIT + 10):
                self.guard.failed("bound", RuntimeError(str(index)), str, "chat-a")
        self.assertLessEqual(len(self.guard._reported), self.guard.REPORTED_LIMIT)

    def test_a_redaction_that_raises_never_escapes_and_never_shows_the_text(self):
        def raising(text):
            raise ValueError("no")
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            self.assertFalse(self.guard.each("redact", lambda: (_ for _ in ()).throw(RuntimeError("secret-xyz")),
                                             raising, "chat-a"))
        self.assertNotIn("secret-xyz", printed.getvalue())


class ReviewDigestTests(unittest.TestCase):
    def test_the_review_digest_binds_any_text(self):
        from angelo_agent import approval_review
        call = SimpleNamespace(arguments={"path": "scene\udc80.blend"}, program_arguments=None)
        self.assertEqual(len(approval_review._canonical_digest(call)), 64)
        import hashlib
        import json
        plain = SimpleNamespace(arguments={"path": "scène.blend"}, program_arguments=None)   # Valid text: as before.
        canonical = json.dumps({"arguments": plain.arguments, "program": None, "recovery_review": False,
                                "recovery_evidence": None}, sort_keys=True, ensure_ascii=False, allow_nan=False,
                               separators=(",", ":"))
        self.assertEqual(approval_review._canonical_digest(plain), hashlib.sha256(canonical.encode("utf-8")).hexdigest())


class StopAwareProvider(HeldProvider):
    """A provider whose turn ends when it is stopped, even with its call unanswered (as a real one is interrupted)."""
    stopped = None

    def run(self, native_id, text, observation=None):
        self.results, done = [], threading.Event()
        for name, arguments in self.tools:
            self.controller.request_tool(name, name, arguments, lambda result: (self.results.append(result),
                                                                                 done.set()))
            while not done.wait(0.01):
                if self.stopped.is_set():
                    return "interrupted"
        return "completed"

    def stop(self):
        self.stopped.set()


class FailedTurnEndsTests(PendingFixture):
    """The sixth break-it read (6b2398e), SHOULD-1: the once-key was set before the stop, so a stop that raised left
    the turn running (its row shown, the provider never stopped, the chat busy for good)."""
    provider, hold = StopAwareProvider, False

    def test_a_stop_that_raises_still_ends_the_turn_and_the_row_shows_once(self):
        StopAwareProvider.stopped = threading.Event()
        self.provider.tools = (INSPECT,)
        self.assertTrue(self.open()["ok"])
        chat = self.chats.hosted(self.session)
        chat.pump_tools = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(DAMAGE))
        chat.stop = lambda reason=None: (_ for _ in ()).throw(RuntimeError("cancel_tools hit the same defect"))
        self.assertTrue(self.ask("chat.send", text="go", attachments=[])["ok"])
        deadline = time.monotonic() + CAP_S   # A hang's cap: with the fix it ends at once.
        with contextlib.redirect_stderr(io.StringIO()):
            while chat.busy and time.monotonic() < deadline:
                self.chats.pump()
                time.sleep(0.002)
            for _ in range(5):
                self.chats.pump()
        self.assertFalse(chat.busy, "a failed stop left the turn running")
        self.chats.outbox.flush()
        errors = [event for event in self.endpoint.events("message")
                  if event["session"] == self.session and (event["data"] or {}).get("role") == "error"]
        self.assertEqual(len(errors), 1, errors)
        self.assertLessEqual(len(errors[0]["data"]["text"]), 300 + len("Angelo could not continue this request: "))


class ShadowEditGetsTheErrorTests(unittest.TestCase):
    """The sixth break-it read, SHOULD-2: a worker's shadow edit (``WorkerHost.edit``, ``ANGELO_WORKER_CHAT_HOST=0``)
    pumps its one chat for its own answer; the guard swallowed the step's error and the loop spun for EDIT_SECONDS,
    answering EXPIRED instead of the error."""

    def test_the_shadow_edit_gets_its_steps_error_at_once(self):
        from angelo_agent import worker_host, worker_values as values
        from test_native_parallel_workers_routing import FakeShadow
        ui = SimpleNamespace(document_key=lambda: ("serial", 34), main_window=lambda: "window", _controllers={},
                             Controller=object, state_directory=lambda: "/state")
        bpy = SimpleNamespace(data=SimpleNamespace(is_dirty=False), context=SimpleNamespace(temp_override=None))
        host = worker_host.WorkerHost(ui, bpy, endpoint=None, part="/w/part.blend")
        shadow = FakeShadow(precondition=(1, (5.0, 0.0, 0.0)))
        shadow.request_tool = lambda call_id, name, arguments, callback: setattr(shadow, "callback", callback)
        steps = []
        shadow.pump_tools = lambda *args, **kwargs: (steps.append(1), (_ for _ in ()).throw(RuntimeError(DAMAGE)))
        with patch.object(worker_host, "EDIT_SECONDS", 2.0), self.assertRaises(RuntimeError) as raised:
            host.edit(shadow, None, {"name": "angelo_set_transform", "arguments": {},
                                     "approved": values.encode((1, (5.0, 0.0, 0.0)))})
        self.assertEqual(str(raised.exception), DAMAGE)
        self.assertEqual(len(steps), 1, "the loop went on pumping after its step failed")
        self.assertEqual(shadow.closed, ["fence"])


if __name__ == "__main__":
    unittest.main()
