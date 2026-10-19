# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The session replay driver's script validation and its refusals (no process is started here)."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bench_mcp_replay as replay


def script(folder, messages):
    path = Path(folder) / "messages.json"
    path.write_text(json.dumps(messages), encoding="utf-8")
    return path


class LoadScriptTests(unittest.TestCase):
    def test_a_valid_script_is_returned_in_order(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "render.png"
            image.write_bytes(b"png")
            messages = [{"id": "01-blockout", "stage": "s1", "text": "Block it out.", "stage_end": True},
                        {"id": "02-notes", "stage": "s2", "text": "Two notes.", "attachments": [str(image)]}]
            self.assertEqual(replay.load_script(script(folder, messages)), messages)

    def test_bad_scripts_name_the_first_problem(self):
        with tempfile.TemporaryDirectory() as folder:
            cases = (([], "non-empty"),
                     ([{"id": "a", "stage": "s1", "text": "  "}], "Message 1 lacks text"),
                     ([{"id": "a", "stage": "s1", "text": "x"}, {"id": "a", "stage": "s1", "text": "y"}], "used twice"),
                     ([{"id": "a", "stage": "s1", "text": "x", "attachments": [str(Path(folder) / "none.png")]}],
                      "missing file"))
            for messages, expected in cases:
                with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                    replay.load_script(script(folder, messages))


class RefusalTests(unittest.TestCase):
    def run_main(self, *arguments):
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["bench_mcp_replay.py", *arguments]), contextlib.redirect_stderr(stderr):
            return replay.main(), stderr.getvalue()

    def test_nothing_runs_without_the_billing_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            code, message = self.run_main("--run", str(Path(folder) / "run"), "--script", "none.json",
                                          "--connection-id", "x", "--source-connections-dir", folder,
                                          "--mcp-addon-dir", folder)
            self.assertEqual(code, 2)
            self.assertIn("--authorize-billed-runs", message)
            self.assertFalse((Path(folder) / "run").exists())

    def test_it_never_overlaps_another_billed_run(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(replay, "other_billed_runs", return_value=["python eval_live_agent.py"]):
            code, message = self.run_main("--authorize-billed-runs", "--run", str(Path(folder) / "run"),
                                          "--script", "none.json", "--connection-id", "x",
                                          "--source-connections-dir", folder, "--mcp-addon-dir", folder)
            self.assertEqual(code, 2)
            self.assertIn("another billed run", message)
            self.assertFalse((Path(folder) / "run").exists())


if __name__ == "__main__":
    unittest.main()
