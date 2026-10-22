# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for the never-slower floor driver (bench_floor_launch): sandbox, summary and failure counting."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench_floor_launch as floor  # noqa: E402


def launch(side, mode="headless", kind="first", **values):
    return dict({"side": side, "mode": mode, "kind": kind}, **values)


class SandboxTests(unittest.TestCase):
    def test_every_writable_location_is_inside_the_box_and_nothing_is_inherited(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
                "os.environ", {"BLENDER_USER_CONFIG": "/real", "ANGELO_STATE_DIR": "/real", "PYTHONPATH": "/x"}):
            box = Path(directory)
            env = floor.sandbox(box)
            for key in ("HOME", "CFFIXED_USER_HOME", "TMPDIR", "BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS",
                        "ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR", "ANGELO_LIBRARY_DIR"):
                self.assertTrue(env[key].startswith(directory), key)
            self.assertNotIn("PYTHONPATH", env)
            self.assertEqual((env["ANGELO_DISABLE_KEYCHAIN"], env["ANGELO_AGENT_RUNTIME"]), ("1", "off"))
            self.assertTrue((box / "user/scripts/startup").is_dir())

    def test_the_trace_is_installed_into_the_profile_only(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Path(directory)
            floor.sandbox(box)
            floor.install_trace(box)
            self.assertTrue((box / "user/scripts/startup/floor_addon_trace.py").is_file())
            self.assertTrue((box / "user/scripts/modules/perf_counters.py").is_file())


class SummaryTests(unittest.TestCase):
    def test_ratios_compare_medians_and_failures_are_counted_not_dropped(self):
        rows = [launch("angelo", ready_ms=200, instructions=3000), launch("angelo", ready_ms=220, instructions=3100),
                launch("angelo", error="timed out after 185s"),
                launch("stock", ready_ms=100, instructions=1000), launch("stock", ready_ms=100, instructions=1000)]
        table = floor.summary(rows)
        self.assertEqual(table["angelo/headless/first"]["launches"], 3)
        self.assertEqual(table["angelo/headless/first"]["failed"], 1)
        self.assertEqual(table["angelo/headless/first"]["ready_ms"]["median"], 210)
        self.assertEqual(table["ratio/headless/first"], {"ready_ms": 2.1, "instructions": 3.05})

    def test_no_ratio_without_both_sides(self):
        self.assertNotIn("ratio/window/returning", floor.summary([launch("angelo", "window", "returning", ready_ms=5)]))


class ArgumentTests(unittest.TestCase):
    def test_modes_kinds_and_launch_counts_are_checked(self):
        base = ["--angelo", "a", "--stock", "b", "--out", "c"]
        self.assertEqual(floor.arguments(base).mode_list, ["headless", "window"])
        for extra in (["--modes", "gpu"], ["--kinds", "warm"], ["--launches", "0"]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                floor.arguments(base + extra)


if __name__ == "__main__":
    unittest.main()
