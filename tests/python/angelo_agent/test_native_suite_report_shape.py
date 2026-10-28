# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every suite the manifest lists writes its named checks in a shape the native runner reads, and starts no tool its
platforms lack.

``native_suite_checks._check_reasons`` takes a check as exactly ``true`` (a dict of checks) or as a list item with
``"passed": true``. Evidence: two parallel suites wrote each check as an object ``{"passed": ..., "details": ...}``,
so ``run_native_suites.py`` read every check as failed although all of them passed (release-106's candidate run,
2026-10-25), while a run that read only exit codes passed them. Static: no suite runs. One rule for every listed suite,
whatever its name: a check stored under a ``checks`` mapping is never written as a dict literal. Likewise a suite the
manifest lets run on Windows never starts a POSIX-only tool: worker_pool's memory figure ran ``ps`` and ended the suite
with WinError 2 on build4 (2026-10-25); ``qa_machine.resident_mb`` reads it on every platform. Nor does such a suite
use a signal or process call Windows lacks (``signal.SIGKILL``, ``os.killpg``...) outside a function that decides by
platform: worker_pool killed its workers with ``os.kill(pid, signal.SIGKILL)``, an AttributeError on Windows, so its
died-worker and two-parts checks never ran there (release-106's C1 run); ``Popen.kill()`` works everywhere.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import native_suite_checks  # noqa: E402
import native_suite_manifest as manifest  # noqa: E402


def _is_checks(node):
    """Whether ``node`` is a ``checks`` mapping: a name ``checks``, or a subscript or attribute ending in it."""
    if isinstance(node, ast.Name):
        return node.id == "checks"
    if isinstance(node, ast.Attribute):
        return node.attr == "checks"
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value == "checks"
    return False


def object_checks(source):
    """Lines where a check under a ``checks`` mapping is assigned a dict (literal or comprehension)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, ast.AnnAssign) else []
        for target in targets:
            if (isinstance(target, ast.Subscript) and _is_checks(target.value)
                    and isinstance(node.value, (ast.Dict, ast.DictComp))):
                found.append(node.lineno)
    return found


# Command-line tools a Windows machine does not have (POSIX process, file and memory tools).
POSIX_ONLY = ("ps", "pgrep", "pkill", "lsof", "footprint", "kill", "killall", "vm_stat")
_STARTS = re.compile(r"""\[\s*["'](%s)["']\s*,""" % "|".join(POSIX_ONLY))
# Signals Windows' ``signal`` module has; any other ``signal.SIG*`` name, and these ``os`` calls, are POSIX-only.
WINDOWS_SIGNALS = {"SIGTERM", "SIGINT", "SIGABRT", "SIGFPE", "SIGILL", "SIGSEGV", "SIGBREAK", "SIG_IGN", "SIG_DFL"}
POSIX_OS = {"killpg", "getpgid", "setsid", "setpgrp", "fork", "getuid", "geteuid"}
# A function that names one of these decides by platform (``hasattr(signal, "SIGSTOP")``, ``os.name != "posix"``).
_GUARDS = ("hasattr(signal", "os.name", "sys.platform")


def posix_only_calls(source):
    """Lines using a signal or ``os`` call Windows lacks, outside any function whose source decides by platform."""
    tree, lines, found = ast.parse(source), source.splitlines(), []

    def visit(node, guarded):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            text = "\n".join(lines[node.lineno - 1:node.end_lineno])
            guarded = guarded or any(guard in text for guard in _GUARDS)
        if (not guarded and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and ((node.value.id == "signal" and node.attr.startswith("SIG") and node.attr not in WINDOWS_SIGNALS)
                     or (node.value.id == "os" and node.attr in POSIX_OS))):
            found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return found


class ReportShapeTests(unittest.TestCase):
    def test_no_suite_that_runs_on_windows_starts_a_posix_only_tool(self):
        started = {}
        for suite in manifest.SUITES:
            path = HERE / suite.script
            if path.is_file() and suite.runs_on("win32"):
                lines = [n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                         if _STARTS.search(line)]
                if lines:
                    started[suite.script] = lines
        self.assertEqual(started, {}, "a tool Windows lacks: FileNotFoundError there (use qa_machine.resident_mb)")

    def test_no_suite_that_runs_on_windows_uses_a_signal_windows_lacks(self):
        used = {}
        for suite in manifest.SUITES:
            path = HERE / suite.script
            if path.is_file() and suite.runs_on("win32"):
                lines = posix_only_calls(path.read_text(encoding="utf-8"))
                if lines:
                    used[suite.script] = lines
        self.assertEqual(used, {}, "AttributeError on Windows: the checks after it never run (kill with Popen.kill())")

    def test_no_listed_suite_writes_a_check_as_an_object(self):
        written = {}
        for suite in manifest.SUITES:
            path = HERE / suite.script
            if path.is_file():
                lines = object_checks(path.read_text(encoding="utf-8"))
                if lines:
                    written[suite.script] = lines
        self.assertEqual(written, {}, "a check the runner reads as failed even when it passed")

    def test_the_rule_matches_what_the_runner_reads(self):
        # The shape this test refuses is the one the runner fails; the shapes it allows are the ones the runner takes.
        self.assertEqual(object_checks('report["checks"][name] = {"passed": True, "details": None}'), [1])
        self.assertEqual(object_checks('checks[name] = {k: v for k, v in pairs}'), [1])
        self.assertEqual(object_checks('report["checks"][name] = bool(passed)\nreport["details"][name] = {}'), [])
        self.assertEqual(object_checks('report["checks"].append({"name": name, "passed": True})'), [])
        self.assertEqual(native_suite_checks._check_reasons({"a": {"passed": True, "details": None}}, False),
                         ["failed checks: a"])
        self.assertEqual(native_suite_checks._check_reasons({"a": True}, False), [])
        self.assertEqual(native_suite_checks._check_reasons([{"name": "a", "passed": True}], False), [])
        self.assertTrue(_STARTS.search('subprocess.run(["ps", "-o", "rss=", "-p", str(pid)])'))
        self.assertFalse(_STARTS.search('subprocess.run([sys.executable, "-c", "pass"])'))
        self.assertEqual(posix_only_calls("os.kill(pid, signal.SIGKILL)\nos.killpg(pid, signal.SIGTERM)"), [1, 2])
        self.assertEqual(posix_only_calls("process.kill()\nos.kill(pid, signal.SIGTERM)"), [])
        self.assertEqual(posix_only_calls('def f():\n    if hasattr(signal, "SIGSTOP"):\n        os.kill(p, signal.SIGSTOP)'),
                         [])
        self.assertEqual(posix_only_calls("def f():\n    if os.name != 'posix':\n        return\n"
                                          "    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)"), [])


if __name__ == "__main__":
    unittest.main()
