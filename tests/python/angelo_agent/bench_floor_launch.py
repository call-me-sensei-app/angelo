# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Never-slower floor rows: Angelo against stock Blender 5.2.x on the same machine (unbilled, no sign-in).

Angelo's promise is never to be slower than Blender. This driver measures the
rows that had no stock comparison (2026-10-21 latency research): app launch,
with a window and headless, for a first and a returning person, and how often
each add-on is enabled per launch. With ``--rows`` it also runs
``bench_floor_blender.py`` (compressed and plain .blend listing, kit.cut
against the stock Boolean at 0-10,000 objects, bulk creation of 2,000 and
20,000 objects) in both binaries.

Run it on a quiet machine, from the repository root (one line)::

    python3 tests/python/angelo_agent/bench_floor_launch.py --angelo <Angelo executable>
        --stock <stock Blender executable> --out DIR [--launches 7] [--rows]

Method, the same for both binaries:

* Every launch runs in a sandboxed profile (HOME, caches, ``BLENDER_USER_*``
  and Angelo's state inside ``DIR``; Angelo's keychain and agent runtime off;
  no provider CLI on PATH), so neither reads the maintainer's settings and
  neither connects to a provider. A ``first`` launch gets an empty profile of
  its own (first run: nothing compiled, nothing saved); a ``returning`` launch
  reuses one profile whose preferences a setup launch saved.
* Ready is defined by ``floor_launch_probe``: headless, when Blender runs the
  ``--python`` script after its startup; with a window, the turn after the
  first frame, plus a reading ``--idle-s`` later for deferred startup work.
  Wall time is from just before the process starts; instructions and cycles
  are the process's own, every thread, from its start.
* Launches alternate sides in ABBA order per round, and each starts only when
  the 1-minute load average is below 2.0 (``perf_counters.wait_for_quiet``).
  A launch that times out or fails is recorded and counted, never dropped.
* One trace launch per side and profile kind (never timed) installs
  ``floor_addon_trace`` and reports every add-on enable and disable.

True cold launches (after the OS dropped its file cache) need ``sudo purge``;
``--purge`` runs ``sudo -n purge`` before every ``first`` launch when allowed
and records whether it did. Writes ``DIR/floor-launch.json``.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import perf_counters  # noqa: E402

LAUNCH_TIMEOUT_S = 180
SIDES = ("angelo", "stock")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--angelo", required=True)
    parser.add_argument("--stock", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--launches", type=int, default=7, help="timed launches per side, mode and profile kind")
    parser.add_argument("--modes", default="headless,window")
    parser.add_argument("--kinds", default="first,returning")
    parser.add_argument("--idle-s", type=float, default=5.0)
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--rows", action="store_true", help="also run bench_floor_blender (listing, cut) ABAB")
    parser.add_argument("--rows-objects", default="0,1000,5000,10000")
    parser.add_argument("--quiet-wait-s", type=int, default=1200)
    parser.add_argument("--note", default="", help="recorded with every row (for example what the binaries carry)")
    args = parser.parse_args(argv)
    args.mode_list = [mode for mode in args.modes.split(",") if mode]
    args.kind_list = [kind for kind in args.kinds.split(",") if kind]
    if not set(args.mode_list) <= {"headless", "window"} or not set(args.kind_list) <= {"first", "returning"}:
        parser.error("Modes are headless and window; kinds are first and returning")
    if not 1 <= args.launches <= 30:
        parser.error("Use 1-30 launches")
    return args


def sandbox(box):
    """Every writable location inside ``box``; no inherited Blender or Angelo setting (the GUI suites' sandbox)."""
    home = box / "home"
    for folder in (home / ".config", home / ".cache", home / "Library/Caches", box / "tmp", box / "user/config",
                   box / "user/scripts/startup", box / "user/scripts/modules"):
        folder.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BLENDER_", "ANGELO_", "SENSEI_", "TOONLAB_", "PYTHON"))}
    env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), TMPDIR=str(box / "tmp"), TMP=str(box / "tmp"),
               TEMP=str(box / "tmp"), XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
               BLENDER_USER_RESOURCES=str(box / "user"), BLENDER_USER_CONFIG=str(box / "user/config"),
               BLENDER_USER_SCRIPTS=str(box / "user/scripts"), BLENDER_USER_DATAFILES=str(box / "user/datafiles"),
               BLENDER_USER_EXTENSIONS=str(box / "user/extensions"), ANGELO_DISABLE_KEYCHAIN="1",
               ANGELO_AGENT_RUNTIME="off", ANGELO_STATE_DIR=str(box / "state"),
               ANGELO_CONNECTIONS_DIR=str(box / "accounts"), ANGELO_LIBRARY_DIR=str(box / "library"),
               PATH="/usr/bin:/bin:/usr/sbin:/sbin")
    return env


