# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's mirror reads each journal frame at the cost of the new records, not the chat (A′, R10). No Blender.

``MirrorStore`` keeps four checks between reads, each by the identity of the object it checked: a result record's
encode check, a mutation-ID ledger entry's check, a row's checked copy and the task memory's rehydrated copy
(``worker_mirror_journal``). These tests pin that a tail read checks only what is new, whatever the chat already
holds; that each kept check gives exactly what the store's own check gives (``sessions._clean_result_payloads``,
``invocations.clean_invocations``, ``SessionStore._rehydrated``); and that the mirror's state still equals a load of
the file. The timing itself is ``bench_worker_mirror_pump``. The task memory's check was the one left out until
1.0.7: its cost grew with the memory, and on a Windows T4 VM a reload of a chat with a full memory stalled the lead's
UI pump past its 10 ms bound (``test_blender_parallel_workers``, ``a_pump_never_waits_for_the_worker``).
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import invocations, sessions, task_memory, worker_mirror_journal  # noqa: E402
from angelo_agent.invocations import clean_invocations  # noqa: E402
from test_native_worker_mirror_reads import Reads  # noqa: E402

DIGEST = "%064x"


def record(index, size=200):
    return {"document": "('doc', 1)", "result": {"ok": True, "values": [index] * size}}


class Counted:
    """Calls of one module function while active."""

    def __init__(self, module, name):
        self.calls, real = [], getattr(module, name)

        def counted(*args, **keywords):
            self.calls.append(args)
            return real(*args, **keywords)
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


class TaskMemoryReadCostTests(Reads):
    """A tail read rehydrates the task memory only when a record set it; what it shows is what a load shows."""

    def remember(self, count):
        """The worker's memory with ``count`` more verified results (a new object, as task_memory installs it)."""
        profile = str(self.worker.store.directory.resolve())
        state = {"task_memory": self.worker.state["task_memory"]}
        document = ("part", 1)
        if not self.worker.state["task_memory"]["scope"]["document"]:
            state = {"task_memory": task_memory.new_memory(profile, document)}
        for index in range(count):
            self.calls += 1
            state = task_memory.on_verified_result(
                state, document, call_id="host:%d" % self.calls, tool="angelo_inspect_scene", profile_id=profile,
                result={"scene": "Scene", "object_count": 1, "mesh_revision": "a" * 64,
                        "objects": [{"name": "Tile %d.%d" % (self.calls, i), "id": "id-%d" % i} for i in range(40)]})
        self.worker.state["task_memory"] = state["task_memory"]

    def test_unchanged_task_memory_is_not_cleaned_again_on_a_tail_read(self):
        self.calls = 0
        self.remember(task_memory.MAX_RESULTS)   # A full memory: the reload that was slow.
        self.write({"role": "user", "text": "first"}, compact=True)
        shown = self.mirror.state["task_memory"]
        self.assertTrue(shown["verified_results"])
        for index in range(4):
            with self.subTest(step=index):
                cleaned = Counted(sessions, "clean_memory")
                self.write({"role": "assistant", "text": "reply %d" % index}, reading=cleaned)
                self.assertEqual(len(cleaned.calls), 0)
                self.assertIs(self.mirror.state["task_memory"], shown)   # The same copy: nothing to draw again.
                self.assert_equals_load()
        self.assertGreaterEqual(self.store().tail_reads, 4)

    def test_a_record_that_sets_the_memory_is_cleaned_once_and_shown_as_a_load_shows_it(self):
        self.calls = 0
        self.remember(2)
        self.write({"role": "user", "text": "first"}, compact=True)
        for step in range(3):
            with self.subTest(step=step):
                self.remember(1)
                cleaned = Counted(sessions, "clean_memory")
                self.write({"role": "assistant", "text": "reply %d" % step}, reading=cleaned)
                self.assertEqual(len(cleaned.calls), 1)
                self.assertEqual(len(self.mirror.state["task_memory"]["verified_results"]), 3 + step)
                self.assert_equals_load()

    def test_a_shown_memory_changed_in_place_is_handed_out_again_as_the_file_has_it(self):
        self.calls = 0
        self.remember(3)
        self.write({"role": "user", "text": "first"}, compact=True)
        self.mirror.state["task_memory"]["verified_results"].clear()   # The lead's display changed its copy.
        self.mirror.state["task_memory"]["extra"] = True
        self.write({"role": "assistant", "text": "second"})
        self.assertEqual(len(self.mirror.state["task_memory"]["verified_results"]), 3)
        self.assertNotIn("extra", self.mirror.state["task_memory"])
        self.assert_equals_load()

    def test_a_tail_read_resolves_no_path(self):
        """A resolve is a system call per path component, each a hand-off of the GIL by the lead's UI thread: none
        on a tail read, whether or not the memory changed."""
        self.calls = 0
        self.remember(1)
        self.write({"role": "user", "text": "first"}, compact=True)
        for step in range(4):
            with self.subTest(step=step):
                if step % 2:
                    self.remember(1)
                resolves = Counted(Path, "resolve")
                self.write({"role": "assistant", "text": "reply %d" % step}, reading=resolves)
                self.assertEqual(resolves.calls, [])
                self.assert_equals_load()

    def test_a_changed_memory_whitelists_only_its_new_result_summaries(self):
        self.calls = 0
        self.remember(task_memory.MAX_RESULTS - 2)
        self.write({"role": "user", "text": "first"}, compact=True)
        for step in range(3):   # The last one drops the oldest entry: a full memory keeps MAX_RESULTS.
            with self.subTest(step=step):
                self.remember(1)
                walked = Counted(task_memory, "_result_summary")
                self.write({"role": "assistant", "text": "reply %d" % step}, reading=walked)
                self.assertEqual(len([call for call in walked.calls if len(call) == 1]), 1)   # Top-level calls.
                self.assert_equals_load()

    def test_the_kept_memory_is_what_the_stores_own_read_gives_for_any_memory(self):
        self.calls = 0
        store = self.store()
        own = sessions.SessionStore(store.directory, store.session_id)
        self.remember(5)
        cases = [self.worker.state["task_memory"], None, {"version": 999}, "not a memory",
                 dict(self.worker.state["task_memory"], scope={"profile": "another-profile"}),
                 task_memory.new_memory("elsewhere")]
        for case in cases + cases:   # Twice: the second pass reads every case from what was kept.
            with self.subTest(case=str(case)[:40]):
                before = repr(case)
                self.assertEqual(store._rehydrated(case), own._rehydrated(case))
                self.assertEqual(repr(case), before)   # The document's own memory is never changed.


