# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker Blender runs only Angelo's own program and script, on a part C says Angelo may write (guard piece 10
MUST-A; doc/angelo/script_confinement.rst, "Angelo's own I/O").

A worker is an Angelo started unconfined by this process, and it saves its part file at the path it is given (it
vouches that path where it arrives, ``worker_main``). So what it runs and where it saves must never come from Python
state a script could change in place: a ``WorkerProcess`` lives in module state (``worker_sessions``, ``worker_pool``)
for seconds to minutes between ``worker_routing.launch`` and its start on another thread. The program is read from
C at the start (``bpy.app.binary_path``), the script is the add-on's own file, the process is started by the module's
own function, and the part is checked with ``owned_paths.require_writable`` before anything is made: a refusal starts
no child and leaves nothing behind. Runs outside Blender: C's ``may_write`` is stood in for by a fixed root.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import owned_paths, worker_process, worker_routing  # noqa: E402

BINARY = Path("/angelo-test/Angelo")   # What C says the running program is (``bpy.app.binary_path``).


class Stop(Exception):
    """Raised by the stand-in start once it saw the command: no child is ever run here."""


def a_listener():
    return SimpleNamespace(child_argument=lambda: "fd:99", pass_fds=lambda: (), close=lambda: None,
                           started=lambda: None, accept=lambda pid, alive=None: None)


class WorkerLaunchInputsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())   # Where C lets Angelo write (its private folders, in the app).
        self.addCleanup(shutil.rmtree, self.root, True)
        self.elsewhere = Path(tempfile.mkdtemp())   # A folder of the person's: C never lets Angelo write there.
        self.addCleanup(shutil.rmtree, self.elsewhere, True)
        self.temporary = self.root / "scratch"
        self.temporary.mkdir()
        self.started, self.planted = [], []

        def may_write(path):
            path = os.path.realpath(path)
            root = os.path.realpath(self.root)
            return path == root or path.startswith(root + os.sep)

        def start(command, options, platform=None):   # The module's own process start, stood in for.
            self.started.append(list(map(str, command)))
            raise Stop()

        def any_popen(command, *args, **options):   # Python's own process start: nothing may reach it here.
            self.started.append(list(map(str, command)))
            raise Stop()

        for patcher in (mock.patch.object(owned_paths, "_MAY_WRITE", may_write),
                        mock.patch.object(worker_process, "default_binary", lambda: BINARY),
                        mock.patch.object(worker_process, "start_process", start, create=True),
                        mock.patch("subprocess.Popen", any_popen)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def worker(self, part):
        return worker_process.WorkerProcess(part, environ={"PATH": "/bin"}, platform="linux", temporary=self.temporary,
                                            listener=a_listener, killpg=lambda *_args: None)

    def test_a_part_c_refuses_starts_no_worker_and_leaves_nothing(self):
        part = self.elsewhere / "Report.blend"   # A part path a script put in module state (a relaunch's record).
        with self.assertRaises(worker_process.WorkerUnavailable) as raised:
            self.worker(part).start(5)
        self.assertFalse(raised.exception.sent)
        self.assertEqual(self.started, [], "no child may start on a part C refuses")
        self.assertEqual(os.listdir(self.temporary), [], "nothing is made for a refused part")

    def test_a_launch_on_a_part_c_refuses_makes_no_folder(self):
        part = self.elsewhere / "made-by-launch" / "part.blend"
        with self.assertRaises(PermissionError):
            worker_routing.launch(part, environ={"PATH": "/bin"}, platform="linux", temporary=self.temporary,
                                  listener=a_listener, killpg=lambda *_args: None)
        self.assertFalse(part.parent.exists(), "launch made a folder where C refuses")

    def test_what_a_worker_runs_never_comes_from_its_attributes(self):
        part = self.root / "parts" / "1" / "part.blend"
        part.parent.mkdir(parents=True)
        worker = self.worker(part)
        evil = self.elsewhere / "evil"

        def planted_popen(command, *args, **options):   # A start a script left on the object.
            self.planted.append(list(map(str, command)))
            raise Stop()
        # What a script that reached the worker could set in place before its start (attributes persist).
        for name, value in (("binary", str(evil)), ("script", str(evil) + ".py"), ("_popen", planted_popen)):
            setattr(worker, name, value)
        with self.assertRaises(Stop):
            worker.start(5)
        self.assertEqual(self.planted, [], "a start left on the object ran")
        self.assertEqual(len(self.started), 1)
        command = self.started[0]
        self.assertEqual(command[0], str(BINARY))
        self.assertEqual(command[command.index("--python") + 1], str(worker_process.default_script()))
        self.assertNotIn(str(evil), " ".join(command))
        self.assertEqual(command[command.index("--file") + 1], str(part))

    def test_a_part_c_allows_starts_with_the_documented_command(self):
        part = self.root / "parts" / "2" / "part.blend"
        part.parent.mkdir(parents=True)
        with self.assertRaises(Stop):
            self.worker(part).start(5)
        command = self.started[0]
        self.assertEqual(command[:2], [str(BINARY), "--background"])
        self.assertEqual(command[command.index("--file") + 1], str(part))
        self.assertEqual(os.listdir(self.temporary), [], "a start that failed removes its private folder")

    def test_a_failed_start_closes_its_log_before_removing_its_folder(self):
        """Windows can't remove a folder holding a file still open: the start's log is closed first, or a failed
        start leaves its private folder behind (VM CI, win-4, on cf5befe2877)."""
        part = self.root / "parts" / "3" / "part.blend"
        part.parent.mkdir(parents=True)
        order, real_close = [], os.close

        def close(fd):
            order.append("close")
            real_close(fd)

        with mock.patch.object(worker_process.os, "close", close), \
                mock.patch.object(owned_paths, "remove_tree", lambda path: order.append("remove")):
            with self.assertRaises(Stop):
                self.worker(part).start(5)
        self.assertIn("remove", order)
        self.assertLess(order.index("close"), order.index("remove"), order)


if __name__ == "__main__":
    unittest.main()
