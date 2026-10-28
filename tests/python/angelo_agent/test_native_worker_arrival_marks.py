# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The worker's reader never waits for a hosted chat's lock (``worker_consent_marks``, ``worker_chat_arrival``).

A provider thread holds the chat's lock while it saves the chat's journal. Before, the reader took that lock to
apply a consent and to mark a Stop, so a slow save held a revocation, and the Stop behind it, for the save's length.
Pinned here on the real reader thread (``WorkerHost._read``) with the real journal save blocked under the lock:

* a revocation and then a Stop arrive; the reader takes both, and a lease op after them, at once; every admission
  and start check meanwhile refuses, also once the lock is free and before the lease is applied;
* a narrowing is acknowledged as it is marked, within the lead's deadline (``worker_consent_held.ACK_SECONDS``)
  while a 5 s save holds the lock, so the lead never stops the chat for it, and nothing autonomous starts;
* a widening arrives the same way, is answered only once applied and is never granted before;
* a mark is judged as its application will judge it: a repeat, an older epoch or an unrelated grant refuses nothing.
"""

import threading
import time
import types
import unittest

from test_native_worker_chat_arrival import ArrivalFixture

from angelo_agent import tool_deadlines, tool_finalization, worker_consent, worker_consent_marks as marks
from angelo_agent.worker_consent_held import ACK_SECONDS, Send
from angelo_agent.worker_chat_hosted import STOPPED

PROMPT = 0.5   # A frame the reader takes at once is answered well within this; the blocked save lasts until released.


class GatedConsent:
    """The host's consent seam, whose lease application waits for ``gate`` (the lock is free, the lease not yet
    applied: the window the marks must cover)."""

    def __init__(self, real):
        self.real, self.gate = real, threading.Event()

    def __getattr__(self, name):
        return getattr(self.real, name)

    def apply_consent(self, *args):
        self.gate.wait(10)
        return self.real.apply_consent(*args)


def unstarted_call(automatic=True, name="angelo_create_cube"):
    return types.SimpleNamespace(name=name, automatic=automatic, revoked="", staging=False, mutation_started=False,
                                 committing=False, executing=False)


class BlockedSaveFixture(ArrivalFixture):
    def hosted(self, grants):
        self.open(options={"approval": "AUTO_BOUNDED"}, consent={"epoch": 1, "grants": grants})
        chat = self.chats.hosted(self.session)
        chat.checkpoint_factory = None
        self.consent = self.chats.consent = GatedConsent(self.chats.consent)
        self.addCleanup(self.consent.gate.set)
        return chat

    def block_journal_save(self, chat, seconds=10):
        """A provider thread's admission save (``tool_finalization.journal_tool``), blocked inside the chat's lock
        for ``seconds`` or until released."""
        in_save, self.release = threading.Event(), threading.Event()
        real = chat.save

        def slow_save(**kwargs):
            in_save.set()
            self.release.wait(seconds)
            return real(**kwargs)
        chat.save = slow_save
        self.addCleanup(self.release.set)
        call = types.SimpleNamespace(id="journal-1", document=None, invocation_digest=None, approved=False,
                                     automatic=True)
        provider = threading.Thread(target=tool_finalization.journal_tool, args=(chat, call, "queued"), daemon=True)
        provider.start()
        self.assertTrue(in_save.wait(5))
        self.assertFalse(chat.lock.acquire(blocking=False), "the provider thread holds the chat's lock")
        return provider

    def taken_at_once(self, seq):
        """A lease op after the frames under test: answered by the reader within ``PROMPT``."""
        began = time.monotonic()
        self.frame(seq, "part.unlease", lease="none")
        answer = self.answer(seq, timeout=PROMPT)
        self.assertIsNotNone(answer, "the reader waited behind the blocked save")
        self.assertLess(time.monotonic() - began, PROMPT)

    def free_lock(self, chat, provider):
        self.release.set()
        provider.join(5)
        self.assertFalse(provider.is_alive())
        self.assertTrue(chat.lock.acquire(timeout=1))
        chat.lock.release()


class ARevocationAndAStopDuringABlockedSave(BlockedSaveFixture):
    def test_the_reader_takes_both_at_once_and_nothing_starts_meanwhile(self):
        chat = self.hosted({"auto": True})
        call = unstarted_call()
        self.assertTrue(worker_consent.admit_autonomous(chat, call))
        provider = self.block_journal_save(chat)

        self.frame(4, "chat.consent", epoch=2, grants={"auto": False})
        self.taken_at_once(5)
        self.assertFalse(worker_consent.admit_autonomous(chat, call))   # The revocation holds from its arrival.
        self.assertFalse(chat._auto_bounded(call, "AUTO_BOUNDED"))
        self.assertEqual(chat.consent_lease.epoch, 1)   # Its application still waits for the lock.

        self.frame(6, "chat.stop", reason="")
        self.taken_at_once(7)
        self.assertTrue(self.chats.arrivals.stop_pending(self.session))
        self.assertTrue(chat.cancelled)
        self.assertEqual(self.answer(4, timeout=PROMPT)["epoch"], 2)   # A narrowing: acknowledged as it was marked.

        self.free_lock(chat, provider)   # The lock is free; the lease is not applied yet (the applier is gated).
        self.assertEqual(chat.consent_lease.epoch, 1)
        self.assertFalse(tool_deadlines.started(chat, call, executing=True))
        self.assertEqual(call.revoked, STOPPED)
        self.assertFalse(call.executing)

        self.consent.gate.set()
        deadline = time.monotonic() + 5
        while chat.consent_lease.epoch != 2 and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertFalse(chat.consent_lease.granted_auto())
        self.assertEqual([frame["seq"] for frame in self.endpoint.responses()].count(4), 1)   # Answered once.
        self.ask("chat.stop", reason="")
        self.chats.arrivals.handled(6)
        self.run_turn()

    def test_a_revocation_alone_refuses_a_start_until_its_lease_is_applied(self):
        chat = self.hosted({"auto": True})
        provider = self.block_journal_save(chat)
        self.frame(4, "chat.consent", epoch=2, grants={"auto": False})
        self.taken_at_once(5)
        self.free_lock(chat, provider)
        call, asked = unstarted_call(), unstarted_call(automatic=False)
        self.assertFalse(tool_deadlines.started(chat, call, executing=True))
        self.assertEqual(call.revoked, worker_consent.CONSENT_REVOKED)
        self.assertTrue(tool_deadlines.started(chat, asked, executing=True))   # A call the person approved starts.
        self.consent.gate.set()
        self.assertEqual(self.answer(4, timeout=5)["epoch"], 2)


class ANarrowingDuringAFiveSecondSave(BlockedSaveFixture):
    def test_it_is_acknowledged_within_the_leads_deadline_and_nothing_autonomous_starts(self):
        chat = self.hosted({"auto": True, "scripts": "nonce-1"})
        provider = self.block_journal_save(chat, seconds=5.0)
        sent = Send(self.session, 2, {"auto": True}, True, time.monotonic())   # The lead's record of a revocation.
        self.frame(4, "chat.consent", epoch=2, grants={"auto": True})   # Withdraws the script grant only.
        answer = self.answer(4, timeout=ACK_SECONDS)
        self.assertIsNotNone(answer, "not acknowledged within the lead's deadline")
        sent.answer, sent.answered = answer, time.monotonic()
        sent.done.set()
        self.assertEqual((answer["ok"], answer["epoch"]), (True, 2))
        self.assertTrue(sent.in_time())   # The lead's ``tick`` kills and stops only a revocation not in time.
        while time.monotonic() - sent.started < ACK_SECONDS:   # Past the deadline, the save still holds the lock.
            self.assertEqual(chat.chat_consents(), ())
            time.sleep(0.05)
        self.assertTrue(provider.is_alive())
        self.assertFalse(self.chats.arrivals.stop_pending(self.session) or chat.cancelled)   # No spurious stop.
        self.assertEqual(chat.consent_lease.epoch, 1)
        self.free_lock(chat, provider)
        script = types.SimpleNamespace(name=worker_consent.SCRIPT, automatic=True, revoked="", staging=False,
                                       mutation_started=False, committing=False, executing=False,
                                       script_consent=chat.consent_lease.token("scripts"), arguments={})
        self.assertFalse(tool_deadlines.started(chat, script, executing=True))
        self.assertEqual(script.revoked, worker_consent.CONSENT_REVOKED)
        self.assertTrue(worker_consent.admit_autonomous(chat, unstarted_call()))   # Auto itself was not withdrawn.
        self.consent.gate.set()
        deadline = time.monotonic() + 5
        while chat.consent_lease.epoch != 2 and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertEqual((chat.consent_lease.epoch, chat.chat_consents()), (2, ()))
        self.assertEqual([frame["seq"] for frame in self.endpoint.responses()].count(4), 1)


class AWideningDuringABlockedSave(BlockedSaveFixture):
    def test_a_widening_is_never_granted_before_its_lease_is_applied(self):
        chat = self.hosted({"auto": False})
        call = unstarted_call()
        provider = self.block_journal_save(chat)
        self.frame(4, "chat.consent", epoch=2, grants={"auto": True, "scripts": "nonce-1"})
        self.taken_at_once(5)
        self.assertIsNone(self.answer(4, timeout=0.05))   # A widening is answered only once applied.
        self.assertFalse(worker_consent.admit_autonomous(chat, call))
        self.assertEqual(chat.chat_consents(), ())
        self.free_lock(chat, provider)
        self.assertFalse(worker_consent.admit_autonomous(chat, call))   # Lock free, lease not applied: not yet.
        self.assertEqual(chat.chat_consents(), ())
        self.assertFalse(tool_deadlines.started(chat, call, executing=True))
        self.consent.gate.set()
        self.assertEqual(self.answer(4, timeout=5)["epoch"], 2)
        self.assertTrue(worker_consent.admit_autonomous(chat, unstarted_call()))
        self.assertEqual(len(chat.chat_consents()), 1)


class MarksAreJudgedAsTheirApplication(unittest.TestCase):
    def lease(self, epoch, grants):
        lease = worker_consent.Lease()
        lease.epoch, lease.auto = epoch, grants["auto"]
        for kind in ("scripts", "always_allow"):
            if grants.get(kind):
                lease._tokens[kind] = (grants[kind], object())
        return lease

    def test_each_kind_of_frame(self):
        held = {"auto": True, "scripts": "n1"}
        lease = self.lease(3, held)
        cases = [
            ((4, {"auto": True, "scripts": "n1"}), {"auto": True, "scripts": True, "always_allow": False}),
            ((4, {"auto": True, "scripts": "n2"}), {"auto": True, "scripts": False}),   # A replaced nonce.
            ((4, {"auto": True}), {"auto": True, "scripts": False}),
            ((4, {"auto": False, "scripts": "n1"}), {"auto": False, "scripts": False}),
            ((3, dict(held)), {"auto": True, "scripts": True}),                          # A repeat.
            ((3, {"auto": True}), {"auto": False, "scripts": False}),                    # A conflict.
            ((2, {"auto": False}), {"auto": True, "scripts": True}),                     # Older: refused.
        ]
        for mark, expected in cases:
            for grant, covered in expected.items():
                with self.subTest(mark=mark, grant=grant):
                    self.assertIs(marks.covers(lease, [mark], grant), covered)

    def test_every_arrived_frame_must_cover(self):
        lease = self.lease(1, {"auto": True})
        self.assertFalse(marks.covers(lease, [(2, {"auto": False}), (3, {"auto": True})], "auto"))
        self.assertTrue(marks.covers(lease, [], "auto"))

    def test_only_a_newer_narrowing_is_acknowledged_on_arrival(self):
        lease = self.lease(3, {"auto": True, "scripts": "n1"})
        cases = [((4, {"auto": False}), [], True), ((4, {"auto": True}), [], True),
                 ((4, {"auto": True, "scripts": "n2"}), [], True), ((4, {"auto": True, "scripts": "n1"}), [], False),
                 ((3, {"auto": False}), [], False), ((2, {"auto": False}), [], False),
                 ((5, {"auto": True, "scripts": "n1"}), [(4, {"auto": True, "scripts": "n1", "always_allow": "a"})],
                  True),
                 ((4, {"auto": False}), [(5, {"auto": True})], False)]
        for mark, earlier, expected in cases:
            with self.subTest(mark=mark, earlier=earlier):
                self.assertIs(marks.acknowledged_on_arrival(lease, earlier, mark), expected)
        self.assertFalse(marks.acknowledged_on_arrival(worker_consent.Lease(), [], (1, {"auto": False})))

    def test_a_frame_that_is_not_a_lease_changes_nothing(self):
        for request in ({"epoch": "2", "grants": {}}, {"epoch": -1, "grants": {}}, {"epoch": 2, "grants": []}):
            self.assertIsNone(marks.mark_of(request))
        self.assertEqual(marks.mark_of({"epoch": 2, "grants": {"auto": 1}}), (2, {"auto": False}))


if __name__ == "__main__":
    unittest.main()
