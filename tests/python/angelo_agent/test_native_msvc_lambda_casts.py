# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No C or C++ source casts a lambda expression C-style, directly or through a macro (fails first).

Stock MSVC (cl.exe) refuses a C-style cast written straight onto a lambda expression, ``(T)[](...) {...}``, with
C2059/C2143/C2447, and every later name in the file then reads as undeclared. clang-cl, which release builds and VM CI
use, accepts it, so nothing caught it until a contributor's ``make.bat lite 2022b`` (MSVC 17.14) failed on
``bpy_angelo_guard_confine_wrap_native.cc`` (2026-11-18; its ``METHOD_ENTRY`` macro expanded to a lambda that three
casts were applied to). A lambda given a function pointer type is a named ``static`` function (or a template of one),
which every compiler takes. Read from the sources: ``source/`` and ``intern/``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hh", ".mm")
LAMBDA = r"\[\s*[\w&=, ]*\]\s*\("
# A macro whose body begins with a lambda: casting its expansion is the same cast.
LAMBDA_MACRO = re.compile(r"#define\s+(\w+)(?:\([^)]*\))?[ \t]*(?:\\[ \t]*\n[ \t]*)?" + LAMBDA)
# A C-style cast (a type in parentheses, a function pointer type included) right before a lambda.
CAST_ON_LAMBDA = re.compile(r"\(\s*[A-Za-z_][\w:<>\s*&,]*(?:\(\s*\*\s*\)\s*\([^()]*\))?\s*\)\s*" + LAMBDA)
NOT_A_CAST = re.compile(r"\b(?:if|while|for|switch|return|sizeof|operator)\s*\($")


def sources():
    for top in ("source", "intern"):
        for path in sorted((ROOT / top).rglob("*")):
            if path.suffix in SUFFIXES and path.is_file():
                yield path


def offenders():
    found = []
    for path in sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "[" not in text:
            continue
        name = path.relative_to(ROOT).as_posix()
        macros = {match.group(1) for match in LAMBDA_MACRO.finditer(text)}
        found += ["%s: macro %s expands to a lambda" % (name, macro) for macro in sorted(macros)]
        for number, line in enumerate(text.split("\n"), 1):
            code = line.split("//")[0]
            for match in CAST_ON_LAMBDA.finditer(code):
                if not NOT_A_CAST.search(code[:match.start() + 1].rstrip()):
                    found.append("%s:%d: %s" % (name, number, line.strip()[:120]))
    return found


class LambdaCasts(unittest.TestCase):
    def test_no_source_casts_a_lambda_c_style(self):
        self.assertTrue((ROOT / "source/blender/python/intern").is_dir(), "the sources are read from this tree")
        self.assertEqual(offenders(), [], "give the lambda's body a named static function (or a template of one)")

    def test_the_rule_finds_both_forms(self):
        """The scan itself: a cast straight onto a lambda, and a lambda-bodied macro, are both found."""
        self.assertTrue(CAST_ON_LAMBDA.search("(PyCFunction)(void (*)(void))[](PyObject *a) { return a; }"))
        self.assertTrue(LAMBDA_MACRO.search("#define ENTRY(i) \\\n  [](PyObject *s) -> PyObject * { return s; }"))
        self.assertFalse(CAST_ON_LAMBDA.search("std::sort(v.begin(), v.end(), [](int a, int b) { return a < b; });"))


if __name__ == "__main__":
    unittest.main()
