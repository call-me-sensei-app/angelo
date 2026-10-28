# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a parallel trial's lead and workers communicated (bench_parallel_comms, BENCH-PREREG Amendment B3, draft), read
from Codex rollouts written in the shape Codex writes them, without Blender.

Pinned here: the lead is the thread that started workers; each worker thread is matched to its part by its brief's
``Part: <name>.`` line and to its number by the start's answer; a merge the host refused never counts as that part's
merge; a turn that ended after the merge is not the worker's done; the lead's waiting status calls are summed, and the
requests it sent while one was out (Codex's ``wait`` polls of a yielded exec cell) are counted with their seconds and
input tokens; a thread that is no worker's is counted, never guessed; every thread a worker chat used joins to that
worker, recorded while the trial runs, and a brief thread the record does not name falls back to the order join
(break-it E2E-18 L-2); a trial with no rollouts says it was not measured; and the parallel report prints each trial's
record without the verdict reading it.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import bench_parallel_comms as comms
import bench_parallel_report as report
import test_native_bench_parallel as verdicts   # Its run fixture; its tests run there.
from angelo_agent.worker_sessions import BRIEF

T0 = 1792849836936


def message(at, text):
    item = {"type": "UserMessage", "content": [{"type": "text", "text": text}]}
    return {"type": "event_msg", "payload": {"type": "item_completed", "started_at_ms": at, "completed_at_ms": at,
                                             "item": item}}


def call(tool, start, end, arguments, answer):
    item = {"type": "DynamicToolCall", "tool": tool, "arguments": arguments, "status": "completed",
            "content_items": [{"type": "inputText", "text": json.dumps(answer)}], "success": True}
    return {"type": "event_msg", "payload": {"type": "item_completed", "started_at_ms": start, "completed_at_ms": end,
                                             "item": item}}


def usage(given, cached):
    return {"type": "token_usage_record", "payload": {"usage": {"input_tokens": given, "cached_input_tokens": cached}}}


def done(at):
    """The turn's reply item, then its end; ``task_complete`` carries only the whole second, as Codex writes it."""
    item = {"type": "AgentMessage", "text": "Done."}
    return [{"type": "event_msg", "payload": {"type": "item_completed", "started_at_ms": at - 300, "completed_at_ms": at,
                                              "item": item}},
            {"type": "event_msg", "payload": {"type": "task_complete", "completed_at": int(at / 1000)}}]


def brief(name, text):
    return BRIEF.format(name=name, brief=text)


def write(folder, name, entries):
    folder.mkdir(parents=True, exist_ok=True)
    flat = [entry for item in entries for entry in (item if isinstance(item, list) else [item])]
    (folder / ("rollout-2026-10-25T05-00-0%s.jsonl" % name)).write_text(
        "\n".join(json.dumps(entry) for entry in flat) + "\n", encoding="utf-8")


def rollouts(folder):
    s = lambda seconds: T0 + int(seconds * 1000)   # noqa: E731
    parts = {"parts": [{"name": "Sofa", "brief": "A grey three-seat sofa."}, {"name": "Table", "brief": "A table."}]}
    write(folder, "0-lead", [
        message(s(0), "Build the room."), usage(30000, 0),
        call(comms.START, s(5), s(6), parts, {"success": True, "starting": [{"worker": 1, "name": "Sofa"},
                                                                           {"worker": 2, "name": "Table"}]}),
        usage(31000, 29000),
        call(comms.STATUS, s(10), s(70), {"wait": True, "timeout_seconds": 600}, {"workers": []}),
        call(comms.STATUS, s(75), s(75.1), {}, {"workers": []}),
        call(comms.MERGE, s(80), s(80.2), {"parts": [{"worker": 1}]}, {"error": "Worker 2 is still working"}),
        call(comms.MERGE, s(90), s(92), {"parts": [{"worker": 1}, {"worker": 2}]},
             {"result": {"merged": [{"worker": 1}, {"worker": 2}]}, "success": True}),
        done(s(95))])
    write(folder, "1-sofa", [
        message(s(7), brief("Sofa", "A grey three-seat sofa.")), usage(28000, 0),
        call("angelo_inspect_scene", s(12), s(12.5), {}, {"success": True, "scene_changed": False}),
        usage(29000, 27500),
        call("angelo_mesh_create", s(20), s(21), {}, {"success": True, "scene_changed": True}), done(s(50))])
    write(folder, "2-table", [
        message(s(8), brief("Table", "A table.")), usage(28100, 0), done(s(60)),
        message(s(96), "One more thing."), done(s(100))])
    write(folder, "3-other", [message(s(1), "Something else entirely."), usage(1000, 0)])


class TrialTests(unittest.TestCase):
    def test_the_lead_and_each_worker(self):
        with tempfile.TemporaryDirectory() as root:
            rollouts(Path(root))
            record = comms.trial(root)
        self.assertTrue(record["measured"])
        self.assertEqual(record["lead"], {"starts": 1, "refused_starts": 0, "status_calls": 2, "waiting_status_calls": 1,
                                          "wait_s": 60.0, "merges": 2, "requests": 2, "tool_calls": 5, "threads": 1,
                                          "while_waiting": None})   # Its rollout has no times to place them by.
        sofa, table = record["workers"]
        self.assertEqual((sofa["worker"], sofa["name"], table["worker"], table["name"]), (1, "Sofa", 2, "Table"))
        self.assertEqual(sofa["brief_bytes"], len(brief("Sofa", "A grey three-seat sofa.").encode("utf-8")))
        self.assertEqual(sofa["lead_written_bytes"], len("A grey three-seat sofa."))
        self.assertEqual((sofa["first_request_input_tokens"], sofa["first_request_cached_tokens"], sofa["requests"],
                          sofa["input_tokens"], sofa["cached_tokens"]), (28000, 0, 2, 57000, 27500))
        self.assertEqual((sofa["brief_to_first_edit_s"], sofa["brief_to_done_s"], sofa["done_to_merge_s"]),
                         (13.0, 43.0, 40.0))   # The refused merge at 80 s is not its merge.
        self.assertEqual((table["brief_to_first_edit_s"], table["brief_to_done_s"], table["done_to_merge_s"],
                          table["messages_received"]), (None, 52.0, 30.0, 2))   # Its turn after the merge is not done.
        self.assertEqual(record["unmatched_threads"], 1)
        text = comms.line(record)
        self.assertIn("lead: 1 start (0 refused), 2 status (1 waiting, 60.0 s waited), 2 merge, 2 requests", text)
        self.assertIn("worker 1 Sofa: brief %d B (lead wrote 23 B), first request 28000 tokens (0 cached)"
                      % sofa["brief_bytes"], text)
        self.assertIn("1 thread(s) matched no worker", text)

    def test_workers_are_joined_by_identity_and_nothing_malformed_raises(self):
        """A malformed start (refused by Angelo, kept by the rollout), a refused start that re-lists a running part, a
        part restarted under a new number, and merges in a second lead thread (break-it E2E-16 M-1, M-5)."""
        s = lambda seconds: T0 + int(seconds * 1000)   # noqa: E731
        sofa = {"parts": [{"name": "Sofa", "brief": "A sofa."}]}
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            write(folder, "0-lead", [
                message(s(0), "Build the room."), usage(30000, 0),
                call(comms.START, s(1), s(1.1), {"parts": ["Sofa", "Table"]}, {"error": "parts[0] must be an object"}),
                call(comms.START, s(2), s(2.1), sofa, {"success": True, "starting": [{"worker": 1, "name": "Sofa"}]}),
                call(comms.START, s(3), s(3.1), {"parts": [{"name": "Sofa", "brief": "x" * 500}]},
                     {"error": "At most 1 worker runs at once in this run"}),
                call(comms.START, s(30), s(30.1), sofa, {"success": True, "starting": [{"worker": 2, "name": "Sofa"}]}),
                done(s(31))])
            write(folder, "1-lead-again", [   # The lead's next turn, a new thread: its merge.
                message(s(60), "Merge it."),
                call(comms.MERGE, s(61), s(62), {"parts": [{"worker": 2}]}, {"merged": [{"worker": 2}]}), done(s(63))])
            write(folder, "2-sofa", [message(s(2.5), brief("Sofa", "A sofa.")), usage(28000, 0), done(s(20))])
            write(folder, "3-sofa-again", [message(s(30.5), brief("Sofa", "A sofa.")), usage(28000, 0), done(s(50))])
            record = comms.trial(root)
        first, second = record["workers"]
        self.assertEqual((record["lead"]["starts"], record["lead"]["refused_starts"], record["lead"]["merges"],
                          record["lead"]["threads"], record["unmatched_threads"]), (2, 2, 1, 2, 0))
        self.assertEqual((first["worker"], first["merged"], second["worker"], second["merged"]), (1, False, 2, True))
        self.assertEqual((first["lead_written_bytes"], second["done_to_merge_s"], second["brief_to_done_s"]),
                         (len("A sofa."), 11.0, 19.5))   # The refused start's brief is not its; ends to the ms.
        with tempfile.TemporaryDirectory() as root:   # The record's identity wins over name and order (E2E-17 M-3).
            folder = Path(root)
            write(folder, "0-lead", [message(s(0), "Build."), call(comms.START, s(1), s(1.1), sofa, {
                "starting": [{"worker": 1, "name": "Sofa"}]}), call(comms.START, s(2), s(2.1), sofa, {
                "starting": [{"worker": 2, "name": "Sofa"}]}), done(s(3))])
            write(folder, "1-early", [message(s(4), brief("Sofa", "A sofa.")), done(s(20))])   # Worker 2, ready first.
            write(folder, "2-late", [message(s(5), brief("Sofa", "A sofa.")), done(s(30))])    # Worker 1.
            threads = {path.name.split("-")[-1].split(".")[0]: path.stem[-36:] for path in folder.glob("*.jsonl")}
            joined = comms.trial(root, [{"worker": 2, "threads": [threads["early"]]},
                                        {"worker": 1, "threads": [threads["late"]]}])
        self.assertEqual([entry["worker"] for entry in joined["workers"]], [2, 1])

    def test_every_thread_a_worker_chat_used_joins_to_that_worker(self):
        """E2E-18 L-2: Table's chat was rebased after its brief (its new thread's first message is the seed, with no
        ``Part:`` line). Whether the record names the new thread, or leaves Table out, Table stays worker 2 and its
        rows count both threads where the record names them."""
        s = lambda seconds: T0 + int(seconds * 1000)   # noqa: E731
        sofa, brief_thread, rebased = ("00000000-0000-4000-8000-0000000000a1", "00000000-0000-4000-8000-0000000000b1",
                                       "00000000-0000-4000-8000-0000000000b2")
        meta = lambda thread: {"type": "session_meta", "payload": {"id": thread}}   # noqa: E731
        parts = {"parts": [{"name": "Sofa", "brief": "A sofa."}, {"name": "Table", "brief": "A table."}]}

        def join(record):
            with tempfile.TemporaryDirectory() as root:
                folder = Path(root)
                write(folder, "0-lead", [
                    message(s(0), "Build the room."), usage(30000, 0),
                    call(comms.START, s(1), s(1.1), parts, {"starting": [{"worker": 1, "name": "Sofa"},
                                                                         {"worker": 2, "name": "Table"}]}),
                    call(comms.MERGE, s(90), s(91), {"parts": [{"worker": 1}, {"worker": 2}]},
                         {"merged": [{"worker": 1}, {"worker": 2}]}), done(s(92))])
                write(folder, "1-sofa", [meta(sofa), message(s(5), brief("Sofa", "A sofa.")), usage(28000, 0),
                                         done(s(50))])
                write(folder, "2-table", [meta(brief_thread), message(s(6), brief("Table", "A table.")),
                                          usage(28000, 0), done(s(60))])
                write(folder, "3-table-rebased", [meta(rebased), message(s(70), json.dumps({"rebased": "seed"})),
                                                  usage(9000, 0), done(s(80))])
                found = comms.trial(root, record)
            return {entry["name"]: entry for entry in found["workers"]}, found["unmatched_threads"]
        both = [{"worker": 1, "threads": [sofa]}, {"worker": 2, "threads": [brief_thread, rebased]}]
        for case, record in (("only its new thread named", [{"worker": 1, "threads": [sofa]},
                                                             {"worker": 2, "threads": [rebased]}]),
                             ("every thread named", both)):
            with self.subTest(case=case):
                workers, unmatched = join(record)
                table = workers["Table"]
                self.assertEqual((table["worker"], table["merged"], table["threads"], table["requests"],
                                  table["messages_received"], table["lead_written_bytes"], unmatched),
                                 (2, True, 2, 2, 2, len("A table."), 0))
                self.assertEqual((table["brief_to_done_s"], table["done_to_merge_s"]), (74.0, 10.0))
                self.assertEqual(table["brief_bytes"], len(brief("Table", "A table.").encode("utf-8")))
        for case, record in (("left out of the record", [{"worker": 1, "threads": [sofa]}]), ("no record", [])):
            with self.subTest(case=case):   # The order join: worker 2, its new thread unknown and said so.
                workers, unmatched = join(record)
                self.assertEqual((workers["Table"]["worker"], workers["Table"]["merged"],
                                  workers["Table"]["done_to_merge_s"], unmatched), (2, True, 30.0, 1))
                self.assertEqual(workers["Sofa"]["worker"], 1)

    def test_the_trial_record_names_every_thread_each_chat_used(self):
        """eval_parallel keeps every runtime thread the lead and each worker chat had, from the running task's ticks
        (``cards``) to its receipts, so one a rebase replaced is still named (E2E-18 L-2)."""
        import importlib
        import eval_parallel
        on = mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"})
        on.start()
        module = importlib.reload(eval_parallel)
        self.addCleanup(lambda: (on.stop(), importlib.reload(eval_parallel)))
        from angelo_agent import parallel_sessions, worker_sessions
        lead = mock.Mock(state={"id": "lead", "messages": [], "native_id": "lead-1", "runtime_sessions": {}},
                         pending={}, busy=False, _worker=None, telemetry=mock.Mock(receipts=[]))
        table = mock.Mock(state={"id": "table", "messages": [], "native_id": "table-1", "runtime_sessions": {}},
                          pending={}, busy=False)
        with mock.patch.object(module, "workers", lambda ui, chat: [table]), \
                mock.patch.object(worker_sessions, "finished", lambda session: True), \
                mock.patch.dict(worker_sessions._parts, {"table": (2, "Table", Path("part.blend"))}), \
                mock.patch.dict(parallel_sessions.names, {"table": "Table"}):
            module.cards(None, lead)   # A tick while its first turn runs.
            table.state.update(native_id=None, runtime_sessions={"c": {"native_id": None, "rebase_pending": True}})
            module.cards(None, lead)   # Between turns: rebased, no thread yet.
            table.state.update(native_id="table-2", runtime_sessions={"c": {"native_id": "table-2"}})
            lead.state.update(native_id="lead-2")
            record = {}
            module.receipts(None, lead, None, record)
        self.assertEqual((record["workers"][0]["worker"], record["workers"][0]["threads"], record["lead_threads"]),
                         (2, ["table-1", "table-2"], ["lead-1", "lead-2"]))

    def test_the_leads_requests_while_a_waiting_status_call_is_out(self):
        """The Codex fork's held exec cell (proposal 1d): a yielded cell is polled with ``wait`` requests while the
        status waits, each counted with its seconds and input tokens (the dev check's shape: two, 3.5 s and 2.7 s); the
        request that made the call and the one after its answer are not; a held cell reads 0. The call's entry is
        written as its answer arrives, and the answer's output after it: that order, not a few ms between their
        timestamps, ends the wait, so an output stamped before the call's end still sends the next request
        (break-it E2E-19B), and a poll stamped late is still the wait's."""
        s = lambda seconds: T0 + int(seconds * 1000)   # noqa: E731

        def stamped(seconds, entry):
            stamp = datetime.fromtimestamp(s(seconds) / 1000, timezone.utc).isoformat(timespec="milliseconds")
            return dict(entry, timestamp=stamp.replace("+00:00", "Z"))

        def given(seconds, kind="custom_tool_call_output"):
            return stamped(seconds, {"type": "response_item", "payload": {"type": kind, "output": "..."}})

        def request(seconds, tokens):
            return stamped(seconds, usage(tokens, tokens - 100))

        def lead(yielded, answered_at, polled_at):
            entries = [stamped(0, {"type": "response_item", "payload": {"type": "message", "role": "user"}}),
                       message(s(0), "Build the room."), request(2, 30000),
                       call(comms.START, s(2.1), s(2.2), {"parts": [{"name": "Sofa", "brief": "A sofa."}]},
                            {"starting": [{"worker": 1, "name": "Sofa"}]}),
                       given(2.3), request(20.025, 31000)]   # The request that made the call: recorded after it began.
            if yielded:   # The cell yields at 31 s: the model polls it twice.
                entries += [given(51), request(54.5, 39686), given(polled_at), request(polled_at + 2.7, 39756)]
            entries += [call(comms.STATUS, s(20), s(76), {"wait": True}, {"workers": []}),   # Written at its answer.
                        given(answered_at, "function_call_output" if yielded else "custom_tool_call_output"),
                        request(78, 40000), done(s(79))]   # Sent by the answer.
            return entries
        polls = {"requests": 2, "s": 6.2, "input_tokens": 79442, "each": [{"s": 3.5, "input_tokens": 39686},
                                                                           {"s": 2.7, "input_tokens": 39756}]}
        held = {"requests": 0, "s": 0.0, "input_tokens": 0, "each": []}
        for yielded, answered_at, polled_at, expected in ((True, 76.005, 65, polls), (False, 76.005, 65, held),
                                                          (True, 75.95, 65, polls), (False, 75.95, 65, held),
                                                          (True, 76.005, 76.02, polls)):
            with self.subTest(yielded=yielded, answered_at=answered_at, polled_at=polled_at), \
                    tempfile.TemporaryDirectory() as root:
                write(Path(root), "0-lead", lead(yielded, answered_at, polled_at))
                write(Path(root), "1-sofa", [message(s(3), brief("Sofa", "A sofa.")), usage(28000, 0), done(s(50))])
                record = comms.trial(root)
                self.assertEqual(record["lead"]["while_waiting"], expected)
                self.assertIn("%d requests while a waiting status call was out" % expected["requests"],
                              comms.line(record))

    def test_a_queued_part_and_a_scripts_changes_are_read_as_the_runtime_now_answers(self):
        """A part past the free places is queued (worker_queue), not refused; a worker that builds with Python answers
        with the script's ``changes``, never ``scene_changed``. Both seen on the 2026-10-25 dev rerun on 948d877ee26."""
        s = lambda seconds: T0 + int(seconds * 1000)   # noqa: E731
        parts = {"parts": [{"name": "Sofa", "brief": "A grey sofa."}, {"name": "Chair", "brief": "An oak chair."}]}
        with tempfile.TemporaryDirectory() as root:
            write(Path(root), "0-lead", [message(s(0), "Build the room."), usage(30000, 0),
                  call(comms.START, s(5), s(6), parts, {"success": True, "starting": [{"worker": 1, "name": "Sofa"}],
                                                       "queued": [{"worker": 2, "name": "Chair", "position": 1}]}),
                  done(s(90))])
            write(Path(root), "1-sofa", [message(s(7), brief("Sofa", "A grey sofa.")), usage(28000, 0),
                  call("angelo_execute_python", s(20), s(21), {}, {"success": True, "changes": {"added": [
                      {"name": "Sofa"}]}}), done(s(30))])
            write(Path(root), "2-chair", [message(s(31), brief("Chair", "An oak chair.")), usage(28000, 23000),
                  call("angelo_execute_python", s(40), s(41), {}, {"success": True, "changes": {"added": []}}),
                  done(s(50))])
            record = comms.trial(root)
        sofa, chair = record["workers"]
        self.assertEqual((record["lead"]["starts"], record["lead"]["refused_starts"]), (1, 0))
        self.assertEqual((sofa["lead_written_bytes"], chair["lead_written_bytes"]), (12, 13))
        self.assertEqual((sofa["brief_to_first_edit_s"], chair["brief_to_first_edit_s"]), (13.0, None))   # Nothing added.

    def test_nothing_to_read_is_said(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(comms.trial(Path(root) / "absent"), {"measured": False, "why": "no rollouts"})
            write(Path(root), "0-lead", [message(T0, "Build the room."), usage(1000, 0)])
            self.assertEqual(comms.trial(root), {"measured": False, "why": "no thread started a worker"})
        self.assertEqual(comms.line({"measured": False, "why": "no rollouts"}), "not measured (no rollouts)")


class ReportTests(unittest.TestCase):
    def test_each_parallel_trials_communication_is_reported_and_never_decides_the_verdict(self):
        same = {"King": [3, 3]}
        folder = verdicts.VerdictTests().run_folder({"single": [600, 620], "parallel": [300, 320]},
                                                    {"single": same, "parallel": same})
        before = report.verdict(folder)
        rollouts(folder / "parallel" / "angelo-trial-01-rollouts")
        result = report.verdict(folder)
        self.assertTrue(result["arms"]["parallel"][0]["communication"]["measured"])
        self.assertEqual((result["ships"], result["ship_rule_holds"]), (before["ships"], before["ship_rule_holds"]))
        text = report.markdown(result)
        self.assertIn("parallel 1: lead: 1 start (0 refused), 2 status (1 waiting, 60.0 s waited)", text)
        self.assertIn("parallel 2: not measured (no rollouts)", text)


if __name__ == "__main__":
    unittest.main()
