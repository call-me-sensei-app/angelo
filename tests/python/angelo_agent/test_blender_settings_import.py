# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Blender user switching to Angelo brings their settings: the setup card, the menus, failures and undo.

Run outside Blender (``run_native_suites.py --gui --include test_blender_settings_import`` does this)::

  python3 tests/python/angelo_agent/test_blender_settings_import.py --binary /abs/Angelo --out /fresh/dir \\
      [--blender /Applications/Blender.app/Contents/MacOS/Blender]

Every launch gets an isolated home (HOME, CFFIXED_USER_HOME, XDG and TMPDIR inside ``--out``) and, like a real
install, **no BLENDER_USER_* variable**, so Angelo resolves its own folder and finds stock Blender's where a person's
is. ``blender_profile_fixture.py`` first builds that Blender folder with ``--blender`` (stock Blender; default: the
Angelo binary with its own start-up turned off): a non-default preference, a custom keymap item, a user add-on with
preferences and a folder of its own, an extension standing in for Blender's MCP add-on, an asset library, a Quick
Favorites entry, an operator preset, an app template, a startup script, a MatCap, bookmarks, a recent file, a start-up
scene and an early Angelo build's state folder. The host fingerprints that folder (every byte, time and link) before
and after each launch: it never changes.

Phases (``--native-phase``, one window process each):

1. ``card``: the setup card says it brings settings from Blender 5.2 (English, then Arabic, captured); Arabic, the
   light look and the toggle; Continue imports. Then every kind of setting is present, the start-up scene and the
   early state are not, Angelo's agent is enabled with its chat and the welcome, the language and look are the ones
   chosen, and the result notice follows the welcome; Quick Favorites are opened and captured.
2. ``restart``: the same home again. Everything persisted, the startup script ran, the start panel shows.
3. ``none``: no Blender folder. The row explains and cannot be turned on; the menu entry is disabled.
4. ``broken`` and ``broken-restart``: an enabled add-on that fails to register. One calm line names it; startup and
   the agent are unaffected, then and on the next launch.
5. ``menu``: an existing Angelo user (saved preferences) imports from File > Defaults: the confirmation card
   (English and Arabic, captured) says where from, what it replaces, what it keeps and the backup; Import; then Undo
   import from the notice puts Angelo's own preferences back exactly and removes what came over.
6. ``upgrade``: an earlier Angelo version's folder (and a Blender one) exist: the card offers "Use my earlier Angelo
   settings" from Angelo 5.1, and Blender's own import (``preferences.copy_prev``) runs with Angelo kept enabled.

Receipts: ``settings-import.json`` and one ``<phase>.json`` per phase, with the PNGs beside them. No provider,
account, network (``--offline-mode``) or real user folder is used.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXPECTED_FAILURE = "Broken Tool fails to register (fixture)"


def fingerprint(root):
    """Every entry under ``root``: path, type, mode, size, times and content (or link target)."""
    found = {}
    for folder, names, files in os.walk(root):
        for name in sorted(names + files):
            path = Path(folder) / name
            info = os.lstat(path)
            key = str(path.relative_to(root))
            if os.path.islink(path):
                found[key] = ["link", os.readlink(path)]
            elif path.is_dir():
                found[key] = ["dir", info.st_mode, info.st_mtime_ns]
            else:
                found[key] = ["file", info.st_mode, info.st_size, info.st_mtime_ns,
                              hashlib.sha256(path.read_bytes()).hexdigest()]
    return found


