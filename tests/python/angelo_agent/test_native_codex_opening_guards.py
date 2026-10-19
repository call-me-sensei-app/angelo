# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an eager opening must never bill or overwrite (codex_bases, codex_opening): the review's reproductions.

A warm-up that made no base is not paid for again at the next launches; a
remembered base another app-server holds is not warmed over at connect and
never replaced; a team sign-in being restored is waited for; a retirement
around the warm-up's turn/start sends no turn or ends it at once; every sent
warm-up leaves a receipt with what is known.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import codex_bases, codex_connect, codex_opening, codex_runtime  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.telemetry import TelemetryStore  # noqa: E402
from test_native_codex_openings import OpeningCase, methods  # noqa: E402
from test_native_codex_runtime import total, wait_for, warm_up_reply  # noqa: E402

UNCLEAN = '{"reply":"Ready.","operations":[]}'


def turn_starts(case):
    """The model requests (turn/start) every process of the case sent."""
    return sum(methods(process).count("turn/start") for process in case.processes)


def warm_up_receipts(case):
    return [r for r in TelemetryStore.shared(case.temp.name).receipts if r.get("kind") == "warm_up"]


class BackoffTests(OpeningCase):
    def answer(self, process, runtime, reply=UNCLEAN, status="completed"):
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
        process.on_event("thread/tokenUsage/updated", {"threadId": record["thread"], "tokenUsage": {
            "total": total(24000), "last": total(24000)}})
        if reply is not None:
            warm_up_reply(process, record["thread"], reply)
        process.on_event("turn/completed", {"threadId": record["thread"], "turn": {"id": "warm-turn",
                                                                                  "status": status}})
        self.assertTrue(wait_for(lambda: record["state"] == "failed"))
        self.assertTrue(wait_for(lambda: len(warm_up_receipts(self)) >= 1))
        return record

    def test_an_unclean_warm_up_is_not_paid_for_again_at_the_next_launches(self):
        """Review B: a variant whose warm-up never comes back clean was warmed again at every launch."""
        process, runtime = self.prepare()
        self.answer(process, runtime)
        for _launch in range(2):
            codex_runtime.close_all()
            process, runtime = self.prepare()
            self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "absent"))
            self.assertEqual(self.base(runtime)["reason"], "backoff")
        self.assertEqual(turn_starts(self), 1, "One billed warm-up over three launches")
        chat = self.chat()
        self.assertTrue(chat.connect().startswith("chat-"))
        codex_connect.after_turn(chat, "completed")
        time.sleep(.1)
        self.assertEqual(turn_starts(self), 1, "A chat's first turn does not warm it again either")

    def test_the_backoff_ends_after_a_day_and_another_variant_is_not_affected(self):
        process, runtime = self.prepare()
        self.answer(process, runtime)
        store = codex_bases.BaseStore(self.temp.name)
        variant = next(iter(runtime.bases))
        self.assertTrue(store.failed_recently(runtime.scope, variant))
        self.assertFalse(store.failed_recently(runtime.scope, "another-variant"))
        later = time.time() + codex_bases.FAILURE_BACKOFF_SECONDS + 1
        with patch.object(codex_bases.time, "time", lambda: later):
            self.assertFalse(store.failed_recently(runtime.scope, variant))
        store.put(runtime.scope, variant, "base-thread", None)
        self.assertFalse(store.failed_recently(runtime.scope, variant), "A clean base clears the failure")
        self.assertEqual(store.get(runtime.scope, variant)["thread"], "base-thread")

    def test_a_warm_up_that_calls_a_tool_is_a_verdict_and_not_paid_for_again(self):
        """Re-check B2: a refused tool call interrupts the warm-up (status interrupted); it was re-sent every launch."""
        for _launch in range(3):
            process, runtime = self.prepare()
            record = self.base(runtime)
            if record["state"] == "warming" and wait_for(lambda: record["thread"] in runtime.warming, 2):
                thread = record["thread"]
                process.on_event("thread/tokenUsage/updated", {"threadId": thread, "tokenUsage": {
                    "total": total(24000), "last": total(24000)}})
                process.on_request("call-1", "item/tool/call", {"threadId": thread, "turnId": "warm-turn",
                                                                "callId": "c", "tool": "angelo_scene_search",
                                                                "arguments": {}})
                process.on_event("turn/completed", {"threadId": thread, "turn": {"id": "warm-turn",
                                                                                "status": "interrupted"}})
                self.assertTrue(wait_for(lambda: record["state"] == "failed"))
                self.assertTrue(wait_for(lambda: len(warm_up_receipts(self)) == 1))
            else:
                self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "absent"))
                self.assertEqual(self.base(runtime)["reason"], "backoff")
            codex_runtime.close_all()
        self.assertEqual(turn_starts(self), 1, "One billed warm-up over three launches, then the day's backoff")
        until = codex_bases.BaseStore(self.temp.name)._failures()
        self.assertEqual(len(until), 1)
        self.assertGreater(next(iter(until.values())) - time.time(), codex_bases.FAILURE_BACKOFF_SECONDS - 60)

    def test_a_transient_failure_backs_off_briefly_and_never_twice_in_a_launch(self):
        """Re-check B3: one stream error or 5xx kept every first chat from forking for a day."""
        process, runtime = self.prepare()
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
        process.on_event("turn/completed", {"threadId": record["thread"], "turn": {
            "id": "warm-turn", "status": "failed", "error": {"message": "stream disconnected"}}})
        self.assertTrue(wait_for(lambda: record["state"] == "failed"))
        self.assertTrue(wait_for(lambda: len(warm_up_receipts(self)) == 1))
        chat = self.chat()
        self.assertTrue(chat.connect().startswith("chat-"))
        codex_connect.after_turn(chat, "completed")
        time.sleep(.1)
        self.assertEqual(turn_starts(self), 1, "No second billed warm-up in the same launch")
        until = next(iter(codex_bases.BaseStore(self.temp.name)._failures().values()))
        self.assertLessEqual(until - time.time(), codex_bases.TRANSIENT_BACKOFF_SECONDS)
        codex_runtime.close_all()
        process, runtime = self.prepare()  # A launch within the backoff: nothing billed.
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "absent"))
        self.assertEqual(turn_starts(self), 1)
        codex_runtime.close_all()
        later = time.time() + codex_bases.TRANSIENT_BACKOFF_SECONDS + 1
        with patch.object(codex_bases.time, "time", lambda: later):
            process, runtime = self.prepare()  # After it: the variant is warmed again, once.
            self.assertTrue(wait_for(lambda: "turn/start" in methods(process)))
        self.assertEqual(turn_starts(self), 2)

    def test_an_earlier_builds_failure_entry_still_reads_as_a_day(self):
        store = codex_bases.BaseStore(self.temp.name)
        store.path.write_text('{"version": 1, "bases": {}, "failures": {"s:v": %f}}' % time.time())
        self.assertTrue(store.failed_recently("s", "v"))

    def test_a_quit_or_a_timeout_is_no_verdict_and_leaves_no_failure(self):
        process, runtime = self.prepare()
        record = self.base(runtime)
        self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
        codex_runtime.close_all()  # The app quits mid-warm-up: its warm-up ends at once.
        self.assertTrue(wait_for(lambda: len(warm_up_receipts(self)) == 1))
        self.assertEqual(warm_up_receipts(self)[0]["status"], "failed")
        self.assertNotIn("usage", warm_up_receipts(self)[0], "Unknown usage, never zero")
        self.assertFalse(codex_bases.BaseStore(self.temp.name).failed_recently(runtime.scope, next(iter(runtime.bases))))
        process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: "turn/start" in methods(process)), "The next launch warms it again")


