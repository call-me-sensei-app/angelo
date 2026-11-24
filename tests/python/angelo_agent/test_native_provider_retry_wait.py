# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A wait the runtime announces is counted down in the chat and never ends the turn as silence.

Angelo 2.1.0's Sol gate, 2026-11-24: ChatGPT answered a burst of requests with HTTP 429 and a Retry-After of up to
576 s. The agent runtime waited it out, but its first retry notice was hidden on WebSockets, so the chat showed
"Thinking" with nothing for nearly ten minutes (Ruling 34). The fork now announces every server-advised or long wait
(``error`` with ``willRetry`` and ``retryDelayMs``), and Claude Code's ``api_retry`` already names its
``retry_delay_ms``. Pinned here, for any provider and any route:

* during an announced wait the host's silence and stretch clocks rest, and both restart at its deadline, so a wait
  longer than ``turn_activity.IDLE_SECONDS`` cannot end the turn as "nothing came from the model" and a runtime that
  then goes silent is still given up on (``TurnActivity.advise``);
* the announced waits of one turn rest the clocks for at most ``turn_activity.ADVISED_SECONDS`` in all, so notices
  that never stop cannot keep a hung turn open;
* the live row counts the wait down ("Provider busy — retrying in N s", ``codex_retry.waiting``) and says
  "Provider busy, retrying…" once it has passed;
* a notice with no wait, or a wait that is not a sane number, changes nothing (an older runtime);
* the Gemini wire's re-send of a failed or silent request (Ruling 31) arrives the same way and is treated the same.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import codex_retry, turn_activity  # noqa: E402
from angelo_agent.chat_titles import live_label  # noqa: E402
from angelo_agent.claude_stream import RoundStream  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from claude_protocol_fixtures import ClaudeAdapterFixture, completed  # noqa: E402
from test_native_codex_turn_errors import RECONNECTING, THREAD, ProviderFixture, answered  # noqa: E402
from test_native_turn_activity import Clock, message_start  # noqa: E402

GEMINI_SILENT = {"message": "Reconnecting... 1/1", "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": None}},
                 "additionalDetails": "stream disconnected before completion: idle timeout waiting for Gemini (360 s without data)",
                 "misalignment": None}


def retry(seconds, error=RECONNECTING, turn="turn-1"):
    params = {"error": error, "willRetry": True, "threadId": THREAD, "turnId": turn}
    if seconds is not None:
        finite = isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds == seconds \
            and abs(seconds) != float("inf")
        params["retryDelayMs"] = round(seconds * 1000) if finite else seconds   # Odd values go out as they are.
    return ("error", params)


def pause(seconds):
    return ("pause", lambda: time.sleep(seconds))


