# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A test that clears the process environment keeps what the operating system gives every process
(``platform_env.cleared``).

``patch.dict(os.environ, {...}, clear=True)`` removes everything, ``SYSTEMROOT`` included. On Windows the first
SSL context a process makes then fails ("[SSL] unknown error"): a test that reaches TLS inside such a block
passed in a shard only because an earlier test had started OpenSSL first, and failed alone (found on a Windows
leg by the first-hour lane; 25 host test files cleared the environment this way, among them the team and
endpoint tests that open TLS connections). On macOS and Linux the same block removed ``HOME``, so
``Path.home()`` fell back to the account's real folder, outside the suite's isolated one.

Pinned:

* ``cleared(values, **named)`` clears what a test controls and keeps the variables every real process has: the
  list Angelo's own child processes keep (``angelo_agent.essential_environment``: ``SYSTEMROOT``, ``COMSPEC``,
  ``TEMP`` and the profile folders on Windows, ``HOME`` and ``TMPDIR`` elsewhere, ``PATH`` everywhere), one list
  for the product and its tests; a name the test gives replaces the kept one, ``without`` drops one on purpose,
  and the environment is as it was afterwards;
* a process whose FIRST SSL context is made inside ``cleared()`` makes it, on every platform;
* no host test file clears ``os.environ`` by itself: the rule that keeps the next one from doing it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import platform_env
from angelo_agent import essential_environment  # noqa: E402  (suite_isolation put the add-ons on the path)

HERE = Path(__file__).resolve().parent
NAMES = tuple(dict.fromkeys(essential_environment.POSIX + essential_environment.WINDOWS))   # Every platform's.


class Cleared(unittest.TestCase):
    def setUp(self):
        present = {name: "kept " + name for name in NAMES}
        entered = mock.patch.dict(os.environ, dict(present, ANGELO_SOMETHING="set", HTTPS_PROXY="http://proxy"))
        entered.start()
        self.addCleanup(entered.stop)
        self.before = dict(os.environ)

    def test_the_names_are_the_ones_every_child_process_of_angelo_keeps(self):
        self.assertEqual(set(platform_env.ESSENTIALS), set(NAMES), "one list: a name every process needs goes there")
        for name in ("SYSTEMROOT", "COMSPEC", "PATH", "TEMP", "TMP", "USERPROFILE", "HOME", "TMPDIR"):
            self.assertIn(name, NAMES)

    def test_it_clears_what_a_test_controls_and_keeps_what_every_process_has(self):
        with platform_env.cleared({"ANGELO_GATEWAY_URL": "http://localhost"}, ANGELO_MODE="x"):
            self.assertEqual(os.environ["ANGELO_GATEWAY_URL"], "http://localhost")
            self.assertEqual(os.environ["ANGELO_MODE"], "x")
            for gone in ("ANGELO_SOMETHING", "HTTPS_PROXY"):
                self.assertNotIn(gone, os.environ)
            for name in NAMES:
                self.assertEqual(os.environ.get(name), "kept " + name, name)
        self.assertEqual(dict(os.environ), self.before)

    def test_a_name_the_test_gives_replaces_the_kept_one_and_without_drops_one(self):
        with platform_env.cleared(PATH="/usr/bin", HOME="/synthetic"):
            self.assertEqual((os.environ["PATH"], os.environ["HOME"]), ("/usr/bin", "/synthetic"))
            self.assertEqual(os.environ["SYSTEMROOT"], "kept SYSTEMROOT")
        with platform_env.cleared(without=("HOME", "path")):
            self.assertNotIn("HOME", os.environ)
            self.assertNotIn("PATH", os.environ)
            self.assertEqual(os.environ["TMPDIR"], "kept TMPDIR")
        self.assertEqual(dict(os.environ), self.before)

    def test_it_is_a_patch_like_the_one_it_replaces(self):
        patcher = platform_env.cleared({"A": "1"})
        patcher.start()
        try:
            self.assertEqual(os.environ["A"], "1")
            self.assertNotIn("ANGELO_SOMETHING", os.environ)
        finally:
            patcher.stop()
        self.assertEqual(dict(os.environ), self.before)

        @platform_env.cleared(B="2")
        def decorated():
            return os.environ.get("B"), "ANGELO_SOMETHING" in os.environ
        self.assertEqual(decorated(), ("2", False))

    def test_essential_environ_gives_the_same_names_for_a_child_process(self):
        kept = platform_env.essential_environ(EXTRA="1")
        self.assertEqual(kept["EXTRA"], "1")
        self.assertNotIn("ANGELO_SOMETHING", kept)
        for name in NAMES:
            self.assertEqual(kept.get(name), "kept " + name, name)


class TheFirstSecureContext(unittest.TestCase):
    def test_a_process_can_make_its_first_ssl_context_inside_a_cleared_environment(self):
        """The Windows failure itself: the child is new, so the context it makes in the block is its first."""
        code = ("import sys\nsys.path.insert(0, %r)\nimport platform_env\n"
                "with platform_env.cleared({'ANGELO_GATEWAY_URL': 'https://example.invalid'}):\n"
                "    import ssl\n    context = ssl.create_default_context()\n"
                "print('made', type(context).__name__)\n" % str(HERE))
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "made SSLContext"), done.stderr[-2000:])


class TheRule(unittest.TestCase):
    def cleared_by_itself(self, source):
        """Line numbers of ``patch.dict(os.environ, ..., clear=True)`` calls in ``source``."""
        found = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            clears = any(key.arg == "clear" and not (isinstance(key.value, ast.Constant) and not key.value.value)
                         for key in node.keywords)
            target = ast.unparse(node.args[0])
            if name == "dict" and clears and target.replace('"', "").replace("'", "") in ("os.environ", "environ"):
                found.append(node.lineno)
        return found

    def test_the_scanner_sees_every_spelling(self):
        for spelling in ('patch.dict(os.environ, {}, clear=True)', 'mock.patch.dict("os.environ", env, clear=True)',
                         'unittest.mock.patch.dict(os.environ,\n    dict(a, B="1"),\n    clear=True)',
                         'patch.dict(os.environ, values, clear=flag)', "patch.dict(environ, {}, clear=True)"):
            self.assertEqual(len(self.cleared_by_itself(spelling)), 1, spelling)
        for fine in ('patch.dict(os.environ, {"A": "1"})', "patch.dict(codex_runtime._POOL, {}, clear=True)",
                     "platform_env.cleared({})", 'patch.dict(os.environ, {}, clear=False)'):
            self.assertEqual(self.cleared_by_itself(fine), [], fine)

    def test_no_host_test_file_clears_the_environment_by_itself(self):
        found = []
        for path in sorted(HERE.glob("*.py")):
            if path.name == "platform_env.py":
                continue
            found += ["%s:%d" % (path.name, line) for line in self.cleared_by_itself(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "use platform_env.cleared(...): it keeps what every process has")


if __name__ == "__main__":
    unittest.main()
