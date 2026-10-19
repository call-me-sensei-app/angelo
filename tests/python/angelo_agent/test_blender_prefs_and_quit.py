# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading preferences again keeps Angelo's agent, and a quit is never held by the welcome screen or a card.

Run outside Blender (``run_native_suites.py --gui --include test_blender_prefs_and_quit`` does this)::

  python3 tests/python/angelo_agent/test_blender_prefs_and_quit.py --binary /abs/Angelo --out /fresh/dir

Every launch is the real default start-up (Director and its chat) in a sandbox: HOME, CFFIXED_USER_HOME, XDG, TMPDIR,
Blender's user folders and Angelo's state, connections and library all inside ``--out``, the stand-in ``claude`` CLI
(``fake_provider_cli``) first on PATH and memory-only keys. No account, network or inference. Phases (``--native-phase``,
one window process each, ``prefs_quit_phases.py``):

1. ``prefs``: a Claude connection made from the chat answers a message. Load Factory Preferences (the operator File >
   Defaults and Preferences > Save & Load run): Angelo stays loaded and listed with the same chats, its own settings
   are back to their defaults and Blender's too, the connection's record and file are unchanged and it is still
   ready, and the chat answers the next message without a restart. Then Revert to Saved Preferences (the saved
   values, Angelo's own included), an application template and back, and Load Factory Settings keep it the same way.
   A person who disabled Angelo stays disabled through a later read.
2. ``quit-open``: the welcome screen open over the 3D view takes its own hover, Tab, a demo tile's click and Escape;
   reopened with the pointer resting on it, a script's quit completes.
3. ``quit-race``: a script asks to quit and the welcome opens at once over the resting pointer (in front of Blender's
   quit handler, as a timer can open it): the quit completes.
4. ``quit-card``: the same with a modal card (About Angelo).

The host checks each process ends with status 0 within ``QUIT_SECONDS`` of the moment its receipt says it asked to
quit, and that no output holds a traceback or a native failure. Receipts: ``prefs-and-quit.json`` and one
``<phase>.json`` per phase, with the PNGs beside them.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PHASES = ("prefs", "quit-open", "quit-race", "quit-card")
QUIT_SECONDS = 5.0     # From the quit request to the process's end.
PHASE_SECONDS = int(os.environ.get("PREFS_QUIT_PHASE_SECONDS", "300"))


def run_host():
    sys.path.insert(0, str(HERE))
    import fake_provider_cli
    import gui_launch
    import native_suite_checks
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--phases", default=",".join(PHASES), help="comma list (default: all; the receipt fails unless all ran)")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh disposable directory")
    out.mkdir(parents=True, exist_ok=True)
    binary = Path(args.binary).resolve(strict=True)
    fake_bin = fake_provider_cli.install(out / "fake-cli-bin")
    report = {"success": False, "phases": [], "binary": str(binary), "paid_requests": 0}

    def environment(name):
        base = out / name
        home, user = base / "home", base / "blender-user"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_"))}
        for folder in (home / ".config", home / ".cache", home / ".local/share", home / ".local/state", base / "tmp",
                       user / "config", user / "scripts", user / "datafiles", user / "extensions", out / "rounds"):
            folder.mkdir(parents=True, exist_ok=True)
        fake_provider_cli.sign_in(home, "claude")   # An everyday Claude Code sign-in that already exists.
        env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), TMPDIR=str(base / "tmp"),
                   XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
                   XDG_DATA_HOME=str(home / ".local/share"), XDG_STATE_HOME=str(home / ".local/state"),
                   BLENDER_USER_RESOURCES=str(user), BLENDER_USER_CONFIG=str(user / "config"),
                   BLENDER_USER_SCRIPTS=str(user / "scripts"), BLENDER_USER_DATAFILES=str(user / "datafiles"),
                   BLENDER_USER_EXTENSIONS=str(user / "extensions"),
                   ANGELO_QA_DIR=str(out), ANGELO_STATE_DIR=str(base / "state"),
                   ANGELO_CONNECTIONS_DIR=str(base / "connections"), ANGELO_LIBRARY_DIR=str(base / "library"),
                   ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_DISABLE_HERO="1",
                   ANGELO_AGENT_RUNTIME="off", ANGELO_FAKE_CLI_STATE=str(base / "fake-cli"),
                   ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5", ANGELO_FAKE_CLI_ROUNDS=str(out / "rounds"),
                   PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
        return env

    def phase(name):
        command = [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags(),
                   "--window-geometry", "60", "60", "1440", "900", "--offline-mode", "--python-exit-code", "1",
                   "--python", str(Path(__file__).resolve()), "--", "--native-phase", name, "--fixture-root", str(out)]
        output = out / (name + ".out")
        with output.open("w") as log:
            process = subprocess.Popen(command, env=environment(name), stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + PHASE_SECONDS
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            ended = time.time()
            if process.poll() is None:
                process.kill()
                process.wait()
        receipt = out / (name + ".json")
        data = json.loads(receipt.read_text()) if receipt.is_file() else {"phase": name, "error": "no receipt"}
        findings = native_suite_checks.scan_log(output.read_text(errors="replace"))
        requested = data.get("quit_requested")
        quit_seconds = round(ended - requested, 2) if requested else None
        host = {"exit_code_zero": process.returncode == 0, "no_failures_in_output": not findings,
                "quit_completed_in_time": quit_seconds is not None and 0 <= quit_seconds <= QUIT_SECONDS}
        data.update(exit_code=process.returncode, findings=findings, host=host, quit_seconds=quit_seconds)
        data["success"] = bool(data.get("success")) and all(host.values())
        report["phases"].append(data)
        print("phase", name, json.dumps({"success": data["success"], "quit_seconds": quit_seconds, "host": host,
                                         "failed_checks": data.get("failed_checks"), "error": data.get("error")}),
              flush=True)

    wanted = args.phases.split(",")
    try:
        for name in wanted:
            phase(name)
        report["success"] = ([p["phase"] for p in report["phases"]] == list(PHASES)
                             and all(item["success"] for item in report["phases"]))
    finally:
        (out / "prefs-and-quit.json").write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps({"success": report["success"], "phases": [(p["phase"], p["success"]) for p in report["phases"]]}))
    if report["success"]:
        print("ANGELO_PREFS_AND_QUIT_OK", flush=True)
    else:
        raise SystemExit(1)


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        run_host()
    raise SystemExit

sys.path.insert(0, str(HERE))
import prefs_quit_phases  # noqa: E402  (the in-app half: Blender only)

prefs_quit_phases.start()
