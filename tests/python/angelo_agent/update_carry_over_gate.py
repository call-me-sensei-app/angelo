# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Release gate: updating from an earlier Angelo to the candidate keeps a person's state (``version_carry_over``).

Run with host Python 3.9+ (``doc/angelo/releasing.rst``, "Update carry-over gate")::

  python3 tests/python/angelo_agent/update_carry_over_gate.py --from-app <Angelo 1.0.8 binary> \\
      --to-app <candidate binary> --out <fresh dir> [--connections-from <signed-in account folder>]

1. **Seed** (the earlier app, ``--background``, ``update_carry_over_seed.py``): its own code writes three chats with
   briefs and native contexts, an attachment, a deduplicated snapshot with a file journal, a saved scene and changed
   preferences. ``--connections-from`` copies the saved coding accounts' metadata (never a credential) from a
   signed-in account folder, each isolated native profile named by its absolute path, as a person's 1.0 install
   names it (``recover_connections.py``), and recorded in the account store's retained-profile list, as every save
   of that install records it (``ConnectionStore.save``, since 1.0.6): Angelo starts a runtime only with a profile in
   its own folders, and that list is what makes such a profile one of them (``private_roots``).
2. **Update** (the candidate, a window): the state moved, every chat, brief, attachment, snapshot and preference is
   here and works, no setup card or Quick Setup, one notice (captured); each account's sign-in command resolved.
3. **Restart** (the candidate again): nothing moves twice, no notice, everything still here.
4. **Go back** (the earlier app, ``--background``): it opens with its own preferences unchanged and starts its own
   chats (the documented design: the chats moved to the newer version).
