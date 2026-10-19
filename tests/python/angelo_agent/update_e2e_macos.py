# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""End-to-end macOS update: a signed Angelo updates itself to a newer signed build, as a person would see it.

Run with any Python 3.13 (Blender's bundled one is fine), on a Mac with a window session::

    # 1. A newer build to update to: a copy of a built Angelo.app with another version (Info.plist, the
    #    application identity and a What's New entry), so the two builds differ the way two releases do.
    python3.13 tests/python/angelo_agent/update_e2e_macos.py newer --app <built Angelo.app> --version 1.0.1 --out <dir>

    # 2. Sign both the way a release is signed (scripts/angelo/sign_notarize_macos.py; --skip-notarize is enough:
    #    the update checks the signature and its team, not the notarization ticket). ``sign`` runs the release
    #    signer for a build of another version than this source's.
    python3.13 tests/python/angelo_agent/update_e2e_macos.py sign --app <dir>/Angelo.app --out <signed-new> \\
        --identity "Developer ID Application: ..." --version 1.0.1

    # 3. The update itself: install A, serve a feed (signed with a throwaway key) announcing B's disk image (and a
    #    delta, with --delta), start A through Launch Services with an isolated profile and the driver
    #    (update_e2e_driver.py), which waits for the ready card, leaves an unsaved change and presses Restart to
    #    Update and Save and Restart with real window events. Then B must be the installed app, verified, running
    #    from the same place and showing the saved file.
    python3.13 tests/python/angelo_agent/update_e2e_macos.py run --old <signed-old>/Angelo.app \\
        --dmg <signed-new>/Angelo-1.0.1-macos-arm64.dmg [--delta <delta>] --work <empty dir> [--install-dir <dir>]

``run`` launches nothing macOS would call damaged: the old app and the update must both pass ``codesign --verify
--deep --strict`` first (an unsigned ``newer`` copy is refused), the installed result must pass it after, and its
copies are unregistered from Launch Services at the end, so nothing resolving Angelo by its identifier lands on one.

``run`` writes ``<work>/qa/update-e2e.json`` (every check, the installer's result and log) and leaves the
screenshots the driver took in ``<work>/qa``. It never touches the person's own profile: HOME,
CFFIXED_USER_HOME, Blender's user folders and Angelo's state are inside ``work`` (the relaunched app gets them
back from the installer, as it carries ``ANGELO_*``, ``BLENDER_*`` and ``CFFIXED_USER_HOME``). It quits only the
processes it started, by process id: never another running Angelo. Contract: doc/angelo/updates_and_reports.rst,
"Installing updates".
"""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from update_e2e_common import Report, isolated_environment, serve_update, wait_for  # noqa: E402

PLATFORM = "macos-arm64"
DRIVER = HERE / "update_e2e_driver.py"


def run_tool(args, **options):
    options.setdefault("check", True)
    return subprocess.run(args, capture_output=True, text=True, **options)


def resources(app):
    """``Contents/Resources/<Blender major.minor>``."""
    return next(path for path in (Path(app) / "Contents/Resources").iterdir()
                if path.is_dir() and path.name[:1].isdigit())


# -- newer ---------------------------------------------------------------------------------------------------

def newer(app, version, out):
    """A copy of ``app`` that reports ``version`` everywhere a build does, with a What's New entry for it."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / Path(app).name
    if target.exists():
        raise SystemExit("%s already exists" % target)
    run_tool(["/bin/cp", "-c", "-R", str(app), str(target)])
    info = target / "Contents/Info.plist"
    document = plistlib.loads(info.read_bytes())
    old = document["CFBundleShortVersionString"]
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        document[key] = version
    if isinstance(document.get("CFBundleGetInfoString"), str):
        document["CFBundleGetInfoString"] = document["CFBundleGetInfoString"].replace(old, version)
    info.write_bytes(plistlib.dumps(document))
    identity = resources(target) / "scripts/modules/angelo_identity.json"
    data = json.loads(identity.read_text(encoding="utf-8"))
    data["version"]["label"] = version
    identity.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    notes = resources(target) / "scripts/addons_core/angelo_agent/assets/release_notes.json"
    data = json.loads(notes.read_text(encoding="utf-8"))
    newest = dict(data["releases"][0])
    newest.update(version=version, date=time.strftime("%Y-%m-%d"), title="Angelo " + version,
                  summary="A test build for the end-to-end update.",
                  highlights=["This build was installed by Angelo's own update."],
                  sections=[{"heading": "Improved", "items": ["Updated from %s to %s in place." % (old, version)]}])
    data["releases"].insert(0, newest)
    notes.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


# -- sign ----------------------------------------------------------------------------------------------------

def sign(app, out, identity, version, keychain=""):
    """The release signer for a build of ``version`` (it otherwise expects this source's own version)."""
    import sign_notarize_macos
    sign_notarize_macos.IDENTITY["version"]["label"] = version
    argv = ["--app", str(app), "--out", str(out), "--identity", identity, "--skip-notarize", "--unstyled-dmg"]
    if keychain:
        argv += ["--keychain", keychain]
    return sign_notarize_macos.main(argv)


# -- run -----------------------------------------------------------------------------------------------------

def processes_running(executable):
    """Process ids whose executable path (argv[0], as ps reports it on macOS) is ``executable``."""
    listing = subprocess.run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    found = []
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if command.strip() == str(executable):
            found.append(int(pid))
    return found


LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def sealed(app):
    """``(True, "")`` when ``codesign --verify --deep --strict`` accepts ``app``, else ``(False, why)``."""
    verify = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True,
                            text=True)
    return verify.returncode == 0, verify.stderr.strip()[-200:]


def run(old, dmg, work, install_dir=None, delta=None, timeout=900):
    from loopback_server import LoopbackServer
    old, work = Path(old), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    report = Report(work / "qa" / "update-e2e.json")
    env = isolated_environment(work)
    install_parent = Path(install_dir) if install_dir else work / "Applications"
    install_parent.mkdir(parents=True, exist_ok=True)
    app = install_parent / old.name
    if app.exists():
        raise SystemExit("%s already exists: give an empty --install-dir" % app)
    run_tool(["/usr/bin/ditto", str(old), str(app)])
    ok, why = sealed(app)
    if not report.check("old_app_signature_is_valid", ok, why):
        run_tool([LSREGISTER, "-u", str(app)], check=False)
        report.finish()
        raise SystemExit("%s fails codesign --verify --deep --strict: it is never opened" % old)
    old_info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    executable = app / "Contents/MacOS" / old_info["CFBundleExecutable"]
    old_version = old_info["CFBundleShortVersionString"]
    mount = run_tool(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountrandom",
                      str(work / "tmp"), str(dmg)]).stdout
    point = mount.strip().splitlines()[-1].split("\t")[-1].strip()
    try:
        new_version = plistlib.loads((Path(point) / old.name / "Contents/Info.plist").read_bytes())[
            "CFBundleShortVersionString"]
        new_ok, why = sealed(Path(point) / old.name)
    finally:
        subprocess.run(["/usr/bin/hdiutil", "detach", point], capture_output=True)
    if not report.check("update_signature_is_valid", new_ok, why):
        run_tool([LSREGISTER, "-u", str(app)], check=False)
        report.finish()
        raise SystemExit("the disk image's app fails codesign --verify --deep --strict: it is never installed")
    report.data.update(old_version=old_version, new_version=new_version, app=str(app), dmg=str(dmg),
                       delta=str(delta or ""))
    server = LoopbackServer().start()
    try:
        env.update(serve_update(server, PLATFORM, new_version, dmg, delta, old_version))
        env.update(CFFIXED_USER_HOME=str(work / "home"), ANGELO_E2E_FILE=str(work / "scene.blend"))
        command = ["/usr/bin/open", "-n"]
        for name, value in sorted(env.items()):
            command += ["--env", "%s=%s" % (name, value)]
        command += ["--stdout", str(work / "qa" / "app-old.log"), "--stderr", str(work / "qa" / "app-old.log")]
        command += [str(app), "--args", "--enable-event-simulate", "--python", str(DRIVER)]
        report.event("starting %s %s" % (old_version, app))
        subprocess.run(command, check=True)
        pids = wait_for(lambda: processes_running(executable), 60)
        report.check("old_version_started", pids)
        for pid in pids:
            subprocess.run(["/usr/bin/renice", "-n", "19", "-p", str(pid)], capture_output=True)
        first = pids[0] if pids else None
        gone = wait_for(lambda: first is not None and first not in processes_running(executable), timeout, 1.0)
        report.check("old_version_quit_for_the_update", gone)
        driver = work / "qa" / "update-e2e-driver.json"
        cache = json.loads(driver.read_text()).get("cachedir") if driver.exists() else ""
        updates = Path(cache or Path(env["CFFIXED_USER_HOME"]) / "Library/Caches/Angelo") / "updates" / new_version
        report.data["updates_folder"] = str(updates)
        result_file = wait_for(lambda: (updates / "result.txt").exists() and updates / "result.txt", 120)
        if result_file:
            report.data["installer_result"] = result_file.read_text()
            log = updates / "install.log"
            report.data["installer_log"] = log.read_text() if log.exists() else ""
            shutil.copytree(updates, work / "qa" / "installer-folder", ignore=shutil.ignore_patterns("*.app"),
                            dirs_exist_ok=True)
        report.check("installer_reports_installed", result_file and "status=installed" in result_file.read_text())
        relaunched = wait_for(lambda: [p for p in processes_running(executable) if p != first], 120)
        report.check("new_version_relaunched_from_the_same_place", relaunched)
        for pid in relaunched or ():
            subprocess.run(["/usr/bin/renice", "-n", "19", "-p", str(pid)], capture_output=True)
        installed = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        report.check("installed_bundle_is_the_new_version", installed["CFBundleShortVersionString"] == new_version,
                     installed["CFBundleShortVersionString"])
        report.check("installed_bundle_signature_is_valid", *sealed(app))
        state_file = Path(env["ANGELO_STATE_DIR"]) / "updates.json"
        seen = wait_for(lambda: state_file.exists() and json.loads(state_file.read_text()).get(
            "last_seen_version") == new_version, 90, 1.0)
        report.check("new_version_showed_whats_new", seen)
        recent = Path(env["BLENDER_USER_CONFIG"]) / "recent-files.txt"
        reopened = wait_for(lambda: recent.exists() and recent.read_text().splitlines()[:1] == [env["ANGELO_E2E_FILE"]],
                            60, 1.0)
        report.check("new_version_reopened_the_saved_file", reopened)
        cleaned = wait_for(lambda: not updates.exists(), 90, 1.0)
        report.check("new_version_removed_the_previous_app_and_download", cleaned)
        if driver.exists():
            report.data["driver"] = json.loads(driver.read_text())
            report.check("driver_checks_passed", all(report.data["driver"].get("checks", {}).values()))
        report.data["requests"] = [(r.method, r.path, r.headers.get("range", "")) for r in server.requests]
        if delta:
            paths = [r.path for r in server.requests]
            report.check("updated_through_the_delta_alone", any(p.endswith(Path(delta).name) for p in paths)
                         and not any(p.endswith(Path(dmg).name) for p in paths))
    finally:
        server.stop()
        for pid in processes_running(executable):
            subprocess.run(["/bin/kill", str(pid)], capture_output=True)
        run_tool([LSREGISTER, "-u", str(app)], check=False)   # The test copy must never answer for Angelo.
    return report.finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("newer")
    make.add_argument("--app", required=True)
    make.add_argument("--version", required=True)
    make.add_argument("--out", required=True)
    signing = commands.add_parser("sign")
    signing.add_argument("--app", required=True)
    signing.add_argument("--out", required=True)
    signing.add_argument("--identity", required=True)
    signing.add_argument("--version", required=True)
    signing.add_argument("--keychain", default="")
    update = commands.add_parser("run")
    update.add_argument("--old", required=True)
    update.add_argument("--dmg", required=True)
    update.add_argument("--delta")
    update.add_argument("--work", required=True)
    update.add_argument("--install-dir")
    update.add_argument("--timeout", type=int, default=900)
    arguments = parser.parse_args(argv)
    if arguments.command == "newer":
        print(newer(arguments.app, arguments.version, arguments.out))
        return 0
    if arguments.command == "sign":
        return sign(arguments.app, arguments.out, arguments.identity, arguments.version, arguments.keychain)
    return 0 if run(arguments.old, arguments.dmg, arguments.work, arguments.install_dir, arguments.delta,
                    arguments.timeout) else 1


if __name__ == "__main__":
    sys.exit(main())
