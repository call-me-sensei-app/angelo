# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-process benchmark counters: retired instructions, cycles, CPU and wall time.

Standard library only, so the same module runs in host Python and in Angelo's
bundled Python. Benchmarks import it from this directory.

Why instructions: on a shared machine, wall time varies with every other
process, and CPU time still varies with cache contention, frequency scaling and
P/E-core placement. The number of retired instructions counts the work the
process did, not how long the machine took to do it, so it is the steadiest
signal for "did this change make the code do more or less work".

It is not perfectly deterministic. Counts cover every thread of the process,
so thread-pool spin-waiting, page faults, lazily initialised caches, hash
randomisation and garbage collection still add variation; measure the spread
(``stability``) instead of assuming it. Counts are only comparable on the same
CPU architecture and the same binary build.

macOS: ``proc_pid_rusage(getpid(), RUSAGE_INFO_V4)`` from libproc fills
``struct rusage_info_v4`` declared in the SDK's ``<sys/resource.h>``; its
``ri_instructions`` and ``ri_cycles`` fields (macOS 10.14+) are read here.
``ri_user_time``/``ri_system_time`` are mach absolute-time ticks, not
nanoseconds, so CPU time comes from ``time.process_time_ns`` instead. Other
platforms report instructions and cycles as unavailable (``None``), never zero.

``read_thread_counts`` reads the calling thread's instructions and cycles with
libsystem_kernel's ``thread_selfcounts(THSC_CPI)`` (xnu's ``struct thsc_cpi``:
instructions, then cycles). It is not in the public SDK headers, so it is used
only where a process-wide count would be wrong: the benchmarks' work clock,
which must not count other threads (see ``benchmark_fixtures``). On a
single-threaded workload it matched the process count within 0.02%; with a busy
second thread it stayed at the workload's count while the process count doubled.

Usage::

    python3 tests/python/angelo_agent/perf_counters.py --repeats 15

