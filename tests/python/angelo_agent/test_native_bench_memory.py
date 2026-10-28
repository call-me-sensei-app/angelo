# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bench's memory record of a trial (bench_memory, BENCH-PREREG Amendments B2 and E), without Blender.

Pinned here: only processes of the trial's binary that descend from the bench count (another app of the same binary
does not; a worker whose lead has gone does, once seen); the lead is the one whose parent is no process of the app, a
worker one started as a worker (``--worker``), and any other a helper (a GPU probe, a worker's own Blender for its
views), never counted as a worker (break-it E2E-16 H-1); each one's and the sum's first, last and peak come out in
bytes with the machine, and every sample on the wall clock; growth is each process's peak over its own baseline, the
lead's last sample at or before the send and each worker's first after its ready, both moments recorded by the trial
(Amendment E1, replacing B2's last minus first, E2); the parallel report shows each trial's memory, worker cap and
most workers at once, and the verdict never reads them (E3); and, on macOS, a real child process is found and measured
by the kernel.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import bench_memory
from host_test_policy import environment_skip
import bench_parallel_report as report
import test_native_bench_parallel as verdicts   # Its run fixture; its tests run there.

APP, OTHER = "/Apps/Angelo.app/Contents/MacOS/Angelo", "/usr/bin/python3"


class ChoiceTests(unittest.TestCase):
    def test_only_this_trials_app_processes_count(self):
        rows = {10: (1, OTHER),            # The bench.
                11: (10, OTHER),           # The harness it started.
                12: (11, APP),             # The lead.
                13: (12, APP), 14: (12, APP),   # Its workers.
                20: (1, APP),              # Someone else's Angelo of the same binary.
                21: (20, APP),
                30: (1, APP)}              # A worker whose lead has gone, seen before.
        self.assertEqual(bench_memory.ours(rows, {APP}, 10, set()), {12: 11, 13: 12, 14: 12})
        self.assertEqual(bench_memory.ours(rows, {APP}, 10, {30}), {12: 11, 13: 12, 14: 12, 30: 1})
        self.assertEqual(bench_memory.ours({5: (5, APP)}, {APP}, 10, set()), {})   # A loop ends.
        started = {10: (1, OTHER), 12: (10, APP + " --background --worker --channel fd:7"),
                   13: (10, APP + "-other"), 14: (10, OTHER + " " + APP)}   # Arguments, as ps -o args gives them.
        self.assertEqual(bench_memory.ours(started, {APP}, 10, set()), {12: 10})


class SummaryTests(unittest.TestCase):
    def test_the_lead_each_worker_and_their_sum(self):
        samples = [(0.0, {12: (11, 100, 90)}),
                   (2.0, {12: (11, 150, 140), 13: (12, 50, 40)}),
                   (4.0, {12: (11, 130, 120), 13: (12, 70, 60), 14: (12, 30, 20), 15: (13, 5, 4)}),   # 15: 13's helper.
                   (6.0, {12: (11, 120, 110)})]
        roles = {12: "other", 13: "worker", 14: "worker", 15: "other"}   # 12 the lead; 15 a probe 13 started.
        record = bench_memory.summary(samples, roles=roles)
        self.assertTrue(record["sampled"])
        self.assertEqual([side["pid"] for side in record["lead"]], [12])
        self.assertEqual([side["pid"] for side in record["workers"]], [13, 14])
        lead = record["lead"][0]
        self.assertEqual((lead["resident_first"], lead["resident_last"], lead["resident_peak"], lead["footprint_peak"]),
                         (100, 120, 150, 140))
        self.assertFalse([key for side in record["lead"] + record["workers"] + [record["sum"]] for key in side
                          if key.endswith("_growth")])   # Amendment E2: B2's last minus first is gone.
        self.assertEqual((record["workers"][0]["resident_peak"], record["workers"][0]["samples"]), (70, 2))
        self.assertEqual([(side["pid"], side["parent"]) for side in record["helpers"]], [(15, 13)])
        self.assertEqual((record["sum"]["resident_peak"], record["sum"]["footprint_peak"]), (235, 204))
        self.assertEqual(record["most_workers_at_once"], 2)   # Workers only: a helper is never a worker.
        self.assertEqual(record["added_peak"], {"resident": 105, "footprint": 84})   # 13, 14 and 15 at 4 s.
        lead_probe = bench_memory.summary([(0.0, {12: (11, 100, 90), 16: (12, 50, 40)})], roles={16: "other"})
        self.assertEqual((lead_probe["workers"], [side["pid"] for side in lead_probe["helpers"]],
                          lead_probe["most_workers_at_once"]), ([], [16], 0))   # A lead's own probe: a helper.
        self.assertIn("platform", record["machine"])
        self.assertEqual(bench_memory.summary([]), {"sampled": False, "platform": sys.platform})


class FromSendTests(unittest.TestCase):
    """Amendment E1: the lead's baseline is its last sample at or before the send, each worker's its first after it
    said ready, and each one's peak over it is read from that sample on, RSS and phys_footprint."""

    SAMPLES = [(0.0, {12: (11, 100, 90)}), (2.0, {12: (11, 120, 110)}),
               (4.0, {12: (11, 300, 250), 13: (12, 40, 30)}), (6.0, {12: (11, 280, 260), 13: (12, 90, 70)}),
               (8.0, {12: (11, 200, 150), 13: (12, 60, 50)})]
    TIMES = [1000.0, 1002.0, 1004.0, 1006.0, 1008.0]

    def record(self):
        return dict(bench_memory.summary(self.SAMPLES, roles={13: "worker"}),
                    timeline=bench_memory.timeline(self.SAMPLES, self.TIMES))

    def test_each_process_from_its_own_moment(self):
        record = self.record()
        self.assertEqual(record["timeline"]["13"], [[1004.0, 40, 30], [1006.0, 90, 70], [1008.0, 60, 50]])
        grown = bench_memory.from_send(record, 1002.0, {13: 1004.0})   # A sample at the send itself is its baseline.
        self.assertEqual(grown["lead"], [{"pid": 12, "baseline_at": 1002.0, "resident": 180, "footprint": 150}])
        self.assertEqual(grown["workers"], [{"pid": 13, "baseline_at": 1006.0, "resident": 0, "footprint": 0}])
        later = bench_memory.from_send(record, 1003.9, {13: 1003.0})   # Last at or before; first after.
        self.assertEqual((later["lead"][0]["baseline_at"], later["workers"][0]["baseline_at"],
                          later["workers"][0]["footprint"]), (1002.0, 1004.0, 40))

    def test_a_moment_not_recorded_or_with_no_sample_is_said_never_a_number(self):
        record = self.record()
        grown = bench_memory.from_send(record, None, {})
        self.assertEqual((grown["lead"], grown["workers"]), (
            [{"pid": 12, "why": "its send was not recorded"}], [{"pid": 13, "why": "its ready moment was not recorded"}]))
        early = bench_memory.from_send(record, 999.0, {13: 1009.0})
        self.assertEqual((early["lead"][0]["why"], early["workers"][0]["why"]),
                         ("no sample at or before it", "no sample after it"))

    def test_each_worker_is_stamped_when_it_said_ready_whatever_path_made_it_ready(self):
        """Break-it E2E-19B M-4: the real ``WorkerProcess.start`` launches a worker ahead on a scratch file (Blender
        loads for 3 s), a start takes it for Sofa (a switch), starts it again on Sofa (nothing new) and, warm, reuses
        it for Table. The ready clock stamps each moment it said ready, on the thread that heard it. The process's
        baseline is its first ready, and each worker chat's part has its own moment, kept after its merge stopped it.
        Rebuilt as the launch time plus the last start's duration, the baseline fell inside Blender's load and
        reported 750 MB of growth where the truth is 300 MB."""
        import importlib
        import threading
        import types
        import eval_parallel
        on = mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"})
        on.start()
        module = importlib.reload(eval_parallel)
        self.addCleanup(lambda: (on.stop(), importlib.reload(eval_parallel)))
        from angelo_agent import parallel_sessions, worker_process, worker_routing, worker_sessions
        t0, clock, Path = 1_800_000_000.0, [1_800_000_000.0], bench_memory.Path

        def launch(self, timeout):   # Blender loads for 3 s, then says ready on the part it was launched on.
            self.started_at, part = time.perf_counter(), self.part
            clock[0] += 3.0
            self.process = types.SimpleNamespace(pid=4242, poll=lambda: None)
            self.client, self.ready, self.serving = types.SimpleNamespace(ended=threading.Event()), {"ok": True}, part
            self.ready_seconds = time.perf_counter() - self.started_at
            return self.ready

        def switch(self, timeout):   # A running worker takes another part file in 0.4 s.
            clock[0] += 0.4
            self.serving = self.part
            return self.ready
        sofa, table = Path("/parts/1-sofa/part.blend"), Path("/parts/2-table/part.blend")
        with mock.patch.object(worker_process.WorkerProcess, "_launch", launch), \
                mock.patch.object(worker_process.WorkerProcess, "_switch", switch), \
                mock.patch.object(worker_process, "_live", set()), \
                mock.patch.object(module, "time", types.SimpleNamespace(time=lambda: clock[0])):
            module.begin([])   # The trial's boot installs the clock (once, however often it is asked).
            module.install_ready_clock()
            worker = worker_process.WorkerProcess("/parts/scratch/part.blend")
            worker.start()   # Launched ahead (worker_pool).
            clock[0] += 5.0
            worker.part = sofa   # Taken for Sofa (worker_pool.take): its own start switches it.
            worker.start()
            worker.start()   # Started again on the part it serves: no new moment.
            hows = [worker.how]
            clock[0] += 60.0
            worker.part = table   # Done with Sofa: its warm process takes Table (worker_pool.reuse).
            worker.start()
            hows.append(worker.how)
        self.assertEqual(hows, ["ready", "switched"])
        self.assertEqual(module._ready[4242], [{"part": "/parts/scratch/part.blend", "how": "launched", "at": t0 + 3},
                                               {"part": str(sofa), "how": "switched", "at": t0 + 8.4},
                                               {"part": str(table), "how": "switched", "at": t0 + 68.8}])
        lead = mock.Mock(state={"id": "lead", "messages": []}, pending={}, busy=False, _worker=None,
                         telemetry=mock.Mock(receipts=[]))
        chats = {name: mock.Mock(state={"id": name, "messages": []}, pending={}, busy=False) for name in ("s", "t")}
        with mock.patch.object(module, "workers", lambda ui, chat: list(chats.values())), \
                mock.patch.object(worker_sessions, "finished", lambda session: True), \
                mock.patch.dict(worker_sessions._parts, {"s": (1, "Sofa", sofa), "t": (2, "Table", table)}), \
                mock.patch.dict(parallel_sessions.names, {"s": "Sofa", "t": "Table"}):
            with mock.patch.dict(worker_routing._workers, {"s": worker, "t": worker}):
                module.cards(None, lead)
            record = {}   # Merged and stopped: gone from the add-on's records by the turn's end.
            module.receipts(None, lead, None, record)
        self.assertEqual([(entry["pid"], entry["ready"]["at"], entry["ready"]["how"]) for entry in record["workers"]],
                         [(4242, t0 + 8.4, "switched"), (4242, t0 + 68.8, "switched")])
        self.assertEqual([(entry["pid"], entry["ready_at_unix"]) for entry in record["worker_processes"]],
                         [(4242, t0 + 3)])   # One baseline per process: its first ready.
        mb = 2 ** 20   # 150 MB while Blender loads, 600 MB once ready, 900 MB building Table.
        timeline = [[t0 + 2 * i, 0, (150 if 2 * i < 3 else 600 if 2 * i < 68.8 else 900) * mb] for i in range(40)]
        launch_record = {"memory": {"sampled": True, "lead": [], "workers": [{"pid": 4242, "footprint_peak": 900 * mb}],
                                    "sum": {"footprint_peak": 900 * mb, "resident_peak": 0},
                                    "timeline": {"4242": timeline}}}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "angelo-trial-01-launch.json").write_text(report.json.dumps(launch_record))
            row = bench_memory.trial(folder, 1, record)
        self.assertEqual(row["workers_over_ready"], [{"pid": 4242, "footprint_mb": 300, "resident_mb": 0}])
        source = (Path(bench_memory.__file__).parent / "eval_live_agent_session.py").read_text()
        self.assertIn('self.record["sent_at_unix"] = round(time.time(), 3)', source)   # The lead's baseline (E1).
        self.assertIn("self.tasks = eval_parallel.begin(", source)


