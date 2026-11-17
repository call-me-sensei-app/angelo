# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing declared between ``UTF16_ENCODE`` and ``UTF16_UN_ENCODE`` is read after them: Windows has to compile.

``UTF16_ENCODE(path)`` opens a block that ``UTF16_UN_ENCODE(path)`` closes (``intern/utfconv/utfconv.hh``:
``if (1) { wchar_t *path_16 = ...`` and ``free(path_16); }``). The braces are in the macros, so the source reads as
one scope while a name declared between the two, the macro's own ``path_16`` included, is gone after the second.
Only a Windows compiler sees it: every other platform compiles the other branch of the file.

Evidence: ``BLI_open_replacing`` declared ``entry_at_name`` between the two and read it after them
(``2bee4cea8e4``, 2026-11-14). The macOS build, the macOS GTests, the host tests and VM CI were all green, because
none compiles that branch; the first Windows GTest build of the branch stopped in ``bf_blenlib`` with "use of
undeclared identifier 'entry_at_name'" (gcp-kit, build3, clang-cl).

What the reading is: comments and literals blanked; for each pair, the names declared at the start of a statement
between the two (a type, then the name) and the macro's ``<name>_16``, less the names the function declared before
the pair or declares again after it; a finding is such a name read in the rest of the scope the pair is in. It is
not a compiler: it finds this scope fault, not other Windows build errors. No Blender needed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE_FOLDERS = ("source", "intern")
SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hh", ".mm")
OPEN, CLOSE = re.compile(r"\bUTF16_ENCODE\s*\(\s*(\w+)\s*\)"), r"\bUTF16_UN_ENCODE\s*\(\s*%s\s*\)"
NOT_CODE = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])+'", re.S)
# A declaration at the start of a statement: a type (qualifiers, a name, template arguments), then the name.
DECLARATION = re.compile(
    r"(?:^|[;{}])\s*((?:(?:const|static|unsigned|signed|struct|volatile)\s+)*[A-Za-z_][\w:]*(?:\s*<[^;{}()]*>)?)"
    r"(?:\s*[*&]+\s*|\s+)(?:const\s+)?([A-Za-z_]\w*)\s*(?==|;|\[|\{|\(|,)", re.M)
NOT_A_TYPE = frozenset({"return", "else", "delete", "goto", "case", "new", "throw", "typedef", "using", "do"})
NOT_A_NAME = frozenset({"if", "while", "for", "switch", "return", "sizeof", "operator", "const"})


def code_only(text):
    """``text`` with comments and literals blanked, every offset and line kept."""
    return NOT_CODE.sub(lambda found: re.sub(r"[^\n]", " ", found.group(0)), text)


def declared(code):
    return {name for kind, name in DECLARATION.findall(code)
            if kind.split()[-1] not in NOT_A_TYPE and name not in NOT_A_NAME}


