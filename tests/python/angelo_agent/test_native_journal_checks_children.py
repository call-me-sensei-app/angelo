# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The journal checks reach every Blender Angelo starts itself (B1 break-it review 2).

Inside Blender a check is on only by its variable (``journal_checks.enabled``: ``_bpy_path`` is built into Blender's
interpreter). A native suite sets the variables for the Angelo it launches, but a worker or headless Blender builds its
environment from a whitelist; in design A' the worker is the single writer of a hosted chat's journal and its pump runs
the changes-only owed saves, so a whitelist that dropped the switches left exactly those saves unchecked.
"""

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import headless_session, journal_checks, worker_process  # noqa: E402

PRODUCT = {"PATH": "/usr/bin:/bin", "HOME": "/tmp/angelo-home"}
SUITE = dict(PRODUCT, ANGELO_JOURNAL_TOUCHES="1", ANGELO_JOURNAL_VERIFY="1")


def enabled_in_blender(environ, name):
    """``journal_checks.enabled(name)`` as a Blender process with ``environ`` evaluates it."""
    with mock.patch.dict(os.environ, environ, clear=True), \
            mock.patch.object(journal_checks.sys, "builtin_module_names", sys.builtin_module_names + ("_bpy_path",)):
        return journal_checks.enabled(name)


def children(environ):
    """Every child Blender's environment Angelo builds from ``environ``."""
    return {"worker": worker_process.worker_environment(environ, tempfile.mkdtemp(), platform="darwin"),
            "worker (Windows)": worker_process.worker_environment(environ, tempfile.mkdtemp(), platform="win32"),
            "headless": headless_session.child_environment(environ, Path(tempfile.mkdtemp())),
            "headless (your preferences)": headless_session.child_environment(environ, Path(tempfile.mkdtemp()), True)}


class ChildBlendersKeepTheChecks(unittest.TestCase):
    def test_the_suites_own_blender_has_the_checks(self):
        """Control: the Angelo a native suite launches (the variables are set) has both checks."""
        for name in journal_checks.SWITCHES:
            self.assertTrue(enabled_in_blender(SUITE, name), name)

    def test_every_child_of_a_checked_angelo_has_every_check(self):
        for kind, kept in children(SUITE).items():
            for name in journal_checks.SWITCHES:
                with self.subTest(kind=kind, check=name):
                    self.assertTrue(enabled_in_blender(kept, name),
                                    "%s Blender runs its journal saves without %s" % (kind, name))

    def test_a_check_turned_off_stays_off_in_the_children(self):
        off = dict(PRODUCT, ANGELO_JOURNAL_TOUCHES="0", ANGELO_JOURNAL_VERIFY="0")
        for kind, kept in children(off).items():
            for name in journal_checks.SWITCHES:
                with self.subTest(kind=kind, check=name):
                    self.assertFalse(enabled_in_blender(kept, name))

    def test_the_products_children_have_no_check_and_no_unknown_value_passes(self):
        for environ in (PRODUCT, dict(PRODUCT, ANGELO_JOURNAL_TOUCHES="yes", ANGELO_JOURNAL_VERIFY="/path")):
            for kind, kept in children(environ).items():
                with self.subTest(kind=kind, environ=sorted(environ)):
                    self.assertFalse(set(journal_checks.SWITCHES) & set(kept))
                    for name in journal_checks.SWITCHES:
                        self.assertFalse(enabled_in_blender(kept, name))

    def test_every_whitelisted_child_environment_carries_the_switches(self):
        """Any function in the add-on that builds an environment from ``KEEP_ENVIRONMENT`` (a child Blender's
        whitelist) also adds ``journal_checks.carried``: a new kind of child cannot drop the checks unnoticed."""
        builders = []
        for path in sorted(ADDON.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
                if "KEEP_ENVIRONMENT" not in names:
                    continue
                calls = {sub.func.attr for sub in ast.walk(node)
                         if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)}
                builders.append(path.name + ":" + node.name)
                self.assertIn("carried", calls, "%s:%s builds a child's environment without journal_checks.carried"
                              % (path.relative_to(ROOT), node.name))
        self.assertGreaterEqual(len(builders), 2, builders)   # The worker's and the headless child's, at least.


if __name__ == "__main__":
    unittest.main()