5. **Signed in** (the host, with the person's own home, where the native CLIs keep their sign-ins): each resolved
   status command reports the account signed in. ``--skip-sign-in`` leaves it out (and the receipt says so).

The account folder and the earlier version's preferences are fingerprinted before and after: they never change.

``--crashing-addon`` (doc/angelo/app_identity.rst, "A start that did not finish") seeds a third-party add-on that the
earlier version enabled and that crashes natively as it loads in the candidate (Blender 5.3 #166395, #166397). The
update's first launch must then end in that crash (the settings import enables it), the next one (``safe``) must open
with it left off and one notice naming it, and the one after (``safe_restart``) must open normally with it still off
and no notice. These replace the update and restart phases. On Windows the crash shows Angelo's crash dialog and
waits; the gate records it, presses its Close button as a person would (``crash_dialog``) and requires it to have
said Angelo stopped and named the crash log, and the exit that follows (11). Nothing suppresses the dialog.
``--launch-while-dialog-open`` (Windows) runs the ``safe`` launch while that dialog is still open, before Close, as a
person starting Angelo again from its icon does: the crashed process still holds its start record, which its
crash handler marked crashed, so that launch must open safely all the same.

Isolation (``--profile``): ``sandbox`` (macOS, Linux) points HOME, CFFIXED_USER_HOME and the XDG folders at
``--out`` and sets no ANGELO_*_DIR or BLENDER_USER_* variable, so both apps use the per-version folders a person's
install does. ``account`` uses this account's own folders (a Windows VM, whose known folders cannot be redirected):
it refuses to start when an Angelo user or data folder exists and moves both into ``--out`` at the end (never
deletes). ``--as-older-version 5.1`` seeds into that version's folder (``BLENDER_USER_RESOURCES`` for the earlier app
only), to prove the mechanism when both apps share a Blender base. Without it, two apps on one base (a patch release)
are gated as that update is: nothing carried over, no notice, the state used in place, and the earlier app listing the
same chats (``same_base`` in the receipt; ``update_carry_over_phases.phase_update_same_base``). Receipt:
``<out>/update-gate.json``.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/modules"))
import angelo_identity  # noqa: E402
import update_carry_over_contract as contract  # noqa: E402
import update_carry_over_prefs  # noqa: E402
import crash_dialog  # noqa: E402
import fake_provider_cli  # noqa: E402
import gui_launch  # noqa: E402
import native_suite_checks  # noqa: E402
from angelo_agent import authentication, private_roots, storage  # noqa: E402

CODING = ("claude_subscription", "codex_subscription", "codex_api")
LAUNCH_TIMEOUT = 300   # Seconds per app launch (each phase quits itself; this is the hard cap).


def product_folder(env, platform=sys.platform):
    """Angelo's folder of per-version folders for ``env`` (``app_identity.rst``, "Folders")."""
    names = angelo_identity.data()["paths"]
    home = Path(env.get("CFFIXED_USER_HOME") or env.get("HOME") or Path.home())
    if platform == "darwin":
        return home / "Library/Application Support" / names["user_dir_name"]
    if platform == "win32":
        return Path(env.get("APPDATA") or home / "AppData/Roaming") / names["user_dir_name"]
    return Path(env.get("XDG_CONFIG_HOME") or home / ".config") / names["user_dir_name_unix"]


def environment(profile, home, fake_bin):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_"))}
    if profile == "sandbox":
        for folder in (home / ".config", home / ".cache", home / ".local/share", home / ".local/state",
                       home / "AppData/Roaming", home / "AppData/Local", home.parent / "tmp"):
            folder.mkdir(parents=True, exist_ok=True)
        env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home.parent / "tmp"),
                   APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"),
                   XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
                   XDG_DATA_HOME=str(home / ".local/share"), XDG_STATE_HOME=str(home / ".local/state"))
    # Stand-in CLIs first: no app phase starts a real provider; the host's sign-in step runs the real ones.
    env.update(ANGELO_DISABLE_KEYCHAIN="1", ANGELO_AGENT_RUNTIME="off", ANGELO_DISABLE_HERO="1",
               ANGELO_FAKE_CLI_STATE=str(home.parent / "fake-cli"),
               PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
    return env


def seed_connections(source, env):
    """The saved coding accounts of ``source`` (metadata only) in the run's account folder; returns them."""
    raw = json.loads((Path(source) / "connections.json").read_text(encoding="utf-8"))
    records = []
    for record in raw.get("connections", []):
        if record.get("mode") not in CODING:
            continue
        record = dict(record)
        if not record.get("reuse_native") and not record.get("native_profile"):
            family = "claude-profile" if record["mode"].startswith("claude_") else "codex-profile"
            profile = Path(source).absolute() / family / record["id"]
            if profile.is_dir():
                record["native_profile"] = str(profile)
        records.append(record)
    folder = storage.connection_directory(env, sys.platform, env.get("HOME"))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / "connections.json").write_text(json.dumps({"version": 3, "active": records[0]["id"] if records else None,
                                                         "connections": records, "ignored_native_modes": []}))
    # What the store's own save writes beside the file (connections.ConnectionStore.save): each named profile in the
    # retained-profile list. Without it the seed is a store no Angelo wrote, whose outside profiles the candidate
    # rightly refuses (storage.ProfileOutside: 2.0.0's Windows state receipt, the bench folder outside the profile).
    if not private_roots.remember_profiles(folder, records):
        raise SystemExit("The seeded accounts' native profiles could not be recorded in %s" % folder)
    return folder


VERSION_LINE = re.compile(r"^Blender (\d+)\.(\d+)\.(\d+)", re.M)
BUILD_HASH = re.compile(r"build hash:\s*([0-9a-f]{7,40})")
BUILD_BRANCH = re.compile(r"build branch:[ \t]*([^\r\n]*)")


