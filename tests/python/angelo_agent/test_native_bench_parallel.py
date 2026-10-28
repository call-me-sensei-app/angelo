# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parallel-agents benchmark's runner, grader and verdict, without a model or Blender.

Pinned here (the runner itself in ``test_native_bench_parallel_runs``): the grader's files never name the arm and its
answer must be exactly the asked scores, and the images it reads carry no metadata naming a trial's folder; the
trial driver's parallel arm sums every chat's usage (the store is shared by every chat) and applies the provider-limit
exclusion to every chat; the verdict ships only when
parallel is faster, no grade class is worse, the floor holds and the smoothness budget is kept (worse or unmeasured
is a blocker), a trial stopped at the cap counts as the cap, and only when a worker ran in every valid parallel trial
(break-it E2E-3 H-1, M-3 to M-6, L-15, L-18). The verdict reads the files the bench writes, under the producers' own
names and keys: its fixtures are records made by ``bench_mcp_report.angelo_records`` (was: hand-written records in a
``results.json`` the bench never writes, read for a scorecard key that file never has).
"""
import struct
import zlib

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parents[2] / "scripts/addons_core"))   # The driver's hooks read angelo_agent's names.
import bench_mcp_report  # noqa: E402
import bench_parallel_grade as grade  # noqa: E402
import oneshot_report  # noqa: E402
import bench_parallel_report as report  # noqa: E402


def png(text):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"File\x00" + text) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


def jpeg(text):
    app1 = b"Exif\x00\x00" + text
    return (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 6) + b"JFIF" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2)
            + app1 + b"\xff\xda" + struct.pack(">H", 2) + b"scan" + b"\xff\xd9")


class GraderTests(unittest.TestCase):
    def test_grader_keeps_only_the_bounded_qa_owner_beside_its_fixed_environment(self):
        for owner in ("a" * 32, "", "a" * 31, "A" * 32, "g" * 32, "a" * 31 + "\n"):
            inherited = {"ANGELO_QA_PROCESS_OWNER": owner, "ANTHROPIC_BASE_URL": "unused", "ANTHROPIC_API_KEY": "unused"}
            with self.subTest(owner=owner), mock.patch.dict(os.environ, inherited, clear=True), \
                    mock.patch.object(grade.subprocess, "run") as run:
                run.return_value.stdout = '{"result": "ok"}'
                self.assertEqual(grade.ask("claude", "pinned-model", "/profile", "unchanged prompt", "/images"), "ok")
                environment = run.call_args.kwargs["env"]
            expected = {"CLAUDE_CONFIG_DIR", "PATH", "HOME"}
            if owner == "a" * 32:
                expected.add("ANGELO_QA_PROCESS_OWNER")
                self.assertEqual(environment["ANGELO_QA_PROCESS_OWNER"], owner)
            self.assertEqual(set(environment), expected)

    def test_the_grader_sees_neutral_names_only(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as folder:
            ref = Path(source) / "closeup_king.jpg"
            built = Path(source) / "parallel" / "angelo-trial-02" / "render-closeup_king.png"
            built.parent.mkdir(parents=True)
            ref.write_bytes(jpeg(b"/runs/single/angelo-trial-01/ref.jpg"))
            built.write_bytes(png(b"/runs/parallel/angelo-trial-02/render.png"))
            prompt, keys = grade.grade_prompt({"part": "King", "what": "a king", "layout": None,
                                               "references": [ref], "built": [built]}, Path(folder))
            copies = b"".join(path.read_bytes() for path in Path(folder).iterdir())
        for word in (b"parallel", b"single", b"trial", b"tEXt", b"Exif"):   # Not in the pictures either.
            self.assertNotIn(word, copies)
        self.assertIn(b"IDAT", copies)
        self.assertIn(b"scan", copies)
        self.assertNotIn("parallel", prompt)
        self.assertNotIn("single", prompt)
        self.assertNotIn("trial", prompt)
        self.assertIn("reference-1.jpg", prompt)
        self.assertIn("built-1.png", prompt)
        self.assertEqual(keys, ["recognisable", "proportions", "materials"])

    def test_an_unreadable_render_leaves_only_that_item_unscored(self):
        with tempfile.TemporaryDirectory() as run:
            good = Path(run) / "good.png"
            good.write_bytes(png(b"x"))
            bad = Path(run) / "empty.png"
            bad.write_bytes(b"")
            (Path(run) / "interleave.json").write_text(json.dumps({"task_set": "oneshot-chess"}))
            queue = [{"arm": arm, "trial": 1, "part": "King", "what": "a king", "layout": None, "references": [good],
                      "built": [good if arm == "single" else bad]} for arm in ("single", "parallel")]
            answer = '{"recognisable": 3, "proportions": 3, "materials": 3}'
            with mock.patch.object(grade, "items", lambda run_folder, test: queue), \
                    mock.patch.object(grade, "ask", lambda *args: answer):
                code = grade.main(["--authorize-billed-runs", "--run", run, "--claude-config-dir", run])
            grades = {g["arm"]: g for g in json.loads((Path(run) / "grades.json").read_text())["grades"]}
        self.assertEqual(code, 1)   # Not every item scored: said so.
        with tempfile.TemporaryDirectory() as run:   # Any failure of one item, not only an unreadable file.
            good = Path(run) / "good.png"
            good.write_bytes(png(b"x"))
            (Path(run) / "interleave.json").write_text(json.dumps({"task_set": "oneshot-chess"}))
            queue = [{"arm": arm, "trial": 1, "part": "King", "what": "a king", "layout": None, "references": [good],
                      "built": [good]} for arm in ("single", "parallel")]
            asked = []

            def ask(*args):
                asked.append(1)
                if len(asked) == 1:
                    raise AttributeError("'list' object has no attribute 'get'")
                return '{"recognisable": 2, "proportions": 2, "materials": 2}'
            with mock.patch.object(grade, "items", lambda run_folder, test: queue), mock.patch.object(grade, "ask", ask):
                grade.main(["--authorize-billed-runs", "--run", run, "--claude-config-dir", run])
            scored = [g["score"] for g in json.loads((Path(run) / "grades.json").read_text())["grades"]]
        self.assertEqual(sorted(scored, key=lambda v: v is None), [2.0, None])
        self.assertEqual(grades["single"]["score"], 3.0)
        self.assertIsNone(grades["parallel"]["score"])
        self.assertIn("error", grades["parallel"])

    def test_only_the_asked_scores_are_an_answer(self):
        keys = ["recognisable", "proportions", "materials"]
        self.assertEqual(grade.parse_answer('{"recognisable": 3, "proportions": 2, "materials": 4, "notes": "ok"}',
                                            keys)["materials"], 4)
        for bad in ("", "no json", '{"recognisable": 5, "proportions": 2, "materials": 4}',
                    '{"recognisable": 3, "proportions": 2}', '{"recognisable": 3.5, "proportions": 2, "materials": 1}'):
            with self.subTest(bad=bad):
                self.assertIsNone(grade.parse_answer(bad, keys))

    def test_every_parallel_test_has_grade_classes_whose_views_are_its_cameras(self):
        import scenario_specs as specs
        for name in specs.ONESHOT_PARALLEL:
            module = specs.module("oneshot_" + name)
            with self.subTest(test=module.ID):
                self.assertTrue(module.GRADE_CLASSES)
                for part, spec in module.GRADE_CLASSES.items():
                    self.assertTrue(set(spec["views"]) <= set(module.CAMERAS), part)
                    self.assertTrue(spec["what"])


class DriverArmTests(unittest.TestCase):
    def test_off_every_hook_is_the_single_arms_behaviour(self):
        with mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "0"}):
            import importlib
            import eval_parallel
            module = importlib.reload(eval_parallel)
        self.assertIsNone(module.decide("angelo_workers_start"))
        self.assertTrue(module.settled(None, None))
        record = {}
        module.receipts(None, None, None, record)
        self.assertEqual(record, {})
        self.assertEqual(module.totals({"provider_usage": {"input_tokens": 1}}, record), {"provider_usage": {"input_tokens": 1}})
        self.assertEqual(module.environment(mock.Mock(parallel_agents=False)),
                         {"ANGELO_ENABLE_PARALLEL_AGENTS": "0", "ANGELO_PARALLEL_WORKER_CAP": ""})

    def test_on_the_tester_approves_only_the_leads_worker_cards_and_usage_is_every_chats(self):
        import importlib
        import eval_parallel
        on = mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"})   # The arm's Angelo, throughout.
        on.start()
        module = importlib.reload(eval_parallel)
        try:
            self.assertEqual(module.decide("angelo_workers_start"), "approve")
            self.assertIsNone(module.decide("angelo_execute_python"))
            summary = module.totals({"provider_usage": {"input_tokens": 1}}, {"usage_all_chats": {"input_tokens": 9}})
            self.assertEqual((summary["provider_usage"], summary["provider_usage_lead"]),
                             ({"input_tokens": 9}, {"input_tokens": 1}))
        finally:
            on.stop()
            with mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "0"}):
                importlib.reload(eval_parallel)


class EveryChatTests(unittest.TestCase):
    """The parallel arm's usage and its provider-limit exclusion cover every chat of the trial."""

    def setUp(self):
        import importlib
        import eval_parallel
        on = mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"})   # The arm's Angelo, throughout.
        on.start()
        self.module = importlib.reload(eval_parallel)
        self.addCleanup(lambda: (on.stop(), importlib.reload(eval_parallel)))
        self.marker = {"session_id": "old"}
        row = lambda session, tokens: {"session_id": session, "usage": {"input_tokens": tokens},  # noqa: E731
                                       "counts": {"provider_turn_requests": 1}}
        store = mock.Mock(receipts=[row("lead", 5), self.marker, row("lead", 100), row("pawn", 300),
                                    row("knight", 250), row("someone-else", 9)])
        self.lead = mock.Mock(state={"id": "lead", "messages": []}, telemetry=store, busy=False, _worker=None)
        self.workers = [mock.Mock(state={"id": "pawn", "messages": []}), mock.Mock(state={"id": "knight",
                                                                                          "messages": []})]

    def test_every_chats_usage_is_summed_from_the_shared_store(self):
        record = {}
        from angelo_agent import worker_sessions
        with mock.patch.object(self.module, "workers", lambda ui, lead: self.workers), \
                mock.patch.object(worker_sessions, "finished", lambda session: session != "knight"):
            self.module.receipts(None, self.lead, self.marker, record)
        self.assertEqual(record["accounting_partial"], [""])   # Knight still ending its turn: said (no tile name here).
        self.assertEqual(record["usage_all_chats"], {"input_tokens": 650})
        self.assertEqual([w["requests"] for w in record["workers"]], [1, 1])

    def test_a_worker_works_once_it_sent_a_request_or_is_answering(self):
        """Rule 4's count (break-it E2E-7 L-6, E2E-8 L-5): a receipt that counts a request, or a reply, thought or
        activity in a first turn still running at the cap (no receipt yet); a worker whose brief failed has neither."""
        from angelo_agent import worker_sessions
        answering = mock.Mock(busy=True, state={"id": "rook", "messages": [{"role": "user", "text": "brief"},
                                                                 {"role": "thinking", "text": "..."}]})
        silent = mock.Mock(busy=False, state={"id": "bishop", "messages": [{"role": "user", "text": "brief"}]})
        record = {}
        with mock.patch.object(self.module, "workers", lambda ui, lead: self.workers + [answering, silent]), \
                mock.patch.object(worker_sessions, "finished", lambda session: True):
            self.module.receipts(None, self.lead, self.marker, record)
        self.assertEqual((record["parallel"]["workers"], record["parallel"]["working"]), (4, 3))

    def test_only_this_tasks_work_counts(self):
        """An idle worker from an earlier task with no receipt since the send is not working now (E2E-9 L-6)."""
        from angelo_agent import worker_sessions
        earlier = mock.Mock(busy=False, state={"id": "queen", "messages": [{"role": "assistant", "text": "done"}]})
        record = {}
        with mock.patch.object(self.module, "workers", lambda ui, lead: self.workers + [earlier]), \
                mock.patch.object(worker_sessions, "finished", lambda session: True):
            self.module.receipts(None, self.lead, self.marker, record)
        self.assertEqual((record["parallel"]["workers"], record["parallel"]["working"]), (3, 2))

    def test_a_lead_still_ending_its_turn_is_named_as_partial(self):
        """Its receipt is not written yet, so the workers' tokens alone are a lower bound (break-it E2E-6 L-5)."""
        self.lead.busy, record = True, {}
        from angelo_agent import worker_sessions
        with mock.patch.object(self.module, "workers", lambda ui, lead: self.workers), \
                mock.patch.object(worker_sessions, "finished", lambda session: True):
            self.module.receipts(None, self.lead, self.marker, record)
        self.assertEqual(record["accounting_partial"], ["the lead"])

    def test_a_stop_cancels_only_its_leads_launching_workers(self):
        """A worker still launching at the cap is never briefed after it (break-it E2E-6 L-6)."""
        import threading
        from angelo_agent import worker_sessions
        mine = {"lead": self.lead, "done": threading.Event(), "worker": mock.Mock()}
        theirs = {"lead": mock.Mock(), "done": threading.Event(), "worker": mock.Mock()}
        import angelo_agent
        with mock.patch.object(worker_sessions, "_launching", [mine, theirs]), \
                mock.patch.object(self.module, "workers", lambda ui, lead: self.workers), \
                mock.patch.dict(sys.modules, {"angelo_agent.ui": mock.Mock()}), \
                mock.patch.object(angelo_agent, "ui", mock.Mock(), create=True):   # The add-on's ui needs bpy.
            self.module.stop(self.lead)
            self.assertEqual(worker_sessions._launching, [theirs])
        mine["done"].set()   # It answers: stopped from its own thread, never bound.
        for _ in range(200):
            if mine["worker"].stop.called:
                break
            threading.Event().wait(0.01)
        mine["worker"].stop.assert_called_once()
        theirs["worker"].stop.assert_not_called()
        self.assertTrue(all(chat.stop.called for chat in self.workers))

    def test_a_workers_provider_limit_makes_the_trial_invalid_too(self):
        import eval_provider_limits as limits
        self.workers[1].state["messages"] = [{"role": "error", "text": "rate_limit_error: 429"}]
        with mock.patch.object(self.module, "workers", lambda ui, lead: self.workers):
            found = self.module.limit(limits, None, self.lead, self.marker, {"receipt": None}, 0)
        self.assertTrue(found)
        self.assertIn("worker", found)


