# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: a live-eval turn that does not end normally leaves evidence of what it was doing.

``eval_abnormal_end`` writes one block for every non-normal ending (a harness deadline, any other Stop, an
interrupted or failed turn, a background job that outlived its wait, a harness exception, the app ending
mid-task), the same for Claude and Codex, with what the provider streamed (``eval_stream_counts``: a runaway
message is steady deltas with no structural progress, a silent stream has none lately); ``eval_trace_export`` copies the Codex trace rows of those turns from a
fixture log database, bounded. Nothing here launches Angelo, a runtime or a provider.
"""

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import eval_abnormal_end as end  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_stream_counts as stream  # noqa: E402
import eval_trace_export as trace  # noqa: E402
from eval_receipts import ProgressWatch  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent.turn_activity import TurnActivity  # noqa: E402

KEY = "abnormal_end"
BASE = 1760000000   # A fixed wall time (unix seconds) for the fixture log rows.


class CodexShaped:
    """What ``turn_activity.signs`` reads of a Codex provider: one TurnActivity per attempt, no keep-alives."""

    def __init__(self, activity):
        self._activity, self._input_accepted, self.thread_id = activity, True, "thread-A"
        self._scope = SimpleNamespace(turn_id="turn-1")


class ClaudeShaped:
    """What ``turn_activity.signs`` reads of a Claude provider: its transport's open turn."""

    def __init__(self, activity):
        self._transport = SimpleNamespace(turn=SimpleNamespace(activity=activity, accepted=True,
                                                               done=threading.Event()))


class Controller:
    def __init__(self, provider, rows=()):
        self.provider, self.busy, self._worker, self.cancelled = provider, True, None, False
        self.status, self.pending, self.stops = "Thinking", {}, 0
        self.state = {"messages": list(rows), "native_id": "thread-A"}

    def stop(self):
        self.stops += 1
        self.cancelled, self.status = True, "Stopping"


def trial_for(controller, seconds_ago=300.0, phase="running"):
    sent = time.monotonic() - seconds_ago
    record = {"trial": 1, "task": "a_task", "kind": "edit", "approved": [], "declined": []}
    return SimpleNamespace(controller=controller, record=record, report={"trial": 1, "tasks": []}, phase=phase,
                           sent=sent, progress=ProgressWatch(sent), first_row=0)


def provider(shape, model_silent_s, heard_ago_s=None):
    activity = TurnActivity()
    now = time.monotonic()
    activity.last = now - model_silent_s
    activity.heard = now - (model_silent_s if heard_ago_s is None else heard_ago_s)
    return shape(activity)


class TimeoutBlockTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in self.out.iterdir()])

    def timed_out(self, shape, **signs):
        rows = [{"role": "assistant", "text": "Scaling the cube."}]
        trial = trial_for(Controller(provider(shape, 298.0, **signs), rows))
        trial.progress.observe("Thinking", now=trial.sent + 2.0, rows=rows)
        end.stop(trial, "timeout")   # As on_running does at the harness's deadline.
        trial.phase = "stopping"
        return trial

    def test_a_simulated_codex_timeout_writes_the_block_before_its_stop(self):
        trial = self.timed_out(CodexShaped)
        self.assertEqual(trial.controller.stops, 1, "the harness's Stop is still sent, once")
        block = trial.record[KEY]
        self.assertEqual((block["reason"], block["captured"], block["phase"]), ("timeout", "deadline", "running"))
        self.assertIn("longest_still_ms", block["progress_marks"], "the progress marks so far")
        self.assertEqual(block["controller_status"], "Thinking", "the status at the deadline, before Stop")
        event = block["last_provider_event"]
        self.assertTrue(event["turn_open"])
        self.assertEqual(event["kind"], "model", "Codex sends no keep-alives")
        self.assertAlmostEqual(event["age_ms"], 298000, delta=3000)
        self.assertEqual((event["session"], event["turn_id"]), ("thread-A", "turn-1"))
        self.assertEqual(block["last_tool_call"]["rows"], 0)
        self.assertEqual(block["interrupt"]["sent_by"], "harness_deadline")
        self.assertAlmostEqual(block["after_send_ms"], 300000, delta=3000)
        self.assertIsNotNone(block["sent_at_unix"])

        trial.controller.busy, trial.controller.status = False, "Interrupted"   # The runtime answered the interrupt.
        trial.record.update(status="timeout", receipt={"status": "interrupted"})
        end.settle(trial, "timeout", self.out)
        block = trial.record[KEY]
        self.assertTrue(block["interrupt"]["acknowledged"])
        self.assertGreaterEqual(block["interrupt"]["waited_ms"], 0)
        self.assertEqual((block["ended_by"], block["receipt_status"], block["status_at_end"]),
                         ("complete", "interrupted", "Interrupted"))
        self.assertFalse([key for key in block if key.startswith("_")], "no private keys in the report")

    def test_a_claude_timeout_writes_the_same_block_and_names_a_keep_alive(self):
        codex, claude = self.timed_out(CodexShaped), self.timed_out(ClaudeShaped, heard_ago_s=4.0)
        self.assertEqual(set(codex.record[KEY]), set(claude.record[KEY]), "the same block for both providers")
        event = claude.record[KEY]["last_provider_event"]
        self.assertEqual(event["kind"], "keep_alive", "the runtime still sent keep-alives while the model was silent")
        self.assertAlmostEqual(event["age_ms"], 4000, delta=1500)
        self.assertAlmostEqual(event["model_event_age_ms"], 298000, delta=3000)

    def test_a_simulated_runaway_timeout_records_its_stream(self):
        trial = trial_for(Controller(provider(CodexShaped, 0.01)))
        counter = stream.Counter()
        trial.progress.stream = counter.cursor(trial.sent)
        runaway(counter, trial.sent, time.monotonic())
        end.stop(trial, "timeout")
        counts = trial.record[KEY]["stream"]
        self.assertGreater(counts["text"]["deltas"], 20000)
        self.assertTrue(counts["current_message"]["open"])
        self.assertAlmostEqual(counts["last_10s"]["deltas_per_s"], 70, delta=2)
        self.assertFalse(counts["burst"]["structural_progress"], "steady deltas, no tool call: a runaway")
        self.assertEqual(trial.record[KEY]["last_provider_event"]["kind"], "model")

    def test_an_unanswered_interrupt_is_recorded_as_not_acknowledged(self):
        trial = self.timed_out(CodexShaped)
        end.settle(trial, "timeout", self.out)   # Still busy after the stop grace.
        self.assertIs(trial.record[KEY]["interrupt"]["acknowledged"], False)

    def test_the_last_host_visible_tool_call_is_named_with_its_age(self):
        rows = [{"role": "activity", "tool": "angelo_move", "status": "done"}]
        trial = trial_for(Controller(provider(CodexShaped, 5.0), rows))
        trial.progress.observe("Working", now=time.monotonic() - 40.0, rows=rows)
        trial.controller.pending = {"c": SimpleNamespace(name="angelo_bevel", approval=True, executing=False)}
        end.stop(trial, "timeout")
        tool = trial.record[KEY]["last_tool_call"]
        self.assertEqual((tool["rows"], tool["name"]), (1, "angelo_move"))
        self.assertAlmostEqual(tool["age_ms"], 40000, delta=2000)
        self.assertEqual(tool["pending"], [{"name": "angelo_bevel", "approval": True, "executing": False}])