class ReportTests(unittest.TestCase):
    def test_each_trials_memory_and_cap_are_reported_and_never_decide_the_verdict(self):
        same = {"King": [3, 3]}
        fixture = verdicts.VerdictTests()
        folder = fixture.run_folder({"single": [600, 620], "parallel": [300, 320]}, {"single": same, "parallel": same})
        before = report.verdict(folder)
        mb = 2 ** 20
        series = lambda *values: [[1000.0 + 2 * n, v * mb, v * mb] for n, v in enumerate(values)]   # noqa: E731
        lead = {"pid": 1, "footprint_peak": 900 * mb, "resident_peak": 950 * mb}
        launch = {"worker_cap": 1, "memory": {
            "sampled": True, "machine": {"model": "Mac16,11"}, "lead": [lead],
            "workers": [{"pid": 2, "footprint_peak": 600 * mb}, {"pid": 3, "footprint_peak": 700 * mb}],
            "most_workers_at_once": 1, "added_peak": {"footprint": 750 * mb, "resident": 800 * mb},
            "sum": {"footprint_peak": 1500 * mb, "resident_peak": 1600 * mb},
            "timeline": {"1": series(500, 600, 900, 700), "2": series(0, 400, 600, 500), "3": series(0, 0, 450, 700)}}}
        task = {"sent_at_unix": 1002.5, "worker_processes": [{"pid": 2, "ready_at_unix": 1001.0},
                                                             {"pid": 3, "ready_at_unix": 1004.5}]}

        def record(trial):   # ``task`` merged into the trial's task record: what it was sent stays (Amendment F).
            path = folder / "parallel" / ("angelo-trial-%02d" % trial) / "eval-live-agent.json"
            stamped = report.json.loads(path.read_text())
            stamped["trials"][0]["tasks"][0].update(task)
            path.write_text(report.json.dumps(stamped))
        for trial in (1, 2):
            (folder / "parallel" / ("angelo-trial-%02d-launch.json" % trial)).write_text(report.json.dumps(launch))
            record(trial)
        result = report.verdict(folder)
        row = result["arms"]["parallel"][0]
        self.assertEqual((row["worker_cap"], row["most_workers_at_once"], row["lead_peak_mb"], row["worker_peak_mb"],
                          row["sum_peak_mb"], row["sum_rss_peak_mb"], row["added_peak_mb"]),
                         (1, 1, 900, 700, 1500, 1600, 750))
        self.assertNotIn("sum_growth_mb", row)   # E2.
        self.assertEqual(row["lead_over_send"], [{"pid": 1, "footprint_mb": 300, "resident_mb": 300}])   # From 600.
        self.assertEqual(row["workers_over_ready"], [{"pid": 2, "footprint_mb": 200, "resident_mb": 200},   # From 400.
                                                     {"pid": 3, "footprint_mb": 0, "resident_mb": 0}])   # From 700.
        self.assertFalse(result["arms"]["single"][0]["memory_sampled"])
        # E3: the verdict never reads it, whatever it says: everything but the per-trial rows is unchanged, and so is
        # every row but its memory.
        memory = {"worker_cap", "memory_sampled", "machine", "lead_peak_mb", "worker_peak_mb", "sum_peak_mb",
                  "sum_rss_peak_mb", "added_peak_mb", "added_rss_peak_mb", "most_workers_at_once", "lead_over_send",
                  "workers_over_ready"}
        decided = lambda r: ({k: v for k, v in r.items() if k != "arms"},   # noqa: E731
                             {arm: [{k: v for k, v in row.items() if k not in memory} for row in rows]
                              for arm, rows in r["arms"].items()})
        self.assertEqual(decided(result), decided(before))
        text = report.markdown(result)
        self.assertIn("single not sampled; parallel sum peak 1500 MB, lead peak 900 MB and 300 MB over the send, "
                      "largest worker peak 700 MB and 200 MB over its ready, workers and their helpers added at most "
                      "750 MB (2 trials)", text)
        (folder / "parallel" / "angelo-trial-01-launch.json").write_text('{"memory": {"sampled": true}}')   # Unreadable.
        broken = report.verdict(folder)   # Reported only: what cannot be read never stops the verdict (E2E-16 M-1).
        self.assertEqual((broken["ships"], broken["arms"]["parallel"][1]["added_peak_mb"]), (before["ships"], 750))
        self.assertEqual(decided(broken)[0], decided(before)[0])
        self.assertIn('Measured on: {"model": "Mac16,11"}', text)
        self.assertIn("| parallel | 1 | True | ", text)
        self.assertIn("(1, None, 1)", text)   # Cap asked, limit applied (not in this fixture), most at once.
        self.assertIn("| 1500; lead +300 (RSS +300); workers +200 (RSS +200), +0 (RSS +0); added 750 |", text)
        task.update(sent_at_unix=None, worker_processes=[{"pid": 2, "ready_at_unix": 1001.0}])   # Unrecorded.
        for trial in (1, 2):
            (folder / "parallel" / ("angelo-trial-%02d-launch.json" % trial)).write_text(report.json.dumps(launch))
            record(trial)
        self.assertIn("| 1500; lead none; workers +200 (RSS +200); added 750 (its send was not recorded; its ready "
                      "moment was not recorded) |", report.markdown(report.verdict(folder)))


