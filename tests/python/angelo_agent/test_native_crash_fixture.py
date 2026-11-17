# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A fixture that must crash natively does, on every platform (``native_crash``).

On Windows ctypes runs every foreign call inside an SEH guard that turns an access violation into ``OSError``. So
``ctypes.string_at(0)``, a native crash on macOS and Linux, is a caught Python error there: the crashing add-on of
the update carry-over gate and of test_blender_settings_import's crashing group only "did not load" on Windows,
and the safe-start proof never ran there (gcp-kit, 2026-11-11). Covered:

* ``native_crash.crash()`` ends a fresh Python process with a real fault: SIGSEGV on POSIX, an unhandled
  STATUS_ACCESS_VIOLATION on Windows, never a Python exception and never a clean exit;
* ``native_crash.SOURCE``, the same function as text that a fixture add-on embeds, does the same;
* no fixture fakes a native crash with a call ctypes can catch, outside suites that run only on POSIX;
* a suite that crashes a process on purpose takes the crash log it leaves. The suite runner reads every
  ``*.crash.txt`` in a suite's folder as a crash of the run, so test_blender_gpu_startup_fallback, whose checks all
  passed, was reported CRASHED on Windows (gcp-kit, 2026-11-13; the same log was left on macOS, where nothing
  looked). ``native_crash.keep_crash_logs`` moves the logs aside under a name the runner does not read.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_checks  # noqa: E402
import native_suite_manifest as manifest  # noqa: E402

ACCESS_VIOLATION = 0xC0000005
# ctypes calls that fault on POSIX but are caught (OSError) on Windows.
CATCHABLE = {"string_at", "memmove", "memset", "wstring_at"}


def died_of_a_fault(returncode):
    if sys.platform == "win32":
        return (returncode & 0xFFFFFFFF) == ACCESS_VIOLATION
    return returncode in (-signal.SIGSEGV, -signal.SIGBUS)


def takes_crash_logs(text):
    """Does this module's code move crash logs aside? A call of ``keep_crash_logs``, or a function that names
    ``.crash.txt`` in its code and renames, moves, copies or removes files. Docstrings and comments do not count."""
    tree = ast.parse(text)
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, scopes) and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)}

    def called(node):
        return {call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                for call in ast.walk(node) if isinstance(call, ast.Call)}

    if "keep_crash_logs" in called(tree):
        return True
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names_a_log = any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                              and ".crash.txt" in node.value and id(node) not in docstrings
                              for node in ast.walk(function))
            if names_a_log and called(function) & {"rename", "unlink", "move", "remove", "copy2"}:
                return True
    return False


def run(code):
    return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)


class CrashTests(unittest.TestCase):
    def test_crash_ends_the_process_with_a_real_fault(self):
        done = run("import sys; sys.path.insert(0, %r)\nimport native_crash\nnative_crash.crash()\n" % str(HERE))
        self.assertTrue(died_of_a_fault(done.returncode), (done.returncode, done.stderr[-400:]))
        self.assertNotIn("Traceback", done.stderr)

    def test_the_embedded_source_does_the_same(self):
        sys.path.insert(0, str(HERE))
        import native_crash
        done = run(native_crash.SOURCE + "\ncrash()\n")
        self.assertTrue(died_of_a_fault(done.returncode), (done.returncode, done.stderr[-400:]))
        self.assertNotIn("Traceback", done.stderr)


