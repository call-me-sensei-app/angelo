# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The installers and their hand-over: update_install.py, and the macOS script run for real on stand-in bundles.

The macOS script (update_helpers/install_update_macos.sh) runs here exactly as Angelo starts it, with
RELAUNCH off: it waits for a process to end, swaps two stand-in bundles, records the result, and puts the old
bundle back when the second rename fails. It is POSIX sh with absolute tool paths, so it runs on the Linux CI
too. The Windows script is checked by its contract here and run for real on Windows (releasing.rst).
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from host_test_policy import environment_skip
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import update_install  # noqa: E402
from angelo_agent.update_install import Request  # noqa: E402

HELPERS = update_install.HELPER_FOLDER


def bundle(path, marker):
    (path / "Contents/MacOS").mkdir(parents=True)
    (path / "Contents/marker.txt").write_text(marker)
    return path


TEST_ID = "org.example.angelo-update-test"   # Never Angelo's own: Launch Services must not route Angelo to a stand-in.
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def adhoc(path, marker):
    """Make the stand-in a real bundle (Info.plist, a Mach-O executable) and seal it with an ad hoc signature."""
    import plistlib
    (path / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": TEST_ID, "CFBundleExecutable": "Angelo",
        "CFBundleShortVersionString": "1.0.1" if marker == "new" else "1.0.0", "CFBundlePackageType": "APPL"}))
    (path / "Contents/Resources").mkdir()
    (path / "Contents/marker.txt").rename(path / "Contents/Resources/marker.txt")   # A loose file is never sealed.
    shutil.copyfile("/usr/bin/true", path / "Contents/MacOS/Angelo")   # Never run: only signed.
    os.chmod(path / "Contents/MacOS/Angelo", 0o755)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", TEST_ID, str(path)], check=True,
                   capture_output=True)


