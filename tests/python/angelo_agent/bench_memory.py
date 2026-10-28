# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Memory of an Angelo trial's processes, sampled from outside the app (the bench), the same way in both arms.

Every ``INTERVAL`` seconds one process listing finds the processes of the trial's app binary that descend from the
bench (the lead, and in the parallel arm each worker Blender, which the lead starts from the same binary; a worker
whose lead has gone is still counted once seen), and the kernel gives each one's resident size and macOS
phys_footprint (``proc_pid_rusage``, one system call per process: no tool is started per process, so an arm with more
processes costs the machine no more per sample). The same listing gives each process's arguments, so its role is read
every sample from how it was started (never cached: a process between fork and exec still has its parent's): a worker
Blender is launched with ``--worker`` (``worker_process.worker_command``); any other descendant of the binary (a probe
of the GPU, a worker's own Blender for its views) is a helper, never a worker (break-it E2E-16 H-1, E2E-17 L-5).
``summary`` gives the lead's, each worker's and each helper's first, last and peak, the same for their sum, and the
most the workers and helpers added at once beside the lead (``added_peak``), in bytes, with the machine it ran on
(BENCH-PREREG Amendment B2); ``timeline`` keeps every sample on the wall clock. Growth is measured from send/ready
(Amendment E, ``from_send``, replacing B2's last minus first sample): the lead from its last sample at or before the
message's send, each worker from its first sample after it told Angelo it is ready, both moments from the trial's own
record (``eval_live_agent_session``, ``eval_parallel``). Reported only, never in the verdict: ``trial`` and ``line``
give the parallel report its figures. Other platforms record that nothing was sampled. Standard library only.
"""

import ctypes
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

INTERVAL = 2.0
RUN = subprocess.run   # Bound at import: a test patching subprocess.run for the trial never sees this thread's calls.
RUSAGE_INFO_V4 = 4
RESIDENT, FOOTPRINT = 64, 72   # Byte offsets of ri_resident_size and ri_phys_footprint in rusage_info_v4.


def _usage(pid, libproc=[]):
    """``(resident, footprint)`` bytes of ``pid``, or None."""
    if not libproc:
        libproc.append(ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True))
    buffer = ctypes.create_string_buffer(512)
    if libproc[0].proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(RUSAGE_INFO_V4), buffer) != 0:
        return None
    read = lambda offset: int.from_bytes(buffer.raw[offset:offset + 8], "little")   # noqa: E731
    return read(RESIDENT), read(FOOTPRINT)


def _listing():
    """{pid: (ppid, arguments)} of every process (its executable first, as it was started)."""
    text = RUN(["ps", "-Ao", "pid=,ppid=,args="], capture_output=True, text=True, timeout=10).stdout
    rows = {}
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            rows[int(parts[0])] = (int(parts[1]), parts[2].strip())
    return rows


def _runs(arguments, binaries):
    return any(arguments == binary or arguments.startswith(binary + " ") for binary in binaries)


def ours(rows, binaries, root, known):
    """{pid: ppid} of the processes running one of ``binaries`` that descend from ``root`` (never ``root`` itself), or
    were seen before."""
    found = {}
    for pid, (ppid, arguments) in rows.items():
        if not _runs(arguments, binaries) or pid == root:
            continue
        step, seen = pid, set()
        while step not in seen and step > 1 and step not in known and step != root:
            seen.add(step)
            step = rows.get(step, (0, ""))[0]
        if step == root or step in known:
            found[pid] = ppid
    return found


def role(arguments):
    """``"worker"`` when a process was started as a worker Blender (``--worker`` among its arguments), else
    ``"other"``."""
    words = arguments.split()
    return "worker" if "--worker" in words and "--channel" in words else "other"


def machine():
    """What the numbers were measured on: hardware model, memory and system, never a name or a path."""
    def sysctl(name):
        try:
            return RUN(["sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    if sys.platform != "darwin":
        return {"platform": sys.platform, "machine": platform.machine()}
    memory = sysctl("hw.memsize")
    return {"platform": sys.platform, "model": sysctl("hw.model"), "chip": sysctl("machdep.cpu.brand_string"),
            "memory_bytes": int(memory) if memory.isdigit() else None, "system": platform.mac_ver()[0]}


class Sampler:
    def __init__(self, binary, root=None, interval=INTERVAL):
        self.binaries = {str(binary), str(Path(binary).resolve())}
        self.root, self.interval = root or os.getpid(), interval
        self.samples, self.known = [], set()   # samples: (seconds since start, {pid: (ppid, resident, footprint)})
        self.times = []   # Each sample's wall-clock seconds (Amendment E: the trial records its send and readies so).
        self.roles = {}   # pid -> its role at its latest sample
        self.missed = 0
        self._stop, self._thread = threading.Event(), None

    def start(self):
        if sys.platform == "darwin":
            self.started = time.monotonic()
            self._thread = threading.Thread(target=self._run, daemon=True, name="AngeloBenchMemory")
            self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                found, rows = {}, _listing()
                for pid, ppid in ours(rows, self.binaries, self.root, self.known).items():
                    usage = _usage(pid)
                    if usage is not None:
                        found[pid] = (ppid, *usage)
                        self.known.add(pid)
                        self.roles[pid] = role(rows[pid][1])
                if found:
                    self.samples.append((round(time.monotonic() - self.started, 1), found))
                    self.times.append(round(time.time(), 3))
            except Exception:   # noqa: BLE001  A measurement never ends or changes the trial: one sample missed.
                self.missed += 1
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(15)
        return dict(summary(self.samples, self.interval, self.roles), missed_samples=self.missed,
                    timeline=timeline(self.samples, self.times))


def _series(values):
    resident, footprint = [v[0] for v in values], [v[1] for v in values]
    return {"samples": len(values), "resident_first": resident[0], "resident_last": resident[-1],
            "resident_peak": max(resident), "footprint_first": footprint[0], "footprint_last": footprint[-1],
            "footprint_peak": max(footprint)}


def summary(samples, interval=INTERVAL, roles=None):
    """The lead's (a process whose parent is no process of the app), each worker's (started as a worker, ``roles``) and
    each helper's (any other) first, last and peak resident size and phys_footprint, the same for their sum, and
    ``added_peak``: the most the workers and helpers held at once beside the lead, in bytes; ``sampled`` False when
    nothing was."""
    if not samples:
        return {"sampled": False, "platform": sys.platform}
    series = {}
    for _seconds, found in samples:
        for pid, (ppid, resident, footprint) in found.items():
            series.setdefault(pid, {"ppid": ppid, "values": []})["values"].append((resident, footprint))
    leads = {pid for pid in series if series[pid]["ppid"] not in series}
    workers = {pid for pid in series if pid not in leads and (roles or {}).get(pid) == "worker"}
    totals = [(sum(v[1] for v in found.values()), sum(v[2] for v in found.values())) for _s, found in samples]
    added = [(sum(v[1] for pid, v in found.items() if pid not in leads),
              sum(v[2] for pid, v in found.items() if pid not in leads)) for _s, found in samples]
    side = lambda pid: dict(pid=pid, parent=series[pid]["ppid"], **_series(series[pid]["values"]))   # noqa: E731
    return {"sampled": True, "machine": machine(), "interval_s": interval, "samples": len(samples),
            "lead": [side(pid) for pid in sorted(leads)], "workers": [side(pid) for pid in sorted(workers)],
            "helpers": [side(pid) for pid in sorted(set(series) - leads - workers)],
            "most_workers_at_once": max(len(set(found) & workers) for _s, found in samples), "sum": _series(totals),
            "added_peak": {"resident": max(v[0] for v in added), "footprint": max(v[1] for v in added)}}


def timeline(samples, times):
    """{pid (as text, as JSON keeps it): [[wall-clock seconds, resident, footprint], ...]}, every sample, oldest first."""
    found = {}
    for (_seconds, processes), at in zip(samples, times):
        for pid, (_ppid, resident, footprint) in processes.items():
            found.setdefault(str(pid), []).append([at, resident, footprint])
    return found


def from_send(record, sent_at, ready_at):
    """Amendment E1, in bytes: each process's peak over its own baseline (resident size and phys_footprint), read from
    that baseline's sample on. The lead's baseline is its last sample at or before ``sent_at`` (the message's send);
    a worker's its first sample after ``ready_at[pid]`` (when it told Angelo it is ready). A process whose moment was
    not recorded, or that has no such sample, says why: never a number from another baseline."""
    line = record.get("timeline") or {}

    def over(pid, moment, lead):
        series = line.get(str(pid)) or []
        if moment is None:
            return {"pid": pid, "why": "its %s was not recorded" % ("send" if lead else "ready moment")}
        index = (max((i for i, sample in enumerate(series) if sample[0] <= moment), default=None) if lead
                 else next((i for i, sample in enumerate(series) if sample[0] > moment), None))
        if index is None:
            return {"pid": pid, "why": "no sample %s it" % ("at or before" if lead else "after")}
        base, later = series[index], series[index:]
        return {"pid": pid, "baseline_at": base[0], "resident": max(s[1] for s in later) - base[1],
                "footprint": max(s[2] for s in later) - base[2]}
    return {"lead": [over(side["pid"], sent_at, True) for side in record.get("lead") or ()],
            "workers": [over(side["pid"], (ready_at or {}).get(side["pid"]), False)
                        for side in record.get("workers") or ()]}


def _mb(value):
    return None if value is None else round(value / 2 ** 20)


def trial(folder, number, task=None):
    """One trial's memory for the parallel report, in MB, from its launch record and ``task`` (the trial record's task:
    its send and each worker process's first ready moment): its worker cap; the lead's, the largest worker's and their
    sum's peak phys_footprint and the sum's RSS peak, what the workers and helpers added at most (B2); and each
    process's peak over its send/ready baseline (E1), phys_footprint and RSS."""
    try:
        launch = json.loads((Path(folder) / ("angelo-trial-%02d-launch.json" % number)).read_text())
    except (OSError, ValueError):
        launch = {}
    record, task = launch.get("memory") or {}, task or {}
    if not record.get("sampled"):
        return {"worker_cap": launch.get("worker_cap"), "memory_sampled": False}
    # Each worker process's first "ready", stamped when it said so (eval_parallel's ready clock), whichever part and
    # path: never rebuilt from a launch time plus a duration (break-it E2E-19B M-4).
    ready = {entry.get("pid"): entry.get("ready_at_unix") for entry in task.get("worker_processes") or ()
             if isinstance(entry, dict)}
    grown = from_send(record, task.get("sent_at_unix"), ready)
    over = lambda side: (dict(pid=side["pid"], footprint_mb=_mb(side["footprint"]), resident_mb=_mb(  # noqa: E731
        side["resident"])) if "why" not in side else side)
    peak = lambda sides: max((side["footprint_peak"] for side in sides), default=None)   # noqa: E731
    added = record.get("added_peak") or {}
    return {"worker_cap": launch.get("worker_cap"), "memory_sampled": True, "machine": record.get("machine"),
            "lead_peak_mb": _mb(peak(record["lead"])), "worker_peak_mb": _mb(peak(record["workers"])),
            "sum_peak_mb": _mb(record["sum"]["footprint_peak"]), "sum_rss_peak_mb": _mb(record["sum"]["resident_peak"]),
            "added_peak_mb": _mb(added.get("footprint")), "added_rss_peak_mb": _mb(added.get("resident")),
            "most_workers_at_once": record.get("most_workers_at_once"),
            "lead_over_send": [over(side) for side in grown["lead"]],
            "workers_over_ready": [over(side) for side in grown["workers"]]}


def _over(row):
    """(the lead's, the largest worker's) phys_footprint peak over its baseline in MB, None where not measured."""
    lead = [side["footprint_mb"] for side in row.get("lead_over_send") or () if "footprint_mb" in side]
    workers = [side["footprint_mb"] for side in row.get("workers_over_ready") or () if "footprint_mb" in side]
    return (lead[0] if lead else None), max(workers, default=None)


def cell(row):
    """One trial's memory in the report's table of every trial: the sum's peak, each process's peak over its own
    baseline (phys_footprint, then RSS) and what the workers and helpers added at most."""
    if not row.get("memory_sampled"):
        return "not sampled"
    over = lambda sides: ", ".join("+%s (RSS +%s)" % (side["footprint_mb"], side["resident_mb"])   # noqa: E731
                                   for side in sides or () if "footprint_mb" in side) or "none"
    gaps = [side["why"] for side in (row.get("lead_over_send") or []) + (row.get("workers_over_ready") or [])
            if "why" in side]
    return "%s; lead %s; workers %s; added %s%s" % (row["sum_peak_mb"], over(row.get("lead_over_send")),
                                                    over(row.get("workers_over_ready")), row["added_peak_mb"],
                                                    " (%s)" % "; ".join(gaps) if gaps else "")


def line(arm, rows):
    """One arm's memory, the median over its compared trials, in a sentence."""
    sampled = [row for row in rows if row["valid"] and row.get("memory_sampled")]
    if not sampled:
        return "%s not sampled" % arm

    def middle(values):
        values = [value for value in values if value is not None]
        return round(statistics.median(values)) if values else None
    return ("%s sum peak %s MB, lead peak %s MB and %s MB over the send, largest worker peak %s MB and %s MB over its "
            "ready, workers and their helpers added at most %s MB (%d trials)" % (
                arm, middle(row["sum_peak_mb"] for row in sampled), middle(row["lead_peak_mb"] for row in sampled),
                middle(_over(row)[0] for row in sampled), middle(row["worker_peak_mb"] for row in sampled),
                middle(_over(row)[1] for row in sampled), middle(row["added_peak_mb"] for row in sampled),
                len(sampled)))
