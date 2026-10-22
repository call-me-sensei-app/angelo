# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for the robust ratchet statistics: paired repeat captures and the ABAB rule (2026-10-21)."""

from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perf_abab  # noqa: E402
import perf_counters  # noqa: E402
import perf_ratchet  # noqa: E402
import snapshot_paired  # noqa: E402

CLEAN = {"pageins": 0, "swapins": 0, "swapouts": 0}


def sample(instructions, **change):
    return dict(CLEAN, instructions=instructions, **change)


class PairedNetTests(unittest.TestCase):
    def test_each_repeat_subtracts_the_mean_of_its_own_references(self):
        entry = snapshot_paired.pair(sample(200), sample(120), sample(140), perf_counters.rejection)
        self.assertEqual((entry["net"], entry["valid"], entry["reasons"]), (70, True, []))

    def test_a_reference_drift_moves_both_sides_and_cancels(self):
        # The 2026-10-21 failure: the same capture work under two kernel costs per byte.
        quiet = [snapshot_paired.pair(sample(75 + 125), sample(125), sample(125), perf_counters.rejection)] * 7
        loaded = [snapshot_paired.pair(sample(75 + 152), sample(151), sample(153), perf_counters.rejection)] * 7
        self.assertEqual(snapshot_paired.statistic(quiet)["median"], snapshot_paired.statistic(loaded)["median"])

    def test_the_retired_statistic_is_reported_unchanged(self):
        self.assertEqual(snapshot_paired.legacy_net([210, 200], [130, 125, 140])["median"], 75)
        self.assertIsNone(snapshot_paired.legacy_net([200, None], [125]))

    def test_a_pair_is_invalid_when_any_of_its_three_samples_is(self):
        reasons = snapshot_paired.pair(sample(200, pageins=2), sample(100), sample(100, swapouts=1),
                                       perf_counters.rejection)["reasons"]
        self.assertEqual(reasons, ["capture pageins", "reference swapouts"])
        missing = snapshot_paired.pair(sample(None), sample(100), sample(100), perf_counters.rejection)
        self.assertEqual((missing["valid"], "net" in missing), (False, False))
        self.assertIn("instructions unavailable", missing["reasons"])

    def test_the_statistic_needs_seven_valid_pairs_and_reports_spread_and_rejections(self):
        pairs = [snapshot_paired.pair(sample(100 + index), sample(50), sample(50), perf_counters.rejection)
                 for index in range(7)]
        pairs.append(snapshot_paired.pair(sample(900, pageins=1), sample(50), sample(50), perf_counters.rejection))
        value = snapshot_paired.statistic(pairs)
        self.assertEqual((value["median"], value["valid"], value["rejected"]), (53, 7, 1))
        self.assertEqual(value["reasons"], {"capture pageins": 1})
        self.assertEqual(value["spread"], round(6 / 53, 5))
        short = snapshot_paired.statistic(pairs[1:])
        self.assertIsNone(short["median"])
        self.assertIsNone(short["spread"])
        self.assertEqual(snapshot_paired.MIN_PAIRS, 7)

    def test_collection_stops_at_enough_valid_pairs_or_the_attempt_cap(self):
        valid = snapshot_paired.pair(sample(10), sample(1), sample(1), perf_counters.rejection)
        invalid = snapshot_paired.pair(sample(10, swapins=1), sample(1), sample(1), perf_counters.rejection)
        self.assertFalse(snapshot_paired.enough([valid] * 6 + [invalid] * 5, 7))
        self.assertTrue(snapshot_paired.enough([valid] * 7, 7))
        self.assertEqual(snapshot_paired.attempts_allowed(7), 21)

    def test_bytes_are_exact_only_when_every_repeat_wrote_the_same_size(self):
        self.assertEqual(snapshot_paired.exact_bytes([5, 5, 5]), {"bytes": 5, "exact": True, "distinct": [5]})
        self.assertFalse(snapshot_paired.exact_bytes([5, 6])["exact"])
        self.assertEqual(snapshot_paired.exact_bytes([]), {"bytes": None, "exact": False})

    def test_mirror_io_writes_the_size_then_syncs_and_removes_its_file(self):
        seen = {}

        def measure(function):
            function()
            seen["sizes"] = [path.stat().st_size for path in folder.iterdir()]
            return None, {"instructions": 1}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.assertEqual(snapshot_paired.mirror_io(folder, 3 * snapshot_paired.WRITE_CHUNK + 7, measure),
                             {"instructions": 1})
            self.assertEqual(seen["sizes"], [3 * snapshot_paired.WRITE_CHUNK + 7])
            self.assertEqual(list(folder.iterdir()), [])