def runaway(counter, sent, until, *, rate=70, claude=False, tool_at=None):
    """A reply, then one message streaming steadily from 2 s after the send to ``until`` with no tool call."""
    parser, step, t = object(), 1.0 / rate, sent + 2.0
    while t <= until:
        if claude:
            stream.claude_event(counter, parser, {"type": "content_block_delta", "index": 1,
                                                  "delta": {"type": "input_json_delta", "partial_json": "    "}}, now=t)
        else:
            stream.codex_event(counter, "item/agentMessage/delta", {"itemId": "msg-1", "delta": "    "}, now=t)
        if tool_at is not None and abs(t - tool_at) < step / 2:
            if claude:
                stream.claude_event(counter, parser, {"type": "content_block_start", "index": 2,
                                                      "content_block": {"type": "tool_use", "name": "angelo_move"}},
                                    now=t)
            else:
                stream.codex_event(counter, "item/started", {"item": {"type": "dynamicToolCall", "id": "c"}}, now=t)
        t += step


class StreamCountTests(unittest.TestCase):
    SENT = 1000.0

    def test_a_runaway_message_is_steady_deltas_with_no_structural_progress(self):
        for claude in (False, True):
            with self.subTest(provider="claude" if claude else "codex"):
                counter = stream.Counter()
                cursor = counter.cursor(self.SENT)
                runaway(counter, self.SENT, self.SENT + 300, claude=claude)
                counts = cursor.summary(now=self.SENT + 300)
                self.assertEqual(counts["observed_by"], "harness")
                self.assertAlmostEqual(counts["text"]["deltas"], 70 * 298, delta=3)
                self.assertEqual(counts["text"]["bytes"], 4 * counts["text"]["deltas"], "UTF-8 bytes of the deltas")
                message = counts["current_message"]
                self.assertTrue(message["open"], "the message never completed")
                self.assertEqual((message["deltas"], message["bytes"]), (counts["text"]["deltas"], counts["text"]["bytes"]))
                self.assertAlmostEqual(counts["last_10s"]["deltas_per_s"], 70, delta=1)
                self.assertAlmostEqual(counts["burst"]["started_after_send_ms"], 2000, delta=20)
                self.assertFalse(counts["burst"]["structural_progress"])
                self.assertEqual(counts["tool_calls"], 0)
                self.assertLess(counts["last_delta_age_ms"], 50)

    def test_a_silent_stream_has_no_recent_deltas(self):
        counter = stream.Counter()
        cursor = counter.cursor(self.SENT)
        runaway(counter, self.SENT, self.SENT + 2.5)
        counts = cursor.summary(now=self.SENT + 300)
        self.assertEqual(counts["last_10s"]["deltas"], 0)
        self.assertAlmostEqual(counts["last_delta_age_ms"], 297500, delta=50)
        self.assertLess(counts["burst"]["lasted_ms"], 600)

    def test_a_tool_call_during_the_burst_is_structural_progress(self):
        for claude in (False, True):
            with self.subTest(provider="claude" if claude else "codex"):
                counter = stream.Counter()
                cursor = counter.cursor(self.SENT)
                runaway(counter, self.SENT, self.SENT + 60, claude=claude, tool_at=self.SENT + 30)
                counts = cursor.summary(now=self.SENT + 60)
                self.assertTrue(counts["burst"]["structural_progress"])
                self.assertEqual(counts["tool_calls"], 1)
        counter = stream.Counter()   # A host-visible step (a plan step row) counts as well.
        cursor = counter.cursor(self.SENT)
        runaway(counter, self.SENT, self.SENT + 60)
        self.assertTrue(cursor.summary(self.SENT + 60, host_structure_at=self.SENT + 40)["burst"]["structural_progress"])

    def test_counts_start_at_the_send_and_a_completed_message_closes(self):
        counter = stream.Counter()
        runaway(counter, self.SENT - 100, self.SENT - 50)   # An earlier turn's stream.
        cursor = counter.cursor(self.SENT)
        stream.codex_event(counter, "item/agentMessage/delta", {"itemId": "m2", "delta": "Done."}, now=self.SENT + 1)
        stream.codex_event(counter, "item/reasoning/summaryTextDelta", {"delta": "abc"}, now=self.SENT + 1.5)
        stream.codex_event(counter, "item/completed", {"item": {"type": "agentMessage", "id": "m2"}}, now=self.SENT + 2)
        counts = cursor.summary(now=self.SENT + 3)
        self.assertEqual((counts["text"], counts["thinking"]), ({"bytes": 5, "deltas": 1}, {"bytes": 3, "deltas": 1}))
        self.assertFalse(counts["current_message"]["open"])
        claude = stream.Counter()
        start = claude.cursor(self.SENT)
        stream.claude_event(claude, None, {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "tool_use", "name": "StructuredOutput"}})
        self.assertEqual(start.summary()["tool_calls"], 0, "the plan's own block is the message, not a tool call")

    def test_install_wraps_both_real_stream_entry_points_and_changes_nothing(self):
        from angelo_agent import claude_stream
        from angelo_agent.providers import CodexProvider
        counter = stream.Counter()
        self.addCleanup(stream.uninstall)
        original = claude_stream.RoundStream._stream
        self.assertTrue(stream.install(counter))
        self.assertEqual(CodexProvider._scoped_event.__wrapped__.__name__, "_scoped_event")
        cursor = counter.cursor()
        event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}}
        self.assertEqual(claude_stream.RoundStream()._stream(event), original(claude_stream.RoundStream(), event))
        self.assertEqual(cursor.summary()["text"], {"bytes": 2, "deltas": 1})
        self.assertTrue(stream.install(counter), "installing twice wraps once")
        self.assertIs(claude_stream.RoundStream._stream.__wrapped__, original)


class EveryEndingTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.out = self.folder / "qa"
        self.out.mkdir()

    def trial(self, **kwargs):
        return trial_for(Controller(provider(CodexShaped, 10.0)), **kwargs)

    def test_a_turn_the_runtime_interrupted_writes_the_block_when_recorded(self):
        trial = self.trial()
        trial.controller.busy = False
        trial.record["receipt"] = {"status": "interrupted"}
        end.settle(trial, "failed", self.out)
        block = trial.record[KEY]
        self.assertEqual((block["reason"], block["captured"]), ("interrupted", "settle"))
        self.assertFalse(block["interrupt"]["sent"], "no Stop was sent")

    def test_a_stop_the_harness_did_not_send_is_seen_at_the_next_tick(self):
        trial = self.trial()
        trial.controller.stop()   # For example the one-shot cost cap (oneshot_session.watch).
        end.refresh(trial, self.out)
        self.assertEqual(trial.record[KEY]["interrupt"]["sent_by"], "other")
        trial.controller.busy = False
        end.settle(trial, "failed", self.out)
        self.assertTrue(trial.record[KEY]["interrupt"]["acknowledged"])

    def test_a_background_job_that_outlived_its_wait_writes_the_block(self):
        trial = self.trial(phase="jobs")
        trial.controller.busy = False
        trial.record.update(receipt={"status": "completed"}, job_wait_timed_out=True)
        end.settle(trial, "completed", self.out)
        self.assertEqual(trial.record[KEY]["reason"], "job_timeout")

    def test_a_harness_exception_mid_task_keeps_that_task(self):
        trial = self.trial()
        trial.report["error"] = "Traceback (most recent call last): ..."
        end.unfinished(trial, self.out)
        unfinished = trial.report["unfinished_task"]
        self.assertEqual((unfinished["task"], unfinished["phase"]), ("a_task", "running"))
        self.assertEqual(unfinished[KEY]["reason"], "harness_exception")
        self.assertEqual(unfinished[KEY]["last_provider_event"]["kind"], "model")

    def test_the_app_ending_mid_task_leaves_its_in_flight_snapshot_for_the_launcher(self):
        for code, reason in ((-11, "app_exit"), ("timeout", "harness_kill")):
            with self.subTest(exit_code=code):
                trial = self.trial()
                end.refresh(trial, self.out)   # Written at the first tick, then every REFRESH_S.
                left = json.loads((self.out / end.IN_FLIGHT).read_text())
                self.assertEqual(left[KEY]["reason"], "in_flight")
                report = end.collect(self.folder, {"tasks": [], "success": False}, code)   # The app wrote nothing.
                block = report["unfinished_task"][KEY]
                self.assertEqual((block["reason"], block["ended_by"], block["exit_code"]), (reason, reason, code))
                self.assertIn("progress_marks", block)

    def test_a_normal_ending_writes_no_block_and_clears_the_snapshot(self):
        for status, receipt in (("completed", "completed"), ("skipped", None)):
            with self.subTest(status=status):
                trial = self.trial()
                end.refresh(trial, self.out)
                trial.controller.busy = False
                trial.record["receipt"] = {"status": receipt} if receipt else None
                end.settle(trial, status, self.out)
                self.assertNotIn(KEY, trial.record)
                self.assertFalse((self.out / end.IN_FLIGHT).exists())

    def test_broken_evidence_never_stops_the_task_it_describes(self):
        trial = self.trial()
        trial.controller.state = None   # Anything the snapshot reads may be mid-teardown.
        end.refresh(trial, self.out)
        end.stop(trial, "timeout")
        self.assertEqual(trial.controller.stops, 1, "the Stop is still sent")
        self.assertIn("error", trial.record[KEY])
        end.settle(trial, "timeout", self.out)
        self.assertEqual(trial.record[KEY]["reason"], "timeout")

    def test_every_ending_path_of_the_harness_goes_through_the_evidence_module(self):
        """Source-level: the in-app driver is bpy-bound, so its wiring is pinned by reading it."""
        session = (HERE / "eval_live_agent_session.py").read_text(encoding="utf-8")
        tree = ast.parse(session)
        methods = {node.name: ast.get_source_segment(session, node) for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)}
        self.assertNotIn("controller.stop()", session, "every harness Stop records the turn first (end.stop)")
        self.assertIn('end.stop(self, "timeout")', methods["on_running"])
        self.assertIn('end.stop(self, "accept_timeout")', methods["on_accepting"])
        self.assertIn("end.refresh(self, OUT)", methods["tick"])
        self.assertIn("end.settle(self, status, OUT)", methods["complete"])
        self.assertIn("end.unfinished(self, OUT)", methods["finish"])
        warm_up = (HERE / "eval_warm_up.py").read_text(encoding="utf-8")
        self.assertNotIn("controller.stop()", warm_up)
        self.assertIn("end.settle(trial, status, None)", warm_up)
        launcher = (HERE / "eval_live_agent.py").read_text(encoding="utf-8")
        self.assertIn("trace_export.after_trial(folder, report, code, accounts, args)", launcher)