def catchable_faults(path):
    """``ctypes.<call>(0, ...)`` calls in ``path`` that only fault on POSIX."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CATCHABLE
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0):
            found.append("%s:%d" % (path.name, node.lineno))
    for node in ast.walk(tree):   # Fixture add-on source kept as text.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for call in CATCHABLE:
                if "ctypes.%s(0" % call in node.value:
                    found.append("%s:%d (in text)" % (path.name, node.lineno))
    return found


class SourceTests(unittest.TestCase):
    def test_no_fixture_fakes_a_native_crash_with_a_call_ctypes_can_catch(self):
        posix_only = {suite.script for suite in manifest.SUITES
                      if suite.platforms and "win32" not in suite.platforms}
        found = []
        for path in sorted(HERE.glob("*.py")):
            if path.name in posix_only or path.name in {"native_crash.py", Path(__file__).name}:
                continue
            found.extend(catchable_faults(path))
        self.assertEqual(found, [], "use native_crash.crash() (or native_crash.SOURCE in an add-on's text)")

    def test_crash_on_load_fixtures_use_the_helper(self):
        for path in sorted(HERE.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ANGELO_QA_CRASH_ON_LOAD" in text and "SOURCE" in text and path.name != Path(__file__).name:
                with self.subTest(path.name):
                    self.assertTrue("native_crash" in text, "a crash-on-load fixture embeds native_crash.SOURCE")


class CrashLogTests(unittest.TestCase):
    def test_kept_crash_logs_are_out_of_the_runners_sight_and_still_there(self):
        import native_crash
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "tmp/deeper").mkdir(parents=True)
            (out / "tmp/angelo.crash.txt").write_text("# backtrace one")
            (out / "tmp/deeper/scene.crash.txt").write_text("# backtrace two")
            self.assertEqual(len(native_suite_checks.find_crash_logs(out)), 2)
            kept = native_crash.keep_crash_logs(out / "tmp", out, "crashes")
            self.assertEqual(sorted(kept), ["crashes-angelo.crash-log.txt", "crashes-scene.crash-log.txt"])
            self.assertEqual(native_suite_checks.find_crash_logs(out), [], "nothing the runner reads as a crash")
            self.assertEqual((out / "crashes-angelo.crash-log.txt").read_text(), "# backtrace one")
            self.assertEqual(native_crash.keep_crash_logs(out / "tmp", out, "crashes"), [], "none left to keep")

    def test_two_logs_of_one_name_are_both_kept(self):
        import native_crash
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            for folder in ("a", "b"):
                (out / "tmp" / folder).mkdir(parents=True)
                (out / "tmp" / folder / "angelo.crash.txt").write_text(folder)
            kept = native_crash.keep_crash_logs(out / "tmp", out, "crashes")
            self.assertEqual(len(set(kept)), 2)
            self.assertEqual(sorted((out / name).read_text() for name in kept), ["a", "b"])
            self.assertEqual(native_suite_checks.find_crash_logs(out), [])

    def test_a_suite_that_crashes_a_process_on_purpose_takes_its_crash_logs(self):
        texts = {path.stem: path.read_text(encoding="utf-8", errors="replace") for path in HERE.glob("*.py")}
        crashes = re.compile(r"native_crash\.(crash|SOURCE)|ANGELO_QA_CRASH_ON_LOAD")
        found, checked = [], 0
        for suite in manifest.SUITES:
            name = Path(suite.script).stem
            seen, todo = set(), [name]
            while todo:   # The suite and the helper modules beside it that it imports.
                module = todo.pop()
                if module in seen or module not in texts:
                    continue
                seen.add(module)
                todo += re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", texts[module], re.MULTILINE)
            seen -= {"native_crash", "native_suite_checks"}   # The helper itself, and the runner's own reader.
            if any(crashes.search(texts[module]) for module in seen):
                checked += 1
                if not any(takes_crash_logs(texts[module]) for module in seen):
                    found.append(suite.script)
        self.assertEqual(found, [], "after its crash is checked, the suite calls native_crash.keep_crash_logs: "
                         "the runner counts every *.crash.txt left in its folder as a crash of the run")
        self.assertGreaterEqual(checked, 2, "the suites that crash on purpose were not found")

    def test_only_code_that_moves_the_logs_counts_as_taking_them(self):
        self.assertTrue(takes_crash_logs("import native_crash\nkept = native_crash.keep_crash_logs(a, b, 'c')\n"))
        self.assertTrue(takes_crash_logs("def tidy(out):\n    for log in out.rglob('*.crash.txt'):\n"
                                         "        log.rename(out / 'kept.txt')\n"))
        self.assertTrue(takes_crash_logs("def tidy(log):\n    if log.name.endswith('.crash.txt'):\n"
                                         "        log.unlink()\n"))
        # A mention is not a move: a docstring, a comment, a name that is only read or renamed as text.
        self.assertFalse(takes_crash_logs('"""Calls keep_crash_logs() and removes every *.crash.txt."""\n'))
        self.assertFalse(takes_crash_logs("def look(out):\n    \"\"\"Unlinks *.crash.txt.\"\"\"\n"
                                          "    return list(out.rglob('*.log'))\n"))
        self.assertFalse(takes_crash_logs("# keep_crash_logs(out)\ndef look(out):\n    return ["
                                          "p.name.replace('.crash.txt', '') for p in out.rglob('*.crash.txt')]\n"))
        self.assertFalse(takes_crash_logs("def a(out):\n    return list(out.rglob('*.crash.txt'))\n"
                                          "def b(path):\n    path.unlink()\n"))


class ThreadTests(unittest.TestCase):
    def test_the_fault_is_on_the_calling_thread_on_windows_too(self):
        # A crash while start-up scripts run is the scripts' only on the main thread
        # (wm_angelo_gpu_startup.cc), so a fixture add-on has to fault on the thread that loads it, as a native
        # add-on does. On Windows that is Python's own read of address 0; the thread ctypes did not start is
        # only for a Python without it.
        import faulthandler
        import native_crash
        self.assertTrue(hasattr(faulthandler, "_read_null"), "this Python cannot fault on the calling thread "
                        "outside ctypes: on Windows the fixture would fault on a thread of its own")
        source = native_crash.SOURCE
        self.assertIn("faulthandler._read_null", source)
        self.assertLess(source.index("read_null()"), source.index("CreateThread("))


if __name__ == "__main__":
    unittest.main()