class AbabTests(unittest.TestCase):
    def test_regressed_needs_both_order_and_magnitude(self):
        self.assertEqual(perf_abab.verdict([100, 102], [120, 121], .1), "regressed")
        self.assertEqual(perf_abab.verdict([100, 102], [104, 105], .1), "pass", "ordered but within tolerance")
        self.assertEqual(perf_abab.verdict([100, 130], [125, 126], .1), "pass", "large but not ordered")
        self.assertEqual(perf_abab.verdict([100, None], [125, 126], .1), "inconclusive")
        self.assertEqual(perf_abab.verdict([100], [125, 126], .1), "inconclusive")

    def test_runs_alternate_baseline_and_candidate_with_the_same_script(self):
        calls = []

        def run(binary, script, label):
            calls.append((binary, script, label))
            return {"operations": {"snap/op": 100 if binary == "A.bin" else 125, "other": 1}}
        result = perf_abab.run_abab(run, "A.bin", "B.bin", ["bench.py"], {"snap/op": "bench.py"}, {"snap/op": .1})
        self.assertEqual(calls, [("A.bin", "bench.py", "A0"), ("B.bin", "bench.py", "B1"),
                                 ("A.bin", "bench.py", "A2"), ("B.bin", "bench.py", "B3")])
        self.assertEqual(result, {"snap/op": {"A": [100, 100], "B": [125, 125], "verdict": "regressed",
                                              "tolerance": .1}})

    def test_a_failed_run_makes_the_operation_inconclusive(self):
        runs = [("A", {"operations": {"op": 5}}), ("B", None), ("A", {"operations": {"op": 5}}),
                ("B", {"operations": {"op": 5}})]
        self.assertEqual(perf_abab.decide(runs, ["op"], {"op": .1})["op"]["verdict"], "inconclusive")

    def test_the_baseline_must_be_the_last_stamped_build(self):
        value = dict(perf_ratchet.empty_ceilings())
        value["platforms"]["darwin-arm64"] = {"operations": {}, "last_measured": {
            "binary_sha256": "a" * 64, "binary_uuid": "u-1", "commit": "abc", "date": "2026-10-21"}}
        self.assertIsNone(perf_abab.baseline_problem(value, "darwin-arm64", "a" * 64))
        self.assertIsNone(perf_abab.baseline_problem(value, "darwin-arm64", "b" * 64, "u-1"), "signed copy: same UUID")
        self.assertIn("not the last stamped build", perf_abab.baseline_problem(value, "darwin-arm64", "b" * 64, "u-2"))
        self.assertIn("not the last stamped build", perf_abab.baseline_problem(value, "darwin-arm64", "b" * 64))
        self.assertIn("no stamped build", perf_abab.baseline_problem(value, "linux-x86_64", "a" * 64))
        self.assertIsNone(perf_abab.baseline_problem(value, "linux-x86_64", "b" * 64, approval="maintainer: v1.0.3"))
        self.assertIn("not the last", perf_abab.baseline_problem(value, "darwin-arm64", "b" * 64, approval="  "))


def macho(uuid_bytes, extra_commands=b""):
    """A minimal 64-bit Mach-O header with an optional command before LC_UUID."""
    import struct
    command = struct.pack("<II", 0x1B, 24) + uuid_bytes
    commands = extra_commands + command
    count = 2 if extra_commands else 1
    return struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, count, len(commands), 0, 0) + commands


class BinaryUuidTests(unittest.TestCase):
    def test_thin_and_fat_mach_o_and_non_mach_o(self):
        import struct
        raw = bytes(range(16))
        expected = "00010203-0405-0607-0809-0a0b0c0d0e0f"
        with tempfile.TemporaryDirectory() as directory:
            thin = Path(directory, "thin")
            thin.write_bytes(macho(raw, struct.pack("<II", 0x19, 16) + b"\0" * 8))
            self.assertEqual(perf_ratchet.binary_uuid(thin), expected)
            body = macho(raw)
            other = macho(bytes(16))
            fat = Path(directory, "fat")   # x86_64 slice first, arm64 second: the arm64 slice is read.
            header = struct.pack(">II", 0xCAFEBABE, 2) + struct.pack(">iiIII", 0x01000007, 3, 64, len(other), 0) \
                + struct.pack(">iiIII", 0x0100000C, 0, 64 + len(other), len(body), 0)
            fat.write_bytes(header.ljust(64, b"\0") + other + body)
            self.assertEqual(perf_ratchet.binary_uuid(fat), expected)
            text = Path(directory, "text")
            text.write_text("#!/bin/sh\n")
            self.assertIsNone(perf_ratchet.binary_uuid(text))
            self.assertIsNone(perf_ratchet.binary_uuid(Path(directory, "missing")))
            no_uuid = Path(directory, "no-uuid")
            no_uuid.write_bytes(struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0))
            self.assertIsNone(perf_ratchet.binary_uuid(no_uuid))


class GateDetailTests(unittest.TestCase):
    def test_a_looser_load_limit_must_say_why(self):
        import contextlib
        import io
        import test_blender_perf_ratchet as gate
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate.main(["--binary", "/nonexistent", "--out", tempfile.mkdtemp(), "--max-load", "5"])

    def test_merged_receipt_keeps_spread_units_gross_and_each_operation_source(self):
        import test_blender_perf_ratchet as gate
        merged, sources = {}, {}
        gate.merge_details(merged, {"operations": {"snapshot/x_bytes": 9, "snapshot/x_paired_net": 5},
                                    "spread": {"snapshot/x_paired_net": .01}, "units": {"snapshot/x_bytes": "bytes"},
                                    "gross": {"snapshot/x_gross": 200}, "rejected": {"snapshot/y_paired_net": {}}},
                           "bench_a.py", sources)
        gate.merge_details(merged, {"operations": {"other/op": 1}, "spread": {"other/op": None}}, "bench_b.py", sources)
        self.assertEqual(merged["spread"], {"snapshot/x_paired_net": .01, "other/op": None})
        self.assertEqual(merged["units"], {"snapshot/x_bytes": "bytes"})
        self.assertEqual(merged["gross"], {"snapshot/x_gross": 200})
        self.assertEqual(sources, {"snapshot/x_bytes": "bench_a.py", "snapshot/x_paired_net": "bench_a.py",
                                   "snapshot/y_paired_net": "bench_a.py", "other/op": "bench_b.py"})


if __name__ == "__main__":
    unittest.main()
