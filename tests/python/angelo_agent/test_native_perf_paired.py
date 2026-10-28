# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for the robust ratchet statistics: paired repeat captures (the three-write sum, 2026-10-27) and ABAB."""

import ast
from pathlib import Path
import sys
import tempfile
import types
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


def pairs_of(*triples):
    """``pair`` entries from (before, capture, after) instruction counts, all valid."""
    return [snapshot_paired.pair(sample(capture), sample(before), sample(after), perf_counters.rejection)
            for before, capture, after in triples]


class PairedNetTests(unittest.TestCase):
    def test_each_repeat_reports_its_three_write_sum_and_its_net(self):
        entry = snapshot_paired.pair(sample(200), sample(120), sample(140), perf_counters.rejection)
        self.assertEqual((entry["three_write_sum"], entry["net"], entry["valid"], entry["reasons"]),
                         (460, 70, True, []))
        self.assertEqual(snapshot_paired.three_write_sum(entry), 460)

    def test_kernel_work_moving_between_adjacent_writes_leaves_the_sum_unchanged(self):
        # The 2026-10-27 failure: the same total work, split differently between a capture and its
        # references from pair to pair and run to run, swung the net while the sum stayed put.
        splits = [(500 - shift, 550 + shift, 480) for shift in (-40, -10, 0, 15, 30, 45, 60)]
        other = [(500 + shift, 550 - shift, 480) for shift in (-40, -10, 0, 15, 30, 45, 60)]
        first, second = pairs_of(*splits), pairs_of(*other)
        self.assertEqual(snapshot_paired.statistic(first)["median"], 1530)
        self.assertEqual(snapshot_paired.statistic(second)["median"], 1530)
        self.assertEqual(snapshot_paired.statistic(first)["spread"], 0.0)
        self.assertNotEqual(snapshot_paired.paired_net(first)["median"], snapshot_paired.paired_net(second)["median"])

    def test_extra_capture_work_shows_one_for_one_in_the_sum(self):
        base = pairs_of(*[(480, 550, 470)] * 7)
        heavier = pairs_of(*[(480, 562, 470)] * 7)
        self.assertEqual(snapshot_paired.statistic(heavier)["median"] - snapshot_paired.statistic(base)["median"], 12)

    def test_a_reference_drift_moves_both_sides_and_cancels_in_the_reported_net(self):
        # The 2026-10-21 failure: the same capture work under two kernel costs per byte.
        quiet = [snapshot_paired.pair(sample(75 + 125), sample(125), sample(125), perf_counters.rejection)] * 7
        loaded = [snapshot_paired.pair(sample(75 + 152), sample(151), sample(153), perf_counters.rejection)] * 7
        self.assertEqual(snapshot_paired.paired_net(quiet)["median"], snapshot_paired.paired_net(loaded)["median"])

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
        pairs.append(snapshot_paired.pair(sample(100), sample(50, swapouts=1), sample(50), perf_counters.rejection))
        value = snapshot_paired.statistic(pairs)
        self.assertEqual((value["median"], value["valid"], value["rejected"]), (203, 7, 2))
        self.assertEqual((value["min"], value["max"]), (200, 206))
        self.assertEqual(value["reasons"], {"capture pageins": 1, "reference swapouts": 1})
        self.assertEqual(value["spread"], round(6 / 203, 5))
        self.assertIn("reference before + capture + reference after", value["statistic"])
        net = snapshot_paired.paired_net(pairs)
        self.assertEqual((net["median"], net["valid"], net["rejected"]), (53, 7, 2), "same validity, same pairs")
        short = snapshot_paired.statistic(pairs[1:])
        self.assertIsNone(short["median"])
        self.assertIsNone(short["spread"])
        self.assertIsNone(snapshot_paired.paired_net(pairs[1:])["median"])
        self.assertEqual(snapshot_paired.MIN_PAIRS, 7)
        self.assertEqual(snapshot_paired.MAX_ATTEMPTS_FACTOR, 3)

    def test_reports_written_before_the_sum_existed_are_scored_the_same(self):
        fresh = pairs_of(*[(40 + index, 100, 60) for index in range(7)])
        older = [{key: value for key, value in entry.items() if key not in ("three_write_sum", "net")}
                 for entry in fresh]
        self.assertEqual(snapshot_paired.statistic(older), snapshot_paired.statistic(fresh))
        self.assertEqual(snapshot_paired.statistic(older)["median"], 203)

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


