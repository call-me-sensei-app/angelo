# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every skip reason in the host suite says whether it is environmental, so no platform's run trips the skip limit.

The runner fails CI on any skip not marked with ``host_test_policy.environment_skip`` (``--max-skips 0``), and a skip
fires only where its condition holds: ``@unittest.skipIf(sys.platform == "win32", "...")`` passes on the Mac and
fails only Windows CI (VM CI ffe232b8253: win-3, 20 tests; win-4, 1; linux, a missing German locale), and a skip for
LFS pointers or an unset fixture variable failed only a macOS full run. This check reads the source of every file the
host suite loads, so any platform's run catches them: each reason goes through ``environment_skip`` (the host can't
provide it) or ``required_skip`` (CI must never take it), and a skip conditioned on the platform is environmental."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

from host_test_policy import ENVIRONMENT_PREFIX

HERE = Path(__file__).resolve().parent
POLICY_FILES = {"host_test_policy.py", "run_host_tests.py"}   # They implement the policy and pass reasons through.
PLATFORM_READS = {("sys", "platform"), ("os", "name"), ("platform", "system")}
MARKERS = {"environment_skip": "environment", "required_skip": "required"}


def _name(node):
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)


def _reason(call):
    """``(True, reason node)`` when ``call`` skips a test: a ``skipIf``/``skipUnless``/``unittest.skip`` decorator, a
    ``self.skipTest(...)`` or a ``SkipTest(...)`` raised in a test or at import; ``(False, None)`` otherwise."""
    function = _name(call.func)
    if function in ("skipIf", "skipUnless"):
        index = 1
    elif function == "skip" and isinstance(call.func, ast.Attribute) and _name(call.func.value) == "unittest":
        index = 0
    elif (function == "skipTest" and isinstance(call.func, ast.Attribute)) or function == "SkipTest":
        index = 0
    else:
        return False, None
    if len(call.args) > index:
        return True, call.args[index]
    return True, next((keyword.value for keyword in call.keywords if keyword.arg == "reason"), None)


def _kind(node, bound):
    """``"environment"``, ``"required"`` or None (unmarked) for a skip reason: a marker call, an ``"[environment] "``
    text, or a name bound to one at module level (``LISTENER = environment_skip(...)``, also as ``module.LISTENER``)."""
    if isinstance(node, ast.Call):
        return MARKERS.get(_name(node.func))
    if isinstance(node, ast.Constant):
        return "environment" if isinstance(node.value, str) and node.value.startswith(ENVIRONMENT_PREFIX) else None
    if isinstance(node, ast.JoinedStr):
        return _kind(node.values[0], bound) if node.values else None
    if isinstance(node, ast.BinOp):   # "[environment] ... %s" % error, or "[environment] " + text.
        return _kind(node.left, bound)
    return bound.get(_name(node)) if isinstance(node, (ast.Name, ast.Attribute)) else None


def _reads_platform(node, platform_names):
    return any((isinstance(part, ast.Attribute) and (_name(part.value), part.attr) in PLATFORM_READS)
               or (isinstance(part, ast.Name) and part.id in platform_names) for part in ast.walk(node))


def _assigned(tree):
    return [(target.id, statement.value) for statement in tree.body if isinstance(statement, ast.Assign)
            for target in statement.targets if isinstance(target, ast.Name)]


def unmarked_skips(sources):
    """``(where, reason)`` of every skip whose reason is unmarked, or required although its condition reads the
    platform (``sys.platform``, ``os.name``, ``platform.system()`` or a module constant made from them): the other
    platform's shard would fail on it. ``sources`` maps a file name to its source text."""
    trees = {name: ast.parse(text) for name, text in sources.items()}
    bound = {}
    for tree in trees.values():
        for name, value in _assigned(tree):
            kind = _kind(value, {})
            if kind:
                bound[name] = kind
    found = []
    for name, tree in sorted(trees.items()):
        platform_names = {target for target, value in _assigned(tree) if _reads_platform(value, set())}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            skips, reason = _reason(node)
            if not skips:
                continue
            kind = _kind(reason, bound) if reason is not None else None
            on_platform = _name(node.func) in ("skipIf", "skipUnless") and node.args and _reads_platform(
                node.args[0], platform_names)
            if kind is None or (on_platform and kind != "environment"):
                found.append((name, node.lineno, ast.unparse(reason) if reason is not None else None))
    return [("%s:%d" % (name, line), reason) for name, line, reason in sorted(found)]


