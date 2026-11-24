# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The program stress suite's injected playbacks against the real controller, with the staging probe forced late.

Regression for the 2.1.0 Mac GUI round on 93d6a3d3336 (``every_playback_was_waited_for``: 79 waits for 80
injections). The suite stopped each injected playback with a fixed 0.25 s timer. When the UI tick that approved a
program had spent its dispatch budget (``ToolDispatchBudget``), the controller's probe
(``Controller._waiting_for_playback``) came on a later tick, after the timer, found no playback and correctly did not
wait. ``program_stress_playback`` stops a playback only after the probe has held its program and gives each injection a
receipt. Here the approval's tick is forced to spend its budget (the "force late workers" method) and many main-loop
passes run before the next pump: the late probe still finds the playback, the request receipt counts one
``playback_waits`` and the injection's own receipt agrees. ``test_a_playback_ended_before_the_probe_is_a_named_miss``
is the old suite's ordering reproduced (playback stopped before the late probe): no wait, and the miss names its call.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest

from agent_runtime_fixtures import AgentRuntimeFixture
from angelo_agent.controller import ToolDispatchBudget
from program_stress_playback import (STOPPED_AFTER_WAIT, STOPPED_AT_TASK_END, STOPPED_ELSEWHERE,
                                     PlaybackInjections)


class MainLoop:
    """Blender's playback state and timers, one pass at a time."""

    def __init__(self):
        self.playing, self.timers, self.stops, self.frame = False, [], 0, 1

    def start(self):
        self.playing = True

    def stop(self):
        self.playing = False
        self.stops += 1

    def later(self, function):
        self.timers.append(function)

    def run(self, passes=1):
        for _ in range(passes):
            self.frame += self.playing   # Playback steps the frame on its own timer.
            due, self.timers = self.timers, []
            for function in due:
                function()


class SpentAtApproval(ToolDispatchBudget):
    """A tick whose budget is gone once its approval ran: the probe waits for a later tick."""

    def __init__(self, test):
        super().__init__(seconds=60)
        self.test = test

    @property
    def exhausted(self):
        return self.test.approved or ToolDispatchBudget.exhausted.fget(self)