class EnvironmentTests(unittest.TestCase):
    def test_the_relaunched_app_keeps_how_angelo_was_started_but_not_what_it_set_for_itself(self):
        environ = {"ANGELO_STATE_DIR": "/s", "BLENDER_USER_CONFIG": "/c", "CFFIXED_USER_HOME": "/h", "PATH": "/bin",
                   "ANGELO_PYCACHE_PREFIX": "/cache/pycache/abc", "ANGELO_UPDATE_PID": "1",
                   "OCIO": "/Apps/Angelo.app/Contents/Resources/5.2/datafiles/colormanagement/config.ocio",
                   "ANGELO_BAD": "two\nlines"}
        carried = update_install.carried_environment(environ, "/Apps/Angelo.app")
        self.assertEqual(carried, {"ANGELO_STATE_DIR": "/s", "BLENDER_USER_CONFIG": "/c", "CFFIXED_USER_HOME": "/h"})
        secrets = {"ANGELO_XAI_API_KEY": "x", "ANGELO_TEAM_GRANT_KEY": "g", "ANGELO_CACHE_KEY_SECRET": "s", "ANGELO_TEAM_CACHE_KEY_SECRET": "s",
                   "BLENDER_AUTH_TOKEN": "t", "ANGELO_PROXY_PASSWORD": "p", "ANGELO_GITHUB_PAT": "h",
                   "ANGELO_OPENAI_APIKEY": "o", "SENSEI_XAI_API_KEY": "l", "ANGELO_SIGNING_PASSPHRASE": "p",
                   "ANGELO_APIKEY2": "k", "ANGELO_BEARER": "b", "ANGELO_PROXY": "https://user:pass@proxy.example:8080/"}
        self.assertEqual(update_install.carried_environment(dict(environ, **secrets), "/Apps/Angelo.app"), carried,
                         "nothing named like a secret reaches open's command line")
        self.assertIn("ANGELO_DISABLE_KEYCHAIN", update_install.carried_environment(
            {"ANGELO_DISABLE_KEYCHAIN": "1"}, "/A"), "a whole-word match: KEYCHAIN is not KEY")
        self.assertIn("SENSEI_STATE_DIR", update_install.carried_environment({"SENSEI_STATE_DIR": "/s"}, "/A"),
                      "the deprecated spellings Angelo still reads are carried too")
        own = dict(environ, OCIO="/studio/config.ocio")
        self.assertEqual(update_install.carried_environment(own, "/Apps/Angelo.app")["OCIO"], "/studio/config.ocio")

    def test_inputs_for_each_platform(self):
        mac = Request("macos", Path("/w"), 42, "1.0.1", "Angelo", open_file="/f/a b.blend", app="/A/Angelo.app",
                      new="/w/staged/Angelo.app")
        inputs = update_install.inputs(mac, {"ANGELO_STATE_DIR": "/s"})
        self.assertEqual((inputs["ANGELO_UPDATE_PID"], inputs["ANGELO_UPDATE_RELAUNCH"], inputs["ANGELO_UPDATE_OPEN"],
                          inputs["ANGELO_UPDATE_ENV"], inputs["ANGELO_UPDATE_GATEKEEPER"]),
                         ("42", "1", "/f/a b.blend", "ANGELO_STATE_DIR=/s", "0"))
        notarized = Request("macos", Path("/w"), 42, "1.0.1", "Angelo", app="/A/Angelo.app", new="/n", gatekeeper=True)
        self.assertEqual(update_install.inputs(notarized, {})["ANGELO_UPDATE_GATEKEEPER"], "1")
        windows = Request("windows", Path("C:/w"), 7, "1.0.1", "Angelo.exe", relaunch=False, msi="C:/w/a.msi",
                          root="C:/P/Angelo", launcher="C:/P/Angelo/Angelo-launcher.exe")
        inputs = update_install.inputs(windows, {})
        self.assertEqual((inputs["ANGELO_UPDATE_MSI"], inputs["ANGELO_UPDATE_ROOT"], inputs["ANGELO_UPDATE_RELAUNCH"]),
                         ("C:/w/a.msi", "C:/P/Angelo", ""))

    def test_the_script_environment(self):
        mac = Request("macos", Path("/w"), 1, "1.0.1", "Angelo", app="/A/Angelo.app", new="/n")
        env = update_install.helper_environment(mac, {"HOME": "/h", "SECRET_TOKEN": "x", "PATH": "/opt/bin"})
        self.assertEqual(env["PATH"], update_install.MAC_TOOL_PATH)
        self.assertNotIn("SECRET_TOKEN", env, "the macOS script gets only what it needs")
        windows = Request("windows", Path("C:/w"), 1, "1.0.1", "Angelo.exe", msi="m", root="C:/P/Angelo")
        env = update_install.helper_environment(windows, {"PATH": "C:/Windows", "ANGELO_PYCACHE_PREFIX": "x",
                                                          "PYTHONPYCACHEPREFIX": "C:/U/Cache/pycache/old-build",
                                                          "PYTHONDONTWRITEBYTECODE": "1",
                                                          "VK_LOADER_LAYERS_ALLOW": "y", "ANGELO_UPDATE_OPEN": "old"})
        self.assertEqual(env["PATH"], "C:/Windows")
        # The running build's bytecode decision (sitecustomize.py exports it): the new build makes its own.
        for name in ("ANGELO_PYCACHE_PREFIX", "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"):
            self.assertNotIn(name, env)
        self.assertNotIn("VK_LOADER_LAYERS_ALLOW", env)
        self.assertEqual(env["ANGELO_UPDATE_OPEN"], "", "an earlier run's inputs never leak in")


class StartTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)

    def test_the_script_is_copied_out_and_started_detached(self):
        (self.work / update_install.READY_FILE).write_text("old")
        (self.work / update_install.RESULT_FILE).write_text("status=failed\n")
        calls = []
        update_install.start(Request("macos", self.work, 1, "1.0.1", "Angelo", app="/A", new="/N"),
                             environ={"HOME": "/h"}, popen=lambda args, **kw: calls.append((args, kw)))
        args, options = calls[0]
        self.assertEqual(args, ["/bin/sh", str(self.work / "install_update_macos.sh")])
        self.assertTrue(options["start_new_session"])
        self.assertEqual((self.work / "install_update_macos.sh").read_bytes(),
                         (HELPERS / "install_update_macos.sh").read_bytes())
        self.assertFalse((self.work / update_install.READY_FILE).exists(), "an earlier marker never counts")
        self.assertIsNone(update_install.read_result(self.work))

    def test_windows_leaves_any_job_and_falls_back_when_a_job_forbids_it(self):
        calls = []

        def popen(args, **kw):
            calls.append(kw["creationflags"])
            if kw["creationflags"] & update_install.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError("job forbids breakaway")
        update_install.start(Request("windows", self.work, 1, "1.0.1", "Angelo.exe", msi="m", root="r"),
                             environ={"SystemRoot": r"C:\Windows"}, popen=popen)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0] & update_install.CREATE_NO_WINDOW and not calls[1] & update_install.CREATE_BREAKAWAY_FROM_JOB)

    def test_results_are_read_strictly(self):
        (self.work / "result.txt").write_text("status=installed\nstep=swap\nversion=1.0.1\nnoise=x\n")
        self.assertEqual(update_install.read_result(self.work), {"status": "installed", "step": "swap", "version": "1.0.1"})
        (self.work / "result.txt").write_text("status=maybe\n")
        self.assertIsNone(update_install.read_result(self.work))

    def test_platform_families(self):
        self.assertEqual([update_install.family(k) for k in ("macos-arm64", "darwin", "windows-x64", "win32", "linux-x64")],
                         ["macos", "macos", "windows", "windows", ""])


class WindowsScriptContractTests(unittest.TestCase):
    def test_the_windows_script_reads_only_its_variables_and_installs_per_user(self):
        raw = (HELPERS / "install_update_windows.cmd").read_bytes()
        self.assertIn(b"\r\n", raw, "cmd.exe scripts keep Windows line endings")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "no bare line feeds")
        raw.decode("ascii")   # An ASCII script: every path arrives through the environment instead.
        text = raw.decode("ascii")
        for name in ("PID", "EXECUTABLE", "MSI", "SHA256", "ROOT", "DIR", "VERSION", "RELAUNCH", "OPEN"):
            self.assertIn("ANGELO_UPDATE_" + name, text)
        for flag in ("/qb!-", "/norestart", "ALLUSERS=2", "MSIINSTALLPERUSER=1", "MSIRESTARTMANAGERCONTROL=Disable",
                     "REBOOT=ReallySuppress", 'INSTALL_ROOT="%ANGELO_UPDATE_ROOT%"'):
            self.assertIn(flag, text)
        install = text.index('start "" /wait msiexec.exe')
        self.assertLess(text.index('mkdir "%dir%\\helper.lock"'), text.index("type nul > \"%dir%\\helper.ready\""),
                        "one installer at a time, and it says it runs before it waits")
        self.assertLess(text.index(":wait"), install, "nothing is installed before Angelo quits")
        self.assertLess(text.index("certutil -hashfile"), install, "the installer's digest is checked again first")
        self.assertIn('findstr /i /x /c:"%ANGELO_UPDATE_SHA256%"', text,
                      "matched as a whole line, whatever else the command processor prints")
        self.assertIn('/FI "USERNAME eq %USERDOMAIN%\\%USERNAME%"', text, "only this user's copies count")
        relaunches = [line.strip() for line in text.splitlines() if line.strip().startswith('start "" ') and
                      "%relaunch%" in line]
        self.assertTrue(relaunches)
        for line in relaunches:
            self.assertIn('/D "%home%"', line, "the new version never starts in the update folder it deletes")
        self.assertIn('set "home=%%~dpr."', text, "its own folder: the launcher's")