def host_suite_sources():
    """The files the host runner loads (its ``test_native_*.py`` files and the helpers beside them) that can skip;
    parsing the rest of over a thousand files would only slow the suite."""
    sources = {}
    for path in sorted(HERE.glob("*.py")):
        loaded = path.name.startswith("test_native_") or not path.name.startswith("test_")
        if loaded and path.name not in POLICY_FILES:
            text = path.read_text(encoding="utf-8")
            if "skip" in text or "SkipTest" in text or ENVIRONMENT_PREFIX in text:
                sources[path.name] = text
    return sources


class SkipReasonTests(unittest.TestCase):
    def test_every_skip_in_the_host_suite_is_marked(self):
        self.assertEqual(unmarked_skips(host_suite_sources()), [],
                         "mark each reason host_test_policy.environment_skip(...) (the host can't provide it) or "
                         "required_skip(...) (CI must never take it); a platform skip is always environmental")

    def test_each_way_to_skip_is_read(self):
        source = '''
import os, platform, sys, unittest
from unittest import SkipTest
WINDOWS = os.name == "nt"
raise unittest.SkipTest("a platform this file is not for")
@unittest.skipIf(sys.platform == "win32", "no flock on Windows")
class A(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "libproc or /proc")
    def test_a(self): pass
    @unittest.skipIf(WINDOWS, reason="POSIX only")
    def test_b(self): pass
    @unittest.skipUnless(platform.system() == "Darwin", required_skip("macOS only"))
    def test_c(self): pass
    @unittest.skip("not yet")
    def test_d(self): pass
    def test_e(self):
        self.skipTest("LFS pointers in this checkout")
    def test_f(self):
        raise SkipTest("set ANGELO_PYTHON_WHEELS_DIR")
    def test_g(self):
        checker.skip(offer)   # Not a test skip: another object's method.
'''
        self.assertEqual([where for where, _ in unmarked_skips({"f.py": source})],
                         ["f.py:%d" % line for line in (5, 6, 8, 10, 12, 14, 17, 19)])

    def test_marked_skips_pass_whichever_way_they_are_marked(self):
        helper = 'from host_test_policy import environment_skip\nLISTENER = environment_skip("Unix socket")\n'
        source = '''
import os, sys, unittest
import helper
from host_test_policy import environment_skip, required_skip
POSIX_ONLY = environment_skip("POSIX files")
NUMPY = required_skip("NumPy is installed in CI")
numpy = None
class A(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", environment_skip("no flock on Windows"))
    def test_a(self): pass
    @unittest.skipIf(os.name == "nt", POSIX_ONLY)
    def test_b(self): pass
    @unittest.skipIf(sys.platform == "win32", helper.LISTENER)
    def test_c(self): pass
    @unittest.skipIf(os.name == "nt", "[environment] POSIX only")
    def test_d(self): pass
    @unittest.skipIf(numpy is None, required_skip("NumPy is not installed"))
    def test_e(self): pass
    @unittest.skipIf(numpy is None, NUMPY)
    def test_f(self): pass
    def test_g(self):
        self.skipTest(environment_skip("this host can't make a symbolic link: %s" % "EPERM"))
        self.skipTest("[environment] no 8.3 alias on this volume: %s" % "fsutil")
'''
        self.assertEqual(unmarked_skips({"helper.py": helper, "f.py": source}), [])


if __name__ == "__main__":
    unittest.main()
