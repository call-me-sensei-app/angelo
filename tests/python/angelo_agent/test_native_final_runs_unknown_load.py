# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The quiet-machine guard never reads an unreadable machine as quiet (``final_runs_load``).

A process list that could not be read (``ps`` or PowerShell failed, timed
out or printed nothing) and a Windows load with no reading are unknown, not
idle: the guard refuses to start and says why, its wait retries and reports
the reason, and a trial sampled while the list could not be read counts as
contended (unknown), never as uncontended. ``subprocess.run`` is patched;
nothing is started.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import final_runs_load as quiet  # noqa: E402

UNREADABLE = "the process list could not be read"


def failing(*args, **kwargs):
    raise subprocess.TimeoutExpired(args[0] if args else "ps", 30)


class UnknownLoadTests(unittest.TestCase):
    def test_a_failed_process_listing_is_unknown_not_an_empty_machine(self):
        for outcome in (failing, mock.Mock(side_effect=OSError("ps missing")),
                        mock.Mock(return_value=mock.Mock(stdout="", returncode=1))):
            with self.subTest(outcome=outcome), mock.patch.object(quiet.subprocess, "run", outcome):
                self.assertIsNone(quiet.processes("darwin"))
                self.assertIsNone(quiet.processes("win32"))

    def test_the_guard_refuses_to_start_when_the_process_list_cannot_be_read(self):
        with mock.patch.object(quiet.subprocess, "run", failing):
            state = quiet.check(6, averages=[1.0, 1.0, 1.0], platform="darwin")
        self.assertFalse(state["quiet"], "Unknown is never quiet")
        self.assertIn(UNREADABLE, "; ".join(state["reasons"]))
        self.assertIsNone(state["other_cores"])

    def test_the_wait_retries_and_reports_why_then_gives_up(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(quiet.subprocess, "run", failing):
            journal = Path(folder) / "journal.jsonl"
            sleeps = []
            with self.assertRaisesRegex(TimeoutError, UNREADABLE):
                quiet.wait_quiet(6, journal, poll=1, max_wait=0, sleep=sleeps.append,
                                 checker=lambda: quiet.check(6, averages=[1.0, 1.0, 1.0], platform="darwin"))
            paused = json.loads(journal.read_text().splitlines()[0])
        self.assertEqual(paused["event"], "paused")
        self.assertIn(UNREADABLE, "; ".join(paused["reasons"]))

    def test_an_empty_windows_load_reading_is_unknown(self):
        with mock.patch.object(quiet.subprocess, "run", return_value=mock.Mock(stdout="\n", returncode=0)):
            self.assertIsNone(quiet.load("win32"))
        with mock.patch.object(quiet.subprocess, "run", return_value=mock.Mock(stdout="25\n", returncode=1)):
            self.assertIsNone(quiet.load("win32"))

    def test_a_trial_sampled_without_a_process_list_is_contended_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            sampler = quiet.Sampler(Path(folder) / "load.jsonl", threshold=6)
            with mock.patch.object(quiet.subprocess, "run", failing), \
                    mock.patch.object(quiet.os, "getloadavg", return_value=(1.0, 1.0, 1.0)):
                sample = sampler.sample()
            self.assertEqual((sample["heavy"], sample["other_cores"]), (None, None))
            sampler.samples = [{"load": [2.0, 1, 1], "heavy": [], "other_cores": 0.4}, sample]
            summary = sampler.summary()
        self.assertEqual(summary["unreadable_samples"], 1)
        self.assertTrue(summary["contended"], "A trial whose machine could not be read is never uncontended")
        self.assertEqual((summary["contended_samples"], summary["max_other_cores"]), (0, 0.4))


if __name__ == "__main__":
    unittest.main()
