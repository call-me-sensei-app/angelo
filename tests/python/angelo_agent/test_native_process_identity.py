# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A process's identity read from the kernel, never by running a program (``process_identity``).

The GUI lock read its own start time with ``/bin/ps`` at every launch (``headless_window_lock.take``), so every
GUI Angelo started a process at register, which the offline GUI fixtures refuse (2.0.1 final 12484412217:
test_blender_signin_reconnect, test_blender_change_chip_details). The identity keeps the exact text ``ps -o lstart=``
prints in the C locale and UTC, so a record an earlier build wrote still verifies."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import locale
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from host_test_policy import environment_skip

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent import process_identity as identity  # noqa: E402

SUPPORTED = sys.platform == "darwin" or sys.platform.startswith("linux")
NATIVE = environment_skip("the start time and executable are read from macOS's sysctl and libproc or Linux's /proc")


@contextlib.contextmanager
def no_process_started(test):
    """Any program started meanwhile fails the test, however it is started. The failure names the command only: the
    half-made ``Popen`` itself can't be printed (its ``__repr__`` reads a ``returncode`` it never set)."""
    def refusal(how):
        def refuse(*args, **kwargs):
            command = args[1:2] if how == "Popen" else args[:1]
            test.fail("a process was started (%s): %s" % (how, " ".join(map(str, command)) or kwargs.get("args")))
        return refuse
    with contextlib.ExitStack() as refusals:
        refusals.enter_context(patch.object(subprocess.Popen, "__init__", refusal("Popen")))
        # Each way this platform's ``os`` has to start a program (Windows has no posix_spawn or fork).
        for how in ("system", "posix_spawn", "posix_spawnp", "fork", "forkpty", "startfile"):
            if hasattr(os, how):
                refusals.enter_context(patch.object(os, how, refusal(how)))
        yield


def ps_start(pid, environ=None):
    """``ps -o lstart=`` for ``pid`` in the C locale and UTC, whitespace collapsed: what earlier builds recorded."""
    listing = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             env=environ or {"LC_ALL": "C", "LANG": "C", "TZ": "UTC", "PATH": "/usr/bin:/bin"})
    return " ".join(listing.stdout.split()) or None


class RefusalTests(unittest.TestCase):
    def test_a_refused_start_names_the_command(self):
        with self.assertRaises(AssertionError) as raised:
            with no_process_started(self):
                subprocess.run(["/usr/bin/osascript", "-e", "beep"])
        self.assertIn("/usr/bin/osascript", str(raised.exception))


class FormatTests(unittest.TestCase):
    def test_the_text_is_ps_lstart_in_the_c_locale_and_utc(self):
        self.assertEqual(identity.start_text(0), "Thu Jan 1 00:00:00 1970")
        self.assertEqual(identity.start_text(1795000000), "Wed Nov 18 11:06:40 2026")
        self.assertEqual(identity.start_text(1795000000.9), "Wed Nov 18 11:06:40 2026", "whole seconds, as ps")

    def test_the_names_are_never_the_callers_locale(self):
        with patch.object(time, "strftime", side_effect=AssertionError("locale-dependent")):
            self.assertEqual(identity.start_text(86400 * 31), "Sun Feb 1 00:00:00 1970")


