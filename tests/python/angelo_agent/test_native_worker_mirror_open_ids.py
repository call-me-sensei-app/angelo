# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""C9 with a real worker chat host: ``chat.open`` answers an ``open_id`` and every frame of that open carries it, so
the lead's mirror follows a chat reopened in a fresh worker ``Controller`` (A′ fix loop FC1). No Blender.

Regression test for the break-it review of 086452d1bdb (``release-1.0.6.noindex/review-concurrency``, F9): after a
park's close and a resumed open the worker's requests count from zero again, and the mirror dropped every event of
the resumed turn, ``finished`` included, so it stayed busy for good.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import worker_chat_events, worker_mirror_events as mirror_events  # noqa: E402
from angelo_agent.worker_mirror import EventRouter, MirrorController  # noqa: E402
import test_native_worker_chat_host as host_tests  # noqa: E402


class OpenIdTests(host_tests.Fixture):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(worker_chat_events.OPENS, {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def frames(self):
        self.chats.outbox.flush()
        return [event for event in self.endpoint.events() if event["session"] == self.session]

    def turn(self, text):
        self.ask("chat.send", text=text, attachments=[])
        self.run_turn()

    def test_every_frame_of_an_open_carries_the_id_its_open_answered(self):
        first = self.open()["open_id"]
        self.turn("one")
        frames = self.frames()
        self.assertTrue(frames)
        self.assertEqual({event.get("open_id") for event in frames}, {first})
        self.assertEqual(self.open()["open_id"], first)   # Already open here: the same open goes on.
        self.ask("chat.close", reason="parked")
        seen = len(self.frames())
        second = self.open(resume=True)["open_id"]
        self.assertNotEqual(second, first)
        self.turn("two")
        self.assertEqual({event.get("open_id") for event in self.frames()[seen:]}, {second})

    def test_a_resumed_open_is_followed_by_the_lead_mirror(self):
        mirror = MirrorController(str(self.state), self.session, lambda *a, **k: {"ok": True})
        router = EventRouter()
        router.open(mirror)
        delivered = 0

        def deliver():
            nonlocal delivered
            frames = self.frames()
            for event in frames[delivered:]:
                router(event)
            delivered = len(frames)
            mirror.pump_events()
        mirror_events.put(mirror, mirror_events.REOPEN, {})   # As worker_chat_wiring.prepare does before each open.
        self.open()
        for text in ("one", "two", "three"):
            mirror.begin_turn()
            self.turn(text)
            deliver()
            self.assertFalse(mirror.busy)
        self.ask("chat.close", reason="parked")
        deliver()
        mirror_events.put(mirror, mirror_events.REOPEN, {})
        self.open(resume=True)   # The relaunched worker's fresh Controller.
        mirror.begin_turn()
        self.turn("four")
        deliver()
        self.assertFalse(mirror.busy, "the resumed turn's end must reach the mirror")


def load_tests(loader, standard_tests, pattern):
    """Only this file's cases (the imported host fixture's own tests run in their own file)."""
    return loader.loadTestsFromTestCase(OpenIdTests)


if __name__ == "__main__":
    unittest.main()