class AdvisedWaitClockTests(unittest.TestCase):
    def test_a_wait_longer_than_the_silence_limit_never_ends_the_turn_and_the_clocks_restart_after_it(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        activity.advise(900)                    # A 429's Retry-After: 15 minutes.
        clock.now += 899
        self.assertIsNone(activity.overdue(), "an announced wait is not silence")
        self.assertEqual(activity.waiting(), (1, turn_activity.PROVIDER_WAIT))
        clock.now += 1 + turn_activity.IDLE_SECONDS - 1
        self.assertIsNone(activity.overdue(), "silence counts from the deadline")
        self.assertIsNone(activity.waiting())
        clock.now += 1
        self.assertEqual(activity.overdue(), turn_activity.SILENT, "a runtime silent after the wait is given up on")

    def test_the_stretch_rests_during_the_wait_and_a_new_one_starts_with_the_resent_request(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        clock.now += turn_activity.STRETCH_SECONDS - 100
        activity.touch()
        activity.advise(500)
        clock.now += 500
        activity.touch()
        self.assertIsNone(activity.overdue(), "the wait is not model work")
        for _ in range(17):          # The re-sent request streams for 1,700 s: its own stretch.
            clock.now += 100
            activity.touch()
        self.assertIsNone(activity.overdue())
        clock.now += 100
        activity.touch()
        self.assertEqual(activity.overdue(), turn_activity.LONG)

    def test_a_wait_longer_than_the_turns_allowance_ends_it_at_once_and_says_how_long(self):
        for seconds in (turn_activity.ADVISED_SECONDS + 1, 7200, 1e9):
            with self.subTest(seconds=seconds):
                activity = turn_activity.TurnActivity(Clock())
                activity.advise(seconds)
                self.assertIsNone(activity.waiting(), "never a countdown that reaches a false zero")
                self.assertEqual(activity.overdue(), turn_activity.ADVISED)
                text = turn_activity.problem("Agent", turn_activity.ADVISED, "Inspect the scene.", activity)
                self.assertIn("the provider asked to wait about %d minutes" % math.ceil(seconds / 60), text)
                self.assertNotIn("nothing came from the model", text)

    def test_waits_past_the_allowance_in_all_end_the_turn_too(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        activity.advise(turn_activity.ADVISED_SECONDS)   # The whole allowance, waited out.
        clock.now += turn_activity.ADVISED_SECONDS
        self.assertIsNone(activity.overdue())
        activity.touch()
        activity.advise(60)                               # Notices that never stop: the next ends the turn.
        self.assertEqual(activity.overdue(), turn_activity.ADVISED)
        text = turn_activity.problem("Agent", turn_activity.ADVISED, "", activity)
        self.assertIn("this turn's waits would pass 60 minutes in all", text, "never 'this wait is too long'")
        self.assertNotIn("about 1 minutes", text)

    def test_minutes_read_as_english(self):
        activity = turn_activity.TurnActivity(Clock())
        activity.too_long = 30
        self.assertIn("about 1 minute before", turn_activity.problem("Agent", turn_activity.ADVISED, "", activity))

    def test_a_later_shorter_announcement_does_not_cut_a_longer_wait(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        activity.advise(800)
        activity.advise(5)
        self.assertEqual(activity.waiting(), (800, turn_activity.PROVIDER_WAIT))

    def test_only_a_sane_number_of_seconds_is_a_wait_and_none_raises(self):
        for value in (None, "600", -1, 0, float("nan"), float("inf"), True, [600], 10 ** 400):
            with self.subTest(value=value):
                activity = turn_activity.TurnActivity(Clock())
                activity.advise(value)
                self.assertIsNone(activity.waiting())
                self.assertIsNone(activity.overdue())
        for value in (10 ** 400, float("inf"), -5, "9", True):
            self.assertIsNone(turn_activity.announced_seconds(value))
        self.assertEqual(turn_activity.announced_seconds(576000), 576.0)

    def test_a_network_wait_is_labelled_so_and_never_restarts_the_stretch(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        shown = []
        for _ in range(turn_activity.STRETCH_SECONDS // 60):   # Offline: the runtime retries every minute.
            activity.touch(answered=False)                       # Codex: the notice itself is an event.
            activity.advise(60, turn_activity.NETWORK_WAIT)
            activity.show_wait(shown.append, None)
            self.assertIsNone(activity.overdue())
            clock.now += 60
        self.assertEqual(set(shown), {codex_retry.NETWORK})
        self.assertEqual(live_label(codex_retry.NETWORK), "Can't reach the provider · check your internet connection")
        self.assertEqual(activity.overdue(), turn_activity.NETWORK, "offline as long as a stretch: not model work")
        text = turn_activity.problem("Agent", turn_activity.NETWORK, "", activity)
        self.assertIn("could not be reached for about 30 minutes", text)
        self.assertNotIn("model work", text)

    def test_an_answer_between_two_offline_spells_starts_the_count_again(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        for spell in range(2):
            for _ in range(5):
                activity.touch(answered=False)            # Codex: the network notice itself is an event.
                activity.advise(60, turn_activity.NETWORK_WAIT)
                clock.now += 60
            if spell == 0:
                activity.touch()                          # The model answered between the two spells.
        self.assertAlmostEqual(activity.offline_seconds(), 300)

    def test_offline_time_counts_from_the_first_network_wait(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        for _ in range(20):                       # 20 minutes of model work, then the network drops.
            clock.now += 60
            activity.touch()
        for _ in range(10):
            activity.touch(answered=False)        # The network notice: an event, not an answer.
            activity.advise(60, turn_activity.NETWORK_WAIT)
            clock.now += 60
        self.assertEqual(activity.overdue(), turn_activity.NETWORK)
        self.assertIn("for about 10 minutes", turn_activity.problem("Agent", turn_activity.NETWORK, "", activity))

    def test_stop_ends_a_wait_and_the_host_ends_an_unconfirmed_stop_itself(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        activity.advise(3000)
        activity.stop()
        self.assertIsNone(activity.waiting(), "after Stop no wait holds the turn open")
        shown = []
        self.assertIsNone(activity.show_wait(shown.append, None))
        self.assertEqual(shown, [], "never over the row's Stopping")
        clock.now += turn_activity.STOP_SECONDS - 1
        self.assertIsNone(activity.overdue())
        clock.now += 1
        self.assertEqual(activity.overdue(), turn_activity.STOPPED)
        activity.advise(600)
        self.assertIsNone(activity.waiting(), "a notice after Stop holds nothing open")

    def test_the_host_working_during_a_wait_does_not_end_it(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        activity.advise(100)
        clock.now += 50
        activity.rest()                          # busy(): the host works for the turn (wait() calls rest()).
        self.assertEqual(activity.waiting(), (50, turn_activity.PROVIDER_WAIT))
        clock.now += 50 + turn_activity.IDLE_SECONDS - 1
        self.assertIsNone(activity.overdue())
        clock.now += 1
        self.assertEqual(activity.overdue(), turn_activity.SILENT)

    def test_retrying_never_follows_an_event_that_came_after_the_wait(self):
        clock = Clock()
        activity = turn_activity.TurnActivity(clock)
        activity.advise(3)
        shown = activity.show_wait(lambda status: None, None)
        clock.now += 4
        activity.touch()                         # The answer began: its own status ("Thinking") stands.
        emitted = []
        self.assertIsNone(activity.show_wait(emitted.append, shown))
        self.assertEqual(emitted, [])
        quiet = turn_activity.TurnActivity(clock)
        quiet.advise(3)
        shown = quiet.show_wait(lambda status: None, None)
        clock.now += 4
        quiet.show_wait(emitted.append, shown)
        self.assertEqual(emitted, [codex_retry.RETRYING], "nothing came yet: the row says retrying")

    def test_the_wait_loop_counts_down_and_says_retrying_when_it_has_passed(self):
        clock, done = Clock(), threading.Event()
        activity = turn_activity.TurnActivity(clock)
        activity.advise(3)
        shown = []

        def show(status):
            shown.append(status)
            clock.now += 1
            if status == codex_retry.RETRYING:
                done.set()

        with patch.object(turn_activity, "POLL_SECONDS", 0.001):
            self.assertIsNone(turn_activity.wait(done, activity, waiting=show))
        self.assertEqual(shown[:4], [codex_retry.waiting(3), codex_retry.waiting(2), codex_retry.waiting(1),
                                     codex_retry.RETRYING])
        self.assertEqual(live_label(codex_retry.waiting(576)), "Provider busy — retrying in 576 s")


class LiveRowDuringAWaitTests(unittest.TestCase):
    def test_a_counted_wait_is_never_called_a_stall(self):
        from angelo_agent import live_progress
        from angelo_agent.live_progress import Facts
        silent = Facts(codex_retry.waiting(400), 0.0, True, 10.0, None, False, False, None, True, None)
        self.assertIsNone(live_progress.note(silent, 300.0), "the row already says how long the provider asked to wait")
        unknown = silent._replace(status=codex_retry.RETRYING)
        self.assertEqual(live_progress.note(unknown, 300.0)[0], live_progress.STALL, "no wait named: as before")


class CodexAnnouncedWaitTests(ProviderFixture):
    """The host's silence limit scaled down to 2 s; each announced wait outlasts it."""

    LIMIT = 2

    def setUp(self):
        super().setUp()
        fast = mock.patch.multiple(turn_activity, IDLE_SECONDS=self.LIMIT, POLL_SECONDS=.05)
        fast.start()
        self.addCleanup(fast.stop)

    def test_a_429_wait_longer_than_the_silence_limit_completes_and_is_counted_down(self):
        rpc = self.script(lambda turn: [retry(self.LIMIT + 1.5), pause(self.LIMIT + 1)] + answered(turn))
        outcome, _ = self.run_request()
        self.assertEqual(outcome, "completed", "the announced wait is not silence")
        self.assertEqual(len(rpc.turn_starts()), 1)
        waits = [status for status in self.statuses if status.startswith(codex_retry.WAITING)]
        self.assertTrue(waits and waits[0] == codex_retry.waiting(self.LIMIT + 1.5), self.statuses)
        self.assertEqual(self.c._request_metrics.counts.get("provider_retries_rate"), 1, "a throttle stays counted")

    def test_the_gemini_wires_resend_is_announced_the_same_way_and_is_not_a_limit(self):
        rpc = self.script(lambda turn: [retry(self.LIMIT + 1.5, GEMINI_SILENT), pause(self.LIMIT + 1)] + answered(turn))
        outcome, _ = self.run_request()
        self.assertEqual(outcome, "completed")
        self.assertEqual(len(rpc.turn_starts()), 1)
        self.assertIn(codex_retry.waiting(self.LIMIT + 1.5), self.statuses)
        self.assertIsNone(self.c._request_metrics.counts.get("provider_retries_rate"), "silence is not a throttle")

    def test_a_network_wait_is_labelled_as_the_network_not_the_provider(self):
        params = retry(self.LIMIT + 1.5, RECONNECTING)[1]
        params["retryWaitKind"] = "network"
        self.script(lambda turn: [("error", params), pause(self.LIMIT + 1)] + answered(turn))
        outcome, _ = self.run_request()
        self.assertEqual(outcome, "completed")
        self.assertIn(codex_retry.NETWORK, self.statuses)
        self.assertFalse([status for status in self.statuses if status.startswith(codex_retry.WAITING)])

    def test_stop_during_a_long_wait_ends_the_turn_even_if_the_runtime_never_confirms(self):
        stop_after = mock.patch.object(turn_activity, "STOP_SECONDS", 1)
        stop_after.start()
        self.addCleanup(stop_after.stop)
        rpc = self.script(lambda turn: [retry(600), pause(30)] + answered(turn))   # A wedged app-server: no reply.
        stopped_at = []

        def stop():
            stopped_at.append(len(self.statuses))
            self.c.stop()

        threading.Timer(0.5, stop).start()
        outcome, seconds = self.run_request()
        self.assertEqual(outcome, "interrupted")
        self.assertLess(seconds, 10, "Stop's own backstop, not the 600 s wait")
        self.assertTrue(any(method == "turn/interrupt" for method, _ in rpc.requests))
        self.assertIn(codex_retry.waiting(600), self.statuses[:stopped_at[0]], "counted down before Stop")
        after = self.statuses[stopped_at[0]:]
        self.assertFalse([status for status in after if status.startswith(codex_retry.WAITING)],
                         "the countdown never overwrites Stopping")

    def test_a_wait_too_long_to_honour_ends_the_turn_saying_so(self):
        self.script(lambda turn: [retry(turn_activity.ADVISED_SECONDS + 60), pause(3)] + answered(turn))
        outcome, _ = self.run_request()
        self.assertIsInstance(outcome, RpcError)
        self.assertIn("the provider asked to wait about 61 minutes", str(outcome))
        self.assertFalse([status for status in self.statuses if status.startswith(codex_retry.WAITING)],
                         "no countdown for a wait Angelo will not wait out")

    def test_a_notice_without_a_wait_changes_nothing(self):
        for value in (None, "4000", -5, float("nan"), True):
            with self.subTest(value=value):
                self.setUp()
                self.script(lambda turn: [retry(value), pause(self.LIMIT + 1.5)] + answered(turn))
                outcome, _ = self.run_request()
                self.assertIsInstance(outcome, RpcError, "only a real announced wait rests the clock")
                self.assertIn("nothing came from the model", str(outcome))


class ClaudeAnnouncedWaitTests(ClaudeAdapterFixture, unittest.TestCase):
    """Claude Code's ``api_retry`` names its wait (``retry_delay_ms``); the real transport reader and waiter. Silence
    limit 1 s, the named wait 3 s, the quiet gap 2.4 s: a loaded machine has 0.6 s either side."""

    def setUp(self):
        super().setUp()
        for name, value in (("IDLE_SECONDS", 1.0), ("STRETCH_SECONDS", 30), ("POLL_SECONDS", 0.01)):
            patcher = patch.object(turn_activity, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def play(self, steps):
        def on_user(process, _event):
            def run():
                for step in steps:
                    if isinstance(step, (int, float)):
                        time.sleep(step)
                    else:
                        process.stdout.emit(step)
            threading.Thread(target=run, daemon=True).start()
        self.process.on_user = on_user

    @staticmethod
    def api_retry(delay_ms):
        event = {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10, "error_status": 429,
                 "error": "rate_limit", "session_id": "fixture-native"}
        if delay_ms is not None:
            event["retry_delay_ms"] = delay_ms
        return event

    def statuses(self):
        return [data["text"] for kind, data in self.host.events if kind == "status"]

    def test_an_announced_wait_longer_than_the_silence_limit_completes_and_is_counted_down(self):
        self.play([message_start(), self.api_retry(3000), 2.4, message_start("msg_2"), completed("Done")])
        self.start(None)
        self.turn(None, "A request the provider throttled")
        self.assertIn(codex_retry.waiting(3), self.statuses())

    def test_a_retry_without_a_wait_still_is_not_the_model_answering(self):
        self.play([message_start(), self.api_retry(None), 2.4, message_start("msg_2"), completed("Done")])
        self.start(None)
        with self.assertRaisesRegex(RpcError, "nothing came from the model"):
            self.turn(None, "A request the provider throttled")
        self.assertNotIn(codex_retry.waiting(3), self.statuses())

    def test_the_stream_itself_still_says_retrying(self):
        self.assertIn(("status", {"text": codex_retry.RETRYING}), RoundStream().events(self.api_retry(3000), {}))


if __name__ == "__main__":
    unittest.main()
