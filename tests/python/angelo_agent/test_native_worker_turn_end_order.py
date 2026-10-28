# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's turn end reaches the lead after its part's save (``worker_chat_part.TurnEnds``). No Blender.

Evidence: Mac 2 (3c351c9, live_view@hosted ``live_drops_deleted_object``): the worker forwarded ``finished`` from the
provider's thread before its main thread saved the part, so the lead's tile showed a deleted object for a moment
after the turn ended. Pinned: from a ``finished`` on, every frame waits in order until the save step after it ran,
whose own ``part_saved`` goes first; a turn end that came during a save step waits for the next one; a main thread
that runs no save step within ``END_WAIT`` never keeps the lead waiting (the frames go, in order); a close releases
everything; the host's loop releases after its save step and after each chat applied its events (a turn end the
lead sees from a save step is over in the worker too).
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_chat_part  # noqa: E402
from angelo_agent.worker_chat_part import TurnEnds  # noqa: E402


class Outbox:
    def __init__(self):
        self.sent, self.closed, self.lock = [], False, threading.Lock()

    def put(self, event):
        with self.lock:
            self.sent.append(event["kind"])

    def flush(self, timeout=None):
        return True

    def close(self):
        self.closed = True


def frame(kind, session="s"):
    return {"session": session, "kind": kind, "data": {}}


class Order(unittest.TestCase):
    def setUp(self):
        self.outbox = Outbox()
        self.ends = TurnEnds(self.outbox)

    def test_the_turn_end_and_what_follows_go_after_the_part_saved(self):
        self.ends.put(frame("message"))
        self.ends.put(frame("finished"))   # The provider's thread.
        self.ends.put(frame("state"))
        cut = self.ends.cut()               # The main thread's save step.
        self.ends.put(frame("part_saved", None))
        self.ends.release(cut)
        self.ends.put(frame("pending"))
        self.assertEqual(self.outbox.sent, ["message", "part_saved", "finished", "state", "pending"])

    def test_a_turn_end_during_the_save_step_waits_for_the_next_one(self):
        cut = self.ends.cut()
        self.ends.put(frame("finished"))
        self.ends.put(frame("part_saved", None))
        self.ends.release(cut)
        self.assertEqual(self.outbox.sent, ["part_saved"])
        cut = self.ends.cut()
        self.ends.put(frame("part_saved", None))
        self.ends.release(cut)
        self.assertEqual(self.outbox.sent, ["part_saved", "part_saved", "finished"])

    def test_a_main_thread_with_no_save_step_never_keeps_the_end_waiting(self):
        with mock.patch.object(worker_chat_part, "END_WAIT", 0.05):
            ends = TurnEnds(self.outbox)
            ends.put(frame("finished"))
            ends.put(frame("state"))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and len(self.outbox.sent) < 2:
                time.sleep(0.005)
            self.assertEqual(self.outbox.sent, ["finished", "state"])
            cut = ends.cut()   # A save step that began before the timer released them covers nothing held since.
            ends.put(frame("finished"))
            ends.release((cut[0] - 1, 5))
            self.assertEqual(self.outbox.sent, ["finished", "state"])
            ends.release()
        self.assertEqual(self.outbox.sent, ["finished", "state", "finished"])

    def test_a_close_releases_everything_in_order(self):
        self.ends.put(frame("finished"))
        self.ends.put(frame("journal"))
        self.ends.close()
        self.assertEqual(self.outbox.sent, ["finished", "journal"])
        self.assertTrue(self.outbox.closed)

    def test_the_host_loop_releases_after_its_save_step(self):
        from angelo_agent.worker_chat_host import WorkerChatHost
        source = Path(WorkerChatHost.pump.__code__.co_filename).read_text(encoding="utf-8")
        pump = source[source.index("    def pump(self):"):source.index("    def _pump_tools")]
        self.assertLess(pump.index("self.outbox.cut()"), pump.index("self._save_part()"))
        applied = pump.index("chat.pump_events()", pump.index("self._save_part()"))   # The end is over here too.
        self.assertLess(pump.index("self._save_part()"), applied)
        self.assertLess(applied, pump.index("self.outbox.release(saved)"))


if __name__ == "__main__":
    unittest.main()
