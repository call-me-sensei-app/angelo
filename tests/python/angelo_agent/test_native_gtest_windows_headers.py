# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No GTest source meets ``windows.h`` before ``testing/testing.h``: the GTests have to build on Windows.

glog (``testing/testing.h`` includes its ``logging.h``) refuses to compile once ``windows.h`` has defined ``ERROR``
(wingdi.h): "ERROR macro is defined. Define GLOG_NO_ABBREVIATED_SEVERITIES before including logging.h". Blender
keeps to the order that works, the testing header before anything that brings ``windows.h``
(``BLI_fileops_test.cc`` includes ``BLI_winstuff.hh`` after it), and so must Angelo's tests.

Angelo's release builds keep ``WITH_GTESTS`` off and no CI job builds the GTests on Windows, so nothing noticed
when four blenloader tests included ``BLI_filereader.hh`` (it includes ``BLI_winstuff.hh`` on Windows) first: the
first Windows GTest build, on 2026-11-13 (gcp-kit, build3), stopped in ``blenloader_tests`` with that error, a
month after the first of them was written. This reads every test source the way a Windows compiler meets its
includes, on any platform.

What the reading is: includes in order, followed into the project's own headers; blocks under ``#ifndef WIN32``,
``#if !defined(WIN32)``, ``__APPLE__``, ``__linux__`` and ``FREE_WINDOWS64`` (MinGW) are skipped, every other
condition is taken as it may be true; a ``windows.h`` included after ``#define NOGDI`` in the same file defines no
``ERROR`` (``atomic_ops_msvc.h``). It is not a compiler: it finds this order, not other Windows build errors.
No Blender needed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE_FOLDERS = ("source", "intern", "tests/gtests")
HEADER_FOLDERS = SOURCE_FOLDERS + ("extern/glog",)
INCLUDE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]')
CONDITION = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)$")
NOT_WINDOWS = re.compile(r"^(defined\s*\(\s*(FREE_WINDOWS64|__APPLE__|__linux__)\s*\)|FREE_WINDOWS64|__APPLE__|"
                         r"__linux__|!\s*defined\s*\(\s*_?WIN32\s*\))$")
TESTING = ("testing/testing.h", "gtest/gtest.h", "gmock/gmock.h", "glog/logging.h")
TEST_SOURCE = re.compile(r"_tests?\.(cc|cpp)$")


def headers(root):
    """File name -> every header of that name in the project."""
    found = {}
    for folder in HEADER_FOLDERS:
        for directory, _folders, files in os.walk(root / folder):
            for name in files:
                if name.endswith((".h", ".hh", ".hpp")):
                    found.setdefault(name, []).append(Path(directory) / name)
    return found


def seen_on_windows(path):
    """What a Windows build meets in one file, in order: ("nogdi", ""), ('"' or "<", included name)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    stack = []   # One entry per open #if: is its current branch compiled on Windows (as far as this can tell)?
    for line in lines:
        found = CONDITION.match(line)
        if found:
            word, rest = found.group(1), found.group(2).split("//")[0].split("/*")[0].strip()
            if word == "ifdef":
                stack.append(rest not in ("FREE_WINDOWS64", "__APPLE__", "__linux__"))
            elif word == "ifndef":
                stack.append(rest not in ("WIN32", "_WIN32"))
            elif word == "if":
                stack.append(not NOT_WINDOWS.match(rest))
            elif word in ("elif", "else") and stack:
                stack[-1] = True   # The other side: taken as it may be compiled.
            elif word == "endif" and stack:
                stack.pop()
            continue
        if not all(stack):
            continue
        if re.match(r"^\s*#\s*define\s+NOGDI\b", line):
            yield "nogdi", ""
        found = INCLUDE.match(line)
        if found:
            yield found.group(1), found.group(2)


def resolve(index, name, including):
    beside = including.parent / name
    if beside.is_file():
        return beside
    return next((path for path in index.get(Path(name).name, ()) if path.as_posix().endswith("/" + name)), None)


def first_met(index, path, seen):
    """``"testing"`` when the testing header is met first; the include trail to a ``windows.h`` that defines
    ``ERROR`` when that is met first; None when neither is met."""
    if path in seen:
        return None
    seen.add(path)
    nogdi = False
    for kind, name in seen_on_windows(path):
        if kind == "nogdi":
            nogdi = True
        elif name.lower() == "windows.h":
            if not nogdi:
                return [path.name, name]
        elif name.endswith(TESTING) or name == "testing.h":
            return "testing"
        elif kind == '"':
            header = resolve(index, name, path)
            met = first_met(index, header, seen) if header is not None else None
            if met == "testing":
                return met
            if met:
                return [path.name] + met
    return None


def windows_before_testing(root):
    """Every test source under ``root`` that meets a ``windows.h`` defining ``ERROR`` before the testing header."""
    index = headers(root)
    found = {}
    for folder in SOURCE_FOLDERS:
        for directory, _folders, files in os.walk(root / folder):
            for name in sorted(files):
                if TEST_SOURCE.search(name):
                    met = first_met(index, Path(directory) / name, set())
                    if met and met != "testing":
                        found[(Path(directory) / name).relative_to(root).as_posix()] = " > ".join(met[1:])
    return found


class WindowsHeaders(unittest.TestCase):
    def test_no_test_source_meets_windows_h_before_the_testing_header(self):
        self.assertTrue((ROOT / "source/blender/blenlib/BLI_winstuff.hh").is_file(), "not a full checkout")
        self.assertEqual(windows_before_testing(ROOT), {}, "include \"testing/testing.h\" before these headers: "
                         "glog does not compile after windows.h has defined ERROR")

    def test_the_reading_finds_the_order_and_only_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "source/lib/winstuff.hh": "#ifndef _WIN32\n#  error only Windows\n#endif\n#include <windows.h>\n",
                "source/lib/reader.hh": "#ifdef WIN32\n#  include \"winstuff.hh\"\n#else\n#  include <unistd.h>\n"
                                        "#endif\n",
                "source/lib/atomic.h": "#define NOGDI\n#include <windows.h>\n",
                "source/lib/mingw.hh": "#if defined(FREE_WINDOWS64)\n#  include <windows.h>\n#endif\n",
                "source/lib/posix.hh": "#ifndef WIN32\n#  include <windows.h>\n#endif\n",
                "source/lib/base_test.h": "#include \"testing/testing.h\"\n",
                "source/a/tests/late_test.cc": "#include \"reader.hh\"\n#include \"testing/testing.h\"\n",
                "source/a/tests/through_base_test.cc": "#include \"reader.hh\"\n#include \"base_test.h\"\n",
                "source/a/tests/first_test.cc": "#include \"testing/testing.h\"\n#include \"reader.hh\"\n",
                "source/a/tests/guarded_test.cc": "#include \"atomic.h\"\n#include \"mingw.hh\"\n"
                                                  "#include \"posix.hh\"\n#include \"testing/testing.h\"\n",
                "source/a/tests/base_first_test.cc": "#include \"base_test.h\"\n#include \"reader.hh\"\n",
                "source/a/intern/reader.cc": "#include \"reader.hh\"\n",
            }
            for name, text in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            self.assertEqual(windows_before_testing(root), {
                "source/a/tests/late_test.cc": "reader.hh > winstuff.hh > windows.h",
                "source/a/tests/through_base_test.cc": "reader.hh > winstuff.hh > windows.h",
            })


if __name__ == "__main__":
    unittest.main()
