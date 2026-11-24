# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every string and character literal in Angelo's C++ is well formed: closed on its line, and only valid escapes.

Code under ``#ifdef _WIN32`` (or ``__APPLE__``, or ``__linux__``) is compiled on one platform only, so a malformed
literal there passes every build and test elsewhere and is found only by that platform's build. On build3 head 25
(2026-11-22), ``dos_device`` in bpy_angelo_guard_confine_reparse.cc had ``L"\\\\.\\"`` written as ``L"\\.\"``
(the closing quote escaped) and ``L"\\\\/"`` as ``L"\\/"`` (an unknown escape): a scripted edit had dropped
backslashes, and the first compiler to see the lines was the Windows one, which stopped the build and every suite.

This reads every Angelo-owned C++ file (a name with ``angelo``) on any platform, whatever its conditions, the way a
lexer meets its literals: comments and raw strings skipped, digit separators (``1'000``) taken as numbers. It is not a
compiler: it finds this class of error, not others. No Blender needed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hh", ".hpp", ".mm")
VALID_ESCAPES = set("'\"?\\abfnrtv01234567xuUN")   # \N{...}: C++23 named characters.
RAW = re.compile(r'(?:u8|[uUL])?R"([^()\\ \t\n]{0,16})\(')


def number_before(text, i):
    """Whether the token that ends at ``i`` is a number (it starts with a digit; separators included)."""
    start = i
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_'"):
        start -= 1
    return start < i and text[start].isdigit()


def problems(text):
    """(line, what) for each malformed literal in C or C++ source text."""
    found = []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            i = end
            continue
        raw = RAW.match(text, i)
        if raw and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            close = ")" + raw.group(1) + '"'
            end = text.find(close, raw.end())
            if end < 0:
                found.append((line, "unterminated raw string"))
                return found
            line += text.count("\n", i, end)
            i = end + len(close)
            continue
        if c in "\"'":
            if c == "'" and number_before(text, i) and text[i + 1:i + 2].isalnum():
                i += 1   # A digit separator (1'000, 0x00'ff), never a prefixed character (L'a', u8'x').
                continue
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\n":
                    found.append((line, "unterminated literal"))
                    break
                if text[j] == "\\":
                    following = text[j + 1:j + 2]
                    if following not in VALID_ESCAPES and following != "\n":
                        found.append((line, "unknown escape \\" + following))
                    j += 2
                    continue
                j += 1
            i = j + 1
            continue
        i += 1
    return found


def angelo_sources(root):
    for folder in ("source", "intern"):
        for path in sorted((root / folder).rglob("*angelo*")):
            if path.suffix in SUFFIXES and path.is_file():
                yield path


class Literals(unittest.TestCase):
    def test_every_angelo_literal_is_well_formed(self):
        sources = list(angelo_sources(ROOT))
        self.assertGreater(len(sources), 50)   # The scan found Angelo's sources.
        bad = []
        for path in sources:
            text = path.read_text(encoding="utf-8", errors="replace")
            bad += ["%s:%d: %s" % (path.relative_to(ROOT), line, what) for line, what in problems(text)]
        self.assertEqual(bad, [])

    def test_the_scan_finds_what_only_one_platform_would_compile(self):
        # The source text L"\\\\.\\" (the four characters \\.\) is right; L"\\.\" escapes its closing quote.
        self.assertEqual(problems('#ifdef _WIN32\nstatic const std::wstring prefix = L"\\\\\\\\.\\\\";\n#endif\n'), [])
        self.assertEqual(problems('#ifdef _WIN32\nauto p = L"\\\\.\\";\n#endif\n'), [(2, "unterminated literal")])
        self.assertEqual(problems('x.find_first_of(L"\\/");\n'), [(1, "unknown escape \\/")])
        self.assertEqual(problems("char c = '\\q';\n"), [(1, "unknown escape \\q")])
        self.assertEqual(problems('const char *s = "open;\n'), [(1, "unterminated literal")])
        self.assertEqual(problems('auto r = R"x(never closed\n'), [(1, "unterminated raw string")])

    def test_what_is_valid_passes(self):
        valid = ('const char *a = "\\\\.\\\\pipe\\\\x\\n\\t\\"\\x41\\101\\u00e9\\?";\n'
                 "char b = '\\'', c = '\"', d = '\\\\';\n"
                 "int e = 1'000'000, f = 0x00'ff;\n"
                 "bool l = c >= L'a' && c <= L'Z' || c == u8'x' || c == U'\\\\' || c == u'\\'';\n"
                 'auto g = R"(C:\\path\\ "quoted" \\q)"; auto h = LR"tag(a)" b)tag";\n'
                 '// a comment with "an open quote and \\q\n'
                 '/* "and a block comment\'s */\n'
                 '#define M "line \\\n  continued"\n'
                 'auto i = u8"x", j = u"y", k = U"z";\n')
        self.assertEqual(problems(valid), [])


if __name__ == "__main__":
    unittest.main()