def benchmark_ratchet_capture():
    """The snapshot benchmark's ``ratchet_capture`` (it imports bpy at module level, so it is read from source)."""
    tree = ast.parse((HERE / "benchmark_blender_snapshot_cost.py").read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "ratchet_capture")
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "ratchet_capture", "exec"), namespace)
    return namespace["ratchet_capture"]


def fixture_ratchet():
    """``benchmark_fixtures.Ratchet``; bpy and bmesh are stubbed only while it imports, when no test provided them."""
    added = [name for name in ("bpy", "bmesh") if name not in sys.modules]
    for name in added:
        sys.modules[name] = types.ModuleType(name)
    try:
        import benchmark_fixtures
    finally:
        for name in added:
            sys.modules.pop(name, None)
    return benchmark_fixtures.Ratchet()


class ReportedOnlyTests(unittest.TestCase):
    def capture(self):
        pairs = pairs_of(*[(480 + index, 550, 470) for index in range(7)])
        return {"repeat": {"three_write_sum": snapshot_paired.statistic(pairs),
                           "paired_net": snapshot_paired.paired_net(pairs), "instructions": {"median": 551}},
                "repeat_bytes": {"bytes": 4096, "exact": True}}

    def test_the_benchmark_gates_the_sum_and_bytes_and_only_reports_net_and_gross(self):
        ratchet = fixture_ratchet()
        benchmark_ratchet_capture()(ratchet, "snapshot/doc_repeat_capture", self.capture())
        section = ratchet.section()
        self.assertEqual(section["operations"], {"snapshot/doc_repeat_capture_bytes": 4096,
                                                 "snapshot/doc_repeat_capture_three_write_sum": 1503})
        self.assertEqual(section["reported"], {"snapshot/doc_repeat_capture_paired_net": 74})
        self.assertEqual(section["gross"], {"snapshot/doc_repeat_capture_gross": 551})
        self.assertEqual(section["validity"]["snapshot/doc_repeat_capture_three_write_sum"],
                         {"samples": 7, "valid": 7, "rejected": 0, "reasons": {}})
        # A ceiling left for the net can never be compared: the net is not a measured operation.
        value = perf_ratchet.empty_ceilings()
        value["platforms"][section["platform"]] = {"operations": {
            "snapshot/doc_repeat_capture_paired_net": {"ceiling": 1}}}
        rows = {row["operation"]: row["status"] for row in perf_ratchet.compare(
            value, section["platform"], section["operations"], perf_ratchet.load_details({"ratchet": section}))}
        self.assertEqual(rows["snapshot/doc_repeat_capture_paired_net"], "unmeasured")
        self.assertEqual(rows["snapshot/doc_repeat_capture_three_write_sum"], "unstamped")

    def test_too_few_valid_pairs_leave_the_sum_rejected_not_measured(self):
        pairs = pairs_of(*[(480, 550, 470)] * 6) + [
            snapshot_paired.pair(sample(550, pageins=3), sample(480), sample(470), perf_counters.rejection)]
        capture = {"repeat": {"three_write_sum": snapshot_paired.statistic(pairs),
                              "paired_net": snapshot_paired.paired_net(pairs), "instructions": {"median": 550}},
                   "repeat_bytes": {"bytes": 4096, "exact": True}}
        ratchet = fixture_ratchet()
        benchmark_ratchet_capture()(ratchet, "snapshot/doc_repeat_capture", capture)
        section = ratchet.section()
        self.assertNotIn("snapshot/doc_repeat_capture_three_write_sum", section["operations"])
        self.assertEqual(section["rejected"]["snapshot/doc_repeat_capture_three_write_sum"]["reasons"],
                         {"capture pageins": 1})
        self.assertEqual(section["reported"], {})


