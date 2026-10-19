# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the native Blender suites against a compiled Angelo binary (stdlib only).

From the repository root, with host Python 3.9 or newer::

    python3 tests/python/angelo_agent/run_native_suites.py --binary /abs/path/to/Angelo
    python3 tests/python/angelo_agent/run_native_suites.py --gui --jobs 2 --include '*sculpt*'
    python3 tests/python/angelo_agent/run_native_suites.py --list

``native_suite_manifest.py`` classifies every suite. BACKGROUND suites run by
default; ``--gui`` adds the window suites. LIVE suites (real providers) run
only with ``--allow-live`` and an exact ``--include`` of their name, and still
need their own opt-in variables from the caller's environment. A suite whose
code depends on other platforms (the manifest's ``platforms``) is skipped with
its reason.

Every suite gets fresh directories for HOME (and Windows' USERPROFILE, APPDATA
and LOCALAPPDATA), TMPDIR/TMP/TEMP (Blender's recovery and crash files),
Blender's user resources, Angelo state/accounts/library and its QA receipts,
and inherits only a small allowlist of the caller's environment, so neither
real profiles nor provider keys reach it. The suites of every run share one
Python bytecode folder, ``PYTHONPYCACHEPREFIX`` = ``<output>/angelo-native-pycache``
(``PYCACHE_FOLDER``): the app never writes bytecode inside itself, and each
fresh HOME would otherwise start every suite with nothing compiled, as a
person's first launch does. ``ANGELO_DISABLE_KEYCHAIN=1``
keeps the app memory-only in every suite, so none reads an OS key store or
waits on its prompt (only the opt-in LIVE Keychain suite calls the macOS
Keychain itself, prompt-free). ``ANGELO_AGENT_RUNTIME=off`` keeps an app that
bundles its agent runtime on the stand-in CLIs; a LIVE suite runs the bundled
runtime, or the caller's ``ANGELO_AGENT_RUNTIME``.
A timed-out suite's whole process tree is killed (its process group on macOS
and Linux, ``taskkill /T`` on Windows). A suite fails on a
nonzero exit, a signal, a timeout, a missing/stale/failed receipt, a missing
completion marker, or native assertion, allocator, crash or Python traceback
output in any of its logs. ``summary.json`` and ``summary.txt`` are written to
the run directory; the exit status is nonzero when any selected suite fails.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(ROOT / "scripts/addons_core"))
import gui_launch
import native_addon_guard as addon_guard
import native_suite_checks as checks
import native_suite_manifest as manifest
import native_suite_paths
from native_suite_summary import human_summary, summarize  # summary.json and summary.txt
import qa_waits
from angelo_agent import product_env  # Standard library only; imports no Blender module.

