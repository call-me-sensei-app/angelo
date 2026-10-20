# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only checks of the machine factor for absolute timing limits (qa_machine.py, qa_waits.machine_limit)."""

from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qa_machine
import qa_waits


def ran(stdout, code=0):
    return lambda *args, **kwargs: subprocess.CompletedProcess(args, code, stdout=stdout, stderr="")


class MachineLimitTests(unittest.TestCase):
    def test_outside_the_runner_a_limit_is_the_calibrated_one(self):
        self.assertEqual(qa_waits.machine_limit(1.5, environ={}), 1.5)

    def test_a_slower_machine_gets_exactly_its_measured_factor(self):
        self.assertAlmostEqual(qa_waits.machine_limit(1.5, environ={qa_waits.FACTOR_ENV: "4.25"}), 6.375)

    def test_a_faster_machine_keeps_the_calibrated_limit(self):
        self.assertEqual(qa_waits.machine_limit(2.0, environ={qa_waits.FACTOR_ENV: "0.5"}), 2.0)

    def test_a_malformed_factor_is_an_error(self):
        for raw in ("", "zero", "-1", "0", "nan", "inf"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                qa_waits.machine_limit(1.0, environ={qa_waits.FACTOR_ENV: raw})


class FactorTests(unittest.TestCase):
    def test_the_factor_is_the_measured_ratio_never_padded_never_below_one(self):
        self.assertEqual(qa_machine.factor(2.0, reference=0.5), 4.0)
        self.assertEqual(qa_machine.factor(0.3, reference=0.5), 1.0)
        with self.assertRaises(ValueError):
            qa_machine.factor(1.0, reference=0)

    def test_measure_reads_the_workloads_line(self):
        out = 'Blender 5.2.2\nANGELO_QA_REFERENCE {"seconds": 1.25, "runs": [1.2, 1.25, 1.3]}\nBlender quit\n'
        self.assertEqual(qa_machine.measure("Angelo", run=ran(out)), 1.25)

    def test_resolve_off_given_and_measured(self):
        self.assertEqual(qa_machine.resolve("off", "Angelo")["factor"], 1.0)
        self.assertEqual(qa_machine.resolve("3", "Angelo")["factor"], 3.0)
        with mock.patch.object(qa_machine, "REFERENCE_SECONDS", 0.5):
            got = qa_machine.resolve("auto", "Angelo", run=ran('ANGELO_QA_REFERENCE {"seconds": 1.0}\n'))
        self.assertEqual((got["factor"], got["source"], got["reference"]), (2.0, "measured", 0.5))

    def test_a_failed_measurement_keeps_the_strict_calibrated_limits(self):
        with mock.patch.object(qa_machine, "REFERENCE_SECONDS", 0.5):
            got = qa_machine.resolve("auto", "Angelo", run=ran("no result\n", code=1))
        self.assertEqual(got["factor"], 1.0)
        self.assertIn("measurement failed", got["source"])

    def test_the_workload_creates_no_data_block(self):
        source = qa_machine.WORKLOAD.read_text(encoding="utf-8")
        self.assertNotIn("bpy.data", source)
        self.assertNotIn("bpy.ops", source)


if __name__ == "__main__":
    unittest.main()
