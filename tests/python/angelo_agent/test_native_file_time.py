# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A file's time is never turned into nanoseconds by a plain multiplication, read from the sources.

A Windows file time counts 100 ns steps since 1601; today's value times 100 does not fit 64 bits, and two places did
that for every file (signed overflow, undefined; an external review with UBSan, 2026-11-15). Seconds times 1e9 has
the same fault for a file stamped before 1677 or after 2262. ``BLI_file_time.hh`` holds the conversions that give
the nearest representable value instead (``BLI_file_time_test.cc`` asks them every edge, as constant expressions and
at run time). This file keeps the callers on them, and finds a new caller that multiplies a time itself: every
native source Angelo adds or changes that reads a file's time. No Blender needed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STORAGE = ROOT / "source/blender/blenlib/intern/storage.cc"
IMAGE_SOURCE = ROOT / "source/blender/blenkernel/intern/angelo_image_source.cc"
# A vouched program's facts: nanoseconds on every host (its header says so), compared for equality.
VOUCHES = ROOT / "source/blender/python/intern/bpy_angelo_guard_confine_vouches.cc"
HELPER = ROOT / "source/blender/blenlib/BLI_file_time.hh"
# What a source that reads a file's time mentions, and a time scaled by hand.
READS_A_TIME = re.compile(r"FILETIME|LastWriteTime|ChangeTime|st_[mcb]tim|st_birthtim|timespec_get|tv_nsec")
SCALED = re.compile(r"([\w\)\]\.>]+)\s*\*\s*(100|1000|1000000|10000000|1000000000)(?![\d.'])")
A_NUMBER = re.compile(r"^(?:int64_t|uint64_t|int|double|float)?\(?[\d.']+\)?$")


def scaled_by_hand(text):
    """``(line number, line)`` of each multiplication of something that is not a plain number by a time unit."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("//")[0]
        if code.lstrip().startswith(("*", "/*")):
            continue
        for match in SCALED.finditer(code):
            if not A_NUMBER.match(match.group(1)):
                found.append((number, line.strip()))
    return found


class FileTimeSources(unittest.TestCase):
    def test_the_callers_convert_through_the_helper(self):
        for path in (STORAGE, IMAGE_SOURCE, VOUCHES):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.name):
                self.assertIn('#include "BLI_file_time.hh"', text)
                self.assertIn("file_time::nanoseconds_from_filetime(", text)
                self.assertIn("file_time::nanoseconds_saturated(", text)
                self.assertEqual(scaled_by_hand(text), [])
        image = IMAGE_SOURCE.read_text(encoding="utf-8")
        self.assertIn("file_time::changed_within(", image, "the age is compared by a subtraction that cannot overflow")
        self.assertNotRegex(image, r"now_ns\(\)\s*-")

    def test_no_native_source_of_angelo_s_scales_a_file_time_by_hand(self):
        """Every Angelo-named native source, and the two Blender files Angelo changed for file identities."""
        sources = [path for folder in ("source", "intern") for path in sorted((ROOT / folder).rglob("*angelo*"))
                   if path.suffix in (".cc", ".hh", ".h", ".c", ".cpp", ".mm")]
        sources += [STORAGE]
        self.assertGreater(len(sources), 20)
        checked = 0
        for path in sources:
            text = path.read_text(encoding="utf-8", errors="replace")
            if not READS_A_TIME.search(text) or path.name.endswith("_test.cc"):
                continue
            checked += 1
            with self.subTest(str(path.relative_to(ROOT))):
                self.assertEqual(scaled_by_hand(text), [])
        self.assertGreaterEqual(checked, 3, "the reading still finds the sources that read a file's time")

    def test_the_reading_finds_what_it_is_for(self):
        old = ("  return int64_t(std::max(stamp.modified, stamp.changed)) * 100;\n"
               "  return ((int64_t(now.dwHighDateTime) << 32) | int64_t(now.dwLowDateTime)) * 100;\n"
               "  return (value - epoch_as_filetime) * 100;\n"
               "  r_identity->modified_ns = int64_t(st.st_mtim.tv_sec) * 1000000000 + st.st_mtim.tv_nsec;\n")
        self.assertEqual(len(scaled_by_hand(old)), 4)
        fine = ("std::atomic<int64_t> racy_window_ns = int64_t(3) * 1000000000;\n"
                "constexpr int64_t hour_ns = int64_t(3600) * 1000000000;\n"
                "  /* 100 ns units since 1601: value * 100 would overflow. */\n"
                "  const float percent = fraction * 100.0f;\n")
        self.assertEqual(scaled_by_hand(fine), [])

    def test_the_helper_and_its_tests_are_built(self):
        cmake = (ROOT / "source/blender/blenlib/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("  BLI_file_time.hh\n", cmake)
        self.assertIn("    tests/BLI_file_time_test.cc\n", cmake)
        helper = HELPER.read_text(encoding="utf-8")
        for name in ("nanoseconds_saturated", "nanoseconds_from_filetime", "difference_saturated", "changed_within"):
            self.assertRegex(helper, r"constexpr \w+ %s\(" % name, "a constant expression: overflow cannot compile")
        tests = (ROOT / "source/blender/blenlib/tests/BLI_file_time_test.cc").read_text(encoding="utf-8")
        self.assertGreaterEqual(tests.count("static_assert("), 40)
        self.assertIn("134391744000000000", tests, "the value the review's sanitizer run used")


if __name__ == "__main__":
    unittest.main()