@unittest.skipUnless(SUPPORTED, NATIVE)
class NativeTests(unittest.TestCase):
    def test_this_processs_start_time_is_the_one_ps_prints(self):
        self.assertEqual(identity.started(os.getpid()), ps_start(os.getpid()))

    def test_another_processs_start_time_is_the_one_ps_prints(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        self.assertEqual(identity.started(child.pid), ps_start(child.pid))
        self.assertEqual(identity.started(1), ps_start(1), "another account's process too, as ps reads it")

    def test_no_such_process_has_no_identity(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertIsNone(identity.started(child.pid))
        self.assertIsNone(identity.executable_name(child.pid))
        self.assertIsNone(identity.started(2 ** 22 + 12345))
        self.assertIsNone(identity.started(-1))
        self.assertIsNone(identity.started("not a pid"))

    def test_the_executable_is_this_processs_own(self):
        self.assertEqual(identity.executable_name(os.getpid()).lower(), Path(sys.executable).resolve().name.lower())

    def test_reading_starts_no_process(self):
        pid = os.getpid()
        with no_process_started(self):
            self.assertTrue(identity.started(pid))
            self.assertTrue(identity.executable_name(pid))

    def test_the_callers_time_zone_and_locale_change_nothing(self):
        expected = identity.started(os.getpid())
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Tokyo"
            time.tzset()
            self.assertEqual(identity.started(os.getpid()), expected)
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()
        for name in ("de_DE.UTF-8", "de_DE", "fr_FR.UTF-8", "ja_JP.UTF-8"):
            try:
                saved = locale.setlocale(locale.LC_TIME)
                locale.setlocale(locale.LC_TIME, name)
            except locale.Error:
                continue
            try:
                self.assertEqual(identity.started(os.getpid()), expected, name)
            finally:
                locale.setlocale(locale.LC_TIME, saved)


@unittest.skipUnless(SUPPORTED, NATIVE)
class UserProcessesTests(unittest.TestCase):
    """This user's processes (``carry_over_processes.process_table`` off Windows): what ``ps -U`` lists, read
    natively. ``version_carry_over`` reads it at register while an earlier version's state may move."""

    def test_children_started_here_are_listed_with_this_process_as_their_parent(self):
        """A controlled set: these processes live for the whole test, so the comparison with ``ps`` is exact
        (VM CI linux 89edda38180: comparing the whole list raced other processes starting and ending)."""
        children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) for _ in range(3)]
        for child in children:
            self.addCleanup(lambda child=child: (child.kill(), child.wait()))
        rows = {pid: (parent, name) for pid, parent, name in identity.user_processes()}
        own = Path(sys.executable).resolve().name
        for child in children:
            self.assertEqual(rows.get(child.pid), (os.getpid(), own))
        self.assertEqual(rows[os.getpid()], (os.getppid(), own))
        watched = [os.getpid()] + [child.pid for child in children]
        listing = subprocess.run(["/bin/ps", "-o", "pid=,ppid=", "-p", ",".join(map(str, watched))],
                                 capture_output=True, text=True).stdout.split()
        self.assertEqual(dict(zip(map(int, listing[::2]), map(int, listing[1::2]))),
                         {pid: rows[pid][0] for pid in watched}, "what ps prints for the same processes")

    def test_listing_starts_no_process(self):
        from angelo_agent import carry_over_processes
        with no_process_started(self):
            rows = carry_over_processes.process_table()
        self.assertIn(os.getpid(), [pid for pid, _parent, _name in rows])

    def test_another_users_processes_are_not_listed(self):
        rows = {pid for pid, _parent, _name in identity.user_processes()}
        self.assertNotIn(1, rows, "launchd or init is root's")


class LinuxProcTreeTests(unittest.TestCase):
    """``/proc`` as Linux writes it, in a tree of the test's own: runs on every platform."""

    def entry(self, root, pid, status, exe=None):
        folder = root / str(pid)
        folder.mkdir()
        (folder / "status").write_text(status)
        if exe:
            os.symlink(exe, folder / "exe")

    def test_this_users_processes_with_their_parents_and_executables(self):
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="proc-"))
        self.addCleanup(__import__("shutil").rmtree, root, True)
        self.entry(root, 10, "Name:\tAngelo\nPPid:\t1\nUid:\t501\t501\t501\t501\n", "/opt/Angelo/Angelo")
        self.entry(root, 11, "Name:\tkworker/0:1 (x)\nPPid:\t2\nUid:\t501\t0\t0\t0\n")   # No exe: its name.
        self.entry(root, 12, "Name:\tsshd\nPPid:\t1\nUid:\t0\t0\t0\t0\n", "/usr/sbin/sshd")   # Root's.
        self.entry(root, 13, "Name:\tpython3\nPPid:\t10\nUid:\t501\t501\t501\t501\n",
                   "/usr/bin/python3.12 (deleted)")
        self.entry(root, 14, "garbled")
        (root / "self").mkdir()
        (root / "stat").write_text("btime 1795000000\n")
        if os.name == "nt":
            with self.assertRaises(OSError):
                identity._linux_rows(str(root), uid=501)
            return
        self.assertEqual(sorted(identity._linux_rows(str(root), uid=501)),
                         [(10, 1, "Angelo"), (11, 2, "kworker/0:1 (x)"), (13, 10, "python3.12")])

    def test_a_pid_out_of_range_is_no_process(self):
        for pid in (0, -5, 2 ** 31, 2 ** 64, True, 1.5, None, "x", [1]):
            with self.subTest(pid=pid):
                self.assertIsNone(identity.started(pid))
                self.assertIsNone(identity.executable_name(pid))


if __name__ == "__main__":
    unittest.main()