class LateProbeTests(AgentRuntimeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.loop, self.approved, self.holds = MainLoop(), False, []
        self.playback = PlaybackInjections(self.loop.start, self.loop.stop, lambda: self.loop.playing,
                                           lambda: self.loop.frame, self.loop.later)
        self.executor.playback_running = lambda: self.loop.playing
        self.c._begin_metrics()
        approve, probe = self.c.approve_tool, self.c._waiting_for_playback

        def approve_tool(call, executor, document):   # As test_blender_program_stress injects: inside the approval.
            result = approve(call, executor, document)
            if call.authorized and call.id not in self.playback.receipts:
                self.approved = True
                self.playback.inject(call.id, cycle=5, tool=call.name)
            return result

        def waiting_for_playback(call, executor):   # The product's probe, observed as the suite observes it.
            waiting = probe(call, executor)
            self.holds.append(waiting)
            self.playback.observed(call.id, waiting)
            return waiting
        self.c.approve_tool, self.c._waiting_for_playback = approve_tool, waiting_for_playback

    def counts(self):
        return self.c._request_metrics.snapshot()["counts"]

    def approve_late(self):
        """The approval's tick spends its budget: approved and playing, never probed in that tick."""
        answers, call = self.enqueue()
        self.c.pump_tools(self.executor, self.c.document, budget=SpentAtApproval(self),
                          apply_mutation=lambda c: self.c.execute_tool(c, self.executor, self.c.document))
        self.assertTrue(call.authorized)
        self.assertEqual(self.holds, [])   # No probe in the approval's tick.
        self.assertTrue(self.loop.playing)
        return answers, call

    def finish(self, answers):
        for _ in range(60):
            self.loop.run()
            self.pump()
            if answers:
                return answers[0]
        self.fail("No final result")

    def test_a_late_probe_still_finds_the_playback_and_is_counted(self):
        answers, call = self.approve_late()
        self.loop.run(50)   # Far past the old 0.25 s timer: nothing stops a playback no probe has seen.
        self.assertTrue(self.loop.playing)
        self.pump()
        self.assertEqual(self.holds, [True])
        self.assertEqual(self.c.status, "Waiting for animation playback to stop")
        self.assertEqual(self.executor.jobs, [])   # Held: nothing staged while playing.
        result = self.finish(answers)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.counts().get("playback_waits"), 1)
        receipt = self.playback.receipts[call.id]
        self.assertEqual((receipt["waited"], receipt["moved"], receipt["stopped"], receipt["cycle"]),
                         (True, True, STOPPED_AFTER_WAIT, 5))
        self.assertEqual((self.playback.misses(), self.loop.stops), ([], 1))
        self.assertEqual(self.playback.summary()["waited"], 1)

    def test_a_playback_ended_before_the_probe_is_a_named_miss(self):
        answers, call = self.approve_late()
        self.loop.stop()   # What the fixed 0.25 s timer did before a late probe.
        result = self.finish(answers)
        self.assertTrue(result["success"], result)   # The fencing is right: nothing played at its probe.
        self.assertNotIn("playback_waits", self.counts())
        self.assertEqual(self.holds[0], False)
        [miss] = self.playback.misses()
        self.assertEqual((miss["call"], miss["cycle"], miss["stopped"]), (call.id, 5, STOPPED_ELSEWHERE))

    def test_a_task_that_ends_unprobed_stops_its_playback_and_names_it(self):
        _, call = self.approve_late()
        self.playback.task_ended(task="transform")
        self.assertFalse(self.loop.playing)
        [miss] = self.playback.misses()
        self.assertEqual((miss["call"], miss["task"], miss["stopped"]), (call.id, "transform", STOPPED_AT_TASK_END))
        self.playback.task_ended(task="later task")   # A later task names only its own receipts.
        self.assertEqual(self.playback.receipts[call.id]["task"], "transform")


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.loop = MainLoop()
        self.playback = PlaybackInjections(self.loop.start, self.loop.stop, lambda: self.loop.playing,
                                           lambda: self.loop.frame, self.loop.later)

    def test_a_deferred_stop_never_stops_a_later_injection(self):
        self.playback.inject("a")
        self.playback.observed("a", True)
        self.playback.observed("a", True)   # Still held: one stop is scheduled, once.
        self.assertEqual(len(self.loop.timers), 1)
        self.loop.stop()
        self.playback.inject("b")
        self.loop.run()   # a's stop, late: b is playing and is not a's.
        self.assertTrue(self.loop.playing)
        self.assertEqual([m["call"] for m in self.playback.misses()], ["b"])

    def test_a_held_playback_runs_until_it_has_moved_the_frame(self):
        self.playback.inject("a")
        self.playback.observed("a", True)   # Held in the approval's own pass: the frame has not moved yet.
        self.loop.timers.pop()()   # The deferred stop runs before playback's first step.
        self.assertTrue(self.loop.playing)
        self.assertEqual(len(self.loop.timers), 1)   # Retried on the next pass.
        self.loop.run()
        self.assertFalse(self.loop.playing)
        self.assertEqual(self.playback.receipts["a"]["stopped"], STOPPED_AFTER_WAIT)

    def test_an_injection_is_refused_twice_or_over_a_running_playback(self):
        self.playback.inject("a")
        with self.assertRaises(RuntimeError):   # The play operator would toggle it off.
            self.playback.inject("b")
        self.loop.stop()
        with self.assertRaises(ValueError):
            self.playback.inject("a")

    def test_a_playback_that_did_not_start_says_so(self):
        playback = PlaybackInjections(lambda: None, self.loop.stop, lambda: False, lambda: 1, self.loop.later)
        self.assertFalse(playback.inject("a"))
        self.assertFalse(playback.receipts["a"]["started"])


if __name__ == "__main__":
    unittest.main()