class AsShippedTests(unittest.TestCase):
    """The gates of record run parallel edit as shipped: the app's own check decides, a worker-start card is declined
    (the preset rule, every side) and the trial counts, and the workers of a lead that starts them are waited for and
    priced like the lead's own work."""

    def test_the_switch_is_left_to_the_app_and_never_inherited(self):
        import argparse
        import eval_parallel
        parser = argparse.ArgumentParser()
        eval_parallel.add_arguments(parser)
        self.assertEqual(eval_parallel.environment(parser.parse_args(["--parallel-as-shipped"])),
                         {"ANGELO_ENABLE_PARALLEL_AGENTS": "", "ANGELO_PARALLEL_WORKER_CAP": ""})   # The machine decides.
        self.assertEqual(eval_parallel.environment(parser.parse_args([])),
                         {"ANGELO_ENABLE_PARALLEL_AGENTS": "0", "ANGELO_PARALLEL_WORKER_CAP": ""})
        with self.assertRaises(SystemExit):
            parser.parse_args(["--parallel-agents", "--parallel-as-shipped"])

    def test_a_worker_start_card_is_declined_in_a_gate_and_its_workers_are_still_waited_for(self):
        import importlib
        import eval_live_tasks as live
        import eval_parallel
        from angelo_agent import parallel_access
        with mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": ""}):
            module = importlib.reload(eval_parallel)
        self.addCleanup(importlib.reload, eval_parallel)
        with mock.patch.object(parallel_access, "enabled", lambda: True):   # A capable gate VM: the family is on.
            self.assertIsNone(module.decide("angelo_workers_start"))    # Not the bench arm: no automatic approval ...
            self.assertEqual(live.approval_decision("angelo_workers_start"), "decline")   # ... the preset rule.
            self.assertTrue(module.on())
            from angelo_agent import worker_sessions
            lead = mock.Mock(state={"id": "lead"})
            with mock.patch.object(worker_sessions, "workers_of", lambda lead_id: ["w"]), \
                    mock.patch.object(worker_sessions, "finished", lambda session: False):
                self.assertFalse(module.settled(None, lead))   # The gate trial waits for the worker it started.


