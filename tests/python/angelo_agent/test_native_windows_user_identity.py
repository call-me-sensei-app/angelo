# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows has no POSIX user ID and no signal 0: nothing of Angelo's that can run there asks either.

Two calls read as harmless questions and are not on Windows (the external audit of 2026-11-14, F1):

* ``os.kill(pid, 0)`` asks "is it alive?" on POSIX; on Windows signal 0 is ``signal.CTRL_C_EVENT`` and CPython
  sends Ctrl+C to that console process group (``GenerateConsoleCtrlEvent``), so the same call interrupts the
  process it asks about or reads it as gone (``test_native_liveness_probes`` scans for
  it; the behaviour is pinned here with real processes).
* ``os.getuid()`` and ``os.geteuid()`` do not exist there: ``info.st_uid != os.getuid()`` raises ``AttributeError``,
  which no ``except OSError`` takes.

The headless sweep (``headless_working_copy.sweep``) did both: its thread would die on the first folder, and with
only the user ID repaired its next line would have ended a live child. Its two questions now have one answer each
on every platform: ``engine_link_protocol.process_alive`` and ``file_owner.owned_by_this_user`` (the POSIX owner
ID, or on Windows the folder's owner SID against this process's own). The CLI's endpoint and key checks
(``external_cli_protocol``), which the ``angelo`` command and an app's launcher run on any platform, ask the same.

The class: every remaining ``os.getuid`` / ``os.geteuid`` call in Angelo's sources either answers Windows first in
its own function, or is in a module that only runs behind a platform gate, and each such gate is pinned here by its
text. A new call anywhere else fails this test.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from host_test_policy import environment_skip

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import engine_link_protocol, external_cli, headless_working_copy  # noqa: E402

SOURCES = (sorted(ADDON.glob("*.py")) + sorted((ROOT / "scripts/modules").glob("angelo*.py"))
           + sorted((ROOT / "scripts/startup").glob("angelo*.py")))
# A function that names Windows, or asks whether the call exists, before it makes it.
WINDOWS = ('"win32"', "'win32'", '"nt"', "'nt'", 'hasattr(os, "geteuid")', 'hasattr(os, "getuid")')
# Modules that only run behind a platform gate: the gate's file and its text, which must stay.
POSIX_ONLY = {
    # The listener refuses to start on Windows before it makes or reads anything (LocalCliServer.start).
    "external_cli.py": [("external_cli.py", 'if sys.platform == "win32" or not hasattr(socket, "AF_UNIX"):')],
    # A headless child is started only by HeadlessSession, which refuses other platforms before it starts one.
    "headless_main.py": [("headless_session.py", 'SUPPORTED_PLATFORMS = ("darwin", "linux")'),
                         ("headless_session.py", "if self.platform not in SUPPORTED_PLATFORMS:")],
    "headless_host.py": [("headless_session.py", "if self.platform not in SUPPORTED_PLATFORMS:")],
    "headless_preferences.py": [("headless_session.py", "if self.platform not in SUPPORTED_PLATFORMS:")],
    # The Fab browser's macOS owners: its dispatch gives Windows its own modules (fab_browser_*_windows).
    "fab_browser_download_file.py": [
        ("fab_browser_platform.py", "    if _platform_name() == 'darwin':\n"
                                    "        from .fab_browser_download_file import Landing, CompletedFile")],
    "fab_browser_profile.py": [
        ("fab_browser_platform.py", "    if _platform_name() == 'darwin':\n"
                                    "        from .fab_browser_profile import ProfileStore, ProfileLease"),
        ("fab_browser_profile.py",
         "if sys.platform!='darwin':raise ProfileUnavailable('PROFILE_PLATFORM_UNVERIFIED')")],
    "fab_browser_ownership.py": [("fab_browser_process.py", "if sys.platform != 'darwin'"),
                                 ("fab_browser_platform.py", "from .fab_browser_process_windows import")],
    "fab_browser_manual_close.py": [("fab_browser_process.py", "if sys.platform != 'darwin'"),
                                    ("fab_browser_platform.py", "from .fab_browser_process_windows import")],
}
HELPER = "file_owner.py"   # The one place that asks the POSIX question, after it answered Windows.


def _parents(tree):
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _answered(source, tree, call):
    """Whether ``call`` (an ``os.getuid()`` or ``os.geteuid()``) cannot run on Windows as its function is written."""
    parents, lines = _parents(tree), source.splitlines()
    node, function = call, None
    while node in parents:
        child, node = node, parents[node]
        if isinstance(node, ast.If) and child in node.body and ast.unparse(node.test) in (
                "sys.platform == 'darwin'", "sys.platform == 'linux'", "sys.platform.startswith('linux')"):
            return True   # Inside a branch only that platform takes.
        if isinstance(node, ast.Try) and child in node.body:
            caught = " ".join(ast.unparse(handler.type) for handler in node.handlers if handler.type is not None)
            earlier = "\n".join(lines[node.lineno - 1:call.lineno])
            if "ImportError" in caught and "import pwd" in earlier:
                return True   # ``pwd`` is not on Windows: the import ends the try before the call.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node
            break
    start = function.lineno if function is not None else 1
    return any(token in "\n".join(lines[start - 1:call.lineno]) for token in WINDOWS)