class MacScriptContractTests(unittest.TestCase):
    def test_nothing_is_registered_or_opened_before_its_signature_check(self):
        text = (HELPERS / "install_update_macos.sh").read_text(encoding="utf-8")
        relaunch = text[text.index("relaunch() {"):text.index("finish() {")]
        self.assertLess(relaunch.index('launchable "$app" ||'), relaunch.index("/usr/bin/open"))
        self.assertEqual(text.count("/usr/bin/open"), 1, "relaunch() is the only way the script opens anything")
        self.assertIn('if [ -x "$lsregister" ] && launchable "$app"; then', text)
        self.assertEqual(text.count('"$lsregister" -f'), 1)
        self.assertLess(text.index('"$lsregister" -f "$app"'), text.index('"$lsregister" -u "$previous"'),
                        "the moved-away previous app is unregistered once the new one is in place")
        self.assertLess(text.index("finish failed installed_check"), text.index('"$lsregister" -u "$previous"'))
        launchable = text[text.index("launchable() {"):text.index("relaunch() {")]
        self.assertIn("/usr/bin/codesign --verify --deep --strict", launchable)
        self.assertIn("/usr/sbin/spctl --assess --type execute", launchable)
        swap = text.index('/bin/mv "$new" "$app"')
        self.assertLess(text.index('/usr/sbin/spctl --assess --type execute "$new"'), swap, "Gatekeeper before the swap")
        self.assertLess(swap, text.index("finish failed installed_check"), "and the installed bundle once in place")