@unittest.skipUnless(sys.platform == "darwin", environment_skip("the kernel's numbers are read on macOS only"))
class LiveTests(unittest.TestCase):
    def test_a_patched_subprocess_run_never_sees_the_samplers_calls(self):
        from unittest import mock
        with mock.patch("subprocess.run", side_effect=AssertionError("the sampler used the patched run")) as run:
            sampler = bench_memory.Sampler("/app/Angelo", interval=0.05).start()
            time.sleep(0.3)
            record = sampler.stop()
        self.assertEqual((run.call_count, record["sampled"], record["missed_samples"]), (0, False, 0))

    def test_a_real_child_is_found_and_measured(self):
        sampler = bench_memory.Sampler(sys.executable, interval=0.2).start()
        child = subprocess.Popen([sys.executable, "-c", "import time; x = bytearray(64 << 20); time.sleep(3)"])
        try:
            time.sleep(1.5)
        finally:
            record = sampler.stop()
            child.kill()
            child.wait()
        self.assertTrue(record["sampled"], record)
        self.assertEqual([side["pid"] for side in record["lead"]], [child.pid])
        stamps = [sample[0] for sample in record["timeline"][str(child.pid)]]   # Wall-clock seconds, every sample.
        self.assertTrue(stamps and stamps == sorted(stamps) and time.time() - 30 < stamps[0] <= time.time())
        self.assertEqual(sampler.roles[child.pid], "other")   # Not started with --worker.
        self.assertEqual((bench_memory.role(APP + " --background --python h.py -- --worker --channel fd:7"),
                          bench_memory.role(APP + " --background --python-expr x")), ("worker", "other"))
        self.assertGreater(record["lead"][0]["resident_peak"], 64 << 20)
        self.assertGreater(record["lead"][0]["footprint_peak"], 64 << 20)
        self.assertTrue(record["machine"]["model"])


if __name__ == "__main__":
    unittest.main()