class CheckParityTests(unittest.TestCase):
    """Each kept check answers exactly what the store's own check answers, through growth and damage."""

    def test_kept_summaries_clean_as_the_memorys_own_clean_does(self):
        document, profile = ("part", 1), "profile-parity"
        summaries = [{"scene": "Scene", "object_count": 2, "objects": [{"name": "A", "id": "x", "object": "scn:1"}]},
                     {"object_count": 1.0}, {"object_count": True}, {"object_count": 1},   # Kept apart by type.
                     {"verified": {"object_count": 3}, "bounds_world": {"min": [0, 0, 0], "max": [1, 2, 3]}},
                     {"name": "line\u2028break \x07 \u202e"}, {"path": "/private/abs"}, {"path": "parts/a.blend"},
                     {"name": "sk-" + "a" * 20}, {"objects": [[1, 2], (3, 4)]}, {"object_count": float("nan")},
                     "not a summary", None, {"unknown": 1}]
        kept = task_memory.KeptSummaries()
        for rehydrating in (True, False, True):
            for start in range(len(summaries)):
                with self.subTest(rehydrating=rehydrating, start=start):
                    memory = task_memory.new_memory(profile, document)
                    memory["verified_results"] = [
                        {"id": "host:%d" % index, "tool": "angelo_inspect_scene", "status": "verified",
                         "document": memory["scope"]["document"], "generation": index % 2,
                         "summary": summary, "object_handles": [], "provenance": {"kind": "host_result",
                                                                                  "id": "host:%d" % index},
                         "stale": index % 3 == 0}
                        for index, summary in enumerate(summaries[start:] + summaries[:start])]
                    own = task_memory.clean_memory(memory, profile, rehydrating)
                    self.assertEqual(repr(task_memory.clean_memory(memory, profile, rehydrating,
                                                                   summaries=kept.turn())), repr(own))

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


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
