# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bench's Angelo side imports the checkout's add-on, which must be the app's bundled one (bench_addon_pin).

A merge into the checkout a bench runs from can change host Python under its trials. The bench and each live-eval
run refuse a checkout whose add-on is not the app's (the native suites' guard), re-check before every trial, and
record what each trial imported. No process, billed message or credential: the refusals all come before any
trial.
"""

import contextlib
import inspect
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_addon_pin as pin  # noqa: E402
import native_addon_guard as guard  # noqa: E402
from test_native_addon_guard import ADDON, Fixture, write  # noqa: E402

CONNECTION = "6f1c1f9e-5c43-4f61-9a43-0d3d2b8d7c10"


class PinTests(Fixture):
    def test_a_matching_app_passes_and_a_changed_checkout_is_refused_naming_both(self):
        binary = self.app()
        self.assertIsNone(pin.problem(binary, False, root=self.source))
        write(self.source / "scripts/addons_core/angelo_agent", {"script_kit.py": "MERGED_LATER = 1\n"})
        text = pin.problem(binary, False, root=self.source)
        self.assertIn("the Angelo side imports this checkout's add-on, which is not the app's bundled one", text)
        self.assertIn(str(self.source), text)
        self.assertIn(str(guard.bundled_scripts(binary)), text)
        self.assertIn("addon/script_kit.py: differs", text)

    def test_the_override_warns_and_goes_on(self):
        binary = self.app(addon=dict(ADDON, **{"script_kit.py": "OLD = 1\n"}))
        lines = []
        self.assertIsNone(pin.problem(binary, True, root=self.source, warn=lambda text, **_: lines.append(text)))
        self.assertTrue(lines[0].startswith("WARNING: %s:" % guard.OVERRIDE))

    def test_each_trial_is_stamped_and_a_checkout_changed_mid_run_ends_the_run(self):
        binary = self.app()
        stamp = pin.trial_stamp(binary, False, root=self.source)
        self.assertNotIn("aborted", stamp)
        self.assertEqual(stamp["addon_digest"], guard.source_digest(self.source))
        self.assertEqual(stamp["app_addon_digest"], stamp["addon_digest"])
        self.assertTrue(stamp["matches_app"])
        self.assertEqual(set(stamp), {"addon_digest", "app_addon_digest", "matches_app", "commit",
                                      "uncommitted_addon_changes", "checkout"})
        write(self.source / "scripts/startup", {"angelo_default_layout.py": "LAYOUT = 2\n"})   # A merge mid-run.
        ended = pin.trial_stamp(binary, False, root=self.source)
        self.assertEqual((ended["tasks"], ended["success"]), ([], False))
        self.assertIn("startup/angelo_default_layout.py: differs", ended["aborted"])
        self.assertFalse(ended["addon"]["matches_app"])
        self.assertNotIn("aborted", pin.trial_stamp(binary, True, root=self.source))

    def test_an_mcp_only_stamp_needs_no_app(self):
        stamp = pin.stamp(None, root=self.source)
        self.assertEqual(stamp["addon_digest"], guard.source_digest(self.source))
        self.assertIsNone(stamp["app_addon_digest"])
        self.assertIsNone(stamp["matches_app"])

    def test_the_real_checkout_is_stamped_with_its_commit(self):
        stamp = pin.stamp(None)
        self.assertRegex(stamp["commit"] or "", r"^[0-9a-f]{40}$")
        self.assertIn(stamp["uncommitted_addon_changes"], (True, False))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.binary = self.root / "Angelo"   # An executable that bundles no add-on: never this checkout's.
        self.binary.write_text("#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o755)

    def run_main(self, main, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(argv)
        return code, output.getvalue()

    def test_the_live_eval_refuses_before_any_trial_and_the_override_warns(self):
        import eval_live_agent
        argv = ["--preflight-only", "--connection-id", CONNECTION, "--blender", str(self.binary), "--trials", "1",
                "--output", str(self.root / "out")]
        code, text = self.run_main(eval_live_agent.main, argv)
        self.assertEqual(code, 2)
        self.assertIn("REFUSED: Refused: the Angelo side imports this checkout's add-on", text)
        self.assertFalse((self.root / "out").exists(), "Refused before its output folder")
        code, text = self.run_main(eval_live_agent.main, argv + [guard.OVERRIDE])
        self.assertIn("WARNING: %s:" % guard.OVERRIDE, text)
        self.assertNotIn("the Angelo side imports this checkout's add-on", text)

    def test_the_bench_refuses_before_anything_runs_and_passes_the_override_to_each_trial(self):
        import bench_mcp_vs_angelo as bench
        argv = ["--sides", "angelo", "--angelo-binary", str(self.binary), "--connection-id", CONNECTION,
                "--output", str(self.root / "bench"), "--preflight-only"]
        code, text = self.run_main(bench.main, argv)
        self.assertEqual(code, 2)
        self.assertIn("the Angelo side imports this checkout's add-on", text)
        self.assertFalse((self.root / "bench").exists())
        args = bench.parse(argv + [guard.OVERRIDE])
        args.source_connections_dir = str(self.root)
        command, _workspace = bench.angelo_command(args, self.root / "bench", self.root / "bench" / "angelo-trial-01")
        self.assertIn(guard.OVERRIDE, command)

    def test_every_trial_s_metadata_carries_what_it_ran(self):
        import bench_mcp_vs_angelo as bench
        import eval_live_agent
        self.assertIn('"addon": addon_pin.stamp(', inspect.getsource(bench.run_angelo_trial))
        gather = inspect.getsource(bench.gather)
        self.assertIn('addon=trial_report.get("addon")', gather)
        self.assertIn('"mcp-trial-%02d-source.json"', gather)
        self.assertEqual(inspect.getsource(bench.main).count('"mcp-trial-%02d-source.json"'), 2, "Runs and dry runs")
        self.assertIn("addon_pin.trial_stamp(", inspect.getsource(eval_live_agent.run_trial))
        self.assertIn("addon=pinned", inspect.getsource(eval_live_agent.run_trial))


if __name__ == "__main__":
    unittest.main()
