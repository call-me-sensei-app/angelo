# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Another running copy of the app is recognised however it was started (update_stage_mac/_win.other_instances).

Restart to Update refuses while another copy of the same install runs, because the swap replaces its files under
it. A copy started through a symbolic link (to the app or to its executable), through a relative path, or (on
Windows) through a short name, a junction or another letter case must still count; a copy of the app elsewhere must
not. On macOS the real processes are started and listed through libproc on the machine itself.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from host_test_policy import environment_skip

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import update_install, update_stage_mac as mac, update_stage_win as win  # noqa: E402


class Folder(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def executable(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"binary")
        return path


class MacOtherCopiesTests(Folder):
    def setUp(self):
        super().setUp()
        self.app = self.root / "Applications" / "Angelo.app"
        self.binary = self.executable("Applications/Angelo.app/Contents/MacOS/Angelo")
        self.copy = self.executable("Elsewhere/Angelo.app/Contents/MacOS/Angelo")   # Another app, same names.
        (self.root / "links").mkdir()
        (self.root / "links" / "angelo").symlink_to(self.binary)              # A link to the executable.
        (self.root / "links" / "Linked.app").symlink_to(self.app)             # A link to the bundle.

    def test_ps_paths_are_compared_as_files_links_followed(self):
        listing = "\n".join("%d %s" % row for row in (
            (10, self.binary), (11, self.root / "links/angelo"),
            (12, self.root / "links/Linked.app/Contents/MacOS/Angelo"),
            (13, self.copy), (14, self.root / "Applications/../Applications/Angelo.app/Contents/MacOS/Angelo"),
            (15, "/bin/sh"), (16, self.binary)))

        def runner(args, timeout, background=True):
            return subprocess.CompletedProcess(args, 0, listing, "")
        self.assertEqual(mac.other_instances(self.app, "Angelo", 16, runner, processes=lambda: None), [10, 11, 12, 14])

    def test_a_relative_ps_path_fails_closed_by_its_file_name(self):
        def runner(args, timeout, background=True):
            return subprocess.CompletedProcess(args, 0, "20 ./MacOS/Angelo\n21 ./MacOS/Other\n", "")
        self.assertEqual(mac.other_instances(self.app, "Angelo", 1, runner, processes=lambda: None), [20])

    def test_the_kernels_paths_win_over_ps_and_argv0(self):
        def runner(args, timeout, background=True):
            self.fail("ps is not asked when libproc answers")
        kernel = [(30, str(self.binary)), (31, str(self.copy)), (32, "/bin/sleep")]
        self.assertEqual(mac.other_instances(self.app, "Angelo", 1, runner, processes=lambda: kernel), [30])
        # An installed app reached only through a link resolves to the same file.
        self.assertEqual(mac.other_instances(self.root / "links/Linked.app", "Angelo", 1, runner,
                                             processes=lambda: kernel), [30])


@unittest.skipUnless(sys.platform == "darwin", environment_skip("libproc and its process paths are macOS's"))
class MacRealProcessTests(Folder):
    """Real processes, listed by the real libproc. The bundle's executable is a link to /bin/sleep (macOS refuses to
    run a copied system binary), so the kernel runs the same file however the copy is started."""

    def start(self, args, cwd=None):
        process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process

    def test_copies_started_through_links_and_relative_paths_are_recognised(self):
        app = self.root / "Applications" / "Angelo.app"
        binary = app / "Contents/MacOS/Angelo"
        binary.parent.mkdir(parents=True)
        binary.symlink_to("/bin/sleep")
        (self.root / "other-name").symlink_to(binary)
        (self.root / "Linked.app").symlink_to(app)
        started = [self.start([str(self.root / "other-name"), "60"]),
                   self.start([str(self.root / "Linked.app/Contents/MacOS/Angelo"), "60"]),
                   self.start(["./MacOS/Angelo", "60"], cwd=app / "Contents"),
                   self.start([str(binary), "60"])]
        time.sleep(0.3)
        listed = dict(mac._kernel_processes())
        for process in started:
            self.assertEqual(os.path.realpath(listed[process.pid]), "/bin/sleep", "the kernel names the file it runs")
        found = mac.other_instances(app, "Angelo", started[-1].pid)
        self.assertTrue({p.pid for p in started[:-1]} <= set(found), (found, [p.pid for p in started]))
        self.assertNotIn(started[-1].pid, found, "never this process itself")
        self.assertNotIn(os.getpid(), found, "a process running another file is not a copy")


class WindowsOtherCopiesTests(Folder):
    """The comparison, on stand-in files: Windows' own names (8.3, junctions, case) differ only in spelling."""

    def test_processes_running_the_same_file_count_whatever_the_spelling(self):
        binary = self.executable("Programs/Angelo/Angelo.exe")
        copy = self.executable("Copy/Angelo/Angelo.exe")
        try:
            (self.root / "Junction").symlink_to(self.root / "Programs", target_is_directory=True)
            (self.root / "launch.exe").symlink_to(binary)
        except OSError as error:   # Windows without the right to create links.
            self.skipTest(environment_skip("symbolic links cannot be created here: %s" % error))
        listed = [(1, str(binary)), (2, str(self.root / "Junction/Angelo/Angelo.exe")), (3, str(copy)),
                  (4, str(self.root / "Programs/Angelo/../Angelo/Angelo.exe")), (5, str(self.root / "launch.exe")),
                  (6, ""), (7, str(binary))]
        self.assertEqual(win.other_instances(str(self.root / "Junction/Angelo/Angelo.exe"), 7, lambda: listed),
                         [1, 2, 4, 5])
        self.assertEqual(win.other_instances(str(binary), 1, lambda: None), [], "no process list: nothing to tell")

    def test_an_unreadable_running_file_falls_back_to_its_final_path(self):
        missing = self.root / "Programs/Angelo/Angelo.exe"
        listed = [(1, str(missing)), (2, str(self.root / "Programs/Other/Angelo.exe"))]
        self.assertEqual(win.other_instances(str(missing), 9, lambda: listed), [1])


class WindowsScriptOtherCopyTests(unittest.TestCase):
    """The Windows installer's last check before msiexec, by its contract (no Windows here: the release's Windows
    end-to-end update runs it, other_copies_windows.ps1 included)."""

    def read(self, name):
        raw = (update_install.HELPER_FOLDER / name).read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), name + " keeps Windows line endings")
        return raw.decode("ascii")

    def test_the_script_asks_the_identity_check_and_falls_back_to_this_users_angelo(self):
        text = self.read("install_update_windows.cmd")
        check = text.index('"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"', text.index(':install'))   # Never PATH's.
        line = text[check:text.index("\r\n", check)]
        for part in ("-NoProfile", "-NonInteractive", "-ExecutionPolicy Bypass", '-File "%dir%\\other_copies_windows.ps1"'):
            self.assertIn(part, line)
        self.assertLess(text.index(":install"), check, "after Angelo has quit")
        self.assertLess(check, text.index("certutil -hashfile"), "before anything is installed")
        branch = text[check:text.index(":no_other_copy")]
        self.assertIn('if "%copies%"=="0" goto no_other_copy', branch)
        self.assertIn('if "%copies%"=="3" goto another_copy', branch)
        # Any other answer (PowerShell blocked, missing, failed): every Angelo.exe of this user counts.
        fallback = branch[branch.index('if "%copies%"=="3"'):]
        self.assertIn('tasklist /FI "IMAGENAME eq %ANGELO_UPDATE_EXECUTABLE%" /FI "USERNAME eq %USERDOMAIN%\\%USERNAME%"',
                      fallback)
        self.assertIn("if errorlevel 1 goto no_other_copy", fallback)
        self.assertLess(fallback.index("if errorlevel 1 goto no_other_copy"), fallback.index(":another_copy"))
        self.assertIn('set "status=aborted" & set "code=another_instance"', fallback[fallback.index(":another_copy"):])

    def test_the_check_compares_files_not_names(self):
        text = self.read("other_copies_windows.ps1")
        self.assertIn("$env:ANGELO_UPDATE_ROOT", text)
        self.assertIn("$env:ANGELO_UPDATE_EXECUTABLE", text)
        self.assertIn("CreateFileW(path, 0x80, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)", text,
                      "read attributes, every sharing mode, OPEN_EXISTING following links, a folder or a file")
        self.assertIn("GetFileInformationByHandle", text)
        layout = text[text.index("struct Information {"):text.index("}", text.index("struct Information {"))]
        fields = re.findall(r"\b([A-Z]\w+)\b(?=[,;])", layout)
        self.assertEqual(fields, ["Attributes", "CreationLow", "CreationHigh", "AccessLow", "AccessHigh", "WriteLow",
                                  "WriteHigh", "VolumeSerial", "SizeHigh", "SizeLow", "Links", "IndexHigh",
                                  "IndexLow"], "BY_HANDLE_FILE_INFORMATION as thirteen DWORDs, in order")
        self.assertNotIn("long", layout, "a FILETIME as one 64-bit field would be padded")
        self.assertIn("information.VolumeSerial", text)
        self.assertIn("Get-CimInstance -ClassName Win32_Process", text)
        self.assertIn("if ([AngeloFileIdentity]::Of($path) -eq $wanted) { exit 3 }", text)
        # A process whose path cannot be read counts by name when it is this user's, or its owner is unknown.
        unreadable = text[text.index("if ([string]::IsNullOrEmpty($path))"):text.index("if ([AngeloFileIdentity]::Of($path)")]
        self.assertIn("$process.Name -ieq $executable", unreadable)
        self.assertIn("$owner.ReturnValue -ne 0", unreadable)
        self.assertIn("{ exit 2 }", text, "the installed executable unreadable: no answer, so the fallback")
        self.assertTrue(text.rstrip().endswith("exit 0"))

    def test_the_check_is_copied_out_beside_the_script(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        request = update_install.Request("windows", work, 1, "1.0.1", "Angelo.exe", msi="m", root="r")
        update_install.prepare_folder(request)
        for name in ("install_update_windows.cmd", "other_copies_windows.ps1"):
            self.assertEqual((work / name).read_bytes(), (update_install.HELPER_FOLDER / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