class AbabTests(unittest.TestCase):
    def test_regressed_needs_both_order_and_magnitude(self):
        self.assertEqual(perf_abab.verdict([100, 102], [120, 121], .1), "regressed")
        self.assertEqual(perf_abab.verdict([100, 102], [104, 105], .1), "pass", "ordered but within tolerance")
        self.assertEqual(perf_abab.verdict([100, 130], [125, 126], .1), "pass", "large but not ordered")
        self.assertEqual(perf_abab.verdict([100, None], [125, 126], .1), "inconclusive")
        self.assertEqual(perf_abab.verdict([100], [125, 126], .1), "inconclusive")

    def test_a_three_write_sum_is_decided_by_the_unchanged_rule_with_its_own_tolerance(self):
        name = "snapshot/dense_500mb_repeat_capture_three_write_sum"
        value = perf_ratchet.empty_ceilings()
        value["platforms"]["darwin-arm64"] = {"operations": {name: {"ceiling": 1512, "tolerance": .012}}}
        rows = perf_ratchet.compare(value, "darwin-arm64", {name: 1530}, {"spread": {name: .03}})
        self.assertEqual((rows[0]["status"], rows[0]["tolerance"]), ("noisy", .012))
        noisy = {row["operation"]: row["tolerance"] for row in rows if row["status"] == "noisy"}

        def runs(a, b):
            return [("A", {"operations": {name: a[0]}}), ("B", {"operations": {name: b[0]}}),
                    ("A", {"operations": {name: a[1]}}), ("B", {"operations": {name: b[1]}})]
        decide = lambda a, b: perf_abab.decide(runs(a, b), [name], noisy)[name]["verdict"]  # noqa: E731
        self.assertEqual(decide([1503, 1509], [1512, 1503]), "pass", "B not above both A runs")
        self.assertEqual(decide([1500, 1505], [1510, 1512]), "pass", "ordered, but within 1.2%")
        self.assertEqual(decide([1500, 1505], [1525, 1530]), "regressed")
        self.assertEqual(decide([1500, None], [1525, 1530]), "inconclusive")
        verdicts = {name: "regressed"}
        self.assertEqual(perf_ratchet.compare(value, "darwin-arm64", {name: 1530}, {"spread": {name: .03}},
                                              verdicts)[0]["status"], "abab_regressed")

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
        gate.merge_details(merged, {"operations": {"snapshot/x_bytes": 9, "snapshot/x_three_write_sum": 5},
                                    "spread": {"snapshot/x_three_write_sum": .01}, "units": {"snapshot/x_bytes": "bytes"},
                                    "gross": {"snapshot/x_gross": 200}, "reported": {"snapshot/x_paired_net": 3},
                                    "rejected": {"snapshot/y_three_write_sum": {}}},
                           "bench_a.py", sources)
        gate.merge_details(merged, {"operations": {"other/op": 1}, "spread": {"other/op": None}}, "bench_b.py", sources)
        self.assertEqual(merged["spread"], {"snapshot/x_three_write_sum": .01, "other/op": None})
        self.assertEqual(merged["units"], {"snapshot/x_bytes": "bytes"})
        self.assertEqual(merged["gross"], {"snapshot/x_gross": 200})
        self.assertEqual(merged["reported"], {"snapshot/x_paired_net": 3})
        self.assertEqual(sources, {"snapshot/x_bytes": "bench_a.py", "snapshot/x_three_write_sum": "bench_a.py",
                                   "snapshot/y_three_write_sum": "bench_a.py", "other/op": "bench_b.py"})


if __name__ == "__main__":
    unittest.main()