class HeldBaseTests(OpeningCase):
    def test_a_second_instance_warms_nothing_at_connect_and_keeps_the_first_ones_base(self):
        """Review C: a second Angelo on the same profile warmed another base at launch and replaced the first's."""
        base = self.remembered()
        self.answers["thread/resume"] = RpcError("thread %s already has an active writer" % base)
        before = turn_starts(self)
        process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "absent"))
        self.assertEqual(self.base(runtime)["reason"], "held")
        self.assertEqual(turn_starts(self), before, "No model request with no chat message")
        chat = self.chat()
        self.assertTrue(chat.connect().startswith("chat-"), "The first chat starts its thread, as before")
        codex_connect.after_turn(chat, "completed")
        self.assertTrue(wait_for(lambda: "turn/start" in methods(process)))
        self.complete_warm_up(process, runtime)  # This launch's own base, after the chat's turn.
        self.assertTrue(self.chat().connect().startswith("fork-"), "It serves this launch's next chats")
        time.sleep(.05)
        store = codex_bases.BaseStore(self.temp.name)
        self.assertEqual(store.get(runtime.scope, next(iter(runtime.bases)))["thread"], base,
                         "The first instance's remembered base is never replaced")


class TeamRestoreTests(OpeningCase):
    def account(self, status):
        state = {"status": status}
        view = lambda: SimpleNamespace(status=state["status"])  # noqa: E731
        return state, SimpleNamespace(view=view)

    def test_the_opening_waits_for_a_team_sign_in_being_restored(self):
        """Review D: the opening computed the chat's tools before a stored team sign-in was restored."""
        from angelo_agent import team_library_access
        state, current = self.account("restoring")
        signed_in = lambda current=None: state["status"] == "signed_in"  # noqa: E731
        with patch.object(team_library_access, "account", lambda: current), \
                patch.object(team_library_access, "enabled", signed_in):
            thread = codex_opening.prepare(self.controller())
            time.sleep(.3)
            self.assertEqual(self.processes, [], "Nothing started while the sign-in is being restored")
            state["status"] = "signed_in"
            thread.join(30)
            process, runtime = self.processes[-1], next(iter(codex_runtime._POOL.values()))
            self.complete_warm_up(process, runtime)
            self.assertTrue(self.chat().connect().startswith("fork-"), "The first chat's tools are the opening's")
        self.assertEqual(turn_starts(self), 1)

    def test_a_restore_that_does_not_settle_prepares_nothing(self):
        from angelo_agent import team_library_access
        _state, current = self.account("restoring")
        with patch.object(team_library_access, "account", lambda: current), \
                patch.object(codex_opening, "TEAM_RESTORE_SECONDS", .2):
            thread = codex_opening.prepare(self.controller())
            thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.processes, [], "The chat's own connect and first turn decide, as before")


