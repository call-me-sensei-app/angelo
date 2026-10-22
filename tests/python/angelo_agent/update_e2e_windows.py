# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""End-to-end Windows update: a per-user Angelo installs a newer per-user MSI through Restart to Update.

Run in the signed-in desktop session (never as SYSTEM: the install is the session user's), with the installed
app's own Python or any Python 3.13, from the source tree::

    python tests\\python\\angelo_agent\\update_e2e_windows.py run --old Angelo-1.0.0-windows-x64.msi \\
        --new Angelo-1.0.1-windows-x64.msi --work C:\\angelo\\update-e2e

It installs the older MSI for the current user (the MSI's default: no administrator prompt), serves a feed
signed with a throwaway key announcing the newer one (as the download host would), starts the older app with an
isolated profile and ``update_e2e_driver.py`` (which presses Restart to Update and Save and Restart with real
window events), and then requires: both MSIs declare that they need no elevation (their summary's Word Count,
which ``InstallPrivileges="limited"`` sets), the old app quit, the installer reported ``installed`` (Windows Installer's
exit code is in the report), the newer app started from the same folder, that folder and Windows Installer both
say the newer version, the product is still installed for this user only, What's New was shown, the saved file
was reopened and the update's folder was cleaned up. Finally it quits the app it started and uninstalls the
per-user product. ``<work>\\qa\\update-e2e.json`` has every check, the installer's result and log. Contract:
doc/angelo/updates_and_reports.rst, "Installing updates".
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/modules"))
import update_e2e_common as common  # noqa: E402

PLATFORM = "windows-x64"


def smartscreen_settings():
    """SmartScreen for apps and files as this machine sets it: Explorer's choice and any policy over it."""
    import winreg
    found = {}
    for label, hive, key, name in (
            ("explorer", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer", "SmartScreenEnabled"),
            ("policy", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\System", "EnableSmartScreen"),
            ("policy_level", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\System", "ShellSmartScreenLevel")):
        try:
            with winreg.OpenKey(hive, key) as opened:
                found[label] = winreg.QueryValueEx(opened, name)[0]
        except OSError:
            found[label] = None   # not set: Windows' default (warn)
    return found
DRIVER = HERE / "update_e2e_driver.py"
VERSION = re.compile(r"-(\d+\.\d+\.\d+)-windows-x64\.msi$")


def identity():
    return json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))


def products():
    from angelo_agent import update_stage_win
    return update_stage_win.related_products(identity()["windows"]["msi_upgrade_code"])


def product_version(code):
    import ctypes
    from angelo_agent import update_stage_win
    return update_stage_win._product_info(ctypes.WinDLL("msi"), code, "VersionString")


def needs_no_elevation(path):
    """True when the package's summary says installing it needs no elevated privileges (Word Count bit 8)."""
    import ctypes
    from ctypes import wintypes
    msi = ctypes.WinDLL("msi")
    handle = wintypes.DWORD()
    if msi.MsiGetSummaryInformationW(0, str(path), 0, ctypes.byref(handle)):
        return False
    try:
        kind, value, size = wintypes.UINT(), ctypes.c_int(), wintypes.DWORD(0)
        code = msi.MsiSummaryInfoGetPropertyW(handle, 15, ctypes.byref(kind), ctypes.byref(value), None, None,
                                               ctypes.byref(size))
        return code == 0 and bool(value.value & 8)
    finally:
        msi.MsiCloseHandle(handle)


def msiexec(arguments, log):
    command = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "msiexec.exe")] + arguments
    command += ["/qn", "/norestart", "/l*v", str(log)]
    return subprocess.run(command).returncode


def installed_version(root):
    data = next(Path(root).glob("*/scripts/modules/angelo_identity.json"), None)
    return json.loads(data.read_text(encoding="utf-8"))["version"]["label"] if data else ""


def running(executable):
    from angelo_agent import update_stage_win
    return update_stage_win.other_instances(str(executable), 0)


def run(old_msi, new_msi, work, timeout=1500):
    work = Path(work)
    if work.exists() and any(work.iterdir()):
        raise SystemExit("%s is not empty" % work)
    report = common.Report(work / "qa" / "update-e2e.json")
    env = dict(os.environ, **common.isolated_environment(work))
    names = identity()["windows"]
    old_version, new_version = VERSION.search(str(old_msi)).group(1), VERSION.search(str(new_msi)).group(1)
    report.data.update(old_version=old_version, new_version=new_version)
    if [p for p in products() if p[1] == "0"]:
        raise SystemExit("Angelo is already installed for this user: uninstall it first")
    # A per-user install asks for no elevation. Proven here from the packages themselves: the test session may be an
    # administrator's (the cloud machine's desktop user is), where a prompt would not show either way.
    report.check("packages_need_no_elevation", needs_no_elevation(old_msi) and needs_no_elevation(new_msi))
    # What SmartScreen is set to on this machine, for the driver's staged_installer_has_no_mark_of_the_web: an update
    # that installs with no one to answer a prompt shows none appeared, and only under SmartScreen does that say much.
    report.data["smartscreen"] = smartscreen_settings()
    code = msiexec(["/i", str(old_msi)], work / "qa" / "install-old.log")
    report.check("old_version_installed_for_this_user", code in (0, 3010), "msiexec %d" % code)
    mine = [p for p in products() if p[1] == "0"]
    report.check("installed_per_user", mine, repr(mine))
    if not mine:
        return report.finish()
    product, _, location = mine[0]
    root = Path(location.rstrip("\\/"))
    report.data.update(install_root=str(root), old_product=product)
    report.check("installed_under_local_app_data", str(root).lower().startswith(
        os.environ["LOCALAPPDATA"].lower()), str(root))
    executable = root / (names["exe_stem"] + ".exe")
    launcher = root / (names["exe_stem"] + names["launcher_suffix"] + ".exe")
    from loopback_server import LoopbackServer
    server = LoopbackServer().start()
    started = []
    try:
        env.update(common.serve_update(server, PLATFORM, new_version, new_msi))
        env["ANGELO_E2E_FILE"] = str(work / "scene.blend")
        log = open(work / "qa" / "app-old.log", "w")
        subprocess.Popen([str(launcher), "--enable-event-simulate", "--python", str(DRIVER)], env=env,
                         stdout=log, stderr=subprocess.STDOUT)
        pids = common.wait_for(lambda: running(executable), 90)
        started += pids
        report.check("old_version_started", pids)
        first = pids[0] if pids else None
        report.check("old_version_quit_for_the_update",
                     common.wait_for(lambda: first is not None and first not in running(executable), timeout, 2.0))
        cache = common.driver_report(work).get("cachedir", "")
        updates = Path(cache) / "updates" / new_version
        report.data["updates_folder"] = str(updates)
        result = common.wait_for(lambda: (updates / "result.txt").exists() and updates / "result.txt", 600, 2.0)
        if result:
            report.data["installer_result"] = result.read_text()
            if (updates / "install.log").exists():
                shutil.copyfile(updates / "install.log", work / "qa" / "msiexec-update.log")
        report.check("installer_reports_installed", result and "status=installed" in result.read_text())
        relaunched = common.wait_for(lambda: [p for p in running(executable) if p != first], 180, 2.0)
        started += relaunched or []
        report.check("new_version_relaunched_from_the_same_folder", relaunched)
        report.check("installed_folder_is_the_new_version", installed_version(root) == new_version,
                     installed_version(root))
        now = [p for p in products() if p[1] == "0"]
        report.check("windows_installer_has_the_new_version_for_this_user_only",
                     len(now) == 1 and product_version(now[0][0]).startswith(new_version) and
                     not [p for p in products() if p[1] == "1" and Path(p[2] or "x") == root], repr(now))
        report.check("new_version_showed_whats_new", common.wait_for(
            lambda: common.last_seen(env["ANGELO_STATE_DIR"]) == new_version, 120, 2.0))
        report.check("new_version_reopened_the_saved_file", common.wait_for(
            lambda: common.first_recent_file(env["BLENDER_USER_CONFIG"]) == env["ANGELO_E2E_FILE"], 60, 2.0))
        report.check("new_version_removed_the_update_folder", common.wait_for(lambda: not updates.exists(), 120, 2.0))
        driver = common.driver_report(work)
        report.data["driver"] = driver
        report.check("driver_checks_passed", driver.get("checks") and all(driver["checks"].values()))
        report.data["requests"] = [(r.method, r.path, r.headers.get("range", "")) for r in server.requests]
    finally:
        server.stop()
        for pid in running(executable):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        for code, assignment, _ in products():
            if assignment == "0":
                report.data.setdefault("uninstalled", []).append(
                    [code, msiexec(["/x", code], work / "qa" / ("uninstall-%s.log" % code.strip("{}")))])
        report.finish()
    return report.data["success"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    update = commands.add_parser("run")
    update.add_argument("--old", required=True)
    update.add_argument("--new", required=True)
    update.add_argument("--work", required=True)
    update.add_argument("--timeout", type=int, default=1500)
    arguments = parser.parse_args(argv)
    return 0 if run(arguments.old, arguments.new, arguments.work, arguments.timeout) else 1


if __name__ == "__main__":
    sys.exit(main())
