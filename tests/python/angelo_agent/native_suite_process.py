# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Running planned native suites (``run_native_suites.plan_suite``): each in its own process group, bounded, killed on
a timeout or Ctrl+C, and classified from its exit, logs, crash files, receipt and marker (stdlib only).

``run_native_suites`` re-exports every name here; its docstring is the contract.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import native_suite_checks as checks

ROOT = Path(__file__).resolve().parents[3]
_running, _running_lock = set(), threading.Lock()


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
                    reasons.append("timed out after %.0f s; suite termination attempted" % plan["timeout"])
    duration = time.monotonic() - clock
    if launched:
        if status is None:
            status, exit_reason = checks.describe_exit(code)
            if exit_reason:
                reasons.append(exit_reason)
        reasons += evidence(plan, started, code)
        if status is None and any(reason.startswith("crash") for reason in reasons):
            status = "crashed"
        status = status or ("failed" if reasons else "passed")
    return {"name": suite.name, "class": suite.kind, "mode": "gui" if plan["window"] else "background",
            "status": status, "duration_seconds": round(duration, 3), "exit_code": code, "reasons": reasons,
            "log": str(plan["log"]), "report": str(plan["report"]) if plan["report"] else None,
            "qa_dir": str(plan["qa"]), "command": plan["command"], "crash_logs": plan.get("crash_logs", {})}


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
    """Terminate the suite group (Unix) or attempt its tree (Windows); detached Unix children need a supervisor."""
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


def evidence(plan, started, code=None):
    """Reasons from logs, crash files (their tails go into the suite's log), the JSON receipt and the marker."""
    suite, reasons = plan["suite"], []
    main_log = ""
    # Its own folder and its profile's (HOME, TMP: a worker's log, a crash file; ``run_native_suites.profile_folder``).
    folders = [plan["directory"]] + [folder for folder in [plan.get("profile")]
                                     if folder is not None and folder != plan["directory"]]
    for path in sorted(path for folder in folders for path in folder.rglob("*.log")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path == plan["log"]:
            main_log = text
        for finding in checks.scan_log(text):
            reasons.append("%s in %s: %s" % (finding["kind"], path.name, finding["line"]))
    for folder in folders:
        reasons += checks.crash_logs(folder, plan["log"], code, plan.setdefault("crash_logs", {}))
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