class RetireRaceTests(OpeningCase):
    """A retired process closes with its last lease, so these keep the runtime the warm-up ran on."""

    def retiring(self, method, answer):
        seen = {}

        def retire(params):
            seen["runtime"] = runtime = next(iter(codex_runtime._POOL.values()))
            with runtime.lock:
                runtime.retired = True  # As retire() sets it.
            return answer
        self.answers[method] = retire
        codex_opening.prepare(self.controller()).join(5)
        self.assertTrue(wait_for(lambda: "runtime" in seen))
        return self.processes[-1], seen["runtime"]

    def test_a_retirement_before_the_turn_is_written_sends_no_turn(self):
        process, runtime = self.retiring("thread/start", {"thread": {"id": "warm-base"}})
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "failed"))
        self.assertNotIn("turn/start", methods(process))
        time.sleep(.05)
        self.assertEqual(warm_up_receipts(self), [], "Nothing was sent, nothing billed")

    def test_a_retirement_while_the_turn_is_written_ends_it_at_once(self):
        """Review E: retire() landing after the check still let the warm-up run its whole turn."""
        process, runtime = self.retiring("turn/start", {"turn": {"id": "warm-turn"}})
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "failed"))
        base = self.base(runtime)["thread"]
        self.assertIn(("turn/interrupt", {"threadId": base, "turnId": "warm-turn"}), process.calls)
        self.assertTrue(wait_for(lambda: len(warm_up_receipts(self)) == 1))
        self.assertEqual(warm_up_receipts(self)[0]["status"], "interrupted")
        self.assertFalse(codex_bases.BaseStore(self.temp.name).failed_recently(runtime.scope, next(iter(runtime.bases))))


class ReceiptGapTests(OpeningCase):
    def test_a_lost_turn_start_reply_is_still_counted(self):
        self.answers["turn/start"] = RpcError("Agent process exited")
        _process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: len(warm_up_receipts(self)) == 1))
        self.assertEqual(warm_up_receipts(self)[0]["status"], "failed")
        self.assertNotIn("usage", warm_up_receipts(self)[0])

    def test_a_turn_codex_refused_is_not_a_billed_request(self):
        self.answers["turn/start"] = RpcError("Invalid request: unknown model")
        _process, runtime = self.prepare()
        self.assertTrue(wait_for(lambda: self.base(runtime)["state"] == "failed"))
        time.sleep(.05)
        self.assertEqual(warm_up_receipts(self), [])

    def test_a_warm_up_past_its_wait_is_interrupted_and_counted(self):
        with patch.object(codex_bases, "WARM_UP_SECONDS", .3):
            process, runtime = self.prepare()
            record = self.base(runtime)
            self.assertTrue(wait_for(lambda: record["thread"] in runtime.warming))
            process.on_event("thread/tokenUsage/updated", {"threadId": record["thread"], "tokenUsage": {
                "total": total(24000), "last": total(24000)}})
            self.assertTrue(wait_for(lambda: ("turn/interrupt", {"threadId": record["thread"], "turnId": "warm-turn"})
                                     in process.calls))
        self.assertTrue(wait_for(lambda: len(warm_up_receipts(self)) == 1))
        receipt = warm_up_receipts(self)[0]
        self.assertEqual((receipt["status"], receipt["usage"]["input_tokens"]), ("interrupted", 24000))


class ResumeClockTests(OpeningCase):
    def test_a_resume_is_timed_from_its_request_not_from_the_capability_check(self):
        self.remembered()
        probe = threading.Event()
        fork_answer = self.answers["thread/fork"]

        def slow_probe(params):
            if "ephemeral" not in params:
                probe.wait(2)
            return fork_answer(params)
        self.answers["thread/fork"] = slow_probe
        self.resumed.clear()
        self.addCleanup(self.resumed.set)
        thread = codex_opening.prepare(self.controller())
        self.assertTrue(wait_for(lambda: codex_runtime._POOL and next(iter(codex_runtime._POOL.values())).bases))
        runtime = next(iter(codex_runtime._POOL.values()))
        record = self.base(runtime)
        self.assertIsNone(record["since"], "Not timed while the capability check runs")
        self.assertEqual(codex_bases.wait_bound(record, time.monotonic() + 5), 0)
        probe.set()
        self.assertTrue(wait_for(lambda: record["since"] is not None))
        self.resumed.set()
        thread.join(30)


if __name__ == "__main__":
    unittest.main()