@unittest.skipIf(os.name == "nt", environment_skip("the macOS script runs on POSIX systems"))
class MacScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.app = bundle(self.root / "Applications" / "Angelo.app", "old")
        self.work = self.root / "updates" / "1.0.1"
        self.new = bundle(self.work / "staged" / "Angelo.app", "new")

    def _cleanup(self):
        for folder, _, _ in os.walk(self.root):
            try:
                os.chmod(folder, 0o755)
            except OSError:
                pass
        shutil.rmtree(self.root, ignore_errors=True)

    def run_script(self, pid, new=None, executable="Angelo", requirement="", locked=False, gatekeeper=False):
        request = Request("macos", self.work, pid, "1.0.1", executable, relaunch=False, app=str(self.app),
                          new=str(new or self.new), requirement=requirement, gatekeeper=gatekeeper)
        script = update_install.prepare_folder(request)
        if locked:
            (self.work / update_install.LOCK_DIR).mkdir()
        env = update_install.helper_environment(request, {"HOME": str(self.root)})
        done = subprocess.run(["/bin/sh", str(script)], env=env, timeout=60, capture_output=True, text=True)
        if locked:
            return done
        self.assertTrue(update_install.ready(self.work), done.stderr)
        return update_install.read_result(self.work)

    def test_a_second_installer_for_the_same_update_does_nothing(self):
        done = self.run_script(999999, locked=True)
        self.assertEqual(done.returncode, 0)
        self.assertFalse(update_install.ready(self.work))
        self.assertIsNone(update_install.read_result(self.work))
        self.assertEqual((self.app / "Contents/marker.txt").read_text(), "old")

    def test_a_stage_that_fails_its_signature_check_at_swap_time_is_never_installed(self):
        result = self.run_script(999999, requirement='identifier "studio.hyperbond.angelo"')   # The stand-in is unsigned.
        self.assertEqual((result["status"], result["step"]), ("failed", "signature"))
        self.assertEqual((self.app / "Contents/marker.txt").read_text(), "old")
        self.assertTrue(self.new.exists())

    @unittest.skipUnless(sys.platform == "darwin", environment_skip("codesign and spctl are macOS tools"))
    def test_a_stage_gatekeeper_refuses_is_never_installed_when_the_running_app_is_approved(self):
        for path, marker in ((self.app, "old"), (self.new, "new")):
            adhoc(path, marker)   # Sealed, but ad hoc: Gatekeeper refuses it.
        # The installed stand-in is registered with Launch Services like a real update: unregistered again here.
        self.addCleanup(subprocess.run, [LSREGISTER, "-u", str(self.app)], capture_output=True)
        requirement = 'identifier "%s"' % TEST_ID
        result = self.run_script(999999, requirement=requirement, gatekeeper=True)
        self.assertEqual((result["status"], result["step"]), ("failed", "gatekeeper"))
        self.assertEqual((self.app / "Contents/Resources/marker.txt").read_text(), "old")
        result = self.run_script(999999, requirement=requirement)   # A development build: the signature alone.
        self.assertEqual((result["status"], result["step"]), ("installed", "swap"))
        self.assertEqual((self.app / "Contents/Resources/marker.txt").read_text(), "new")

    def test_it_waits_for_angelo_then_swaps_the_bundles(self):
        # Never reaped while the script runs (a zombie once it ends), as when Angelo's parent does not wait for it.
        angelo = subprocess.Popen(["/bin/sleep", "1"])
        self.addCleanup(angelo.wait)
        started = time.monotonic()
        result = self.run_script(angelo.pid)
        self.assertGreaterEqual(time.monotonic() - started, 0.9, "nothing moved while Angelo ran")
        self.assertEqual((result["status"], result["step"], result["version"]), ("installed", "swap", "1.0.1"))
        self.assertEqual((self.app / "Contents/marker.txt").read_text(), "new")
        previous = Path((self.work / "previous.txt").read_text().strip())
        self.assertEqual((previous / "Contents/marker.txt").read_text(), "old")
        self.assertFalse(self.new.exists())

    def test_a_failed_second_rename_puts_the_old_app_back(self):
        os.chmod(self.new.parent, stat.S_IRUSR | stat.S_IXUSR)   # The staged bundle can't be moved out.
        result = self.run_script(999999)
        self.assertEqual((result["status"], result["step"]), ("failed", "move_new"))
        self.assertEqual((self.app / "Contents/marker.txt").read_text(), "old")

    def test_a_missing_update_changes_nothing(self):
        result = self.run_script(999999, new=self.work / "nothing" / "Angelo.app")
        self.assertEqual((result["status"], result["step"]), ("failed", "missing_update"))
        self.assertEqual((self.app / "Contents/marker.txt").read_text(), "old")

    def test_another_copy_of_the_app_running_stops_the_swap(self):
        executable = self.app / "Contents/MacOS/Angelo"
        if sys.platform == "darwin":
            # macOS reports a process by the path it was started as (argv[0]), and refuses to run a copied
            # system binary, so the stand-in copy runs sleep under the bundle's executable path.
            other = subprocess.Popen([str(executable), "5"], executable=shutil.which("sleep"))
        else:
            shutil.copyfile(shutil.which("sleep"), executable)   # /proc names the executable file itself.
            os.chmod(executable, 0o755)
            other = subprocess.Popen([str(executable), "5"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        time.sleep(0.2)
        result = self.run_script(999999)
        self.assertEqual((result["status"], result["step"]), ("aborted", "another_instance"))
        self.assertEqual((self.app / "Contents/marker.txt").read_text(), "old")


class WindowsUpdateEvidenceTests(unittest.TestCase):
    """The Windows end-to-end update proves what the download page says: the app's own download carries no Mark of
    the Web, so SmartScreen does not ask again for an update (the e2e runs on the VM; its parts are pinned here)."""

    def test_the_driver_checks_the_staged_installer_for_the_mark_of_the_web(self):
        here = Path(__file__).resolve().parent
        driver = (here / "update_e2e_driver.py").read_text(encoding="utf-8")
        self.assertIn('open(str(path) + ":Zone.Identifier", "rb")', driver)
        self.assertIn('check("staged_installer_has_no_mark_of_the_web"', driver)
        ready = driver.index('state["wait_reason"] = update_ready.wait_reason()')
        self.assertLess(ready, driver.index("staged_installer_has_no_mark_of_the_web"), "checked once the update is ready")
        e2e = (here / "update_e2e_windows.py").read_text(encoding="utf-8")
        self.assertIn('report.data["smartscreen"] = smartscreen_settings()', e2e)
        self.assertIn("driver_checks_passed", e2e, "the driver's checks, this one included, decide the run")


if __name__ == "__main__":
    unittest.main()
