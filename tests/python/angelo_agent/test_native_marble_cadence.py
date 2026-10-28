# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The real animation scheduler bounds redraw demand without coarsening its clock or event polling."""

import contextlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import angelo_agent
from angelo_agent import live_progress, marble_wait


class DecorativeCadence(unittest.TestCase):
    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.dict(sys.modules, {"bpy": SimpleNamespace()}))
        # Load the actual host privately so this test's bare bpy cannot remain in a shared host import.
        spec = importlib.util.spec_from_file_location("angelo_agent._cadence_host_test",
            Path(marble_wait.__file__).with_name("marble_host.py"))
        marble_host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(marble_host)
        self.host = marble_host
        self.now, self.moving, self.reason = 0.0, True, None
        self.wait = SimpleNamespace(reduced=False, next_frame=lambda now: 0.0 if self.moving else None)
        self.requests = []
        self.frames = SimpleNamespace(frame_cost=0.0002, release=mock.Mock())
        stubs = {"chat_view": SimpleNamespace(_enabled=True), "marble_frames": self.frames,
                 "ui": SimpleNamespace(_controller=SimpleNamespace(state={"id": "turn"}))}
        self.patches.enter_context(mock.patch.dict(sys.modules, {
            "angelo_agent." + name: value for name, value in stubs.items()}))
        for name, value in stubs.items():
            self.patches.enter_context(mock.patch.object(angelo_agent, name, value, create=True))
        tracker = SimpleNamespace(waits={}, end=lambda ids: None, update=lambda *args: self.wait)
        self.patches.enter_context(mock.patch.multiple(marble_host, tracker=tracker, ready=True, enabled=True,
            last_error=None, pauses=0, last_pause=None, marble_frames_asked=0, _last_frame=-1e9,
            time=SimpleNamespace(monotonic=lambda: self.now)))
        self.patches.enter_context(mock.patch.object(marble_host, "paused", side_effect=lambda now: self.reason))
        self.patches.enter_context(mock.patch.object(live_progress, "started", return_value=0.0))

        def redraw(frame=True):
            self.requests.append((self.now, frame))
            marble_host._last_frame = self.now
        self.patches.enter_context(mock.patch.object(marble_host, "_redraw_chats", side_effect=redraw))

    def step(self):
        delay = self.host._step()
        self.assertIsNone(self.host.last_error)
        return delay

    def run_for(self, seconds):
        while self.now < seconds:
            delay = self.step()
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, marble_wait.POLL)
            self.now += delay + 1e-9

    def frame_times(self):
        return [at for at, frame in self.requests if frame]

    def test_continuous_motion_requests_at_most_twelve_redraws_a_second(self):
        self.run_for(4)
        times = self.frame_times()
        self.assertGreater(len(times), 40, "Keep the decoration visibly animated")
        self.assertLessEqual(len(times), 49)
        self.assertTrue(all(b - a >= 1 / 12 - 1e-8 for a, b in zip(times, times[1:])), times)
        self.assertAlmostEqual(marble_wait.FRAME, 1 / 24, msg="Keep the existing scheduling granularity")

    def test_an_early_timer_call_cannot_bypass_the_redraw_interval(self):
        for step in range(481):
            self.now = step / 240
            self.step()
        times = self.frame_times()
        self.assertLessEqual(len(times), 25)
        self.assertTrue(all(b - a >= 1 / 12 - 1e-8 for a, b in zip(times, times[1:])), times)

    def test_costly_frames_still_back_off_further(self):
        self.frames.frame_cost = .004
        self.run_for(4)
        times = self.frame_times()
        self.assertLessEqual(len(times), 21)
        self.assertTrue(all(b - a >= .2 - 1e-8 for a, b in zip(times, times[1:])), times)

    def test_idle_building_retains_the_eight_hz_status_pulse(self):
        self.moving = False
        self.run_for(2)
        times = self.frame_times()
        self.assertEqual(len(times), 16)
        self.assertTrue(all(abs(b - a - .125) < 1e-7 for a, b in zip(times, times[1:])), times)

    def test_reduce_motion_keeps_seconds_without_a_pulse(self):
        self.moving, self.wait.reduced = False, True
        self.run_for(3.5)
        times = self.frame_times()
        self.assertEqual(len(times), 3)
        for actual, expected in zip(times, (1, 2, 3)):
            self.assertGreaterEqual(actual, expected)
            self.assertLessEqual(actual, expected + 1 / 24 + 1e-7, "The next fine scheduler wake shows the second")

    def test_interaction_pauses_every_frame(self):
        for reason in ("modal", "navigating", "playback", "hidden"):
            with self.subTest(reason=reason):
                self.reason, self.now, self.host._last_frame = reason, 2.01, 0.0
                self.requests.clear()
                self.assertEqual(self.step(), marble_wait.POLL)
                self.assertEqual(self.requests, [])

    def test_unfocused_keeps_only_the_seconds(self):
        self.reason = "unfocused"
        self.run_for(3.5)
        self.assertEqual(len(self.frame_times()), 3)

    def test_ending_releases_the_picture_and_returns_no_next_timer(self):
        self.wait = None
        self.assertIsNone(self.step())
        self.frames.release.assert_called_once_with()
        self.assertEqual(self.requests, [(0.0, False)])


if __name__ == "__main__":
    unittest.main()