runs a fixed pure-Python workload and prints the spread of each metric.
"""

import argparse
import ctypes
import json
import math
import os
import statistics
import sys
import time

RUSAGE_INFO_V4 = 4
LIBPROC_PATH = "/usr/lib/libproc.dylib"
# struct rusage_info_v4 after its leading uint8_t ri_uuid[16], in header order.
RUSAGE_V4_FIELDS = (
    "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
    "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
    "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time", "ri_child_pkg_idle_wkups",
    "ri_child_interrupt_wkups", "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
    "ri_diskio_byteswritten", "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance",
    "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility", "ri_cpu_time_qos_legacy",
    "ri_cpu_time_qos_user_initiated", "ri_cpu_time_qos_user_interactive", "ri_billed_system_time",
    "ri_serviced_system_time", "ri_logical_writes", "ri_lifetime_max_phys_footprint", "ri_instructions",
    "ri_cycles", "ri_billed_energy", "ri_serviced_energy", "ri_interval_max_phys_footprint",
    "ri_runnable_time",
)
METRICS = ("wall_ms", "cpu_ms", "instructions", "cycles", "disk_written_bytes")


class RusageInfoV4(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in RUSAGE_V4_FIELDS]


_libproc = None
_libproc_error = ""


def _load_libproc():
    """The libproc handle, or None with a reason; loaded once per process."""
    global _libproc, _libproc_error
    if _libproc is not None or _libproc_error:
        return _libproc
    if sys.platform != "darwin":
        _libproc_error = "proc_pid_rusage instruction counters are macOS-only"
        return None
    try:
        library = ctypes.CDLL(LIBPROC_PATH)
        function = library.proc_pid_rusage
    except (OSError, AttributeError) as error:
        _libproc_error = "libproc is unavailable: " + str(error)
        return None
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    function.restype = ctypes.c_int
    _libproc = function
    return _libproc


def read_rusage():
    """Raw selected ``rusage_info_v4`` fields for this process, or None."""
    function = _load_libproc()
    if function is None:
        return None
    info = RusageInfoV4()
    if function(os.getpid(), RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        return None
    return {"instructions": int(info.ri_instructions), "cycles": int(info.ri_cycles),
            "disk_written_bytes": int(info.ri_diskio_byteswritten),
            "disk_read_bytes": int(info.ri_diskio_bytesread)}


THSC_CPI = 1
_thread_selfcounts = None
_thread_error = ""


def read_thread_counts():
    """``{"instructions", "cycles"}`` retired by the calling thread, or None (non-macOS or refused)."""
    global _thread_selfcounts, _thread_error
    if _thread_selfcounts is None and not _thread_error:
        if sys.platform != "darwin":
            _thread_error = "thread_selfcounts is macOS-only"
        else:
            try:
                function = ctypes.CDLL(None).thread_selfcounts
                function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
                function.restype = ctypes.c_int
                _thread_selfcounts = function
            except (OSError, AttributeError) as error:
                _thread_error = "thread_selfcounts is unavailable: " + str(error)
    if _thread_selfcounts is None:
        return None
    counts = (ctypes.c_uint64 * 2)()
    if _thread_selfcounts(THSC_CPI, counts, ctypes.sizeof(counts)) != 0:
        return None
    return {"instructions": int(counts[0]), "cycles": int(counts[1])}


def availability():
    """Whether instruction/cycle counters work here, with the reason if not."""
    if read_rusage() is None:
        return {"instructions": "unavailable", "reason": _libproc_error or "proc_pid_rusage failed",
                "platform": sys.platform}
    return {"instructions": "available", "source": "proc_pid_rusage(RUSAGE_INFO_V4)", "platform": sys.platform,
            "scope": "all threads of this process"}


def read():
    """One reading. Take wall time last at the start and first at the end."""
    counters = read_rusage()
    return {"cpu_ns": time.process_time_ns(), "wall_ns": time.perf_counter_ns(), "counters": counters}


def difference(start, end):
    """Metric deltas between two readings; unavailable counters stay None."""
    result = {"wall_ms": (end["wall_ns"] - start["wall_ns"]) / 1e6,
              "cpu_ms": (end["cpu_ns"] - start["cpu_ns"]) / 1e6}
    for name in ("instructions", "cycles", "disk_written_bytes"):
        if start["counters"] is None or end["counters"] is None:
            result[name] = None
        else:
            result[name] = end["counters"][name] - start["counters"][name]
    return result


def measure(function):
    """Call ``function()`` once; return ``(result, metrics)``.

    Wall time is read innermost, so counter reads (about 17,000 instructions
    for the pair on Apple M4) are excluded from it but included in the counts.
    """
    start = read()
    result = function()
    end_wall = time.perf_counter_ns()
    end = read()
    end["wall_ns"] = end_wall
    return result, difference(start, end)


def percentile(values, fraction):
    """Linear-interpolated percentile of a non-empty sequence (0 <= fraction <= 1)."""
    if not values:
        raise ValueError("A percentile needs at least one value")
    if not 0 <= fraction <= 1:
        raise ValueError("The percentile fraction must be between 0 and 1")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def describe(values):
    """Spread of one metric's samples, or None when the metric is unavailable."""
    values = [value for value in values if value is not None]
    if not values:
        return None
    median = statistics.median(values)
    mean = statistics.fmean(values)
    deviation = statistics.pstdev(values) if len(values) > 1 else 0.
    return {"samples": len(values), "median": round(median, 3), "p75": round(percentile(values, .75), 3),
            "p95": round(percentile(values, .95), 3), "min": round(min(values), 3), "max": round(max(values), 3),
            # Coefficient of variation and relative range: comparable across metrics.
            "cv": round(deviation / mean, 5) if mean else 0.,
            "relative_range": round((max(values) - min(values)) / median, 5) if median else 0.}


def summarize(samples):
    """Per-metric ``describe`` over a list of ``difference`` dictionaries."""
    return {name: describe([sample[name] for sample in samples]) for name in METRICS}


def repeat(function, samples=5, warmup=1):
    """Warm up, then measure ``samples`` calls; returns (summary, raw samples, last result)."""
    if samples < 1 or warmup < 0:
        raise ValueError("Use at least one sample and a non-negative warmup")
    result, raw = None, []
    for index in range(warmup + samples):
        result, metrics = measure(function)
        if index >= warmup:
            raw.append(metrics)
    return summarize(raw), raw, result


def fixed_workload(size=60000):
    """Deterministic CPU-bound pure-Python work: build, sort, hash and join."""
    values = [(index * 7919) % 104729 for index in range(size)]
    values.sort()
    table = {value: str(value) for value in values}
    return len(",".join(table[value] for value in values[::3]))


def stability(workload=fixed_workload, repeats=15, warmup=2):
    """Run one fixed workload repeatedly and report each metric's spread."""
    summary, raw, _ = repeat(workload, samples=repeats, warmup=warmup)
    return {"availability": availability(), "repeats": repeats, "warmup": warmup,
            "metrics": summary, "samples": raw}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure counter stability on a fixed workload.")
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--size", type=int, default=60000)
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    args = parser.parse_args(argv)
    report = stability(lambda: fixed_workload(args.size), repeats=args.repeats)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print("counters:", json.dumps(report["availability"]))
    for name, value in report["metrics"].items():
        if value is None:
            print("%-19s unavailable" % name)
        else:
            print("%-19s median %-16s cv %.4f  range %.4f" % (name, value["median"], value["cv"], value["relative_range"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