class CacheTests(unittest.TestCase):
    def test_each_threads_first_call_and_later_share_come_from_its_rollout(self):
        with tempfile.TemporaryDirectory() as folder:
            def rollout(name, calls):
                lines = [json.dumps({"type": "token_usage_record", "payload": {"usage": {
                    "input_tokens": total, "cached_input_tokens": cached}}}) for cached, total in calls]
                (Path(folder) / ("rollout-2026-10-25T01-00-0%s-%s.jsonl" % (name, "0" * 35 + name))).write_text(
                    "\n".join(lines))
            rollout("1", [(0, 1000), (900, 1000), (950, 1000)])    # The lead: cold first, warm later.
            rollout("2", [(800, 1000)])                             # A worker with one call.
            lead, worker = report.thread_cache(folder)
        self.assertEqual((lead["first_call_cache_read"], lead["calls_2plus_share"], lead["calls"]), (0.0, 0.925, 3))
        self.assertEqual((worker["first_call_cache_read"], worker["calls_2plus_share"]), (0.8, None))


class MainLoopTests(unittest.TestCase):
    def test_lateness_is_measured_between_the_watchers_own_polls(self):
        import eval_receipts
        watch = eval_receipts.ProgressWatch(0.0)
        self.assertEqual(watch.main_loop(), {})
        watch._late = [0.0] * 90 + [0.01] * 9 + [0.2]   # 100 polls: one 200 ms stall, nine 10 ms late.
        loop = watch.main_loop()
        self.assertEqual((loop["main_loop_late_ms_p50"], loop["main_loop_late_ms_max"], loop["main_loop_polls"],
                          loop["main_loop_late_over_33ms"]), (0.0, 200.0, 100, 1))
        self.assertEqual(loop["main_loop_late_ms_p95"], 10.0)

    def test_each_poll_records_when_its_stall_ended_and_the_longest_are_kept_with_their_times(self):
        # Measurement only (BENCH-PREREG instrumentation note): which work a long stall belongs to.
        import eval_receipts
        from unittest import mock
        watch = eval_receipts.ProgressWatch(100.0)
        clock = iter([100.5, 100.52, 101.25, 101.27, 103.0])   # Polls: a 0.71 s stall ends at 1.25 s, 1.71 s at 3.0 s.
        with mock.patch.object(eval_receipts.time, "monotonic", lambda: next(clock)):
            for _ in range(5):
                watch._poll()
        loop = watch.main_loop()
        self.assertEqual(loop["main_loop_stalls"][:2], [[3000.0, 1710.0], [1250.0, 710.0]])
        self.assertEqual(loop["main_loop_late_ms_max"], 1710.0)
        self.assertEqual(len(loop["main_loop_stalls"]), 4)
        watch._late, watch._late_at = [0.001 * i for i in range(30)], [float(i) for i in range(30)]
        self.assertEqual(len(watch.main_loop()["main_loop_stalls"]), eval_receipts.ProgressWatch.STALLS)