# The only caller variables a suite inherits. Everything else, including
# provider keys and profile overrides such as CODEX_HOME, is dropped. The
# Windows names (os.environ spells them in upper case there) locate the system,
# installed programs and processor, never a user profile: Windows processes
# need SYSTEMROOT and WINDIR, shells COMSPEC and PATHEXT.
INHERITED_ENV = ("PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TERM", "USER", "LOGNAME", "SHELL",
                 "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
                 "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "OS", "USERNAME",
                 "PROGRAMDATA", "ALLUSERSPROFILE", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
                 "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432",
                 "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "PROCESSOR_IDENTIFIER")
# LIVE suites use the caller's real provider CLI logins, which live under HOME (USERPROFILE on Windows).
LIVE_PROFILE_ENV = ("HOME", "CFFIXED_USER_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH")
# Below the output root, beside the run directories: the bytecode stays warm across runs, as it did when the
# app still wrote it into the build (doc/angelo/releasing.rst, "Nothing is written inside the app").
PYCACHE_FOLDER = "angelo-native-pycache"
_running, _running_lock = set(), threading.Lock()


def default_output_root():
    # Host drivers with output_root (the external CLI and saved-project continuity fixtures) only accept
    # fixtures under the short temp root: /private/tmp on macOS, the temp folder elsewhere.
    return native_suite_paths.short_temp_root()


def required_output_root(suite):
    """The folder a suite's run directory must be inside, resolved for this platform, or None."""
    if suite.output_root == manifest.SHORT_TEMP_ROOT:
        return native_suite_paths.short_temp_root()
    return Path(suite.output_root) if suite.output_root else None


def select_suites(suites, *, gui=False, includes=(), excludes=(), allow_live=False, platform=None):
    """Split the manifest into ``(selected, skipped)``; skipped items carry a reason."""
    platform = sys.platform if platform is None else platform
    selected, skipped = [], []
    for suite in suites:
        names = (suite.name, suite.script)
        exact = any(pattern in names for pattern in includes)
        if includes and not any(fnmatchcase(name, pattern) for name in names for pattern in includes):
            skipped.append((suite, "not included"))
        elif any(fnmatchcase(name, pattern) for name in names for pattern in excludes):
            skipped.append((suite, "excluded"))
        elif not suite.runs_on(platform):
            skipped.append((suite, "not applicable on %s (runs on %s): %s"
                            % (platform, ", ".join(suite.platforms), suite.platform_reason)))
        elif suite.kind == manifest.LIVE and not (allow_live and exact):
            skipped.append((suite, "LIVE: needs --allow-live and an exact --include of its name"))
        elif suite.kind == manifest.GUI and not gui:
            skipped.append((suite, "GUI class not selected (pass --gui)"))
        else:
            selected.append(suite)
    return selected, skipped


def uses_window(suite, gui):
    return suite.needs_window or (gui and suite.gui_superset)


def isolated_paths(directory, state_in_qa=False):
    """Fresh locations for one suite; ``state_in_qa`` nests state, accounts and user resources in QA."""
    qa = directory / "qa"
    base = qa if state_in_qa else directory
    user = base / "blender-user"
    return {"qa": qa, "home": directory / "home", "tmp": directory / "tmp",
            "user": user, "config": user / "config", "scripts": user / "scripts",
            "datafiles": user / "datafiles", "extensions": user / "extensions",
            "state": base / "state", "connections": base / "connections",
            "library": base / "library"}


def build_environment(suite, directory, binary, base_env, log_path, report_path=None, pycache=None):
    """The complete child environment; every writable location is inside ``directory``, except the run's shared
    bytecode folder ``pycache`` (``PYCACHE_FOLDER``)."""
    paths = isolated_paths(directory, suite.state_in_qa)
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    home = paths["home"]
    # Windows' profile folders, inside the private home like HOME itself.
    roaming, local = home / "AppData/Roaming", home / "AppData/Local"
    for path in (roaming, local):
        path.mkdir(parents=True, exist_ok=True)
    env = {key: base_env[key] for key in INHERITED_ENV if key in base_env}
    env.update(
        # Cocoa takes the home folder from CFFIXED_USER_HOME, not HOME: without it a window suite shares the
        # developer's macOS app state, and one crashed suite makes every later launch wait on a modal
        # "reopen windows?" prompt (crash history) until someone dismisses it.
        HOME=str(home), CFFIXED_USER_HOME=str(home), TMPDIR=str(paths["tmp"]), TMP=str(paths["tmp"]),
        TEMP=str(paths["tmp"]), USERPROFILE=str(home), APPDATA=str(roaming), LOCALAPPDATA=str(local),
        XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
        XDG_DATA_HOME=str(home / ".local/share"), XDG_STATE_HOME=str(home / ".local/state"),
        BLENDER_USER_RESOURCES=str(paths["user"]), BLENDER_USER_CONFIG=str(paths["config"]),
        BLENDER_USER_SCRIPTS=str(paths["scripts"]), BLENDER_USER_DATAFILES=str(paths["datafiles"]),
        BLENDER_USER_EXTENSIONS=str(paths["extensions"]),
        ANGELO_QA_DIR=str(paths["qa"]), ANGELO_QA_LOG=str(log_path), ANGELO_RUNTIME_QA_DIR=str(paths["qa"]),
        ANGELO_STATE_DIR=str(paths["state"]), ANGELO_CONNECTIONS_DIR=str(paths["connections"]),
        ANGELO_LIBRARY_DIR=str(paths["library"]), ANGELO_EXPECTED_BINARY=str(binary),
        # The app must never read, write or wait on an OS key store in a suite.
        ANGELO_DISABLE_KEYCHAIN="1",
        # The first-launch cards and welcome would cover every fresh-state window; the suites that prove them
        # (``first_launch``) start without this, so they see the real first frames.
        ANGELO_DISABLE_INTRO="1",
        # So would the not-connected showcase over the 3D view (connect_hero); test_blender_connect_hero turns it on.
        ANGELO_DISABLE_HERO="1",
        # The stand-in CLIs on PATH play Codex: an app that bundles its agent runtime would otherwise run the real
        # one (agent_runtime.py), which reaches the real service. LIVE suites use what ships (below).
        ANGELO_AGENT_RUNTIME="off",
        # Every conversation save reads its journal back and must reproduce the saved state (sessions.py); a
        # row changed in place that no save was told about fails the suite. LIVE suites run as shipped (below).
        ANGELO_JOURNAL_VERIFY="1")
    if pycache is not None:
        env["PYTHONPYCACHEPREFIX"] = str(pycache)
    if not suite.default_chat:
        env["ANGELO_DISABLE_DEFAULT_CHAT"] = "1"
    if suite.first_launch:
        del env["ANGELO_DISABLE_INTRO"]
    if suite.report is not None and suite.report.env:
        env[suite.report.env] = str(report_path)
    env.update({key: base_env[key] for key in suite.options_env if key in base_env})
    if suite.kind == manifest.LIVE:
        env.pop("ANGELO_JOURNAL_VERIFY")
        for key in LIVE_PROFILE_ENV:
            env.pop(key, None)
            if key in base_env:
                env[key] = base_env[key]
        # The bundled agent runtime, or the caller's ANGELO_AGENT_RUNTIME (that spelling only: the variable is
        # newer than the rename) for a build that bundles none.
        env.pop("ANGELO_AGENT_RUNTIME", None)
        if (runtime := base_env.get("ANGELO_AGENT_RUNTIME")) is not None:
            env["ANGELO_AGENT_RUNTIME"] = runtime
        env.update({key: value for key in suite.live_env if (value := caller_value(base_env, key)) is not None})
    return env


def caller_value(base_env, key):
    """A LIVE opt-in value from the caller. A deprecated spelling of an ``ANGELO_`` name
    counts (product_env); the suite always receives it under its ``ANGELO_`` name."""
    if key.startswith(product_env.PREFIX):
        return product_env.get(key[len(product_env.PREFIX):], base_env)
    return base_env.get(key)


def build_command(suite, binary, window, qa_dir):
    script = HERE / suite.script
    if suite.host_launcher:
        values = {"binary": str(binary), "qa": str(qa_dir)}
        # A templated argument is a path ("{qa}/fixture"): written in the platform's own form.
        return [sys.executable, str(script)] + [str(Path(arg.format(**values))) if "{" in arg else arg
                                                for arg in suite.host_args]
    command = [str(binary), "--factory-startup", "--disable-autoexec", "--python-exit-code", "1"]
    if not window:
        command.append("--background")
    else:
        # Window suites open behind the developer's applications and never take focus (gui_launch). Nor do they
        # take the machine's input: with event simulation Blender ignores real keyboard and mouse events (and keeps
        # its own clipboard), so a key meant for another application cannot start playback or change the frame
        # mid-suite, as one once did to test_blender_eval_live_agent_dryrun. Its main loop then
        # does not idle-sleep (one busy core per window suite).
        command += gui_launch.window_flags(input_isolation=suite.event_simulate or not suite.real_input)
    return command + list(suite.extra_args) + ["--python", str(script)]


def plan_suite(suite, run_dir, binary, base_env, gui=False, timeout_scale=1.0):
    directory = run_dir / suite.name
    directory.mkdir(parents=True)
    window = uses_window(suite, gui)
    log_path = directory / "suite.log"
    qa_dir = isolated_paths(directory)["qa"]
    report_path = qa_dir / suite.report.path if suite.report else None
    env = build_environment(suite, directory, binary, base_env, log_path, report_path,
                            pycache=Path(run_dir).parent / PYCACHE_FOLDER)
    env[qa_waits.SCALE_ENV] = repr(float(timeout_scale))  # Fixture waits scale with the suite timeout.
    preconditions = []
    missing = [key for key in suite.live_env if caller_value(base_env, key) is None]
    if missing:
        preconditions.append("missing opt-in variables: " + ", ".join(missing))
    required = required_output_root(suite)
    if required and not Path(directory).resolve().is_relative_to(required.resolve()):
        preconditions.append("needs --output under %s" % required)
    return {"suite": suite, "directory": directory, "window": window, "log": log_path, "qa": qa_dir,
            "report": report_path, "env": env, "command": build_command(suite, binary, window, qa_dir),
            "timeout": suite.timeout * timeout_scale, "preconditions": preconditions}


def run_plan(plan, launch=subprocess.Popen):
    """Run one planned suite to completion and classify the outcome (never raises for suite failures)."""
    suite, started, clock = plan["suite"], time.time(), time.monotonic()
    status, reasons, code, launched = None, list(plan["preconditions"]), None, False
    if reasons:
        status = "error"
    else:
        with open(plan["log"], "w", encoding="utf-8") as log:
            try:
                process = launch(plan["command"], cwd=str(ROOT), env=plan["env"], stdout=log,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **_session_options())
            except OSError as error:
                status = "error"
                reasons.append("launch failed: %s" % error)
            else:
                launched = True
                code, timed_out = _wait(process, plan["timeout"])
                if timed_out:
                    status = "timeout"
                    reasons.append("timed out after %.0f s; process tree killed" % plan["timeout"])
    duration = time.monotonic() - clock
    if launched:
        if status is None:
            status, exit_reason = checks.describe_exit(code)
            if exit_reason:
                reasons.append(exit_reason)
        reasons += evidence(plan, started)
        if status is None and any(reason.startswith("crash") for reason in reasons):
            status = "crashed"
        status = status or ("failed" if reasons else "passed")
    return {"name": suite.name, "class": suite.kind, "mode": "gui" if plan["window"] else "background",
            "status": status, "duration_seconds": round(duration, 3), "exit_code": code, "reasons": reasons,
            "log": str(plan["log"]), "report": str(plan["report"]) if plan["report"] else None,
            "qa_dir": str(plan["qa"]), "command": plan["command"]}


def _wait(process, timeout):
    with _running_lock:
        _running.add(process)
    try:
        return process.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        _kill(process)
        return process.wait(), True
    finally:
        with _running_lock:
            _running.discard(process)


def _session_options(platform=None):
    """Popen options that give a suite its own process group, away from the runner's Ctrl+C."""
    if (sys.platform if platform is None else platform) == "win32":
        # start_new_session is ignored on Windows; a new process group keeps console Ctrl+C from the suite.
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def _kill(process, platform=None):
    """Kill the suite's whole process tree (a host driver's Blender children included)."""
    if (sys.platform if platform is None else platform) == "win32":
        # Windows has no process group signal (os.killpg): end the tree under the suite's PID instead.
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.kill()  # Only the suite's own process if taskkill was unavailable; harmless after it.
        except OSError:
            pass
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError):
        process.kill()


