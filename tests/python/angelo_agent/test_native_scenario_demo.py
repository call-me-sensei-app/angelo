# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The demo recording of scenario runs (``--demo-record``), offline: flags, launch, encoding plan and report.

The in-app recorder needs a window; the dry run proves it end to end
(``bench_mcp_vs_angelo.py --task-set scenario-bridge --dry-run-scenario
--demo-record``). These tests pin the host side: where the flag is accepted,
what it changes in the trial launch, the encoder's reuse and speed, and how
the scorecard and report link the video and poster.
"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402
import scenario_demo as demo  # noqa: E402
import scenario_report as report  # noqa: E402
import scenario_specs as specs  # noqa: E402

try:
    import scenario_scorecard as scorecard  # noqa: E402  (imports NumPy through scenario_scoring)
except ImportError:  # CI installs NumPy (run_host_tests --max-skips 0); Blender bundles it.
    scorecard = None

CONNECTION = "11111111-1111-4111-8111-111111111111"


class FlagTests(unittest.TestCase):
    def test_only_scenario_sets_record(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                bench.parse(["--demo-record"])
            with self.assertRaises(SystemExit):
                eval_live_agent.parse(["--demo-record"])
        plan = bench.plan(bench.parse(["--task-set", "scenario-bridge", "--demo-record", "--connection-id", CONNECTION]))
        self.assertTrue(plan["demo_record"])

    def test_launch_command_and_environment(self):
        args = bench.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--task-set", "scenario-bridge",
                            "--demo-record"])
        args.source_connections_dir = "/nonexistent"
        with tempfile.TemporaryDirectory() as folder, mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            bench.run_angelo_trial(args, Path(folder), 1)
        self.assertIn("--demo-record", run.call_args.args[0])
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            plain = eval_live_agent.trial_environment(folder, folder, eval_live_agent.parse(["--task-set",
                                                                                               "scenario-bridge"]), 1)
            recorded = eval_live_agent.trial_environment(
                folder, folder, eval_live_agent.parse(["--task-set", "scenario-bridge", "--demo-record"]), 1)
        self.assertEqual((plain["ANGELO_EVAL_DEMO_RECORD"], recorded["ANGELO_EVAL_DEMO_RECORD"]), ("", "1"))

    def test_window_is_16_by_9_and_every_scenario_has_a_view(self):
        self.assertEqual(eval_live_agent.demo_geometry(), demo.WINDOW_GEOMETRY)
        width, height = (int(value) for value in demo.WINDOW_GEOMETRY[2:])
        self.assertAlmostEqual(width / height, 16 / 9, places=3)
        for name in specs.SCENARIO_IDS:
            view = demo.demo_view(name)
            self.assertGreater(math.dist(view["location"], view["target"]), 1.0)
        # The island is recorded through its own hero camera, so the stages read as one fixed shot.
        hero = specs.module("island").HERO
        self.assertEqual({key: demo.demo_view("island")[key] for key in ("location", "target", "lens")}, hero)

    def test_recording_never_changes_the_references(self):
        self.assertFalse({"scenario_demo.py", "scenario_demo_job.py"} & set(specs.SHARED_SOURCES))


class EncodeTests(unittest.TestCase):
    def frames(self, folder, index, interval=0.5):
        frames = Path(folder, "demo")
        frames.mkdir(exist_ok=True)
        (frames / "frames.json").write_text(json.dumps({"interval_s": interval, "frames_index": index,
                                                        "errors": ["x"] if not index else []}))
        return frames

    def test_finished_output_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            frames, out = self.frames(folder, [{"file": "frames/f.png"}]), Path(folder, "out")
            out.mkdir()
            for name in ("bridge-angelo-1080p.mp4", "poster-1080p.png"):
                (out / name).write_bytes(b"x")
            with mock.patch("scenario_references.run_job") as job:
                result = demo.encode(frames, out, "bridge")
            job.assert_not_called()
        self.assertEqual((result["speed"], result["hold"], result["size"], result["fps"]), (15.0, 1, [1920, 1080], 30))

    def test_real_time_holds_each_grab(self):
        with tempfile.TemporaryDirectory() as folder:
            frames, out = self.frames(folder, [{"file": "frames/f.png"}]), Path(folder, "out")
            with mock.patch("scenario_references.run_job", return_value=1) as job:
                failed = demo.encode(frames, out, "bridge", speed=1)
            arguments = job.call_args.args[1]
            self.assertEqual(arguments[arguments.index("--hold") + 1], "15")
            self.assertEqual(job.call_args.kwargs["script"].name, "scenario_demo_job.py")
        self.assertIn("encoding failed", failed["error"])

    def test_no_frames_is_reported_not_encoded(self):
        with tempfile.TemporaryDirectory() as folder:
            frames = self.frames(folder, [])
            with mock.patch("scenario_references.run_job") as job:
                result = demo.encode(frames, Path(folder, "out"), "bridge")
            job.assert_not_called()
        self.assertEqual(result, {"error": "no frames were recorded", "errors": ["x"]})


@unittest.skipIf(scorecard is None, "NumPy is installed in CI and bundled with Blender")
class ReportTests(unittest.TestCase):
    def test_recording_is_encoded_flagged_and_linked(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.dict(os.environ, {specs.REFERENCES_ENV: str(Path(root, "references"))}):
            output = Path(root, "run")
            folder = output / "angelo-trial-01"
            frames = folder / "trial-01" / "qa" / "demo"
            (frames / "frames").mkdir(parents=True)
            (frames / "frames.json").write_text(json.dumps({"interval_s": 0.5,
                                                            "frames_index": [{"file": "frames/a.png"}]}))
            rows = [{"task": turn["id"], "status": "completed", "host_check_passed": True, "total_ms": 1000.0}
                    for turn in specs.module("bridge").TURNS]
            (folder / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": rows, "scenario": {
                "undo": {"undo_steps": 9}, "hygiene": {"issues": 0}, "demo": {"frames": 1}}}]}))

            def encode(frames_folder, out, name):
                out.mkdir(parents=True)
                (out / "bridge-angelo-1080p.mp4").write_bytes(b"mp4")
                (out / "poster-1080p.png").write_bytes(b"png")
                return {"video": str(out / "bridge-angelo-1080p.mp4"), "poster": str(out / "poster-1080p.png")}
            with mock.patch.object(demo, "encode", side_effect=encode) as called:
                card = scorecard.collect(output, "scenario-bridge")
            self.assertEqual(called.call_args.args, (frames, folder / "demo", "bridge"))
            self.assertEqual(card["trials"][0]["demo"]["recording"], {"frames": 1})
            report.write(output, card, {"finished_at": "now", "plan": {"model": "claude-opus-5-5", "runtime": "claude",
                                                                       "effort": "low", "demo_record": True}})
            text = (output / "SCENARIO-REPORT.md").read_text()
            page = (output / "scenario-report.html").read_text()
            copied = (output / "report-assets" / "angelo-01-demo-1080p.mp4").read_bytes()
        self.assertIn("timings here are not comparable", text)
        self.assertIn("## Demo recordings", text)
        self.assertIn('<video controls muted playsinline preload="metadata" '
                      'poster="report-assets/angelo-01-poster-1080p.png" src="report-assets/angelo-01-demo-1080p.mp4">',
                      page)
        self.assertEqual(copied, b"mp4")


if __name__ == "__main__":
    unittest.main()
