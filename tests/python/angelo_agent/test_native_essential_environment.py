# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every child process Angelo starts from an allow-list of variables keeps what its OS needs.

``essential_environment`` holds the list.

A child started with only the variables an allow-list names loses the rest; on Windows that dropped SYSTEMROOT,
COMSPEC, USERPROFILE and TEMP from some children, so OpenSSL could not start (``ssl.create_default_context`` fails
without SYSTEMROOT), a shell command found no ``cmd.exe`` and ``~`` meant nothing (VM CI 508360d287da-20261115112517,
where a test cleared the environment the same way). So:

* every allow-list builder in the add-on starts from ``essential_environment`` (the inventory below fails on one that
  does not, or on a new allow-list nobody classified);
* each builder's environment, in a fresh interpreter, can make an SSL context, hash with OpenSSL, write a temporary
  file, find the home folder and run a shell command.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

PROBE = """
import hashlib, os, ssl, subprocess, tempfile
ssl.create_default_context()
hashlib.sha256(b"x").hexdigest()
hashlib.md5(b"x", usedforsecurity=False).hexdigest()
with tempfile.TemporaryFile() as handle:
    handle.write(b"x")
assert os.path.expanduser("~") != "~", "no home folder"
assert subprocess.run("exit 0", shell=True).returncode == 0, "no shell"
print("ok")
"""

# Every function that builds a child's environment from an allow-list; each starts from essential_environment.
BUILDERS = {"worker_process.py": "worker_environment",
            "endpoint_catalog.py": "_quiet_environment", "fab_convert.py": "environment",
            "pdf_text.py": "worker_environment", "view_exr_codecs.py": "_environment",
            "fab_browser_process.py": "browser_environment", "update_install.py": "helper_environment"}
# Modules that name environment variables in a list but start no child with them, each with why.
NOT_BUILDERS = {
    "private_conventions.py": "reads APPDATA and LOCALAPPDATA to find the person's private folders; starts no child",
    "worker_merge_spellings.py": "reads HOME and USERPROFILE (and every variable) to know how text can spell a private "
                                 "folder, for the merge's privacy check; starts no child",
}
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_()]*$")


def builders(folder):
    """{(module, function or None): (essentials, by name)} of every environment allow-list in the add-on."""
    sys.path.insert(0, str(ROOT / "scripts/addons_core"))
    from angelo_agent import essential_environment
    essentials = set(essential_environment.WINDOWS) | set(essential_environment.POSIX)
    found = {}
    for path in sorted(folder.rglob("*.py")):
        if path.name == "essential_environment.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.Set, ast.List)) or len(node.elts) < 2:
                continue
            if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) and _NAME.match(e.value)
                       for e in node.elts):
                continue
            named = {e.value.upper() for e in node.elts} & essentials
            if len(named) >= 2:
                found.setdefault(path.name, set()).update(named)
    return found


def environments():
    """(name, the environment) of every builder that can run on this platform."""
    from angelo_agent import endpoint_catalog, fab_browser_process, fab_convert, pdf_text
    from angelo_agent import view_exr_codecs, worker_process
    work = Path(tempfile.mkdtemp(prefix="angelo-essentials-"))
    windows = sys.platform == "win32"
    return [("worker_process.worker_environment", lambda: worker_process.worker_environment(os.environ, work)),
            ("endpoint_catalog._quiet_environment", lambda: endpoint_catalog._quiet_environment(work / "codex")),
            ("fab_convert.environment", lambda: fab_convert.environment(work / "fab")),
            ("pdf_text.worker_environment", lambda: pdf_text.worker_environment()),
            ("view_exr_codecs._environment", lambda: view_exr_codecs._environment()),
            ("fab_browser_process.browser_environment", lambda: fab_browser_process.browser_environment(windows))]


class InventoryTests(unittest.TestCase):
    def test_every_allow_list_builder_starts_from_the_essentials(self):
        found = builders(ADDON)   # Allow-lists that still name the OS's variables themselves.
        for name in sorted(found):
            with self.subTest(module=name):
                if name in NOT_BUILDERS:
                    continue
                self.assertIn("essential_environment", (ADDON / name).read_text(encoding="utf-8"),
                              "%s names %s in an allow-list without essential_environment"
                              % (name, sorted(found[name])))
        for name in NOT_BUILDERS:
            self.assertIn(name, found, "a stale NOT_BUILDERS entry")
        for module, function in BUILDERS.items():   # Each known builder calls it (Fab's Windows browser through
            with self.subTest(builder=module):     # fab_browser_process.browser_environment).
                node = next(n for n in ast.walk(ast.parse((ADDON / module).read_text(encoding="utf-8")))
                            if isinstance(n, ast.FunctionDef) and n.name == function)
                self.assertIn("essential_environment.environment(", ast.unparse(node), module + ":" + function)

    def test_the_essentials_match_names_on_windows_whatever_their_case(self):
        from angelo_agent import essential_environment as essential
        kept = essential.environment(("LC_ALL",), {"SystemRoot": "C:\\Windows", "Path": "C:\\Windows", "LC_ALL": "C",
                                                     "ComSpec": "cmd.exe", "SECRET": "x"}, "win32", HOME_FOR="x")
        self.assertEqual(kept, {"SYSTEMROOT": "C:\\Windows", "PATH": "C:\\Windows", "COMSPEC": "cmd.exe",
                                "LC_ALL": "C", "HOME_FOR": "x"})
        kept = essential.environment((), {"HOME": "/h", "PATH": "/bin", "SystemRoot": "x", "SECRET": "x"}, "darwin",
                                     TMPDIR="/t")
        self.assertEqual(kept, {"HOME": "/h", "PATH": "/bin", "TMPDIR": "/t"})


class FreshProcessTests(unittest.TestCase):
    def test_each_builders_environment_can_run_tls_hashing_temp_files_and_a_shell(self):
        for name, build in environments():
            with self.subTest(builder=name):
                done = subprocess.run([sys.executable, "-I", "-c", PROBE], env=build(), capture_output=True,
                                      text=True, timeout=120, cwd=tempfile.gettempdir())
                self.assertEqual((done.returncode, done.stdout.strip()), (0, "ok"), done.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
