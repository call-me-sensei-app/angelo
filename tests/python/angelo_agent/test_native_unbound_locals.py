# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No shipped function reads a name before the binding that makes it local (UnboundLocalError), on any host.

2.0.1: ``fab_browser_profile.ProfileLease.cancel_pending`` read the module's ``owned_paths`` a few lines before its own
``from . import owned_paths``, so every call raised UnboundLocalError on every platform since 2.0.0. Its tests run on
macOS only (the macOS profile store) and VM CI runs Linux and Windows, so nothing ran them. This check is plain
``ast`` (``unbound_locals``) over every Python file under scripts/ and source/, so every host's suite runs it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import unbound_locals  # noqa: E402

# Reads that only an earlier pass of a loop can precede, each safe for the reason given. A new finding, or one of these
# changing, fails: look at it, fix it, or add it here with why it can never raise.
KNOWN = {
    ("scripts/modules/bpy/utils/__init__.py", "unregister_tool.tool_list_clean", "prev_is_none"):
        "Upstream Blender. Trailing separators are trimmed first, so the first pass's is_none is False and "
        "``is_none and prev_is_none`` never reads it before the loop has bound it.",
}


def shipped_python(root=ROOT):
    """Every Python file under scripts/ and source/ (caches and virtual environments excluded)."""
    for folder in ("scripts", "source"):
        for path in sorted((root / folder).rglob("*.py")):
            parts = set(path.relative_to(root).parts)
            if not parts & {"__pycache__", ".venv", "venv", "site-packages"}:
                yield path


class ScannerTests(unittest.TestCase):
    def found(self, source):
        return [(item.name, item.shadowing) for item in unbound_locals.findings(source)]

    def test_the_2_0_1_case_a_local_import_after_a_use_of_the_module_s_name(self):
        source = ("from . import owned_paths\n"
                  "class Lease:\n"
                  "    def cancel(self):\n"
                  "        owned_paths.require(self)\n"
                  "        import shutil\n"
                  "        from . import owned_paths\n"
                  "        shutil.rmtree(owned_paths.path)\n")
        self.assertEqual(self.found(source), [("owned_paths", True)])

    def test_every_kind_of_binding_counts(self):
        for binding in ("x = 1", "import x", "from m import x", "x += 1", "for x in []: pass", "with f() as x: pass",
                        "def x(): pass", "class x: pass", "try:\n        pass\n    except E as x:\n        pass",
                        "(x := 1)", "del x"):
            with self.subTest(binding=binding):
                source = "x = 0\ndef f():\n    print(x)\n    %s\n" % binding
                self.assertEqual(self.found(source), [("x", True)])

    def test_code_python_runs_is_never_reported(self):
        for body in ("x = 1\n    print(x)",                           # Bound first.
                     "global x\n    print(x)\n    x = 1",              # Declared global.
                     "x = [x for x in y]\n    print(x)",               # A comprehension's own scope.
                     "def g():\n        return x\n    x = 1",          # A closure reads it later.
                     "print(y)\n    def g(a=y): pass",                  # Defaults run here, y is the module's.
                     "lambda: x\n    x = 1",                            # The lambda's body is its own scope.
                     "if a:\n        x = 1\n    else:\n        x = 2\n    print(x)"):
            with self.subTest(body=body):
                source = "y = 1\ndef f(a=0):\n    %s\n" % body
                self.assertEqual(self.found(source), [], body)

    def test_an_assignment_reads_its_value_first(self):
        self.assertEqual(self.found("x = 0\ndef f():\n    x = x + 1\n"), [("x", True)])

    def test_a_read_only_a_loop_s_earlier_pass_precedes_is_reported_not_shadowing(self):
        source = "def f(items):\n    for item in items:\n        if prev: pass\n        prev = item\n"
        self.assertEqual(self.found(source), [("prev", False)])


class ShippedCodeTests(unittest.TestCase):
    def test_no_shipped_function_reads_a_name_before_the_binding_that_makes_it_local(self):
        """Failed on ffe232b8253: fab_browser_profile.ProfileLease.cancel_pending, owned_paths at line 245."""
        found, scanned = {}, 0
        for path in shipped_python():
            relative = path.relative_to(ROOT).as_posix()
            try:
                items = unbound_locals.findings(path.read_bytes().decode("utf-8"), relative)
            except SyntaxError:
                continue   # Not Python 3 this interpreter parses (a template): compiled nowhere, so it cannot raise.
            scanned += 1
            for item in items:
                found[(relative, item.function, item.name)] = "%s:%d %s" % (relative, item.line, item)
        self.assertGreater(scanned, 1000, "the shipped scripts were found")
        self.assertEqual(sorted(text for key, text in found.items() if key not in KNOWN), [],
                         "a function reads a name before the binding that makes it local: UnboundLocalError")
        self.assertEqual(sorted(set(KNOWN) - set(found)), [], "a known case changed or went: update KNOWN")


if __name__ == "__main__":
    unittest.main()
