# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Vulkan start that died before its first frame is not tried again: the next launch draws with OpenGL.

Run outside Blender (``run_native_suites.py --gui --include test_blender_gpu_startup_fallback`` does this), on a
machine that can draw with Vulkan and with OpenGL (Windows x64, Linux)::

  python3 tests/python/angelo_agent/test_blender_gpu_startup_fallback.py --binary /abs/Angelo --out /fresh/dir

Blender 5.3 draws with Vulkan by default there. A driver that accepts Vulkan and then crashes while Blender starts
left a person with an app that dies at every launch, before any interface could offer OpenGL (upstream #166510).
``wm_angelo_gpu_startup.cc`` keeps a record in the configuration folder from before the GPU is touched until a frame
is presented, and says in it when start-up scripts run (a death then is theirs). Every launch below is a real window
process in one sandboxed profile (the record's folder), so the launches are one machine's history. No hardware fault
is needed: a launch dies without any exit handling (``os._exit``), or with a real native crash (``native_crash``),
at a chosen moment of its start.

Launches (``PHASES``, in order; the in-app half of each is ``gpu_startup_phases.py``):

1. ``first``: default preferences. Draws with Vulkan; while its script runs the record names this process and says
   scripts run; it is gone after the first frame. Saves the preferences (they say Vulkan) and quits.
2. ``dies-loading``: ends its process while add-ons load (a start-up script). 3. ``dies-script``: ends it from a
   script on the command line. Neither is the GPU's: each leaves a record that says scripts ran, and
4. ``after-scripts`` draws with Vulkan again, watched as in launch 1.
5. ``dies``: ends its process inside its first frame's draw. The record stays, naming that process.
6. ``fallback``: draws with OpenGL, the preference says OpenGL and is unsaved, the record says the start failed and
   the log says so. A screenshot holds Blender's "Failed to load using Vulkan" alert for a person to look at.
   Quits; the usual save on exit keeps the preference.
7. ``stays``: draws with OpenGL from the saved preference, with no word of a fallback. Then a preference file that
   says Vulkan is read inside the session (as importing settings from an earlier version does): OpenGL stays.
8. ``command-line``: ``--gpu-backend vulkan`` is never overridden: draws with Vulkan, the record untouched.
9. ``imported``: the host puts the preference file of launch 1 back before the launch. A file is not the person's
   choice: OpenGL again, quietly (the alert is for the launch that learned of the failure). Then the person
   chooses Vulkan in Preferences (the property is set): the record is gone at once. Saves and quits.
10. ``again``: draws with Vulkan, watched as in launch 1.
11. ``script-exit``: a script that ends Blender with a failing status leaves no record.
12. ``held``, with ``beside`` and ``beside-drawing``: launches at once. ``held`` waits while its script runs, and
    ``beside`` goes on with Vulkan at once, unwatched, leaving the record alone; then ``held`` waits inside its first
    frame's draw, and ``beside-drawing`` waits a bounded time for it before it does the same.
13. ``crashes`` and ``after-crash``: a real native crash inside the first frame's draw. Its crash handler marks the
    record and lets go of it before the Windows crash dialog, which keeps the process alive; ``after-crash`` starts
    while it may still be, and draws with OpenGL. The crash leaves Blender's crash log: exactly one, which the
    suite then keeps beside its receipts under a name the suite runner does not read as a crash of the run.
14. ``crashes-script`` and ``after-script-crash``: a native crash on the main thread while the start-up script
    runs is the script's. Its crash handler empties the record, and the next launch sets out with Vulkan.
15. ``crashes-thread`` and ``after-thread-crash``: the same crash on another thread counts (Vulkan's submission
    thread and the driver's own threads work on while scripts run): the record says crashed, and the next launch
    draws with OpenGL and says so.
16. ``held-failed``: the record says failed and another program holds its file, so the launch cannot take it. It
    waits its bounded time, reads the record, draws with OpenGL and writes nothing.

Every in-app phase also says which process it ran in: it must be the one the host started (a relaunch through
``suite_isolation`` would put the record in a parent the checks never see; the host marks its sandbox as the
isolated profile so that none happens).

Receipts: ``gpu-startup-fallback.json`` and one ``<phase>.json`` per in-app phase. No provider, account or network.
``--smoke`` runs every launch on a machine without the two backends (macOS), where nothing is watched: it shows that
the launches, receipts and exit statuses work there, and its receipt never passes.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import argparse
import contextlib
import json
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORD = "angelo-gpu-startup.txt"
RECEIPT = "gpu-startup-fallback.json"
PHASES = ("first", "dies-loading", "dies-script", "after-scripts", "dies", "fallback", "stays", "command-line",
          "imported", "again", "script-exit", "beside", "beside-drawing", "held", "after-crash", "crashes",
          "after-script-crash", "crashes-script", "after-thread-crash", "crashes-thread", "held-failed")
PHASE_SECONDS = 180
# The launch's own log lines (wm_angelo_gpu_startup.cc).
ANNOUNCED = "The last start with Vulkan ended before its first frame"   # With the alert: it learned of the failure.
QUIET = "Vulkan did not start here earlier: using OpenGL"               # A later launch: OpenGL, no alert.
LATER_READ = "the preferences read now keep OpenGL"                     # A file read inside a session.
NOT_WATCHED = "Another launch is still starting with Vulkan"
NOT_TAKEN = "The start-up record could not be taken"                    # Held or not openable, but it says failed.
DIE_LOADING = '''import os
if os.environ.get("ANGELO_QA_GPU_DIE_LOADING") == "1":
    os._exit(5)   # While add-ons and start-up scripts load.


def register():
    pass


def unregister():
    pass
'''


@contextlib.contextmanager
def held_by_another_program(path):
    """Hold ``path`` as a program other than Angelo would: open for writing on Windows (Angelo's own open then
    meets a sharing violation), an exclusive ``flock`` elsewhere."""
    if sys.platform == "win32":
        with open(path, "r+b"):
            yield
    else:
        import fcntl
        descriptor = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def run_host():
    sys.path.insert(0, str(HERE))
    import crash_dialog
    import gui_launch
    import native_crash
    import native_suite_checks
    import provider_isolation
    import qa_waits
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--smoke", action="store_true",
                        help="on a machine without both backends: run every launch anyway, to see that the "
                             "launches, receipts and exit statuses work there; the receipt never passes")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh disposable directory")
    binary = Path(args.binary).resolve(strict=True)
    home, user = out / "home", out / "blender-user"
    config = user / "config"
    record, preferences = config / RECORD, config / "userpref.blend"
    limit = PHASE_SECONDS * qa_waits.timeout_scale()
    report = {"success": False, "phases": [], "binary": str(binary), "checks": {}, "smoke": args.smoke}
    checks = report["checks"]

    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_"))}
    for folder in (home / ".config", home / ".cache", home / ".local/share", home / ".local/state",
                   home / "AppData/Roaming", home / "AppData/Local", out / "tmp", config,
                   user / "scripts/startup", user / "datafiles", user / "extensions"):
        folder.mkdir(parents=True, exist_ok=True)
    (user / "scripts/startup/qa_gpu_startup_die.py").write_text(DIE_LOADING)
    env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), USERPROFILE=str(home), TMPDIR=str(out / "tmp"),
               TMP=str(out / "tmp"), TEMP=str(out / "tmp"), APPDATA=str(home / "AppData/Roaming"),
               LOCALAPPDATA=str(home / "AppData/Local"),
               XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
               XDG_DATA_HOME=str(home / ".local/share"), XDG_STATE_HOME=str(home / ".local/state"),
               BLENDER_USER_RESOURCES=str(user), BLENDER_USER_CONFIG=str(config),
               BLENDER_USER_SCRIPTS=str(user / "scripts"), BLENDER_USER_DATAFILES=str(user / "datafiles"),
               BLENDER_USER_EXTENSIONS=str(user / "extensions"),
               ANGELO_QA_DIR=str(out), ANGELO_STATE_DIR=str(out / "state"),
               ANGELO_CONNECTIONS_DIR=str(out / "connections"), ANGELO_LIBRARY_DIR=str(out / "library"),
               ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_DISABLE_HERO="1",
               ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_AGENT_RUNTIME="off")
    # This sandboxed home is the launches' isolated profile (suite_isolation): marked as one, a launched app keeps
    # it. Unmarked, the in-app half would start Angelo again as a child and wait for it, and the record would be the
    # waiting parent's.
    provider_isolation.prepare(home)
    env = provider_isolation.isolated_environment(env, home, keep_home=True)

    def read_record():
        try:
            return record.read_text().strip() or None   # An emptied record is no record.
        except OSError:
            return None

    def command(*extra):
        return [str(binary), "--disable-autoexec", *gui_launch.window_flags(), "--window-geometry", "60", "60",
                "1280", "800", "--offline-mode", *extra]

    def in_app(name, *extra):
        return command(*extra, "--python-exit-code", "1", "--python", str(Path(__file__).resolve()), "--",
                       "--native-phase", name, "--fixture-root", str(out))

    def start(name, arguments, **more):
        log = (out / (name + ".out")).open("w")
        return subprocess.Popen(arguments, env=dict(env, **more), stdout=log, stderr=subprocess.STDOUT), log

    def finish(name, process, log, expect_exit=0, scan=True, in_app_phase=True, crashed=False):
        dialog = None
        if crashed:   # On Windows the crash dialog keeps the process until it is closed, as a person closes it.
            try:
                dialog = crash_dialog.wait(process, limit)[1]
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
        else:
            deadline = time.monotonic() + limit
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            timed_out = process.poll() is None
            if timed_out:
                process.kill()
                process.wait()
        log.close()
        receipt = out / (name + ".json")
        data = json.loads(receipt.read_text()) if receipt.is_file() else {"phase": name}
        findings = native_suite_checks.scan_log((out / (name + ".out")).read_text(errors="replace")) if scan else []
        data.update(phase=name, pid=process.pid, exit_code=process.returncode, timed_out=timed_out,
                    findings=findings, record_after=read_record(), crash_dialog=dialog)
        if crashed:
            checks[name + ": ended by its crash"] = process.returncode not in (0, None) and not timed_out
        else:
            checks[name + ": exit status %d" % expect_exit] = process.returncode == expect_exit and not timed_out
            checks[name + ": no failure in the output"] = not findings
        if in_app_phase:
            checks[name + ": ran in the process the host started"] = data.get("app_pid") == process.pid
        report["phases"].append(data)
        print("phase", name, json.dumps({key: data.get(key) for key in (
            "exit_code", "backend", "preference", "record_after", "failed_checks", "error")}), flush=True)
        return data

    def launch(name, arguments, **options):
        more = options.pop("env", {})
        return finish(name, *start(name, arguments, **more), **options)

    def said(name, line):
        return line in (out / (name + ".out")).read_text(errors="replace")

    def passed(name, data):
        checks[name + ": every check inside the app"] = bool(data.get("success"))

    def no_crash_log_before(name):
        checks[name + ": no crash log was there before it"] = not native_suite_checks.find_crash_logs(out)

    def keep_crash_log(name):
        # The crash left Blender's crash log, as a person's would. Checked, then kept beside the receipts under a
        # name the suite runner does not read as a crash of this run.
        kept = native_crash.keep_crash_logs(out, out, name)
        report.setdefault("crash_logs_kept", []).extend(kept)
        checks[name + ": its crash left one crash log, Blender's"] = len(kept) == 1

    def wait_for(what, condition, process=None):
        deadline = time.monotonic() + limit
        while not condition() and time.monotonic() < deadline and (process is None or process.poll() is None):
            time.sleep(0.05)
        checks[what] = bool(condition())

    try:
        data = launch("first", in_app("first"))
        passed("first", data)
        if data.get("backend") != "VULKAN" and not args.smoke:
            raise RuntimeError("this machine does not start Angelo with Vulkan (%r): nothing to fall back from"
                               % data.get("backend"))
        checks["first: the record is gone once the launch has drawn and quit"] = data["record_after"] is None
        shutil.copy2(preferences, out / "userpref-says-vulkan.blend")

        # ``os._exit`` ends the process with no exit handling at all, as a kill does; the status is the launch's own.
        data = launch("dies-loading", command(), expect_exit=5, scan=False, in_app_phase=False,
                      env={"ANGELO_QA_GPU_DIE_LOADING": "1"})
        checks["dies-loading: its record says scripts ran"] = (
            data["record_after"] == "vulkan starting %d scripts" % data["pid"])
        data = launch("dies-script", command("--python-expr", "import os; os._exit(6)"), expect_exit=6, scan=False,
                      in_app_phase=False)
        checks["dies-script: it set out with Vulkan again, and its record says scripts ran"] = (
            data["record_after"] == "vulkan starting %d scripts" % data["pid"])
        data = launch("after-scripts", in_app("after-scripts"))
        passed("after-scripts", data)
        checks["after-scripts: no fallback after deaths that were the scripts'"] = not (
            said("after-scripts", ANNOUNCED) or said("after-scripts", QUIET))
        checks["after-scripts: no record is left"] = data["record_after"] is None

        data = launch("dies", in_app("dies"), expect_exit=3, scan=False)
        passed("dies", data)
        checks["dies: its record stays and names the process that died in its first frame"] = (
            data["record_after"] == "vulkan starting %d" % data["pid"])

        data = launch("fallback", in_app("fallback"))
        passed("fallback", data)
        checks["fallback: the record says the start failed"] = data["record_after"] == "vulkan failed"
        checks["fallback: its log says why it draws with OpenGL"] = said("fallback", ANNOUNCED)

        data = launch("stays", in_app("stays"))
        passed("stays", data)
        checks["stays: the record is kept"] = data["record_after"] == "vulkan failed"
        checks["stays: OpenGL came from the saved preference, not from falling back again"] = not (
            said("stays", ANNOUNCED) or said("stays", QUIET))
        checks["stays: the file read inside the session was turned to OpenGL"] = said("stays", LATER_READ)

        data = launch("command-line", in_app("command-line", "--gpu-backend", "vulkan"))
        passed("command-line", data)
        checks["command-line: the record is untouched"] = data["record_after"] == "vulkan failed"

        shutil.copy2(out / "userpref-says-vulkan.blend", preferences)   # A file that says Vulkan arrives.
        data = launch("imported", in_app("imported"))
        passed("imported", data)
        checks["imported: OpenGL again, without telling the person again"] = (
            said("imported", QUIET) and not said("imported", ANNOUNCED))
        checks["imported: choosing Vulkan removed the record"] = data["record_after"] is None

        data = launch("again", in_app("again"))
        passed("again", data)
        checks["again: no record is left"] = data["record_after"] is None

        data = launch("script-exit", command("--python-expr", "import sys; sys.exit(7)"), expect_exit=7, scan=False,
                      in_app_phase=False)
        checks["script-exit: a script's own failing exit leaves no record"] = data["record_after"] is None

        # Launches at once. ``held`` waits while its script runs, then inside its first frame's draw.
        held, held_log = start("held", in_app("held"))
        wait_for("held: it is waiting while its script runs", lambda: (out / "held-waiting").is_file(), held)
        held_record = read_record()
        checks["held: its record names it and says scripts run"] = (
            held_record == "vulkan starting %d scripts" % held.pid)
        data = launch("beside", in_app("beside"))
        passed("beside", data)
        checks["beside: the other launch's record is left alone"] = data["record_after"] == held_record
        checks["beside: its log says it was not watched"] = said("beside", NOT_WATCHED)
        (out / "release-held").write_text("go")
        wait_for("held: it is waiting inside its first frame's draw", lambda: (out / "held-drawing").is_file(), held)
        held_record = read_record()
        checks["held: its record no longer says scripts run"] = held_record == "vulkan starting %d" % held.pid
        data = launch("beside-drawing", in_app("beside-drawing"))
        passed("beside-drawing", data)
        checks["beside-drawing: the other launch's record is left alone"] = data["record_after"] == held_record
        checks["beside-drawing: its log says it was not watched"] = said("beside-drawing", NOT_WATCHED)
        (out / "release-drawing").write_text("go")
        data = finish("held", held, held_log)
        passed("held", data)
        checks["held: its record is gone once it has drawn and quit"] = data["record_after"] is None

        # A real native crash inside the first frame's draw. Its handler marks the record and lets go of it
        # before the Windows crash dialog, so the launch made meanwhile already knows.
        no_crash_log_before("crashes")
        crashes, crashes_log = start("crashes", in_app("crashes"))
        wait_for("crashes: its crash handler marked the record or the process ended", lambda: (
            read_record() == "vulkan crashed" or crashes.poll() is not None))
        checks["crashes: the record says the start crashed"] = read_record() == "vulkan crashed"
        report["crashed_process_alive_at_the_next_launch"] = crashes.poll() is None
        data = launch("after-crash", in_app("after-crash"))
        passed("after-crash", data)
        checks["after-crash: the record says the start failed"] = data["record_after"] == "vulkan failed"
        checks["after-crash: its log says why it draws with OpenGL"] = said("after-crash", ANNOUNCED)
        finish("crashes", crashes, crashes_log, scan=False, crashed=True)
        keep_crash_log("crashes")

        # A native crash while the start-up script runs, on the main thread: the script's. Its handler empties the
        # record before the crash dialog, and the next launch sets out with Vulkan.
        record.unlink(missing_ok=True)
        shutil.copy2(out / "userpref-says-vulkan.blend", preferences)
        no_crash_log_before("crashes-script")
        crashes, crashes_log = start("crashes-script", in_app("crashes-script"))
        wait_for("crashes-script: it crashed in its script and its crash handler emptied the record", lambda: (
            (out / "crashes-script.json").is_file() and read_record() is None), crashes)
        data = launch("after-script-crash", in_app("after-script-crash"))
        passed("after-script-crash", data)
        checks["after-script-crash: no fallback after a crash that was the script's"] = not (
            said("after-script-crash", ANNOUNCED) or said("after-script-crash", QUIET))
        checks["after-script-crash: no record is left"] = data["record_after"] is None
        data = finish("crashes-script", crashes, crashes_log, scan=False, crashed=True)
        passed("crashes-script", data)
        keep_crash_log("crashes-script")

        # The same crash on another thread while the script runs: it may be the GPU's, so it counts.
        no_crash_log_before("crashes-thread")
        crashes, crashes_log = start("crashes-thread", in_app("crashes-thread"))
        wait_for("crashes-thread: its crash handler marked the record or the process ended", lambda: (
            read_record() == "vulkan crashed" or crashes.poll() is not None))
        checks["crashes-thread: a crash on another thread while the script ran counts"] = (
            read_record() == "vulkan crashed")
        data = launch("after-thread-crash", in_app("after-thread-crash"))
        passed("after-thread-crash", data)
        checks["after-thread-crash: the record says the start failed"] = data["record_after"] == "vulkan failed"
        checks["after-thread-crash: its log says why it draws with OpenGL"] = said("after-thread-crash", ANNOUNCED)
        data = finish("crashes-thread", crashes, crashes_log, scan=False, crashed=True)
        passed("crashes-thread", data)
        keep_crash_log("crashes-thread")

        # The record says failed and another program holds its file: read, not taken, and still believed.
        shutil.copy2(out / "userpref-says-vulkan.blend", preferences)   # The preferences ask for Vulkan again.
        # The record of a failed start, byte for byte as Angelo writes it, whatever the launches before left: this
        # case stands on its own. (After after-thread-crash it already says this; where nothing is watched, or in
        # a build that discards that crash, there is no record at all.)
        record.write_bytes(b"vulkan failed\n")
        with held_by_another_program(record):
            data = launch("held-failed", in_app("held-failed"))
        passed("held-failed", data)
        checks["held-failed: the record it could not take is untouched"] = data["record_after"] == "vulkan failed"
        checks["held-failed: OpenGL, without telling the person again"] = (
            said("held-failed", QUIET) and not said("held-failed", ANNOUNCED))
        checks["held-failed: its log says the record could not be taken"] = said("held-failed", NOT_TAKEN)
        report["success"] = not args.smoke and [p["phase"] for p in report["phases"]] == list(PHASES) and all(
            checks.values())
    except Exception as error:   # The receipt carries the failure; the runner reads it.
        report["error"] = repr(error)
    finally:
        report["failed_checks"] = sorted(name for name, ok in checks.items() if not ok)
        (out / RECEIPT).write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps({"success": report["success"], "failed_checks": report["failed_checks"],
                          "error": report.get("error")}))
    if report["success"]:
        print("ANGELO_GPU_STARTUP_FALLBACK_OK", flush=True)
    else:
        raise SystemExit(1)


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        run_host()
    raise SystemExit

sys.path.insert(0, str(HERE))
import gpu_startup_phases  # noqa: E402  (the in-app half: Blender only)

gpu_startup_phases.start()