def evidence(plan, started):
    """Reasons from logs, crash files, the JSON receipt and the completion marker."""
    suite, reasons = plan["suite"], []
    main_log = ""
    for path in sorted(plan["directory"].rglob("*.log")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path == plan["log"]:
            main_log = text
        for finding in checks.scan_log(text):
            reasons.append("%s in %s: %s" % (finding["kind"], path.name, finding["line"]))
    reasons += ["crash log written: " + path for path in checks.find_crash_logs(plan["directory"])]
    if suite.report is not None:
        reasons += checks.validate_report(suite.report, plan["report"], started)
    if suite.marker and suite.marker not in main_log:
        reasons.append("completion marker %s missing from output" % suite.marker)
    return reasons


def run_all(plans, jobs, launch=subprocess.Popen, progress=print):
    """Parallel suites first (bounded by ``jobs``), then documented serial suites alone."""
    results, total = {}, len(plans)

    def run(plan):
        result = run_plan(plan, launch)
        results[plan["suite"].name] = result
        progress("[%d/%d] %-7s %8.1fs  %s" % (len(results), total, result["status"].upper(),
                                              result["duration_seconds"], result["name"]))
        return result

    parallel = [plan for plan in plans if not plan["suite"].serial]
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        try:
            list(pool.map(run, parallel))
        except KeyboardInterrupt:
            # Kill children before the pool's shutdown waits on their threads.
            pool.shutdown(wait=False, cancel_futures=True)
            kill_running()
            raise
    for plan in plans:
        if plan["suite"].serial:
            run(plan)
    return [results[plan["suite"].name] for plan in plans]


def kill_running():
    with _running_lock:
        for process in list(_running):
            _kill(process)


def binary_info(binary):
    digest = hashlib.sha256()
    with open(binary, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    stat = binary.stat()
    return {"path": str(binary), "sha256": digest.hexdigest(), "bytes": stat.st_size,
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")}


def source_revision():
    try:
        result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                                timeout=10, env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def list_suites(suites):
    for suite in suites:
        flags = [flag for flag, on in (("event-simulate", suite.event_simulate), ("real-input", suite.real_input),
                                       ("default-chat", suite.default_chat), ("first-launch", suite.first_launch),
                                       ("serial", suite.serial), ("gui-superset", suite.gui_superset),
                                       ("host-driver", suite.host_launcher), ("state-in-qa", suite.state_in_qa),
                                       ("only " + "/".join(suite.platforms), bool(suite.platforms)))
                 if on]
        suffix = " [" + ", ".join(flags) + "]" if flags else ""
        print("%-10s %-42s %s%s" % (suite.kind, suite.name, suite.purpose, suffix))
    for script, reason in sorted(manifest.EXCLUDED.items()):
        print("%-10s %-42s %s" % ("EXCLUDED", script[:-3], reason))


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run Angelo's native Blender suites with isolated state.")
    parser.add_argument("--binary", default=product_env.get("BINARY"),
                        help="compiled Angelo executable (default: $ANGELO_BINARY or a deprecated spelling)")
    parser.add_argument("--gui", action="store_true", help="also run GUI suites (opens windows)")
    parser.add_argument("--allow-live", action="store_true",
                        help="permit LIVE suites named exactly by --include (real providers; may bill)")
    parser.add_argument("--include", action="append", default=[], metavar="GLOB", help="only matching suites")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip matching suites")
    parser.add_argument("--jobs", type=int, default=1, help="suites run concurrently (default 1)")
    parser.add_argument("--timeout-scale", type=float, default=1.0, help="multiply every suite timeout")
    parser.add_argument("--output", type=Path, default=None,
                        help="parent directory for a new run directory (default /private/tmp on macOS, "
                             "the temp folder elsewhere)")
    parser.add_argument("--list", action="store_true", help="print the manifest classification and exit")
    parser.add_argument(addon_guard.OVERRIDE, action="store_true",
                        help="run although the app's bundled add-on is not this checkout's (native_addon_guard)")
    args = parser.parse_args(argv)
    if args.timeout_scale <= 0:
        parser.error("--timeout-scale must be positive")
    return args


def main(argv=None, suites=manifest.SUITES, base_env=None, launch=subprocess.Popen, addon_check=addon_guard.check):
    args = parse_args(argv)
    if args.list:
        list_suites(suites)
        return 0
    if not args.binary:
        print("Pass --binary or set ANGELO_BINARY to the compiled Angelo executable.", file=sys.stderr)
        return 2
    binary = Path(args.binary).expanduser()
    if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
        print("Not an absolute path to an executable: %s" % args.binary, file=sys.stderr)
        return 2
    binary = binary.resolve()
    bundled = addon_check(binary, ROOT)   # The app runs its own add-on: it must be this checkout's.
    if not bundled["matches"]:
        if not args.allow_addon_mismatch:
            print(addon_guard.refusal(bundled), file=sys.stderr, end="")
            return 2
        print(addon_guard.warning(bundled), end="", flush=True)
    selected, skipped = select_suites(suites, gui=args.gui, includes=args.include, excludes=args.exclude,
                                      allow_live=args.allow_live, platform=sys.platform)
    if not selected:
        print("No suites selected; check --include/--exclude/--gui (see --list).", file=sys.stderr)
        return 2
    output_root = (args.output or default_output_root()).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="angelo-native-suites-", dir=str(output_root)))
    base_env = dict(os.environ) if base_env is None else base_env
    started = time.time()
    started_at = datetime.datetime.fromtimestamp(started).isoformat(timespec="seconds")
    settings = {"binary": binary_info(binary), "source_root": str(ROOT), "source_revision": source_revision(),
                "platform": sys.platform, "output": str(run_dir), "started_at": started_at,
                "jobs": max(1, args.jobs), "gui": args.gui, "allow_live": args.allow_live,
                "includes": args.include, "excludes": args.exclude, "timeout_scale": args.timeout_scale,
                "bundled_addon": bundled}
    plans = [plan_suite(suite, run_dir, binary, base_env, args.gui, args.timeout_scale) for suite in selected]
    print("Running %d native suite(s) with %d job(s); output %s" % (len(plans), settings["jobs"], run_dir), flush=True)
    interrupted = False
    try:
        results = run_all(plans, settings["jobs"], launch, progress=lambda line: print(line, flush=True))
    except KeyboardInterrupt:
        kill_running()
        interrupted, results = True, []
    settings["duration_seconds"] = round(time.time() - started, 3)
    summary = summarize(results, skipped, settings)
    summary["interrupted"] = interrupted
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    text = human_summary(summary)
    (run_dir / "summary.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    if interrupted:
        return 130
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
