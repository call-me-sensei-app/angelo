# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for perf_counters: struct layout, availability and statistics.

No test is skipped on any platform: macOS checks real counters, other
platforms check that counters are reported unavailable rather than zero.
"""

import ctypes
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perf_counters  # noqa: E402


class LayoutTests(unittest.TestCase):
    def test_rusage_info_v4_layout_is_pinned(self):
        # 16-byte UUID then 35 uint64 fields; instructions and cycles are the
        # 30th and 31st, as declared in <sys/resource.h>.
        self.assertEqual(len(perf_counters.RUSAGE_V4_FIELDS), 35)
        self.assertEqual(ctypes.sizeof(perf_counters.RusageInfoV4), 296)
        self.assertEqual(perf_counters.RusageInfoV4.ri_instructions.offset, 248)
        self.assertEqual(perf_counters.RusageInfoV4.ri_cycles.offset, 256)
        self.assertEqual(perf_counters.RusageInfoV4.ri_diskio_byteswritten.offset, 16 + 17 * 8)

    def test_layout_matches_installed_sdk_header_when_present(self):
        # The pinned layout above is the contract; this cross-checks it against
        # the SDK on machines that have one, without skipping elsewhere.
        header = None
        if sys.platform == "darwin" and shutil.which("xcrun"):
            try:
                sdk = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True,
                                     timeout=20).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                sdk = ""
            candidate = Path(sdk) / "usr/include/sys/resource.h"
            header = candidate if sdk and candidate.is_file() else None
        if header is None:
            self.assertEqual(perf_counters.RUSAGE_V4_FIELDS[29:31], ("ri_instructions", "ri_cycles"))
            return
        body = re.search(r"struct rusage_info_v4 \{(.*?)\};", header.read_text(), re.S).group(1)
        fields = re.findall(r"uint64_t\s+(\w+);", body)
        self.assertEqual(tuple(fields), perf_counters.RUSAGE_V4_FIELDS)
        self.assertIn("uint8_t  ri_uuid[16];", body)

    def test_vm_statistics64_layout_is_pinned(self):
        # <mach/vm_statistics.h>: 152 bytes through rev1 plus swapped_count (rev2); the fields read are uint64_t.
        self.assertEqual(ctypes.sizeof(perf_counters.VmStatistics64), 160)
        self.assertEqual(perf_counters.VmStatistics64.swapins.offset, 112)
        self.assertEqual(perf_counters.VmStatistics64.swapouts.offset, 120)
        self.assertEqual(perf_counters.VmStatistics64.pageouts.offset, 40)
        header = None
        if sys.platform == "darwin" and shutil.which("xcrun"):
            try:
                sdk = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True,
                                     timeout=20).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                sdk = ""
            candidate = Path(sdk) / "usr/include/mach/vm_statistics.h"
            header = candidate if sdk and candidate.is_file() else None
        if header is None:
            return
        body = re.search(r"struct vm_statistics64 \{(.*?)\} __attribute", header.read_text(), re.S).group(1)
        fields = re.findall(r"(natural_t|uint64_t)\s+(\w+);", body)
        # Newer SDKs append revisions; the kernel fills only the count passed, so ours must be a prefix.
        declared = tuple((name, 4 if kind == "natural_t" else 8) for kind, name in fields)
        self.assertEqual(declared[:len(perf_counters.VM_STATISTICS64_FIELDS)], perf_counters.VM_STATISTICS64_FIELDS)


class AvailabilityTests(unittest.TestCase):
    def test_counters_report_platform_truthfully(self):
        _, metrics = perf_counters.measure(lambda: perf_counters.fixed_workload(20000))
        availability = perf_counters.availability()
        self.assertGreater(metrics["wall_ms"], 0)
        self.assertGreaterEqual(metrics["cpu_ms"], 0)
        if sys.platform == "darwin":
            self.assertEqual(availability["instructions"], "available")
            # A 20,000-element build/sort/join retires millions of instructions.
            self.assertGreater(metrics["instructions"], 1_000_000)
            self.assertGreater(metrics["cycles"], 0)
            for name in ("pageins", "swapins", "swapouts", "compressions"):
                self.assertIsInstance(metrics[name], int)
        else:
            self.assertEqual(availability["instructions"], "unavailable")
            self.assertIn("macOS", availability["reason"])
            self.assertIsNone(metrics["instructions"])
            self.assertIsNone(metrics["cycles"])
            self.assertIsNone(perf_counters.read_rusage())

    def test_missing_counters_stay_none_never_zero(self):
        start = {"wall_ns": 0, "cpu_ns": 0, "counters": None}
        end = {"wall_ns": 2_000_000, "cpu_ns": 1_000_000, "counters": None}
        self.assertEqual(perf_counters.difference(start, end),
                         {"wall_ms": 2.0, "cpu_ms": 1.0, "instructions": None, "cycles": None, "disk_written_bytes": None,
                          "pageins": None})
        self.assertEqual(set(perf_counters.vm_difference(None, {"swapins": 1})), set(perf_counters.VM_RECORDED))
        self.assertIsNone(perf_counters.vm_difference(None, None)["swapins"])


class StatisticsTests(unittest.TestCase):
    def test_percentile_interpolates_linearly(self):
        self.assertEqual(perf_counters.percentile([4, 1, 3, 2], .5), 2.5)
        self.assertEqual(perf_counters.percentile([1, 2, 3, 4, 5], .75), 4)
        self.assertEqual(perf_counters.percentile([10], .95), 10)
        with self.assertRaises(ValueError):
            perf_counters.percentile([], .5)
        with self.assertRaises(ValueError):
            perf_counters.percentile([1], 1.5)

    def test_describe_reports_spread_and_ignores_missing_values(self):
        value = perf_counters.describe([100, 102, None, 98, 100])
        self.assertEqual(value["samples"], 4)
        self.assertEqual(value["median"], 100)
        self.assertEqual(value["relative_range"], .04)
        self.assertAlmostEqual(value["cv"], 0.01414, places=4)
        self.assertIsNone(perf_counters.describe([None, None]))

    def test_repeat_discards_warmup_and_summarizes_each_metric(self):
        calls = []
        summary, raw, result = perf_counters.repeat(lambda: calls.append(1) or len(calls), samples=3, warmup=2)
        self.assertEqual((len(calls), len(raw), result), (5, 3, 5))
        self.assertEqual(set(summary), set(perf_counters.METRICS) | {"validity"})
        self.assertEqual(summary["wall_ms"]["samples"], 3)
        with self.assertRaises(ValueError):
            perf_counters.repeat(lambda: None, samples=0)

    def test_samples_with_pageins_swap_or_unknown_evidence_are_rejected(self):
        clean = {"instructions": 100, "cycles": 50, "wall_ms": 1.0, "pageins": 0, "swapins": 0, "swapouts": 0}
        self.assertEqual(perf_counters.rejection(clean), [])
        self.assertEqual(perf_counters.rejection(dict(clean, pageins=3)), ["pageins"])
        self.assertEqual(perf_counters.rejection(dict(clean, swapouts=1, swapins=2)), ["swapins", "swapouts"])
        self.assertEqual(perf_counters.rejection(dict(clean, swapins=None)), ["swapins unknown"])
        self.assertEqual(perf_counters.rejection({"instructions": 1}),
                         ["pageins unknown", "swapins unknown", "swapouts unknown"])

    def test_summarize_counts_only_valid_samples_but_times_every_sample(self):
        base = {"cpu_ms": 1.0, "disk_written_bytes": 0, "pageins": 0, "swapins": 0, "swapouts": 0}
        samples = [dict(base, wall_ms=1.0, instructions=100, cycles=10), dict(base, wall_ms=2.0, instructions=102, cycles=10),
                   dict(base, wall_ms=9.0, instructions=900, cycles=90, pageins=4)]
        summary = perf_counters.summarize(samples)
        self.assertEqual(summary["instructions"]["samples"], 2)
        self.assertEqual(summary["instructions"]["max"], 102)
        self.assertEqual(summary["wall_ms"]["samples"], 3)
        self.assertEqual(summary["validity"], {"samples": 3, "valid": 2, "rejected": 1, "reasons": {"pageins": 1}})
        self.assertIsNone(perf_counters.summarize([samples[2]])["instructions"])
        self.assertEqual(perf_counters.describe_valid(samples, "instructions")["median"], 101)

    def test_wait_for_quiet_waits_for_load_below_the_limit_and_gives_up(self):
        loads, slept = iter([3.1, 2.4, 1.6]), []
        self.assertEqual(perf_counters.wait_for_quiet(2.0, 60, 15, sleep=slept.append, load=lambda: next(loads)),
                         (True, 1.6))
        self.assertEqual(slept, [15, 15])
        self.assertEqual(perf_counters.wait_for_quiet(2.0, 0, 15, sleep=slept.append, load=lambda: 2.0), (False, 2.0))

    def test_stability_report_shape(self):
        report = perf_counters.stability(lambda: perf_counters.fixed_workload(2000), repeats=3, warmup=1)
        self.assertEqual(report["repeats"], 3)
        self.assertEqual(len(report["samples"]), 3)
        self.assertEqual(report["metrics"]["cpu_ms"]["samples"], 3)


if __name__ == "__main__":
    unittest.main()