def sent(arm, task_set="oneshot-chess"):   # A trial's task records as the harness stamps them (Amendment F).
    import bench_integrity, eval_live_tasks, eval_parallel  # noqa: E401
    with mock.patch.object(eval_parallel, "ENABLED", arm == "parallel"):
        tasks = eval_parallel.split_request(eval_live_tasks.tasks_for(task_set))
    return [dict(bench_integrity.trial_stamp(task), task=task["id"]) for task in tasks]


class VerdictTests(unittest.TestCase):
    def task(self, arm, wall, loop, workers):
        """One task of an eval trial report, as eval_live_agent_session records it."""
        row = {"task": "oneshot-chess", "kind": "scenario", "status": "completed" if wall is not None else "timeout",
               "main_loop_late_ms_p95": loop[arm][0], "main_loop_late_ms_max": loop[arm][1], "approved": []}
        if wall is not None:
            row["send_to_idle_ms"] = wall * 1000
        if arm == "parallel" and workers is not None:   # eval_parallel.receipts, in the arm that has it on.
            row["parallel"] = {"workers": workers, "working": workers, "requests": workers + 1}
        return row

    def run_folder(self, walls, scores, loop=None, workers=2, trials=None, task_set="oneshot-chess"):
        """A run as the bench, the runner and the grader leave it: the plan's trial count (the longest arm's, unless
        ``trials``), a score for every grade class, 3 unless ``scores`` names it (audit P1), and its message (F)."""
        folder = Path(tempfile.mkdtemp())
        classes = report.expected(self.plan(folder, trials or max(len(w) for w in walls.values()), task_set))[1]
        scores = {arm: dict({part: [3] * len(walls[arm]) for part in classes}, **scores[arm]) for arm in report.ARMS}
        loop = loop or {"single": (10.0, 50.0), "parallel": (10.0, 50.0)}
        for arm in report.ARMS:
            (folder / arm).mkdir()
            records = []
            for n, wall in enumerate(walls[arm]):   # The bench's own normalizer, then its pricing's one field.
                record, = bench_mcp_report.angelo_records({"tasks": [self.task(arm, wall, loop, workers)]}, n + 1, {})
                records.append(dict(record, list_price_usd=1.0 if wall is not None else None))
            (folder / arm / report.RESULTS).write_text(json.dumps({"records": records}))
            for t in (folder / arm / ("angelo-trial-%02d" % (n + 1)) for n in range(len(walls[arm]))):
                (t / "scenario-capture").mkdir(parents=True)   # The end-of-session capture the grader reads.
                (t / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": sent(arm, task_set)}]}))
            sides = [{"side": "angelo", "trial": n + 1, "checks": {"passed": 1, "total": 1},
                      "saved_scene_checks": {"passed": 70 + n, "total": 74}} for n in range(len(walls[arm]))]
            (folder / arm / report.SCORECARD).write_text(json.dumps({"sides": sides}))
        grades = [{"arm": arm, "trial": n + 1, "part": part, "score": value}
                  for arm in report.ARMS for part, values in scores[arm].items() for n, value in enumerate(values)]
        (folder / "grades.json").write_text(json.dumps({"grades": grades}))
        return folder

    @staticmethod
    def crash(folder, arm, trial, measured=False):
        """``trial`` crashed after its message: its task record stays; its capture, grades and main loop never came."""
        import shutil
        shutil.rmtree(folder / arm / ("angelo-trial-%02d" % trial) / "scenario-capture")
        for name, rows, keep in ((report.RESULTS, "records", None), ("grades.json", "grades", (arm, trial))):
            path = folder / (arm if name == report.RESULTS else "") / name
            value = json.loads(path.read_text())
            if keep:
                value[rows] = [g for g in value[rows] if (g["arm"], g["trial"]) != keep]
            for record in value[rows] if not keep else ():
                if record["trial"] == trial:
                    record.update(status="error")
                    record.pop("main_loop", None)
                    record.pop("send_to_idle_ms", None) if not measured else None
            path.write_text(json.dumps(value))
        return folder

    def test_a_crash_after_the_message_is_a_failed_trial_of_its_arm_never_incomplete(self):
        # BENCH-PREREG Amendment C (the maintainer's "Failed trial", break-it E2E-15 L-4), the same for both arms.
        walls, same = {"single": [600, 620, 640], "parallel": [300, 320, 350]}, {"King": [3, 3, 3]}
        folder = self.crash(self.run_folder(walls, {"single": same, "parallel": same}), "parallel", 3)
        self.assertIsNone(grade.capture_of(folder / "parallel" / "angelo-trial-03"))   # The grader makes no item.
        result = report.verdict(folder)
        self.assertEqual((result["complete"], result["crashed"]), (True, {"single": [], "parallel": [3]}))
        self.assertEqual(result["trials_with_a_broken_piece"], {"single": [], "parallel": [3]})
        self.assertFalse(result["ships"])
        self.assertEqual((result["arms"]["parallel"][2]["wall_s"], result["arms"]["parallel"][2]["wall_capped"]),
                         (3600.0, True))
        self.assertIn("Crashed, not captured", report.markdown(result))
        self.assertNotIn("INCOMPLETE", report.markdown(result))
        single = report.verdict(self.crash(self.run_folder(walls, {"single": same, "parallel": same}), "single", 3))
        self.assertEqual(single["trials_with_a_broken_piece"], {"single": [3], "parallel": []})
        self.assertEqual((single["summary"]["single"]["wall_max_s"], single["complete"], single["ships"]),
                         (3600.0, True, True))
        measured = report.verdict(self.crash(self.run_folder(walls, {"single": same, "parallel": same}), "parallel",
                                             3, measured=True))   # A wall its record measured stays.
        self.assertEqual((measured["arms"]["parallel"][2]["wall_s"], measured["arms"]["parallel"][2]["wall_capped"]),
                         (350.0, False))

    def test_two_crashes_never_ship_the_one_that_crashed_sooner(self):
        both = self.run_folder({"single": [600], "parallel": [30]}, {"single": {}, "parallel": {}})
        result = report.verdict(self.crash(self.crash(both, "single", 1), "parallel", 1))
        self.assertEqual((result["complete"], result["trials_with_a_broken_piece"]),
                         (True, {"single": [1], "parallel": [1]}))
        self.assertEqual((result["faster"], result["ship_rule_holds"], result["ships"]), (False, False, False))

    @staticmethod
    def plan(folder, trials, task_set="oneshot-chess"):
        (folder / "interleave.json").write_text(json.dumps({"task_set": task_set, "trials": trials}))
        return folder

    def test_parallel_ships_only_faster_and_never_worse_on_any_class(self):
        walls = {"single": [600, 620, 640], "parallel": [300, 320, 350]}
        same = {"King": [3, 3, 3], "Board": [4, 4, 4]}
        result = report.verdict(self.run_folder(walls, {"single": same, "parallel": same}))
        self.assertTrue(result["ships"])
        self.assertEqual(result["speedup_median"], round(620 / 320, 2))
        worse = report.verdict(self.run_folder(walls, {"single": same, "parallel": {"King": [2, 3, 2], "Board": [4, 4, 4]}}))
        self.assertFalse(worse["ships"])
        self.assertEqual(worse["quality_misses"], ["King"])
        broken = report.verdict(self.run_folder(walls, {"single": same, "parallel": {"King": [3, 4, 1], "Board": [4, 4, 4]}}))
        self.assertEqual(broken["quality_misses"], [])     # The medians hold ...
        self.assertFalse(broken["ships"])                  # ... but a broken piece the single arm never had does not.
        self.assertEqual(broken["trials_with_a_broken_piece"], {"single": [], "parallel": [3]})
        slower = report.verdict(self.run_folder({"single": [300], "parallel": [400]},
                                                {"single": {"King": [3]}, "parallel": {"King": [4]}}))
        self.assertFalse(slower["ships"])

    def test_a_trial_stopped_at_the_cap_counts_as_the_cap(self):
        result = report.verdict(self.run_folder({"single": [None], "parallel": [2400]},
                                                {"single": {"King": [3]}, "parallel": {"King": [3]}}))
        self.assertEqual(result["summary"]["single"]["wall_median_s"], 3600.0)
        self.assertTrue(result["arms"]["single"][0]["wall_capped"])
        self.assertTrue(result["ships"])
        self.assertIn("(cap)", report.markdown(result))

    def test_the_report_names_unscored_items_partial_accounting_and_every_worker_card(self):
        folder = self.run_folder({"single": [600], "parallel": [300]}, {"single": {"King": [3]}, "parallel": {"King": [None]}})
        records = json.loads((folder / "parallel" / report.RESULTS).read_text())
        records["records"][0].update(accounting_partial=["Pawn"], declined=["worker:angelo_create_cube"])
        (folder / "parallel" / report.RESULTS).write_text(json.dumps(records))
        result = report.verdict(folder)
        text = report.markdown(result)
        self.assertEqual((result["summary"]["parallel"]["cost_median_usd"],   # A lower bound, never in the median.
                          result["summary"]["parallel"]["cost_lower_bounds_usd"]), (None, [1.0]))
        self.assertIn("parallel King trial 1", text)
        self.assertIn("parallel 1 (Pawn)", text)
        self.assertRegex(text, r"Worker cards the tester answered .*: parallel 1")

    def test_worse_or_unmeasured_smoothness_is_a_blocker(self):
        same = {"single": {"King": [3]}, "parallel": {"King": [3]}}
        worse = report.verdict(self.run_folder({"single": [600], "parallel": [300]}, same,
                                               {"single": (10.0, 50.0), "parallel": (12.0, 50.0)}))
        self.assertTrue(worse["ship_rule_holds"])
        self.assertFalse(worse["ships"])
        self.assertEqual(worse["smoothness_blocker"], ["main_loop_p95_median_ms"])
        self.assertIn("BLOCKER", report.markdown(worse))
        unmeasured = report.verdict(self.run_folder({"single": [600], "parallel": [300]}, same,
                                                    {"single": (10.0, 50.0), "parallel": (None, None)}))
        self.assertFalse(unmeasured["ships"])

    def test_a_parallel_trial_without_a_worker_is_an_invalid_treatment(self):
        """BENCH-PREREG Amendment A rule 4: named, never counted as a parallel result, not re-run; no ship."""
        same = {"single": {"King": [3]}, "parallel": {"King": [3]}}
        for workers in (0, None):   # None: the record does not say, never read as a split.
            with self.subTest(workers=workers):
                result = report.verdict(self.run_folder({"single": [600], "parallel": [300]}, same, workers=workers))
                self.assertEqual((result["treatment_applied"], result["exercised"], result["ships"]),
                                 (False, False, False))
                self.assertEqual(result["parallel_trials_without_a_worker"], [1])
                self.assertEqual(result["summary"]["parallel"]["valid_trials"], 0)   # Never a parallel result.
                self.assertFalse(result["faster"])
                text = report.markdown(result)
                self.assertIn("TREATMENT NOT APPLIED: does not ship", text)
                self.assertIn("invalid treatment in parallel trial 1", text)
                self.assertTrue(text.startswith("# With parallel edit in use (the person asks to split the work)"))
        self.assertTrue(report.verdict(self.run_folder({"single": [600], "parallel": [300]}, same, workers=1))["ships"])

    def test_the_verdict_reads_what_the_bench_writes(self):
        import bench_mcp_vs_angelo
        self.assertEqual(report.RESULTS, bench_mcp_vs_angelo.RESULTS)
        self.assertEqual(report.SCORECARD, oneshot_report.SCORECARD)
        import inspect   # The saved scene's closed checks: the end-of-session ones, not the in-session host check.
        self.assertIn("saved_scene_checks=(card_trial or {}).get(\"end_of_session_checks\")",
                      inspect.getsource(oneshot_report.side_result))
        result = report.verdict(self.run_folder({"single": [600, 610], "parallel": [300, 310]},
                                                {"single": {"King": [3, 3]}, "parallel": {"King": [3, 3]}}, workers=3))
        self.assertEqual([(r["checks_passed"], r["checks_total"]) for r in result["arms"]["parallel"]],
                         [(70, 74), (71, 74)])
        self.assertEqual([r["workers_started"] for r in result["arms"]["parallel"]], [3, 3])
        text = report.markdown(result)   # Each arm's counts side by side (report-format note, 2026-10-25).
        self.assertIn("Closed checks on the saved scene, per arm", text)
        self.assertRegex(text, r"\| 1 \| [^|]+ \| 70/74 \|\n\| 2 \| [^|]+ \| 71/74 \|")
        self.assertEqual([r["workers_started"] for r in result["arms"]["single"]], [None, None])
        record, = bench_mcp_report.angelo_records({"tasks": [dict(self.task("parallel", 300, {"parallel": (1, 2)}, 2),
                                                                  invalid="provider usage limit",
                                                                  accounting_partial=["Pawn"],
                                                                  provider_usage_lead={"input_tokens": 9})]}, 1, {})
        self.assertEqual((record["invalid"], record["accounting_partial"], record["provider_usage_lead"]),
                         ("provider usage limit", ["Pawn"], {"input_tokens": 9}))


class ClosedChecksTests(unittest.TestCase):
    def test_each_arm_s_checks_side_by_side_and_which_failed(self):
        import bench_parallel_notes
        row = lambda trial, passed, failed: {"trial": trial, "checks_passed": passed, "checks_total": 297,   # noqa: E731
                                             "checks_failed": failed}
        lines = bench_parallel_notes.closed_checks({"arms": {
            "single": [row(1, 297, [])], "parallel": [row(1, 281, ["brief: a chair on four legs"]), row(2, 296, [])]}})
        self.assertIn("| trial | single | parallel |", lines)
        self.assertIn("| 1 | 297/297 | 281/297 |", lines)
        self.assertIn("| 2 | - | 296/297 |", lines)
        self.assertIn("- parallel 1 failed: brief: a chair on four legs", lines)


if __name__ == "__main__":
    unittest.main()