def scope_after(code, start):
    """The code from ``start`` to the end of the scope it is in (the first brace that closes more than it opened)."""
    depth = 0
    for index in range(start, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth < 0:
                return code[start:index]
    return code[start:]


def pairs(code):
    """``(opening match, closing match)`` of every use of the two macros in ``code`` (their definition is not one)."""
    for opened in OPEN.finditer(code):
        if code[code.rfind("\n", 0, opened.start()) + 1:opened.start()].lstrip().startswith("#"):
            continue
        closed = re.compile(CLOSE % re.escape(opened.group(1))).search(code, opened.end())
        if closed is not None:
            yield opened, closed


def read_after_the_block(text):
    """``[(line, name)]``: names declared inside a ``UTF16_ENCODE`` ... ``UTF16_UN_ENCODE`` pair and read after it."""
    code, found = code_only(text), []
    for opened, closed in pairs(code):
        body = code.rfind("\n{\n", 0, opened.start())   # The function's opening brace, at column 0.
        before = declared(code[max(body, 0):opened.start()])
        after = scope_after(code, closed.end())
        inside = declared(code[opened.end():closed.start()]) | {opened.group(1) + "_16"}
        for name in sorted(inside - before - declared(after)):
            use = re.search(r"(?<![\w.>])%s\b" % re.escape(name), after)
            if use:
                found.append((code.count("\n", 0, closed.end() + use.start()) + 1, name))
    return found


def scan(root):
    """``({file: [(line, name)]}, pairs read)`` for every source under ``root`` that uses the macros."""
    result, read = {}, 0
    for folder in SOURCE_FOLDERS:
        for path in sorted((root / folder).rglob("*")):
            if path.suffix in SUFFIXES and path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                if "UTF16_ENCODE" in text:
                    read += sum(1 for _pair in pairs(code_only(text)))
                    found = read_after_the_block(text)
                    if found:
                        result[path.relative_to(root).as_posix()] = found
    return result, read


FUNCTION = "int open_it(const char *path, int flags)\n{\n%s}\n"
SAMPLES = {
    # Declared between the two and read after: the fault.
    "source/a/declared_inside.cc": (FUNCTION % (
        "  HANDLE handle = INVALID_HANDLE_VALUE;\n  UTF16_ENCODE(path);\n  handle = CreateFileW(path_16, flags);\n"
        "  bool found = false;\n  if (handle == INVALID_HANDLE_VALUE) {\n    found = exists(path_16);\n  }\n"
        "  UTF16_UN_ENCODE(path);\n\n  return found ? 1 : 0;\n"), [(12, "found")]),
    # The macro's own wide string, read after it was freed and its block closed.
    "source/a/wide_string_after.cc": (FUNCTION % (
        "  UTF16_ENCODE(path);\n  touch(path_16);\n  UTF16_UN_ENCODE(path);\n  return length(path_16);\n"),
        [(6, "path_16")]),
    # A pointer declared inside, read after, in a nested scope of the function.
    "intern/b/pointer_inside.c": (FUNCTION % (
        "  if (flags) {\n    UTF16_ENCODE(path);\n    const wchar_t *tail = last(path_16);\n"
        "    UTF16_UN_ENCODE(path);\n    use(tail);\n  }\n  return 0;\n"), [(7, "tail")]),
    # Declared before the pair: in scope after it.
    "source/a/declared_before.cc": (FUNCTION % (
        "  bool found = false;\n  UTF16_ENCODE(path);\n  found = exists(path_16);\n  UTF16_UN_ENCODE(path);\n"
        "  return found ? 1 : 0;\n"), None),
    # Declared inside, and a new variable of the same name declared after the pair.
    "source/a/declared_again.cc": (FUNCTION % (
        "  UTF16_ENCODE(path);\n  int result = open_wide(path_16);\n  keep(result);\n  UTF16_UN_ENCODE(path);\n"
        "  int result = 0;\n  return result;\n"), None),
    # The same word as a member, in a comment and in a string after the pair: none is the variable.
    "source/a/not_the_variable.cc": (FUNCTION % (
        "  UTF16_ENCODE(path);\n  int size = measure(path_16);\n  keep(size);\n  UTF16_UN_ENCODE(path);\n"
        "  /* size is gone here. */\n  log(\"size\");\n  return state.size + other->size;\n"), None),
    # Read only inside the pair, and a later function that has its own variable of the name.
    "source/a/next_function.cc": (FUNCTION % (
        "  UTF16_ENCODE(path);\n  int count = measure(path_16);\n  keep(count);\n  UTF16_UN_ENCODE(path);\n"
        "  return 0;\n") + "int other(void)\n{\n  int count = 2;\n  return count;\n}\n", None),
    # Two pairs, one inside the other, each closed in turn.
    "source/a/two_pairs.cc": (
        "int rename_it(const char *from, const char *to)\n{\n  int result = -1;\n  UTF16_ENCODE(from);\n"
        "  UTF16_ENCODE(to);\n  result = move(from_16, to_16);\n  UTF16_UN_ENCODE(to);\n  UTF16_UN_ENCODE(from);\n"
        "  return result;\n}\n", None),
}


class Utf16BlockScope(unittest.TestCase):
    def test_no_source_reads_a_name_after_the_block_that_declared_it(self):
        self.assertTrue((ROOT / "intern/utfconv/utfconv.hh").is_file(), "not a full checkout")
        found, read = scan(ROOT)
        self.assertEqual(found, {}, "declare these before UTF16_ENCODE: the macros open and close a block, so "
                         "Windows does not compile a read after UTF16_UN_ENCODE")
        self.assertGreaterEqual(read, 30, "the reading found too few uses of the macros to mean anything")

    def test_the_macros_still_open_and_close_a_block(self):
        """The rule exists because of how the two are defined; if that changes, so must this reading."""
        text = (ROOT / "intern/utfconv/utfconv.hh").read_text(encoding="utf-8")
        opening = text.split("#define UTF16_ENCODE(in8str)", 1)[1].split("#define", 1)[0]
        closing = text.split("#define UTF16_UN_ENCODE(in8str)", 1)[1].split("#endif", 1)[0]
        self.assertIn("{", opening)
        self.assertIn("in8str##_16", opening)
        self.assertIn("}", closing)

    def test_the_reading_finds_the_fault_and_only_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (text, _expected) in SAMPLES.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            (root / "source/a/utfconv.hh").write_text(
                "#define UTF16_ENCODE(in8str) \\\n  if (1) { \\\n    wchar_t *in8str##_16 = widen(in8str)\n\n"
                "#define UTF16_UN_ENCODE(in8str) \\\n  free(in8str##_16); \\\n  } \\\n  (void)0\n")
            found, read = scan(root)
            self.assertEqual(found, {name: expected for name, (_text, expected) in SAMPLES.items() if expected})
            self.assertEqual(read, len(SAMPLES) + 1)   # Every sample has one pair; one has two.


if __name__ == "__main__":
    unittest.main()