def app_version(binary, env):
    """What the app itself says it is (``--version``): its Blender version, base (major.minor), build hash and branch
    (" (modified)" when built from a changed tree), and the executable's own SHA-256, which names the very binary
    (a build hash names a commit, and an upstream one when the tree was ahead)."""
    result = subprocess.run([str(binary), "--version"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=120)
    text = result.stdout.decode("utf-8", errors="replace")
    version, built, branch = VERSION_LINE.search(text), BUILD_HASH.search(text), BUILD_BRANCH.search(text)
    return {"blender": ".".join(version.groups()) if version else None,
            "base": "%s.%s" % version.groups()[:2] if version else None,
            "build_hash": built.group(1) if built else None,
            "build_branch": branch.group(1).strip() if branch else None,
            "executable_sha256": digest(binary)}


def same_base(earlier, candidate, as_older_version=None):
    """Whether the update stays on one Blender base, from both apps' own versions: then nothing is carried over by
    design and the gate checks that update's contract. ``--as-older-version`` seeds an earlier base's folder: never."""
    if not earlier["base"] or not candidate["base"]:
        raise SystemExit("An app did not say its Blender version (--version): %s, %s" % (earlier, candidate))
    return earlier["base"] == candidate["base"] and not as_older_version


GATE_FILES = ("update_carry_over_gate.py", "update_carry_over_phases.py", "update_carry_over_contract.py",
              "update_carry_over_prefs.py", "update_carry_over_seed.py")


def tools():
    """What produced this receipt: the source commit the runner proved (ANGELO_SOURCE_COMMIT, set by the bench session
    or the driver), each gate file's SHA-256, and a session command's own files (ANGELO_GATE_SESSION_TOOLS)."""
    try:
        session = json.loads(os.environ.get("ANGELO_GATE_SESSION_TOOLS") or "{}")
    except ValueError:
        session = {"unreadable": os.environ.get("ANGELO_GATE_SESSION_TOOLS")}
    return {"source_commit": os.environ.get("ANGELO_SOURCE_COMMIT"),
            "files": {name: digest(HERE / name) for name in GATE_FILES}, "session": session}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def run_app(binary, args, env, log):
    with open(log, "w") as output:
        result = subprocess.run([str(binary), *args], env=env, stdout=output, stderr=subprocess.STDOUT,
                                timeout=LAUNCH_TIMEOUT)
    text = Path(log).read_text(errors="replace")
    return result.returncode, native_suite_checks.scan_log(text)


def run_window(binary, args, env, log, while_open=None):
    """A windowed launch: ``(exit code, findings, crash dialog)``. On Windows a crash waits on Angelo's crash dialog
    until it is closed (``crash_dialog``): it is recorded (``<log>-crash-dialog.png`` beside the log) and closed with
    its Close button, as a person would; ``while_open()`` runs first, while it is still open."""
    log = Path(log)

    def close(window):
        if while_open is not None:
            while_open()
        return crash_dialog.press(window, "Close")
    with open(log, "w") as output:
        process = subprocess.Popen([str(binary), *args], env=env, stdout=output, stderr=subprocess.STDOUT)
        code, dialog = crash_dialog.wait(process, LAUNCH_TIMEOUT, record=lambda window: crash_dialog.record(
            window, log.with_name(log.stem + "-crash-dialog.png")), close=close)
    return code, native_suite_checks.scan_log(log.read_text(errors="replace")), dialog


def dialog_checks(dialog, code, platform=sys.platform):
    """What the crashing launch must have shown and how it must have ended: on Windows Angelo's crash dialog, saying
    it stopped and naming the crash log, closed with its Close button, then exit 11; elsewhere a fault signal's exit
    (no dialog)."""
    if platform != "win32":
        return {"no_crash_dialog_elsewhere": dialog is None}
    shown = " ".join((dialog or {}).get("texts") or [])
    crash_file = angelo_identity.data()["paths"]["crash_file"]
    return {"showed_the_crash_dialog": bool(dialog) and dialog.get("title") == angelo_identity.PRODUCT_NAME
            and "has stopped working" in shown,
            "the_dialog_names_the_crash_log": "crash log can be found" in shown and crash_file.lower() in shown.lower(),
            "closed_with_its_close_button": bool(dialog) and dialog.get("closed_by") in crash_dialog.PRESSED_BY,
            "ended_after_closing_it": code == crash_dialog.CLOSED_EXIT}


def sign_in_status(entry, executables):
    """The host-side status of one resolved account, the command Angelo resolved, run with the person's home:
    (Angelo's own whitelisted status text, the command's exit code). The output itself is never kept (it can name
    the account); the exit code says why a status is "Not connected" (a CLI that refused, or one never signed in)."""
    env = dict(os.environ)
    env.update(entry["profile_env"])
    name = "claude" if entry["mode"].startswith("claude_") else "codex"
    executable = executables.get(name) or shutil.which(name)
    if not executable:
        return "No %s command on this computer" % name, None
    command = [executable] + entry["command"][1:]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
    return authentication.metadata_status(entry["mode"], result.returncode, result.stdout + result.stderr), result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from-app", required=True, help="the earlier Angelo's executable (1.0.8)")
    parser.add_argument("--to-app", required=True, help="the candidate's executable")
    parser.add_argument("--out", required=True, help="a fresh disposable directory")
    parser.add_argument("--connections-from", help="a signed-in account folder whose coding accounts to seed")
    parser.add_argument("--profile", choices=("sandbox", "account"), default="sandbox" if sys.platform != "win32"
                        else "account")
    parser.add_argument("--as-older-version", help="seed into this earlier version's folder (both apps share a base)")
    parser.add_argument("--skip-sign-in", action="store_true")
    parser.add_argument("--crashing-addon", action="store_true",
                        help="seed an add-on that crashes natively on load in the candidate (the safe-start case)")
    parser.add_argument("--launch-while-dialog-open", action="store_true",
                        help="Windows, with --crashing-addon: the safe launch starts while the crash dialog is still "
                             "open, as a person starting Angelo again from its icon does")
    parser.add_argument("--claude", help="the claude command for the sign-in step (default: on PATH)")
    parser.add_argument("--codex", help="the codex command for the sign-in step (default: on PATH)")
    args = parser.parse_args()
    if args.launch_while_dialog_open and (sys.platform != "win32" or not args.crashing_addon):
        parser.error("--launch-while-dialog-open needs Windows (its crash dialog) and --crashing-addon")
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("Use a fresh disposable directory")
    out.mkdir(parents=True, exist_ok=True)
    fixture = out / "fixture"
    fixture.mkdir()
    home = out / "home"
    home.mkdir()
    env = environment(args.profile, home, fake_provider_cli.install(out / "fake-cli-bin"))
    products = [product_folder(env), storage.data_directory(env, sys.platform, env.get("HOME"))]
    if args.profile == "account" and any(path.exists() for path in products):
        raise SystemExit("This account already has Angelo folders (%s); run the gate on an account without them"
                         % ", ".join(str(p) for p in products if p.exists()))
    report = {"success": False, "from_app": args.from_app, "to_app": args.to_app, "profile": args.profile,
              "as_older_version": args.as_older_version, "platform": sys.platform, "checks": {}, "phases": {}}

    def check(name, condition):
        report["checks"][name] = bool(condition)

    try:
        report["apps"] = {"from": app_version(args.from_app, env), "to": app_version(args.to_app, env)}
        report["same_base"] = same_base(report["apps"]["from"], report["apps"]["to"], args.as_older_version)
        accounts = seed_connections(args.connections_from, env) if args.connections_from else None
        before_accounts = digest(accounts / "connections.json") if accounts else None
        earlier_env = dict(env, ANGELO_QA_DIR=str(fixture))
        if args.as_older_version:
            earlier_env["BLENDER_USER_RESOURCES"] = str(product_folder(env) / args.as_older_version)
        # The seed imports the earlier add-on's stores itself; its start-up (and its own migrations) stay off.
        seed_env = dict(earlier_env, ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_INTRO="1")
        code, findings = run_app(args.from_app, ["--background", "--python",
                                                 str(HERE / "update_carry_over_seed.py"), "--", "--receipt",
                                                 str(fixture / "seed.json")]
                                 + (["--crashing-addon"] if args.crashing_addon else []), seed_env, out / "seed.out")
        seed = json.loads((fixture / "seed.json").read_text())
        report["phases"]["seed"] = {"exit": code, "findings": findings, "success": seed["success"],
                                    "error": seed.get("error")}
        if not seed["success"] or code != 0:
            raise AssertionError("The earlier app could not seed its state")
        earlier_prefs = Path(seed["config"]) / "userpref.blend"
        before_prefs = digest(earlier_prefs)
        check("seeded_preferences_saved", before_prefs is not None)
        candidate_env = dict(env, ANGELO_QA_DIR=str(fixture))
        if args.crashing_addon:
            candidate_env["ANGELO_QA_CRASH_ON_LOAD"] = "1"
        phases = ("crash", "safe", "safe_restart") if args.crashing_addon else ("update", "restart")
        early = args.crashing_addon and args.launch_while_dialog_open

        def launch(phase, while_open=None, binary=None, env=None):
            code, findings, dialog = run_window(binary or args.to_app, ["--disable-autoexec", *gui_launch.window_flags(),
                                                   "--window-geometry", "60", "60", "1440", "900", "--offline-mode",
                                                   "--python", str(HERE / "update_carry_over_phases.py"), "--",
                                                   "--native-phase", phase, "--fixture-root", str(fixture),
                                                   "--same-base", "1" if report["same_base"] else "0"],
                                                env or candidate_env, out / (phase + ".out"), while_open=while_open)
            path = fixture / (phase + ".json")
            data = json.loads(path.read_text()) if path.exists() else {}
            report["phases"][phase] = dict(data, exit=code, findings=findings, crash_dialog=dialog)
            if phase == "crash":   # The add-on ends the process as the update's import enables it.
                check("first_launch_ended_in_the_native_crash", code != 0 and not data.get("success"))
                for name, ok in dialog_checks(dialog, code).items():
                    check("first_launch_" + name, ok)
                return
            check(phase + "_passed", data.get("success") and code == 0 and not findings)
        # Both contracts: the earlier app opens in its window once, as the person who used it has (a keymap edit of
        # theirs made there), and its own reading of every preference then is the "before" (a background seed never
        # ran its window's start-up, and has no user keymap items).
        launch("earlier_open", binary=args.from_app, env=earlier_env)
        before_values = report["phases"]["earlier_open"].pop("preference_values", None) or {}
        before_prefs = digest(earlier_prefs)
        report["preference_families"] = update_carry_over_prefs.families(before_values)
        report["preference_unreadable"] = {"before": update_carry_over_prefs.unreadable(before_values)}
        for name, ok in contract.seeded_in_before(before_values).items():
            check(name, ok)
        for phase in phases:
            if phase == "crash" and early:   # The crashed process still holds its record under its open dialog.
                launch("crash", while_open=lambda: launch("safe"))
                check("safe_launched_while_the_crash_dialog_was_open", "safe" in report["phases"])
            elif not (phase == "safe" and early):
                launch(phase)
        rewritten = digest(earlier_prefs) != before_prefs
        report["earlier_preferences_file_rewritten"] = rewritten
        # The candidate's own reading in its windows: every value the person had, as the candidate applies it.
        report["candidate_preferences"] = {}
        for phase in ("update", "restart"):
            if phase in report["phases"]:
                held = report["phases"][phase].pop("preference_values", None) or {}
                report["preference_unreadable"][phase] = update_carry_over_prefs.unreadable(held)
                verdict, details = contract.candidate_preferences(phase, before_values, held, report["same_base"])
                report["candidate_preferences"][phase] = dict(details, compared=len(held))
                for name, ok in verdict.items():
                    check(name, ok)
        if accounts:
            check("account_folder_unchanged", digest(accounts / "connections.json") == before_accounts)
        code, findings = run_app(args.from_app, ["--background", "--python-expr", GO_BACK],
                                 dict(earlier_env, ANGELO_QA_HELPERS=str(HERE)),
                                 out / "back.out")
        back = json.loads((fixture / "back.json").read_text()) if (fixture / "back.json").exists() else {}
        back_values = back.pop("preference_values", None) or {}   # never into the receipt: only keys and counts
        report["phases"]["go_back"] = dict(back, exit=code, findings=findings)
        check("earlier_app_opens_with_its_preferences", code == 0 and not findings and back.get("preferences_kept"))
        for name, ok in contract.go_back(report["same_base"], back, len(seed["chats"])).items():
            check(name, ok)
        after_values = back_values
        if report["same_base"]:   # The earlier app in its window again: the "after", read as the "before" was.
            launch("earlier_reopen", binary=args.from_app, env=earlier_env)
            after_values = report["phases"]["earlier_reopen"].pop("preference_values", None) or {}
            report["preference_unreadable"]["after"] = update_carry_over_prefs.unreadable(after_values)
        report["preference_values_compared"] = len(set(before_values) | set(after_values))
        report["tools"] = tools()
        report["preference_exclusions"] = dict(update_carry_over_prefs.EXCLUDED)
        report["preference_differences"] = update_carry_over_prefs.differences(before_values, after_values)
        for name, ok in contract.preferences_kept(report["same_base"], rewritten, before_values, after_values).items():
            check(name, ok)
        entries = report["phases"].get("update", {}).get("sign_ins", [])
        if args.skip_sign_in:
            report["sign_in"] = "skipped (--skip-sign-in)"
        else:
            check("accounts_seeded_for_the_sign_in_step", bool(entries))
            executables = {"claude": args.claude, "codex": args.codex}
            report["sign_in"] = [dict(entry, **dict(zip(("status", "exit"), sign_in_status(entry, executables))))
                                 for entry in entries]
            for entry in report["sign_in"]:
                check("signed_in_%s_%s" % (entry["mode"], entry["id"][:8]), contract.signed_in(entry["status"]))
        report["success"] = bool(report["checks"]) and all(report["checks"].values())
    except Exception as error:   # Recorded in the receipt; the exit status fails the gate.
        report["error"] = "%s: %s" % (type(error).__name__, error)
    finally:
        report["failed_checks"] = [name for name, ok in report["checks"].items() if not ok]
        if args.profile == "account":
            for path in products:
                if path.exists():
                    shutil.move(str(path), str(out / ("account-" + path.name + "-" + path.parent.name)))
        (out / "update-gate.json").write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps({"success": report["success"], "failed": report["failed_checks"],
                          "error": report.get("error")}))
    if not report["success"]:
        raise SystemExit(1)
    print("ANGELO_UPDATE_GATE_OK", flush=True)


# The go-back launch (the earlier app): its preferences load and its chat list starts without the moved chats.
GO_BACK = """
import bpy, json, os, sys
from pathlib import Path
fixture = Path(os.environ["ANGELO_QA_DIR"])
seed = json.loads((fixture / "seed.json").read_text())
prefs = bpy.context.preferences
kept = all(getattr(getattr(prefs, g), n) == v for g, n, v in seed["preferences"])
sys.path.insert(0, os.environ["ANGELO_QA_HELPERS"])
import update_carry_over_prefs
from angelo_agent import ui
from angelo_agent.sessions import SessionCollection
listed = SessionCollection(Path(ui.state_directory())).records
count = sum(1 for chat in seed["chats"] if chat["id"] in listed)
(fixture / os.environ.get("ANGELO_QA_BACK", "back.json")).write_text(json.dumps({"preferences_kept": kept, "seeded_chats_listed": count,
                                               "preference_values": update_carry_over_prefs.values(prefs, bpy.context.window_manager.keyconfigs.user)}))
"""


if __name__ == "__main__":
    main()