def run_host():
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(ROOT / "scripts/addons_core"))
    sys.path.insert(0, str(ROOT / "scripts/modules"))
    import fake_provider_cli
    import gui_launch
    import native_suite_checks
    from angelo_agent import blender_folders
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--blender", default=os.environ.get("ANGELO_QA_STOCK_BLENDER"),
                        help="stock Blender for the fixture folder (default: --binary, start-up off)")
    parser.add_argument("--groups", default="switcher,fresh,broken,menu,upgrade",
                        help="comma list of phase groups to run (default: all; the receipt fails unless all ran)")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh disposable directory")
    out.mkdir(parents=True, exist_ok=True)
    binary = Path(args.binary).resolve(strict=True)
    blender = Path(args.blender).resolve(strict=True) if args.blender else binary
    fake_bin = fake_provider_cli.install(out / "fake-cli-bin")
    report = {"success": False, "phases": [], "binary": str(binary), "fixture_blender": str(blender),
              "provider_starts": 0, "paid_requests": 0}

    def environment(home, extra=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_"))}
        for folder in (home / ".config", home / ".cache", home / ".local/share", home / ".local/state",
                       home / "AppData/Roaming", home / "AppData/Local", home.parent / "tmp"):
            folder.mkdir(parents=True, exist_ok=True)
        env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home.parent / "tmp"),
                   APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"),
                   XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
                   XDG_DATA_HOME=str(home / ".local/share"), XDG_STATE_HOME=str(home / ".local/state"),
                   ANGELO_DISABLE_KEYCHAIN="1", ANGELO_AGENT_RUNTIME="off", ANGELO_DISABLE_HERO="1",
                   ANGELO_STATE_DIR=str(home.parent / "state"), ANGELO_CONNECTIONS_DIR=str(home.parent / "accounts"),
                   ANGELO_LIBRARY_DIR=str(home.parent / "library"), ANGELO_FAKE_CLI_STATE=str(home.parent / "fake-cli"),
                   PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
        env.update(extra or {})
        return env

    def blender_folder(home, env):
        return blender_folders.version_folder(sys.platform, home, env, "5.2")

    def earlier_angelo_folder(home, env):
        import angelo_identity
        names = angelo_identity.data()["paths"]
        name = names["user_dir_name"] if sys.platform in {"darwin", "win32"} else names["user_dir_name_unix"]
        return blender_folder(home, env).parent.parent / name / "5.1"

    def make_profile(name, variant):
        home = out / name / "home"
        home.mkdir(parents=True)
        env = environment(home)
        folder = blender_folder(home, env)
        folder.mkdir(parents=True)
        env.update(BLENDER_USER_RESOURCES=str(folder), ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_INTRO="1")
        command = [str(blender), "--factory-startup", *gui_launch.window_flags(input_isolation=False),
                   "--window-geometry", "60", "60", "1200", "800", "--python", str(HERE / "blender_profile_fixture.py"), "--", "--variant", variant,
                   "--fixture", str(out / name)]
        with (out / name / "profile.out").open("w") as log:
            subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        made = json.loads((out / name / "profile.json").read_text())
        if not made["success"]:
            raise AssertionError("The Blender fixture folder was not made: " + str(made.get("error")))
        return home

    def phase(name, home, sandbox, allowed_failure=False):
        env = environment(home, {"ANGELO_QA_DIR": str(out)})
        folder = blender_folder(home, env)
        before = fingerprint(folder) if folder.exists() else None
        command = [str(binary), "--disable-autoexec", *gui_launch.window_flags(), "--window-geometry", "60", "60",
                   "1440", "900", "--offline-mode", "--python", str(Path(__file__).resolve()),
                   "--", "--native-phase", name, "--fixture-root", str(out), "--sandbox", str(sandbox)]
        output = out / (name + ".out")   # Not *.log: the broken add-on's traceback is expected (checked below).
        with output.open("w") as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=240)
        text = output.read_text(errors="replace")
        data = json.loads((out / (name + ".json")).read_text())
        findings = [f for f in native_suite_checks.scan_log(text)
                    if not (allowed_failure and f["kind"] == "python_traceback" and EXPECTED_FAILURE in f["line"])]
        host = {"exit_code_zero": result.returncode == 0, "no_unexpected_failures_in_output": not findings,
                "blenders_folder_unchanged": before is None or fingerprint(folder) == before}
        data.update(exit_code=result.returncode, findings=findings, host=host)
        data["success"] = bool(data.get("success")) and all(host.values())
        report["phases"].append(data)
        if not data["success"]:
            raise AssertionError("Phase failed: %s %s %s" % (name, json.dumps(host), data.get("error")))

    def upgrade():
        home = make_profile("upgrade", "full")
        env = environment(home)
        earlier = earlier_angelo_folder(home, env)
        earlier.parent.mkdir(parents=True, exist_ok=True)
        blender_folder(home, env).rename(earlier)   # Made by the same fixture, as an Angelo 5.1 install saved it.
        made = json.loads((out / "upgrade/profile.json").read_text())
        (out / "upgrade/profile.json").write_text(json.dumps(dict(made, root=str(earlier))))
        blender_folder(home, env).mkdir(parents=True)   # A Blender folder too: the earlier Angelo still wins.
        (blender_folder(home, env) / "config").mkdir()
        (blender_folder(home, env) / "config/bookmarks.txt").write_text("[Bookmarks]\n")
        phase("upgrade", home, "upgrade")

    groups = set(args.groups.split(","))
    try:
        if "switcher" in groups:
            home = make_profile("switcher", "full")
            phase("card", home, "switcher")
            phase("restart", home, "switcher")
        if "fresh" in groups:
            home = out / "fresh" / "home"
            home.mkdir(parents=True)
            phase("none", home, "fresh")
        if "broken" in groups:
            home = make_profile("broken", "broken")
            phase("broken", home, "broken", allowed_failure=True)
            phase("broken-restart", home, "broken", allowed_failure=True)
        if "menu" in groups:
            home = make_profile("menu", "full")
            phase("menu", home, "menu")
        if "upgrade" in groups:
            upgrade()
        report["success"] = len(report["phases"]) == 7 and all(item["success"] for item in report["phases"])
    finally:
        (out / "settings-import.json").write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps({"success": report["success"], "phases": [(p["phase"], p["success"]) for p in report["phases"]]}))
    if report["success"]:
        print("ANGELO_SETTINGS_IMPORT_OK", flush=True)
    else:
        raise SystemExit(1)


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        run_host()
    raise SystemExit

sys.path.insert(0, str(HERE))
import settings_import_phases  # noqa: E402  (the in-app half: Blender only)

settings_import_phases.start()
