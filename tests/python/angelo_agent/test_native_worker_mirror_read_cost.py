# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's mirror reads each journal frame at the cost of the new records, not the chat (A′, R10). No Blender.

``MirrorStore`` keeps three checks between reads, each by the identity of the object it checked: a result record's
encode check, a mutation-ID ledger entry's check and a row's checked copy (``worker_mirror_journal``). These tests pin
that a tail read checks only what is new, whatever the chat already holds; that each kept check gives exactly what
the store's own check gives (``sessions._clean_result_payloads``, ``invocations.clean_invocations``); and that the
mirror's state still equals a load of the file. The timing itself is ``bench_worker_mirror_pump``.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import invocations, sessions, worker_mirror_journal  # noqa: E402
from angelo_agent.invocations import clean_invocations  # noqa: E402
from test_native_worker_mirror_reads import Reads  # noqa: E402

DIGEST = "%064x"


def record(index, size=200):
    return {"document": "('doc', 1)", "result": {"ok": True, "values": [index] * size}}


class Counted:
    """Calls of one module function while active."""

    def __init__(self, module, name):
        self.calls, real = [], getattr(module, name)

        def counted(*args):
            self.calls.append(args)
            return real(*args)
        self.patch = mock.patch.object(module, name, counted)

    def __enter__(self):
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class TailReadCostTests(Reads):
    def call(self, identity, size=200):
        self.worker.state["results"][identity] = record(len(self.worker.state["results"]), size)
        self.worker.state["invocations"]["entries"][identity] = {"digest": DIGEST % len(identity),
                                                                 "document": DIGEST % 1}
        return {"role": "activity", "call_id": identity, "tool": "angelo_create_cube", "arguments": {},
                "result": {"ok": True}, "status": "completed", "text": ""}

    def test_a_tail_read_encodes_only_new_results_whatever_the_chat_holds(self):
        for earlier in (0, 40):
            with self.subTest(earlier=earlier):
                self.setUp()
                rows = [self.call("old-%d-%d" % (earlier, index)) for index in range(earlier)]
                self.write(*rows, compact=True)
                for step in range(3):
                    encoded, entries = Counted(worker_mirror_journal, "_encodes"), Counted(invocations, "_entry")
                    with encoded:   # The mirror's read only: the worker's own save checks its ledger too.
                        self.write(self.call("new-%d-%d" % (earlier, step)), reading=entries)
                    self.assertEqual(self.store().tail_reads, step + 1)
                    self.assertEqual(len(encoded.calls), 1)   # The new record alone, never the earlier ones.
                    self.assertEqual(len(entries.calls), 1)
                self.assert_equals_load()

    def test_an_unchanged_row_keeps_its_object_and_a_replaced_one_is_checked_again(self):
        self.write({"role": "user", "text": "first"}, {"role": "assistant", "text": "second"}, compact=True)
        shown = list(self.mirror.state["messages"])
        self.write({"role": "assistant", "text": "third"})
        self.assertIs(self.mirror.state["messages"][0], shown[0])
        self.assertIs(self.mirror.state["messages"][1], shown[1])
        self.worker.state["messages"][1] = {"role": "assistant", "text": "second, edited"}
        self.worker.store.touch(1)
        self.write()
        self.assertIs(self.mirror.state["messages"][0], shown[0])
        self.assertEqual(self.mirror.state["messages"][1]["text"], "second, edited")
        self.assert_equals_load()

    def test_a_shown_row_changed_in_place_is_handed_out_again_as_the_file_has_it(self):
        self.write({"role": "user", "text": "first"}, compact=True)
        self.mirror.state["messages"][0]["text"] = "changed on the lead's display"   # Not a live row.
        self.write({"role": "assistant", "text": "second"})
        self.assertEqual(self.mirror.state["messages"][0]["text"], "first")
        self.mirror.state["messages"][0]["extra"] = True   # A key the file's row does not have.
        self.write({"role": "assistant", "text": "third"})
        self.assertNotIn("extra", self.mirror.state["messages"][0])
        self.assert_equals_load()

    def test_a_result_that_cannot_be_saved_stays_left_out_on_every_read(self):
        self.write({"role": "user", "text": "first"}, compact=True)
        store = self.store()
        bad = {"document": "d", "result": {"value": float("nan")}}
        raw = {"good": record(1), "bad": bad}
        self.assertEqual(store._results(raw), sessions._clean_result_payloads(raw))
        self.assertEqual(store._results(raw), {"good": raw["good"]})
        raw["bad"] = {"document": "d", "result": {"value": 1}}   # A replaced record is checked again.
        self.assertEqual(store._results(raw), sessions._clean_result_payloads(raw))
        self.assertIn("bad", store._results(raw))


class CheckParityTests(unittest.TestCase):
    """Each kept check answers exactly what the store's own check answers, through growth and damage."""

    def test_results_match_the_stores_own_check(self):
        store = worker_mirror_journal.MirrorStore(self.folder())
        value = {}
        cases = [record(index, 3) for index in range(300)]
        cases[5] = {"document": "d", "result": {"text": "\ud800"}}      # Not UTF-8.
        cases[9] = {"document": 3, "result": {}}                          # Not a record.
        cases[11] = {"document": "d", "result": {"x": float("inf")}}      # Not finite.
        cases[13] = "not a dict"
        for index, case in enumerate(cases):
            value["call-%d" % index] = case
            if index % 37 == 0 or index > 250:
                self.assertEqual(list(store._results(value).items()),
                                 list(sessions._clean_result_payloads(value).items()))
        value.pop("call-0")
        value["call-7"] = {"document": "d", "result": {"x": float("nan")}}
        self.assertEqual(list(store._results(value).items()), list(sessions._clean_result_payloads(value).items()))
        self.assertEqual(store._results([]), sessions._clean_result_payloads([]))

    def test_the_ledger_matches_the_stores_own_check(self):
        known = {}
        ledger = invocations.new_invocations()
        for index in range(60):
            ledger["entries"]["call-%d" % index] = {"digest": DIGEST % index, "document": DIGEST % 1}
            if index == 20:
                ledger["entries"]["bad\x01id"] = {"digest": DIGEST % index, "document": DIGEST % 1}
            if index == 30:
                ledger["entries"]["damaged"] = {"digest": "short", "document": DIGEST % 1}
            if index % 9 == 0:
                self.assertEqual(clean_invocations(ledger, known), clean_invocations(ledger))
        ledger["entries"]["damaged"] = {"digest": DIGEST % 3, "document": DIGEST % 1}   # Replaced: checked again.
        self.assertEqual(clean_invocations(ledger, known), clean_invocations(ledger))
        ledger["version"] = 2
        self.assertEqual(clean_invocations(ledger, known), clean_invocations(ledger))
        self.assertEqual(clean_invocations("not a ledger", known), clean_invocations("not a ledger"))

    def test_the_ledger_checks_only_entries_it_has_not_seen(self):
        known, ledger = {}, invocations.new_invocations()
        for index in range(50):
            ledger["entries"]["call-%d" % index] = {"digest": DIGEST % index, "document": DIGEST % 1}
        clean_invocations(ledger, known)
        ledger["entries"]["call-new"] = {"digest": DIGEST % 99, "document": DIGEST % 1}
        with Counted(invocations, "_entry") as entries, Counted(invocations, "_key") as keys:
            clean_invocations(ledger, known)
        self.assertEqual((len(entries.calls), len(keys.calls)), (1, 1))

    def folder(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return temporary.name


if __name__ == "__main__":
    unittest.main()
