# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""GUI suites own the sign-in status their connect rows depend on (``qa_auth``).

test_blender_signin_reconnect drew its "Sign in again" row from Angelo's start-up status check, run through its own
launch stand-in inside the product's worker thread. From 46804fbd49c the status check launches with
``keep_gui_programs=False`` while the stand-in asserted True: the worker swallowed the assertion into "Native runtime
unavailable", the row never appeared and the suite timed out on every build (first read as "no bundled runtime"). Covered:
``qa_auth``'s completion signal against a real ``NativeAuthentication`` whose worker is held late, ``own_status``, and
over every GUI suite: a suite that depends on a provider mode's connect rows owns their status, and nothing a suite
installs over a product function asserts (it records, and the suite fails on the record).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import threading
import unittest
from pathlib import Path
from unittest import mock

import host_waits
import qa_auth
from angelo_agent import authentication

HERE = Path(__file__).resolve().parent
UI = HERE.parents[2] / "scripts/addons_core/angelo_agent/ui.py"
SUITES = sorted(HERE.glob("test_blender_*.py"))
# A suite reads a provider mode's connect rows (or the status behind them) when it names one of these.
DEPENDS = ("welcome_connect", "welcome_reconnect", "connect_options(", "runtime_available(", "cli_installed(",
           "native_suggestions.suggestions(")
# Suites that name a row without depending on a mode's status, with the reason.
NO_MODE_STATUS = {
    "test_blender_fresh_chat_document.py": "checks only that some welcome row is drawn; the API-key row needs no status",
}


class LateProcess:
    """A status check's process that answers only when the test lets it (the worker held late)."""

    def __init__(self, release):
        self.release, self.returncode = release, None

    def communicate(self, input=None, timeout=None):
        assert self.release.wait(host_waits.CAP_S), "the test never released the worker"
        self.returncode = 1
        return "Not logged in", None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class CompletionSignal(unittest.TestCase):
    def setUp(self):
        self.auth = authentication.NativeAuthentication()
        self.release = threading.Event()
        self.launched = threading.Event()
        self.addCleanup(self.release.set)

        def late_launch(command, **kwargs):
            self.launched.set()
            return LateProcess(self.release)

        def command(mode, settings, state_dir, login=False, api_key=False):
            return ["stand-in", "login", "status"], {}

        for target, value in (("launch", late_launch), ("native_auth_command", command)):
            patcher = mock.patch.object(authentication, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, modes=qa_auth.STARTUP_MODES):
        import tempfile
        folder = self.enterContext(tempfile.TemporaryDirectory())
        for mode in modes:
            self.auth.start(mode, {}, folder)

    def pump_until_done(self):
        host_waits.until(lambda: not self.auth.running, pump=self.auth.pump, what="the released status check")

    def test_not_settled_while_the_worker_is_late_nor_before_its_result_is_applied(self):
        self.start()
        self.assertTrue(self.launched.wait(host_waits.CAP_S))
        self.assertFalse(qa_auth.startup_settled(self.auth), "a running check is not settled")
        self.release.set()
        for thread in [t for t in threading.enumerate() if t.name == "AngeloNativeAuthentication"]:
            thread.join(host_waits.CAP_S)
        self.assertFalse(qa_auth.startup_settled(self.auth),
                         "a finished worker whose result is not yet pumped is not settled: pump would replace the status")
        self.pump_until_done()
        self.assertTrue(qa_auth.startup_settled(self.auth))

    def test_a_check_that_could_not_start_is_settled_and_a_mode_never_checked_is_not(self):
        self.auth.status["codex_subscription"] = "Native runtime unavailable"   # start_native_checks' except branch
        self.assertFalse(qa_auth.startup_settled(self.auth), "claude_subscription and codex_api were never checked")
        self.auth.status.update(claude_subscription="Not connected", codex_api="Not connected")
        self.assertTrue(qa_auth.startup_settled(self.auth))

    def test_own_status_refuses_while_running_then_owns_and_records_classes_only(self):
        self.start(("codex_subscription",))
        self.assertTrue(self.launched.wait(host_waits.CAP_S))
        with self.assertRaises(RuntimeError):
            qa_auth.own_status(self.auth, {"codex_subscription": "Not connected"})
        self.release.set()
        self.pump_until_done()
        self.auth.status["codex_subscription"] = "Native runtime unavailable; check its executable path"
        evidence = {}
        qa_auth.own_status(self.auth, {"codex_subscription": "Not connected", "claude_subscription": "Not connected"},
                           evidence)
        self.assertEqual(self.auth.status["codex_subscription"], "Not connected")
        self.assertEqual(evidence, {"codex_subscription": "missing", "claude_subscription": "absent"})
        self.assertEqual(qa_auth.status_class("Not connected"), "found")

    def test_startup_modes_are_ui_registers(self):
        tree = ast.parse(UI.read_text(encoding="utf-8"))
        checked = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)
                    and "_authentication.start(mode" in ast.unparse(node)):
                checked.append(tuple(ast.literal_eval(node.iter)))
        self.assertEqual(checked, [qa_auth.STARTUP_MODES])


def installed_replacements(tree):
    """``(target, function)`` for each module-level ``owner.attr = function`` a suite installs over product code."""
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute)
                and isinstance(node.value, ast.Name) and node.value.id in functions):
            yield ast.unparse(node.targets[0]), node.value.id, functions


def reached(name, functions, seen=None):
    """The suite's own functions and classes ``name`` reaches by calling them by name (classes: all their methods)."""
    seen = set() if seen is None else seen
    if name in seen or name not in functions:
        return seen
    seen.add(name)
    for node in ast.walk(functions[name]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            reached(node.func.id, functions, seen)
    return seen


class SuitesOwnStatus(unittest.TestCase):
    def test_no_replacement_of_product_code_asserts(self):
        """An assertion in code the product calls (often on its worker, inside its own ``except``) becomes product
        state, never a failure: the replacement records the violation and the suite fails on the record."""
        found = []
        for path in SUITES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for target, name, functions in installed_replacements(tree):
                for part in sorted(reached(name, functions)):
                    lines = [n.lineno for n in ast.walk(functions[part]) if isinstance(n, ast.Assert)]
                    if lines:
                        found.append("%s: %s = %s reaches %s, which asserts at %s" % (path.name, target, name, part,
                                                                                       lines))
        self.assertEqual(found, [])

    def test_a_suite_that_reads_a_modes_rows_owns_its_status(self):
        missing = []
        for path in SUITES:
            text = path.read_text(encoding="utf-8")
            if path.name in NO_MODE_STATUS or not any(token in text for token in DEPENDS):
                continue
            stubbed = "ui._authentication.start =" in text   # No start-up check runs; the suite sets the status.
            stand_in_cli = "fake_provider_cli.install(" in text and "authentication.launch =" not in text
            owned = "qa_auth.own_status(" in text and "qa_auth.startup_settled(" in text
            if not (stubbed or stand_in_cli or owned):
                missing.append(path.name)
        self.assertEqual(missing, [], "these suites read a provider mode's connect rows from a status they do not own"
                                      " (qa_auth's docstring lists the three ways)")

    def test_the_exemptions_still_name_a_row(self):
        for name in NO_MODE_STATUS:
            self.assertTrue(any(token in (HERE / name).read_text(encoding="utf-8") for token in DEPENDS), name)


if __name__ == "__main__":
    unittest.main()
