# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Is the machine quiet enough for timed runs? Load, heavy processes and a load log (final_runs.py).

Timings in the final runs must be uncontended. ``check`` refuses a start when
the load average (macOS, Linux: the 1- and 5-minute averages; Windows: total
CPU use expressed as busy cores, so one threshold means the same everywhere)
is above the threshold, or when another heavy process runs: a build or test
tool by name, or programs outside the run using more than the threshold in
cores together. ``wait_quiet`` pauses until both hold (Stop with a STOP file).
``Sampler`` writes the load, the other heavy processes and the cores used
outside the run every few seconds while a trial runs, so each trial's record
says what the machine was doing. A trial is contended when another heavy
process ran or programs outside the run used more than ``threshold`` cores; the
load average alone does not decide it, as it includes the trial's own work (a
one-shot's final render uses every core).

What cannot be read is unknown, never idle: a process list that failed (the
command raised, timed out, exited with an error or printed nothing) is
``None``, and so is a Windows load without a reading. ``check`` then refuses
with the reason, ``wait_quiet`` keeps retrying and reports it, and a trial
with a sample whose process list was unreadable is contended (unknown).

Standard library only; the process list comes from ``ps`` or, on Windows,
from PowerShell's performance counters. Nothing here starts or stops a process.
"""

import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

DEFAULT_THRESHOLD = 6.0
# Resident desktop programs (a chat app's renderer, a menu-bar tool) often use most of a core on their own; one such
# process does not contend a trial, so the guard counts the cores everything outside the run uses together.
HEAVY_NAMES = frozenset({
    "ninja", "cmake", "make", "clang", "clang++", "cc1", "cc1plus", "ld", "ld64", "ld64.lld", "lld", "swift-frontend",
    "xcodebuild", "blender_test", "msbuild", "cl", "link", "lld-link", "rustc", "cargo"})
HEAVY_SCRIPTS = ("run_host_tests.py", "run_native_suites.py", "bench_mcp_vs_angelo.py", "eval_live_agent.py",
                 "run_pilot.py")
IDLE_NAMES = frozenset({"kernel_task", "windowserver", "system idle process", "idle", "_total"})


def load(platform=None):
    """``[1, 5, 15]``-minute load averages; on Windows the current busy cores, three times."""
    if (platform or sys.platform) != "win32":
        return [round(value, 2) for value in os.getloadavg()]
    command = ["powershell.exe", "-NoProfile", "-Command",
               "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        percent = float(result.stdout.strip()) if result.returncode == 0 else None   # Empty: ValueError, unknown.
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if percent is None:
        return None
    busy = round(percent / 100 * (os.cpu_count() or 1), 2)
    return [busy, busy, busy]


def processes(platform=None):
    """``[{pid, ppid, cpu, name, command}]`` of every process (cpu: percent of one core), or None when the list
    could not be read: never an empty list, which would read as an idle machine."""
    if (platform or sys.platform) == "win32":
        script = ("Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | ForEach-Object { "
                  "[pscustomobject]@{pid=$_.IDProcess; ppid=$_.CreatingProcessID; cpu=$_.PercentProcessorTime; "
                  "name=$_.Name; command=$_.Name} } | ConvertTo-Json -Compress")
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True,
                                    text=True, timeout=60)
            rows = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        if rows is None:
            return None
        rows = rows if isinstance(rows, list) else [rows]
        return [{"pid": int(r.get("pid") or 0), "ppid": int(r.get("ppid") or 0), "cpu": float(r.get("cpu") or 0),
                 "name": str(r.get("name") or "").lower().split("#")[0].removesuffix(".exe"),
                 "command": str(r.get("command") or "")}
                for r in rows] or None
    try:   # comm last (it may hold spaces: "Claude Helper (Renderer)"); the command lines from a second call.
        listed = subprocess.run(["ps", "-axo", "pid=,ppid=,pcpu=,comm="], capture_output=True, text=True, timeout=30)
        described = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0 or described.returncode != 0:
        return None
    names, commands = listed.stdout, described.stdout
    args = {}
    for line in commands.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and fields[0].isdigit():
            args[int(fields[0])] = fields[1]
    rows = []
    for line in names.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) < 4:
            continue
        try:
            pid = int(fields[0])
            rows.append({"pid": pid, "ppid": int(fields[1]), "cpu": float(fields[2]), "name": Path(fields[3]).name,
                         "command": args.get(pid, fields[3])})
        except ValueError:
            continue
    return rows or None   # Nothing parsed: unreadable, not an empty machine.


def _descendants(rows, roots):
    children, found, pending = {}, set(roots), list(roots)
    for row in rows:
        children.setdefault(row["ppid"], []).append(row["pid"])
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def heavy(rows, own=(), cpu_percent=None):
    """Other heavy processes: build and test tools by name, harness scripts, and (only when given) anything above
    ``cpu_percent`` of a core.

    ``own``: process IDs whose whole trees belong to this run (and this process with its ancestors' shells).
    """
    found = []
    for row in _others(rows, own):
        script = next((name for name in HEAVY_SCRIPTS if name in row["command"]), None)
        if row["name"].lower() in HEAVY_NAMES or script or (cpu_percent is not None and row["cpu"] > cpu_percent):
            found.append({"pid": row["pid"], "name": script or row["name"], "cpu": row["cpu"]})
    return sorted(found, key=lambda item: -item["cpu"])


def _others(rows, own=()):
    """Processes outside this run (``own`` trees, this process) and the system's idle entries."""
    mine = _descendants(rows, set(own) | {os.getpid()})
    return [row for row in rows if row["pid"] not in mine and row["name"].lower() not in IDLE_NAMES
            and row["pid"] not in (0, 1)]


def other_cores(rows, own=()):
    """Cores used by every process outside this run (their CPU percentages of one core, summed)."""
    return round(sum(row["cpu"] for row in _others(rows, own)) / 100, 2)


def check(threshold=DEFAULT_THRESHOLD, own=(), *, rows=None, averages=None, platform=None):
    """``{"quiet", "reasons", "load", "heavy"}``: whether a timed trial may start now (never when unknown)."""
    averages = load(platform) if averages is None else averages
    rows = processes(platform) if rows is None else rows
    found = heavy(rows, own) if rows is not None else []
    reasons = [] if rows is not None else ["the process list could not be read, so other programs' load is unknown"]
    if averages is None:
        reasons.append("the load could not be read")
    else:
        if averages[0] > threshold:
            reasons.append("1-minute load %.2f is above %.1f" % (averages[0], threshold))
        if averages[1] > threshold:
            reasons.append("5-minute load %.2f is above %.1f" % (averages[1], threshold))
    if found:
        reasons.append("other heavy processes: " + ", ".join("%s (%d, %.0f%%)" % (item["name"], item["pid"], item["cpu"])
                                                             for item in found[:6]))
    cores = other_cores(rows, own) if rows is not None else None
    if cores is not None and cores > threshold:
        reasons.append("programs outside the run use %.1f cores (above %.1f)" % (cores, threshold))
    return {"quiet": not reasons, "reasons": reasons, "load": averages, "heavy": found[:12], "other_cores": cores,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def wait_quiet(threshold, journal, *, poll=60, max_wait=None, stop_file=None, checker=None, sleep=time.sleep):
    """Wait until ``check`` passes; every pause is written to ``journal`` (JSON lines). Returns seconds waited.

    Raises ``TimeoutError`` after ``max_wait`` seconds and ``InterruptedError`` when ``stop_file`` appears.
    """
    checker = checker or (lambda: check(threshold))
    started, paused = time.monotonic(), False
    while True:
        state = checker()
        if state["quiet"]:
            if paused:
                _append(journal, {"event": "resumed", "after_s": round(time.monotonic() - started, 1), **state})
            return round(time.monotonic() - started, 1)
        if not paused:
            _append(journal, {"event": "paused", **state})
            paused = True
        if stop_file and Path(stop_file).exists():
            raise InterruptedError("STOP file present")
        if max_wait is not None and time.monotonic() - started >= max_wait:
            raise TimeoutError("the machine stayed busy for %d s: %s" % (max_wait, "; ".join(state["reasons"])))
        sleep(poll)


def _append(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")


class Sampler:
    """The load and other heavy processes every ``interval`` seconds into ``path``, while a trial runs."""

    def __init__(self, path, threshold=DEFAULT_THRESHOLD, interval=5.0, own=()):
        self.path, self.threshold, self.interval, self.own = Path(path), threshold, interval, own
        self.samples, self._stop = [], threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="FinalRunsLoad")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=self.interval + 30)

    def sample(self):
        """The machine now; ``heavy`` and ``other_cores`` are None when the process list could not be read."""
        rows = processes()
        return {"t": round(time.time(), 1), "load": load(),
                "heavy": heavy(rows, self.own)[:6] if rows is not None else None,
                "other_cores": other_cores(rows, self.own) if rows is not None else None}

    def _run(self):
        while True:
            sample = self.sample()
            self.samples.append(sample)
            _append(self.path, sample)
            if self._stop.wait(self.interval):
                return

    def summary(self):
        """Per trial: samples, median and maximum 1-minute load and outside cores, and whether it was contended.

        A sample whose process list could not be read (``unreadable_samples``) makes the trial contended: what
        ran beside it is unknown, never assumed quiet.
        """
        values = [s["load"][0] for s in self.samples if s.get("load")]
        unreadable = [s for s in self.samples if s.get("other_cores") is None or s.get("heavy") is None]
        others = [s["other_cores"] for s in self.samples if s not in unreadable]
        busy = [s for s in self.samples if s not in unreadable and (s["heavy"] or s["other_cores"] > self.threshold)]
        return {"samples": len(self.samples), "median_1m": round(statistics.median(values), 2) if values else None,
                "max_1m": max(values) if values else None,
                "load_over_threshold_samples": sum(value > self.threshold for value in values),
                "max_other_cores": max(others) if others else None, "contended_samples": len(busy),
                "unreadable_samples": len(unreadable), "contended": bool(busy or unreadable),
                "threshold": self.threshold}