def logs_database(path, rows, body_column="feedback_log_body"):
    """A Codex log database with its ``logs`` table (codex-rs state/logs_migrations)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, ts_nanos "
                       "INTEGER NOT NULL, level TEXT NOT NULL, target TEXT NOT NULL, %s TEXT, module_path TEXT, "
                       "file TEXT, line INTEGER, thread_id TEXT, process_uuid TEXT, estimated_bytes INTEGER NOT NULL "
                       "DEFAULT 0)" % body_column)
    connection.executemany("INSERT INTO logs (ts, ts_nanos, level, target, %s, file, line, thread_id) "
                           "VALUES (?, ?, 'TRACE', 'codex_api::endpoint::responses_websocket', ?, 'ws.rs', 7, ?)"
                           % body_column, [(int(t), int(round((t % 1) * 1e9)), body, thread)
                                           for t, body, thread in rows])
    connection.commit()
    connection.close()


def stalled_block(task="edit_task"):
    return {"version": 1, "reason": "timeout", "sent_at_unix": BASE + 10.0, "ended_at_unix": BASE + 310.0,
            "last_provider_event": {"session": "thread-A", "kind": "model"}, "interrupt": {"sent": True}}


def lines_of(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TraceExportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.home, self.folder = self.root / "codex-profile" / "id", self.root / "trial-01"
        self.folder.mkdir(parents=True)

    def test_an_interrupted_codex_turn_exports_its_thread_rows_inside_its_window(self):
        logs_database(self.home / "logs_2.sqlite", [
            (BASE + 12.0, "websocket event: response.output_text.delta", "thread-A"),
            (BASE + 12.5, "request headers: authorization: Bearer abc.def-ghi", "thread-A"),
            (BASE + 100.0, "app-server: no event for 87 s", None),          # Threadless rows of the app-server.
            (BASE + 50.0, "another chat's row", "thread-B"),               # Another thread: never exported.
            (BASE + 5.0, "before the send", "thread-A"), (BASE + 400.0, "after the end", "thread-A")])
        report = {"mode": "codex_subscription", "tasks": [{"task": "edit_task", KEY: stalled_block()}]}
        trace.export(self.folder, report, self.home, "codex_subscription")
        rows = lines_of(self.folder / trace.TRACE_FILE)
        self.assertEqual([row["body"] for row in rows], ["websocket event: response.output_text.delta",
                                                         "request headers: authorization: [redacted]",
                                                         "app-server: no event for 87 s"])
        self.assertEqual({row["task"] for row in rows}, {"edit_task"})
        self.assertNotIn("abc.def", (self.folder / trace.TRACE_FILE).read_text())
        facts = report["tasks"][0][KEY]["trace"]
        self.assertEqual((facts["rows"], facts["truncated"], facts["database"]), (3, False, "logs_2.sqlite"))

    def test_the_older_schema_under_state_is_read_too(self):
        logs_database(self.home / "state" / "logs_2.sqlite", [(BASE + 20.0, "stream idle", "thread-A")],
                      body_column="message")
        report = {"tasks": [{"task": "edit_task", KEY: stalled_block()}]}
        trace.export(self.folder, report, self.home, "codex_subscription")
        self.assertEqual([row["body"] for row in lines_of(self.folder / trace.TRACE_FILE)], ["stream idle"])

    def test_the_export_is_bounded_and_keeps_the_window_start_and_end(self):
        count = 20000
        logs_database(self.home / "logs_2.sqlite", [(BASE + 11 + i * 0.01, "event %05d " % i + "x" * 1000, "thread-A")
                                                    for i in range(count)] +
                      [(BASE + 300.0, "y" * (trace.MAX_BODY * 5), "thread-A")])
        report = {"tasks": [{"task": "edit_task", KEY: stalled_block()}]}
        trace.export(self.folder, report, self.home, "codex_subscription")
        path = self.folder / trace.TRACE_FILE
        self.assertLessEqual(path.stat().st_size, trace.MAX_BYTES + 200)
        rows, facts = lines_of(path), report["tasks"][0][KEY]["trace"]
        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["rows_in_window"], count + 1)
        self.assertEqual(facts["omitted_rows"], count + 1 - facts["rows"])
        self.assertTrue(rows[0]["body"].startswith("event 00000"), "the window's start")
        self.assertEqual(rows[-1]["body_cut"], trace.MAX_BODY * 4, "the window's end, its long body cut")
        self.assertEqual(sum("omitted_rows" in row for row in rows), 1)

    def test_a_trial_with_many_stalled_turns_stays_within_its_file_budget(self):
        logs_database(self.home / "logs_2.sqlite", [(BASE + 11 + i * 0.02, "z" * 900, "thread-A") for i in range(8000)])
        report = {"tasks": [{"task": "t%d" % i, KEY: stalled_block()} for i in range(6)]}
        trace.export(self.folder, report, self.home, "codex_subscription")
        path = self.folder / trace.TRACE_FILE
        self.assertLessEqual(path.stat().st_size, trace.MAX_FILE_BYTES)
        self.assertTrue(all(isinstance(row, dict) for row in lines_of(path)), "whole rows only")

    def test_claude_has_no_trace_log_and_a_shared_profile_is_never_read(self):
        report = {"tasks": [{"task": "edit_task", KEY: stalled_block()}]}
        trace.export(self.folder, report, None, "claude_subscription")
        self.assertEqual(report["tasks"][0][KEY]["trace"], {"rows": 0, "note": trace.CLAUDE_NOTE})
        source = self.root / "source"
        record = ConnectionStore(source).add("OPENAI", "SUBSCRIPTION")
        args = SimpleNamespace(codex_home="", connection_id=record["id"])
        store = ConnectionStore(source)
        store.records[record["id"]]["reuse_native"] = True
        store.save()
        self.assertIsNone(trace.codex_home(source, args), "a reused native sign-in is the person's own")
        isolated = self.root / "isolated"
        eval_live_agent.isolated_connection(source, record["id"], isolated, codex_home="")
        self.assertIsNone(trace.codex_home(isolated, args))
        dedicated = ConnectionStore(self.root / "own").add("OPENAI", "SUBSCRIPTION")   # Its own profile directory.
        args = SimpleNamespace(codex_home="", connection_id=dedicated["id"])
        eval_live_agent.isolated_connection(self.root / "own", dedicated["id"], self.root / "copy", codex_home="")
        self.assertEqual(trace.codex_home(self.root / "copy", args),
                         (self.root / "own").absolute() / "codex-profile" / dedicated["id"])

    def test_the_launcher_exports_a_crashed_codex_turn_beside_its_trial(self):
        """``eval_live_agent.main`` (the Mac harness and the VM bench alike): the app died mid-task."""
        identity = ConnectionStore(self.root / "source").add("OPENAI", "SUBSCRIPTION")["id"]
        home = self.root / "bench" / "codex-profile" / identity
        home.mkdir(parents=True)
        (home / "auth.json").write_text("{}")   # Only its existence is checked.
        now = time.time()
        logs_database(home / "logs_2.sqlite", [(now - 30, "response.created", "thread-A"),
                                               (now - 29, "websocket: waiting for the next event", None)])
        binary = self.root / "Angelo"
        binary.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(binary, 0o755)

        def fake_run(command, cwd, env, stdout, stderr, timeout):
            qa = Path(env["ANGELO_QA_DIR"])
            block = dict(stalled_block(), reason="in_flight", sent_at_unix=now - 31, captured_at_unix=now - 28)
            (qa / end.IN_FLIGHT).write_text(json.dumps({"trial": 1, "task": "edit_task", "phase": "running",
                                                        KEY: block}))
            (qa / "trial.json").write_text(json.dumps({"mode": "codex_subscription", "success": False,
                                                       "tasks": [{"task": "cube_creation", "status": "completed"}]}))
            return subprocess.CompletedProcess(command, -11)

        output = self.root / "out"
        with patch.object(subprocess, "run", fake_run), contextlib.redirect_stdout(io.StringIO()), \
                patch.object(eval_live_agent.addon_pin, "problem", lambda *a: None), \
                patch.object(eval_live_agent.addon_pin, "trial_stamp", lambda *a: {}):
            eval_live_agent.main(["--authorize-billed-runs", "--connection-id", identity, "--trials", "1",
                                  "--blender", str(binary), "--output", str(output), "--codex-home", str(home),
                                  "--source-connections-dir", str(self.root / "source")])
        trial = json.loads((output / "eval-live-agent.json").read_text())["trials"][0]
        block = trial["unfinished_task"][KEY]
        self.assertEqual((block["reason"], block["exit_code"]), ("app_exit", -11))
        self.assertEqual(block["trace"]["rows"], 2)
        self.assertEqual(len(lines_of(output / "trial-01" / trace.TRACE_FILE)), 2)


if __name__ == "__main__":
    unittest.main()