def install_trace(box):
    shutil.copy2(HERE / "floor_addon_trace.py", box / "user/scripts/startup/floor_addon_trace.py")
    shutil.copy2(HERE / "perf_counters.py", box / "user/scripts/modules/perf_counters.py")


def launch(binary, box, mode, out, idle_s, extra=()):
    """One launch to ready; returns its row (probe readings, wall time, exit) or an error row."""
    command = [str(binary)] + (["--background"] if mode == "headless" else []) + [
        "--python", str(HERE / "floor_launch_probe.py"), "--", "--mode", mode, "--out", str(out),
        "--idle-s", str(idle_s), *extra]
    out.unlink(missing_ok=True)
    started = time.monotonic_ns()
    try:
        completed = subprocess.run(command, env=sandbox(box), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=LAUNCH_TIMEOUT_S + idle_s)
        code = completed.returncode
    except subprocess.TimeoutExpired:
        return {"error": "timed out after %ds" % (LAUNCH_TIMEOUT_S + idle_s)}
    exited = time.monotonic_ns()
    if not out.is_file():
        return {"error": "no probe report (exit %s)" % code}
    probe = json.loads(out.read_text(encoding="utf-8"))
    ready = probe.get("ready") or {}
    row = {"exit": code, "exit_ms": round((exited - started) / 1e6, 1), "probe": probe}
    if ready.get("monotonic_ns"):
        row["ready_ms"] = round((ready["monotonic_ns"] - started) / 1e6, 1)
        row["instructions"], row["cycles"], row["pageins"] = ready["instructions"], ready["cycles"], ready["pageins"]
    idle = probe.get("idle")
    if idle and ready.get("instructions") is not None and idle.get("instructions") is not None:
        row["after_first_frame_instructions"] = idle["instructions"] - ready["instructions"]
    return row


