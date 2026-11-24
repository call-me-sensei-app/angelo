# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's start-up sign-in checks run only once the native guard knows Angelo's private folders.

A check whose runtime profile is Angelo's own (an API-key connection, a connection that doesn't reuse the person's
login) builds that profile through ``storage.private_profile``, which asks C whether the folder is private
(``owned_replace._private``, the guard's ``judge_path``). The guard learns those folders in
``script_confinement_blender.install_app`` (``guard.add_roots``). ``ui.register`` ran the checks before that, so every
such check was refused with ``ProfileOutside`` at start-up: its status read "Native runtime unavailable" / "Not
connected" until the person pressed Refresh status (seen in test_blender_signin_reconnect's report, codex_api). The
add-on's register now installs the guard's folders first, then starts the checks (``ui.start_native_checks``).
Pinned by source (``ast``): no ``_authentication.start`` is reachable from ``ui.register``, and ``__init__.register``
calls ``ui.start_native_checks`` after ``install_app`` and ``register_fixed_folders``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def module(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


def function(tree, name):
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def reached(tree, name, seen=None):
    """``name`` and every module-level function of ``tree`` it calls by name, transitively."""
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    seen = set() if seen is None else seen
    if name in seen or name not in functions:
        return seen
    seen.add(name)
    for node in ast.walk(functions[name]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            reached(tree, node.func.id, seen)
    return seen


def starts(node):
    """Line numbers of each ``_authentication.start(...)`` call inside ``node``."""
    return [call.lineno for call in ast.walk(node) if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute) and call.func.attr == "start"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "_authentication"]


def first_call(node, attribute, owner):
    """The line of the first ``owner.attribute(...)`` call in ``node``, or None."""
    lines = [call.lineno for call in ast.walk(node) if isinstance(call, ast.Call)
             and isinstance(call.func, ast.Attribute) and call.func.attr == attribute
             and isinstance(call.func.value, ast.Name) and call.func.value.id == owner]
    return min(lines) if lines else None


class StartupChecksAfterGuardFolders(unittest.TestCase):
    def test_ui_register_starts_no_sign_in_check(self):
        tree = module("ui.py")
        found = {name: starts(function(tree, name)) for name in sorted(reached(tree, "register"))}
        self.assertEqual({name: lines for name, lines in found.items() if lines}, {},
                         "ui.register runs before install_app: a check started there is refused its private profile")

    def test_the_add_on_starts_them_after_the_guard_knows_the_folders(self):
        register = function(module("__init__.py"), "register")
        ui_register = first_call(register, "register", "ui")
        install = first_call(register, "install_app", "script_confinement_blender")
        fixed = first_call(register, "register_fixed_folders", "script_confinement_blender")
        checks = first_call(register, "start_native_checks", "ui")
        self.assertIsNotNone(checks, "__init__.register never starts the start-up sign-in checks")
        self.assertTrue(ui_register < install < checks and fixed < checks, (ui_register, install, fixed, checks))

    def test_start_native_checks_checks_every_mode_and_every_own_profile_record(self):
        """Every check moves, not one mode's: the three start-up modes and each connection with its own profile."""
        tree = module("ui.py")
        body = function(tree, "start_native_checks")
        self.assertEqual(len(starts(body)), 2)
        modes = [tuple(ast.literal_eval(node.iter)) for node in ast.walk(body)
                 if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)]
        self.assertEqual(modes, [("codex_subscription", "claude_subscription", "codex_api")])
        self.assertIn("connections().records.values()", ast.unparse(body))


if __name__ == "__main__":
    unittest.main()
