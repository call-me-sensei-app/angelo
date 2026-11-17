# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Instruction counts that drift with the machine are judged side by side, never by an absolute ceiling alone. No
Blender.

Maintainer ruling 2026-11-12, asked how the sculpt-mesh perf limit should be settled: "Side-by-side from now on".
e1d956ec1ef's native_reads/sculpt_mesh read 69,956 against a 66,073 ceiling, while the stamped 1.0.10 binary read
69,959 the same hour. That was no change in Angelo, only the machine's day. ``perf_side_by_side`` applies the rule
the release checks apply over 1.0.11's gate:

* Judged side by side: an instruction-count operation the run failed as ``regressed`` (its ceiling, not its spread),
  given an interleaved ABAB of the stamped baseline (A) and the candidate (B). It must be a ``native_reads/*``
  operation, or one whose ceiling the baseline itself no longer reproduces (mean of A over the ceiling by more than
  the tolerance).
* It passes (``side_by_side``) when perf_abab's pre-registered rule passes and mean(B) / mean(A) is within the
  tolerance. Otherwise it stays ``regressed``: a real regression still fails, side by side or not.
* The stamp sets a passing operation's ceiling to the measured value, the same-session number. It never raises one
  whose B/A regressed, because the gate fails on that one. ``update`` never moves a side-by-side ceiling.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import perf_ratchet  # noqa: E402
from test_native_perf_ratchet import PLATFORM, ceilings, fake_checkout, results  # noqa: E402

READ = "native_reads/sculpt_mesh"


def row_for(rows, name):
    return next(row for row in rows if row["operation"] == name)


class SideBySideTests(unittest.TestCase):
    def compare(self, value, measured, side_by_side, **details):
        return perf_ratchet.compare(value, PLATFORM, measured, details or None, None, side_by_side=side_by_side)

    def test_a_native_read_that_drifts_with_the_machine_passes_when_its_ba_is_clean(self):
        value = ceilings(**{READ: {"ceiling": 66073}})
        rows = self.compare(value, {READ: 69956}, {READ: {"A": [69959, 69950], "B": [69956, 69960]}})
        row = row_for(rows, READ)
        self.assertEqual(row["status"], "side_by_side")
        self.assertEqual(row["ceiling_status"], "regressed")
        self.assertAlmostEqual(row["side_by_side"]["ratio"], 1.00005, places=5)
        self.assertEqual(perf_ratchet.gate_problems(rows), [])

    def test_a_real_regression_still_fails_side_by_side(self):
        value = ceilings(**{READ: {"ceiling": 1000, "tolerance": .05}})
        rows = self.compare(value, {READ: 1200}, {READ: {"A": [1000, 1002], "B": [1200, 1201]}})
        row = row_for(rows, READ)
        self.assertEqual(row["status"], "regressed")
        self.assertIn("more work", row["side_by_side"]["why"])
        self.assertTrue(perf_ratchet.gate_problems(rows)[0].startswith("regressed: " + READ))

    def test_ba_just_over_the_tolerance_fails_and_just_within_passes(self):
        value = ceilings(**{READ: {"ceiling": 1000, "tolerance": .05}})
        over = self.compare(value, {READ: 1100}, {READ: {"A": [1040, 1040], "B": [1093, 1093]}})
        within = self.compare(value, {READ: 1092}, {READ: {"A": [1040, 1040], "B": [1092, 1092]}})
        self.assertEqual(row_for(over, READ)["status"], "regressed")
        self.assertEqual(row_for(within, READ)["status"], "side_by_side")

    def test_another_operation_is_side_by_side_only_when_the_baseline_no_longer_reproduces_its_ceiling(self):
        value = ceilings(kept={"ceiling": 1000, "tolerance": .05}, drifted={"ceiling": 1000, "tolerance": .05})
        rows = self.compare(value, {"kept": 1060, "drifted": 1101},
                            {"kept": {"A": [1040, 1040], "B": [1060, 1060]},          # A reproduces 1000 within 5%.
                             "drifted": {"A": [1100, 1099], "B": [1101, 1100]}})      # A itself is 10% over.
        self.assertEqual(row_for(rows, "kept")["status"], "regressed")
        self.assertIn("reproduces its ceiling", row_for(rows, "kept")["side_by_side"]["why"])
        self.assertEqual(row_for(rows, "drifted")["status"], "side_by_side")

    def test_a_run_without_every_value_stays_regressed(self):
        value = ceilings(**{READ: {"ceiling": 1000}})
        for values in ({"A": [1100, None], "B": [1100, 1100]}, {"A": [1100], "B": [1100, 1100]}, {}):
            with self.subTest(values=values):
                rows = self.compare(value, {READ: 1100}, {READ: values})
                self.assertEqual(row_for(rows, READ)["status"], "regressed")

    def test_no_abab_leaves_the_ceiling_to_decide(self):
        value = ceilings(**{READ: {"ceiling": 1000}})
        self.assertEqual(row_for(self.compare(value, {READ: 1100}, None), READ)["status"], "regressed")
        self.assertEqual(row_for(self.compare(value, {READ: 1000}, {READ: {"A": [1, 1], "B": [9, 9]}}), READ)["status"],
                         "within", "an operation within its ceiling is never judged side by side")

    def test_byte_operations_are_never_side_by_side(self):
        value = ceilings(size={"ceiling": 4096, "unit": "bytes", "tolerance": 0.0})
        rows = self.compare(value, {"size": 4097}, {"size": {"A": [4097, 4097], "B": [4097, 4097]}},
                            units={"size": "bytes"})
        self.assertEqual(row_for(rows, "size")["status"], "regressed")

    def test_candidates_are_the_ceiling_regressions_of_instruction_operations(self):
        value = ceilings(**{READ: {"ceiling": 1000}, "ok": {"ceiling": 1000},
                            "size": {"ceiling": 10, "unit": "bytes", "tolerance": 0.0}})
        rows = perf_ratchet.compare(value, PLATFORM, {READ: 1100, "ok": 1000, "size": 11}, {"units": {"size": "bytes"}})
        self.assertEqual(perf_ratchet.side_by_side_candidates(rows), {READ: .05})


class StampTests(unittest.TestCase):
    def receipt(self, root, measured, abab):
        return dict(results(**measured), suite=perf_ratchet.GATE_SUITE, success=True, commit="abc",
                    binary_sha256="b" * 64, sources_sha256=perf_ratchet.sources_digest(root), abab=abab)

    def test_the_stamp_records_the_same_session_number_of_a_side_by_side_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_checkout(root)
            value = ceilings(**{READ: {"ceiling": 66073}, "op": {"ceiling": 1000}})
            receipt = self.receipt(root, {READ: 69956, "op": 1000},
                                   {READ: {"A": [69959, 69950], "B": [69956, 69960], "verdict": "pass", "tolerance": .05}})
            stamped = perf_ratchet.stamp(value, receipt, root=root, today="2026-11-12")["platforms"][PLATFORM]
            self.assertEqual(stamped["operations"][READ], {"ceiling": 69956})
            self.assertEqual(stamped["operations"]["op"], {"ceiling": 1000})
            self.assertEqual(sorted(stamped["last_measured"]["side_by_side"]), [READ])
            self.assertEqual(stamped["last_measured"]["side_by_side"][READ]["A"], [69959, 69950])

    def test_the_stamp_never_raises_an_operation_whose_ba_regressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_checkout(root)
            value = ceilings(**{READ: {"ceiling": 1000}})
            receipt = self.receipt(root, {READ: 1200}, {READ: {"A": [1000, 1000], "B": [1200, 1200], "verdict": "regressed",
                                                                "tolerance": .05}})
            with self.assertRaisesRegex(perf_ratchet.RatchetError, "regressed: " + READ):
                perf_ratchet.stamp(value, receipt, root=root)

    def test_update_never_moves_a_side_by_side_ceiling(self):
        value = ceilings(**{READ: {"ceiling": 1000}})
        rows = perf_ratchet.compare(value, PLATFORM, {READ: 1100}, None, None,
                                    side_by_side={READ: {"A": [1100, 1100], "B": [1100, 1100]}})
        self.assertEqual(perf_ratchet.update(value, PLATFORM, rows)["platforms"][PLATFORM]["operations"][READ],
                         {"ceiling": 1000})


class GateWiringTests(unittest.TestCase):
    def test_the_gate_runs_the_abab_for_its_ceiling_regressions_and_judges_them_side_by_side(self):
        tree = ast.parse((HERE / "test_blender_perf_ratchet.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertTrue(any(call.func.attr == "side_by_side_candidates" for call in calls),
                        "the gate never asks which ceiling regressions to run side by side")
        compares = [call for call in calls if call.func.attr == "compare"]
        self.assertTrue(any(keyword.arg == "side_by_side" for call in compares for keyword in call.keywords),
                        "the gate never hands its ABAB values to compare")


if __name__ == "__main__":
    unittest.main()