def purge(enabled):
    if not enabled:
        return None
    try:
        return subprocess.run(["sudo", "-n", "purge"], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def describe(rows, key):
    values = [row[key] for row in rows if row.get(key) is not None]
    return perf_counters.describe(values)


def summary(rows):
    """Per side, mode and kind: launch statistics over every successful launch, failures counted."""
    result = {}
    for side in SIDES:
        for row in rows:
            if row["side"] != side:
                continue
            key = "%s/%s/%s" % (side, row["mode"], row["kind"])
            result.setdefault(key, []).append(row)
    table = {}
    for key, found in sorted(result.items()):
        table[key] = {"launches": len(found), "failed": sum(1 for row in found if "error" in row),
                      "ready_ms": describe(found, "ready_ms"), "instructions": describe(found, "instructions"),
                      "after_first_frame_instructions": describe(found, "after_first_frame_instructions")}
    for mode_kind in sorted({key.split("/", 1)[1] for key in table}):
        angelo, stock = table.get("angelo/" + mode_kind), table.get("stock/" + mode_kind)
        if angelo and stock:
            for metric in ("ready_ms", "instructions"):
                if angelo[metric] and stock[metric] and stock[metric]["median"]:
                    table.setdefault("ratio/" + mode_kind, {})[metric] = round(
                        angelo[metric]["median"] / stock[metric]["median"], 3)
    return table


def wait(args, report, label):
    quiet, load = perf_counters.wait_for_quiet(perf_counters.QUIET_LOAD, args.quiet_wait_s)
    report["loads"].append({"before": label, "load_1m": load, "quiet": quiet})
    return quiet


def run_launches(args, out, binaries, report):
    returning = {side: out / "profiles" / side / "returning" for side in SIDES}
    for side in SIDES:
        if "returning" in args.kind_list:   # The setup launch: preferences saved, so later launches read them.
            setup = launch(binaries[side], returning[side], "headless", out / "probe-setup.json", 0, ("--save-userpref",))
            report["setup"][side] = {key: value for key, value in setup.items() if key != "probe"}
    for round_index in range(args.launches):
        order = SIDES if round_index % 2 == 0 else tuple(reversed(SIDES))   # ABBA across rounds.
        for kind in args.kind_list:
            for mode in args.mode_list:
                for side in order:
                    box = (out / "profiles" / side / ("first-%02d-%s" % (round_index, mode)) if kind == "first"
                           else returning[side])
                    label = "%s/%s/%s/%d" % (side, mode, kind, round_index)
                    if not wait(args, report, label):
                        report["launches"].append({"side": side, "mode": mode, "kind": kind, "round": round_index,
                                                   "error": "machine not quiet"})
                        continue
                    purged = purge(args.purge) if kind == "first" else None
                    row = launch(binaries[side], box, mode, out / "probe.json", args.idle_s)
                    row.update(side=side, mode=mode, kind=kind, round=round_index, purged=purged, note=args.note)
                    if kind == "first":
                        shutil.rmtree(box, ignore_errors=True)   # Compiled caches and state: up to ~100 MB each.
                    report["launches"].append(row)
                    print("FLOOR_LAUNCH", label, row.get("ready_ms"), row.get("instructions"), row.get("error", ""),
                          flush=True)
    for side in SIDES:   # Trace launches: never timed.
        for kind in args.kind_list:
            box = out / "profiles" / side / ("trace-first" if kind == "first" else "returning")
            install_trace(box)
            row = launch(binaries[side], box, "headless", out / "probe-trace.json", 0)
            report["addon_trace"]["%s/%s" % (side, kind)] = (row.get("probe") or {}).get("addon_trace") or row.get("error")
            (box / "user/scripts/startup/floor_addon_trace.py").unlink(missing_ok=True)
            (box / "user/scripts/modules/perf_counters.py").unlink(missing_ok=True)


def run_rows(args, out, binaries, report):
    """bench_floor_blender in both binaries, A B A B per phase; the Angelo binary prepares the listing files once."""
    script = str(HERE / "bench_floor_blender.py")
    for phase in ("listing", "cut", "bulk"):
        for index, side in enumerate(("angelo", "stock", "angelo", "stock")):
            folder = out / "rows" / ("%s-%d" % (side, index))
            extra = ["--prepare"] if phase == "listing" and index == 0 else []
            phase_args = (["--objects", args.rows_objects] if phase == "cut" else
                          ["--runs", "3"] if phase == "bulk" else [])
            # No --factory-startup: the sandboxed profile is already factory, and Angelo's add-on starts as a
            # person's does (its handlers active), which --factory-startup could change.
            command = [str(binaries[side]), "-b", "--python", script, "--", "--out",
                       str(out / "rows"), "--phase", phase, *extra, *phase_args]
            label = "rows %s %s %d" % (phase, side, index)
            if not wait(args, report, label):
                report["rows"].append({"phase": phase, "side": side, "run": index, "error": "machine not quiet"})
                continue
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "benchmark.log", "w", encoding="utf-8") as log:
                code = subprocess.run(command, env=sandbox(folder), stdout=log, stderr=subprocess.STDOUT,
                                      timeout=7200).returncode
            result = out / "rows" / ("floor-%s-%s.json" % (phase, side))
            row = {"phase": phase, "side": side, "run": index, "exit": code, "note": args.note}
            if code == 0 and result.is_file():
                row["report"] = json.loads(result.read_text(encoding="utf-8"))
                result.rename(folder / result.name)
            else:
                row["error"] = "exit %s; see %s" % (code, folder / "benchmark.log")
            report["rows"].append(row)
            print("FLOOR_ROWS", label, row.get("error", "ok"), flush=True)


def main(argv=None):
    args = arguments(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    binaries = {"angelo": Path(args.angelo).resolve(), "stock": Path(args.stock).resolve()}
    report = {"method": "bench_floor_launch (see its docstring)", "note": args.note, "binaries": {side: str(path) for side, path in
              binaries.items()}, "launches": [], "setup": {}, "addon_trace": {}, "loads": [], "rows": [],
              "machine": {"cores": os.cpu_count(), "platform": sys.platform}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        run_launches(args, out, binaries, report)
        if args.rows:
            run_rows(args, out, binaries, report)
    finally:
        report["summary"] = summary(report["launches"])
        report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (out / "floor-launch.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("FLOOR_LAUNCH_DONE", out / "floor-launch.json", json.dumps(report["summary"].get("ratio/headless/first")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
