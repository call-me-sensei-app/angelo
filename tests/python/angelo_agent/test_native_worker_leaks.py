# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing held is left behind (re-review R3, R4). No Blender.

* A part-count lease granted is released once, whichever way its ask is dropped: read by the pump, replaced by a
  newer version's ask (the worker saved between the count's stat and the grant), or dropped by ``reset`` before or
  after its grant. A leaked lease would stall the worker's next save, and so a merge's hold, for its whole bound.
* A rewrite whose own stat after the replace failed never locks a chat's saves out: the next save writes on when the
  file holds exactly what this writer wrote, and another writer's content is still refused.

Grants and releases land on threads of their own: a check waits for the state it needs (``BOUND``, reached only on a
failure), and every thread a test started has ended before its stand-ins are taken away.
"""

import os
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import (session_guard, session_journal, sessions, worker_chat_lifecycle as lifecycle,  # noqa: E402
                          worker_part_counts as counts)


BOUND = 10.0   # Seconds a thread's effect is waited for (a slow runner's hop): only a failing check waits it out.


def wait_for(predicate, seconds=BOUND):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.002)
    return predicate()


class PartCountLeases(unittest.TestCase):
    def setUp(self):
        counts.reset()
        self.addCleanup(counts.reset)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.part = Path(directory.name) / "part.blend"
        self.part.write_bytes(b"v1")
        self.worker, self.released, self.gate, self.threads = object(), [], threading.Event(), []
        for patcher in (mock.patch.dict(sys.modules, {"bpy": types.ModuleType("bpy")}),
                        mock.patch.object(counts, "_spawn", self.spawn),
                        mock.patch.object(lifecycle, "enabled", lambda environ=None: True),
                        mock.patch.object(lifecycle, "_serving", lambda path: self.worker),
                        mock.patch.object(lifecycle, "_unlease",
                                          lambda lease: self.released.append(lease[:2])),
                        mock.patch.object(counts, "_read", lambda key: 1)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.settle)   # Runs first: before the stand-ins above are taken away.

    def spawn(self, function):
        thread = threading.Thread(target=function, daemon=True)
        self.threads.append(thread)
        thread.start()

    def joined(self):
        """Every lease and release thread started so far has ended (one may start another)."""
        deadline = time.monotonic() + BOUND
        while time.monotonic() < deadline:
            alive = [thread for thread in list(self.threads) if thread.is_alive()]
            if not alive:
                return True
            alive[0].join(max(0.0, deadline - time.monotonic()))
        return False

    def settle(self):
        """Nothing is left waiting, granted or running to call a stand-in after the test, or the real channel."""
        self.gate.set()
        counts.reset()
        self.assertTrue(self.joined(), "a part-count thread outlived its test")

    def state(self):
        ask = counts._asks.get(str(self.part))
        return None if ask is None else ask.state

    def granted(self, token, saved=False):
        def grant(path, wait):
            self.gate.wait(5)
            if saved:   # The worker saved between the count's stat and its grant.
                os.utime(self.part, ns=(time.time_ns(), time.time_ns() + 10**9))
                self.part.write_bytes(b"v2-longer")
            return (self.worker, token, lifecycle.LEASE_SECONDS)   # (worker, token, seconds granted)
        return mock.patch.object(lifecycle, "_lease", grant)

    def test_a_granted_ask_replaced_for_a_newer_version_releases_its_lease(self):
        with self.granted("token-1", saved=True):
            self.assertEqual(counts.count(str(self.part)), counts.UNKNOWN)
            self.gate.set()
            self.assertTrue(wait_for(lambda: self.state() == "granted"))
        with self.granted("token-2"):
            counts.count(str(self.part))   # The stale granted ask is replaced.
            self.assertTrue(wait_for(lambda: (self.worker, "token-1") in self.released))
            # The pump reads only what is granted by then, and the new ask's grant lands on a thread of its own.
            self.assertTrue(wait_for(lambda: self.state() == "granted"))
            counts.pump(now=0.0)
            self.assertTrue(wait_for(lambda: (self.worker, "token-2") in self.released))
        self.assertEqual(sorted(self.released, key=str), [(self.worker, "token-1"), (self.worker, "token-2")])

    def test_a_lease_granted_after_reset_dropped_its_ask_is_released(self):
        with self.granted("token-1"):
            counts.count(str(self.part))
            counts.reset()
            self.gate.set()
            self.assertTrue(wait_for(lambda: (self.worker, "token-1") in self.released))

    def test_reset_releases_a_granted_lease_and_the_pump_never_releases_it_again(self):
        with self.granted("token-1"):
            counts.count(str(self.part))
            self.gate.set()
            self.assertTrue(wait_for(lambda: self.state() == "granted"))
            ask = counts._asks[str(self.part)]
            counts.reset()
            self.assertTrue(wait_for(lambda: self.released))
            counts._release(ask)
            self.assertTrue(self.joined())   # Any second release would have landed by now.
        self.assertEqual(self.released, [(self.worker, "token-1")])


class RewriteWithoutIdentity(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = sessions.SessionStore(directory.name, str(uuid.uuid4()))   # A plain lead chat's store.
        self.state = self.store.load()
        self.real = session_journal.rewrite

    def rewrite_without_identity(self, stat_fails=False):
        replaced, real_identity = [], session_guard.identity_of

        def no_identity(path, data, *rest):
            self.real(path, data, *rest)
            replaced.append(path)
            return None   # Its os.stat after os.replace failed.

        def identity_of(path):   # Failing too from the replace on, until this save ends.
            return session_guard.unknown() if replaced else real_identity(path)
        patches = [mock.patch.object(session_journal, "rewrite", no_identity)]
        if stat_fails:
            patches.append(mock.patch.object(session_guard, "identity_of", identity_of))
        for patcher in patches:
            patcher.start()
        try:
            self.store.save(self.state)
        finally:
            for patcher in patches:
                patcher.stop()

    def more(self, text):
        self.state["messages"].append({"role": "user", "text": text})
        self.store.save(self.state)

    def test_its_next_saves_write_on(self):
        self.rewrite_without_identity()
        for text in ("one", "two", "three"):
            self.more(text)
        self.assertEqual([m["text"] for m in self.store.load()["messages"]], ["one", "two", "three"])

    def test_when_the_stat_now_fails_too_the_next_save_checks_the_bytes_and_writes_on(self):
        self.rewrite_without_identity(stat_fails=True)
        self.assertIs(self.store._writer.expected, session_guard.WRITTEN)
        self.more("one")
        self.more("two")
        self.assertEqual([m["text"] for m in self.store.load()["messages"]], ["one", "two"])

    def test_another_writers_content_is_still_refused(self):
        self.rewrite_without_identity(stat_fails=True)
        with open(self.store.path, "ab") as handle:
            handle.write(b'{"another": "writer"}\n')
        with self.assertRaises(session_journal.JournalTaken):
            self.more("one")


if __name__ == "__main__":
    unittest.main()