def user_id_calls(path):
    """``[(line, answered)]`` for each ``os.getuid()`` / ``os.geteuid()`` in ``path``."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [(node.lineno, _answered(source, tree, node)) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("os.getuid", "os.geteuid")]


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ended():
    """The process ID of a child that has ended and been waited for."""
    child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    child.wait(timeout=60)
    return child.pid


class UserIdCalls(unittest.TestCase):
    def test_every_user_id_call_answers_windows_or_runs_behind_a_pinned_gate(self):
        loose, seen = [], 0
        for path in SOURCES:
            for line, answered in user_id_calls(path):
                seen += 1
                if not answered and path.name not in POSIX_ONLY and path.name != HELPER:
                    loose.append("%s:%d" % (path.relative_to(ROOT).as_posix(), line))
        self.assertEqual(loose, [], "ask file_owner.owned_by_this_user, or answer Windows first in the function")
        self.assertGreaterEqual(seen, 20, "the scan found too few calls to mean anything")

    def test_each_gate_a_posix_only_module_runs_behind_is_still_there(self):
        for module, gates in POSIX_ONLY.items():
            self.assertTrue((ADDON / module).is_file(), module)
            for holder, text in gates:
                with self.subTest(module=module, gate=holder):
                    self.assertIn(text, (ADDON / holder).read_text(encoding="utf-8"))

    def test_the_scan_knows_an_answered_call_from_a_loose_one(self):
        samples = {
            "import os\ndef mine(info):\n    return info.st_uid == os.getuid()\n": [(3, False)],
            "import os, sys\ndef mine(info):\n    if sys.platform == 'win32':\n        return True\n"
            "    return info.st_uid == os.geteuid()\n": [(5, True)],
            "import os\ndef mine(info):\n    return not hasattr(os, \"geteuid\") or info.st_uid == os.geteuid()\n":
                [(3, True)],
            "import os, sys\ndef restart():\n    if sys.platform == \"darwin\":\n        run(os.getuid())\n":
                [(4, True)],
            # Naming macOS is not an answer for Windows: the other branch still runs there.
            "import os\ndef home(platform):\n    base = '/Users' if platform == 'darwin' else '/home'\n"
            "    return base, os.getuid()\n": [(4, False)],
            "import os\ndef name():\n    try:\n        import pwd\n        return pwd.getpwuid(os.getuid()).pw_name\n"
            "    except (ImportError, KeyError):\n        return ''\n": [(5, True)],
            "import os\ndef name():\n    import pwd\n    return pwd.getpwuid(os.getuid()).pw_name\n": [(4, False)],
        }
        with tempfile.TemporaryDirectory() as folder:
            for index, (source, expected) in enumerate(samples.items()):
                path = Path(folder) / ("sample_%d.py" % index)
                path.write_text(source, encoding="utf-8")
                with self.subTest(source=source):
                    self.assertEqual(user_id_calls(path), expected)


class Owner(unittest.TestCase):
    def test_a_folder_this_process_made_is_its_own_and_a_system_folder_is_not(self):
        from angelo_agent import file_owner
        with tempfile.TemporaryDirectory() as folder:
            made = Path(folder) / "made"
            made.mkdir()
            (made / "file").write_text("x", encoding="utf-8")
            self.assertTrue(file_owner.owned_by_this_user(made))
            self.assertTrue(file_owner.owned_by_this_user(made, os.lstat(made)))
            self.assertTrue(file_owner.owned_by_this_user(made / "file"))
            self.assertFalse(file_owner.owned_by_this_user(made / "missing"))
        if os.name == "nt":
            system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "kernel32.dll"
        elif os.getuid() == 0:
            self.skipTest(environment_skip("every file is root's own when the tests run as root"))
        else:
            system = Path("/")
        self.assertFalse(file_owner.owned_by_this_user(system), system)

    def test_what_cannot_be_read_is_never_counted_as_its_own(self):
        from angelo_agent import file_owner
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(file_owner, "_windows_owner", side_effect=OSError("denied")), \
                    patch.object(file_owner, "_windows_own", side_effect=OSError("denied")), \
                    patch.object(file_owner, "WINDOWS", True):
                self.assertFalse(file_owner.owned_by_this_user(folder))
            with patch.object(file_owner, "_windows_owner", return_value="S-1-5-21-1-2-3-1001"), \
                    patch.object(file_owner, "_windows_own", return_value=("S-1-5-21-1-2-3-1002", "S-1-5-32-544")), \
                    patch.object(file_owner, "WINDOWS", True):
                self.assertFalse(file_owner.owned_by_this_user(folder))
            with patch.object(file_owner, "_windows_owner", return_value="S-1-5-21-1-2-3-1001"), \
                    patch.object(file_owner, "_windows_own", return_value=("S-1-5-21-1-2-3-1001", "S-1-5-32-544")), \
                    patch.object(file_owner, "WINDOWS", True):
                self.assertTrue(file_owner.owned_by_this_user(folder))


class Probes(unittest.TestCase):
    def test_asking_whether_a_process_runs_leaves_it_running(self):
        """Real processes, on every platform: on Windows a signal-0 ``os.kill`` here sends the child Ctrl+C."""
        child = _sleeper()
        self.addCleanup(child.wait, 60)
        self.addCleanup(child.kill)
        for ask in (engine_link_protocol.process_alive, external_cli.process_alive):
            with self.subTest(ask=ask.__module__):
                self.assertTrue(ask(child.pid))
                self.assertTrue(ask(os.getpid()))
                self.assertIsNone(child.poll(), "the question ended the process")
                self.assertFalse(ask(_ended()))

    def test_the_cli_asks_the_platform_aware_check_and_sends_no_signal(self):
        with patch.object(os, "kill", side_effect=AssertionError("a signal was sent")), \
                patch.object(engine_link_protocol, "process_alive", side_effect=lambda pid: pid == 4242) as asked:
            self.assertTrue(external_cli.process_alive(4242))
            self.assertFalse(external_cli.process_alive(4243))
        self.assertEqual(asked.call_count, 2)


class Sweep(unittest.TestCase):
    PREFIX = "qa-headless-"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def left(self, name, pid):
        folder = self.root / (self.PREFIX + name)
        folder.mkdir()
        (folder / "child.pid").write_text(str(pid), encoding="ascii")
        (folder / "work.blend").write_bytes(b"BLENDER")
        return folder

    def test_a_live_child_s_folder_stays_and_the_child_runs_on_and_a_dead_one_s_goes(self):
        """Real processes, on every platform (on Windows before the fix: an ``AttributeError`` on the first folder)."""
        child = _sleeper()
        self.addCleanup(child.wait, 60)
        self.addCleanup(child.kill)
        live, dead = self.left("live", child.pid), self.left("dead", _ended())
        other = self.root / "someone-else"
        other.mkdir()
        headless_working_copy.sweep(self.root, self.PREFIX)
        self.assertTrue(live.is_dir())
        self.assertIsNone(child.poll(), "the sweep ended a live child")
        self.assertFalse(dead.exists())
        self.assertTrue(other.is_dir(), "a folder without the prefix is not Angelo's to remove")

    def test_where_there_is_no_posix_user_id_the_sweep_asks_neither_it_nor_a_signal(self):
        """Windows, as far as another platform can stand in for it: both POSIX calls fail if the sweep makes them."""
        from angelo_agent import file_owner
        live, dead = self.left("live", 4242), self.left("dead", 4243)
        missing = AttributeError("module 'os' has no attribute 'getuid'")
        with patch.object(os, "getuid", side_effect=missing, create=True), \
                patch.object(os, "geteuid", side_effect=missing, create=True), \
                patch.object(os, "kill", side_effect=AssertionError("a signal was sent")), \
                patch.object(file_owner, "owned_by_this_user", return_value=True) as owned, \
                patch.object(engine_link_protocol, "process_alive", side_effect=lambda pid: pid == 4242) as alive:
            headless_working_copy.sweep(self.root, self.PREFIX)
        self.assertTrue(live.is_dir())
        self.assertFalse(dead.exists())
        self.assertEqual((owned.call_count, alive.call_count), (2, 2))

    def test_another_user_s_folder_is_never_removed(self):
        """The ownership check is asked on every platform, never skipped where the POSIX ID is missing."""
        from angelo_agent import file_owner
        theirs = self.left("theirs", _ended())
        with patch.object(file_owner, "owned_by_this_user", return_value=False) as owned:
            headless_working_copy.sweep(self.root, self.PREFIX)
        self.assertTrue(theirs.is_dir())
        self.assertEqual(owned.call_count, 1)

    def test_a_link_with_the_prefix_is_left_alone(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "child.pid").write_text(str(_ended()), encoding="ascii")
        link = self.root / (self.PREFIX + "link")
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest(environment_skip("this account cannot make a symbolic link"))
        headless_working_copy.sweep(self.root, self.PREFIX)
        self.assertTrue(target.is_dir())
        self.assertTrue((target / "child.pid").is_file())


if __name__ == "__main__":
    unittest.main()
