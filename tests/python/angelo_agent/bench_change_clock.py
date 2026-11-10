# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What ``angelo_change_clock`` costs, on Windows, in the user-facing operations it touches: before and after the fix.

A witness now settles (waits, at most one ~15.6 ms clock tick) before Angelo relies on it, so the fix can add latency
wherever a file was stamped in the current tick. This measures that on the real code path, without Blender:

* **Arms** on the SAME tree, each in a FRESH Python process, interleaved off, on, off, on ... (``--pairs``). ``off``
  patches ``angelo_change_clock.settle`` to return its witness, ``settle_all`` to return, ``settled`` to True and
  ``cache_key``/``settled_key`` to their key: the pre-fix behaviour, as those calls are all the fix added here.
  ``on`` is as shipped. Nothing else differs.
* **Operations**, each timed with ``perf_counter_ns``, ``--reps`` times per process after ``--warmup`` untimed ones,
  each repetition in a new checkpoint profile primed (untimed) by a tiny capture and restore:
  ``capture_<size>_<when>``, a Sculpt (v4) pair captured end to end through the real anchored backend
  (``checkpoint_capture.CaptureOwner``: reserve, ``_native_begin``, BASE and RUNTIME through ``_native_output``,
  ``publish``, ``finish``, ``_native_end``, ``abandon``) as ``test_native_checkpoint_capture_publication`` drives it,
  with its stand-in native witness (``_bpy._angelo_checkpoint.capture_facts``); ``.publish_finish`` is that part.
  ``restore_prep_<size>_<when>``, ``checkpoint_recovery.CheckpointRecovery.prepare`` of that capture, as
  ``test_native_checkpoint_recovery_resources`` drives it (its working copies are always freshly written).
  ``capture_<size>_<when>.publish`` is ``publish`` alone: one settle of both components before they are hashed, the
  hashing, and one settle of both sealed components once both are linked, as its last step.
  ``snapshot_take_<when>`` is ``export_snapshot.take`` of a small saved file (the copy an export
  makes, and its identity); ``mirror_read_<when>`` is a worker chat mirror's whole read while its worker hosts the
  chat (``worker_mirror_journal.MirrorStore._read_whole``, under the store's ``io`` lock, no settle) and
  ``mirror_load_<when>`` the same read after a hand-off (it settles: a save writes over what it read).
  ``volume_listing`` is one full listing of the volumes (``angelo_change_clock._volume_steps``, every volume asked
  at once), which the app runs on a thread of its own once per process and when a new device is first seen;
  ``volume_recheck`` the same with the answers kept (a share seen again: the names are listed, nothing is asked).
  Each child lists once before its first settle.
  ``file_read_<when>`` (``files.FileWorkspace.execute("angelo_read_file")``, so ``_read``), ``write_precondition_<when>``
  (``FileWorkspace.precondition("angelo_write_file")``) and ``journal_load_<when>`` (``session_journal.read``), each
  of a new small file. Sizes: ``--small-bytes`` and ``--large-bytes`` per capture component.
* ``<when>``: ``fresh`` runs the operation right after its file was written (a stamp in the current tick, the worst
  case, where a settle waits); ``steady`` first waits, untimed and in both arms, until the unpatched ``settled`` says
  the clock passed every stamp. A capture's wait sits between its writes and ``publish`` and is excluded from its time.
* **Output** (``--out``, and a ``.summary.txt`` beside it): per arm and operation every sample, n, median, p95,
  quartiles, per-process medians and the settle counters (``waits``, ``waited_s``; zero in ``off`` by construction);
  the deltas (on - off, ms and %); Python, Windows, CPU count, the timer resolution (``NtQueryTimerResolution``,
  read-only), the processes holding it raised (``powercfg /energy``'s outstanding timer requests, read-only, skipped
  when unavailable, said when unreadable), ``capture_*.publish``'s own settle waits (waits x the tick is their cost
  at any resolution) and the observed ``GetSystemTimeAsFileTime`` step before and after; each process's outcome. No
  process starts after ``--max-seconds`` (counted once those host facts are read) and a running one stops before its
  next repetition (``capped``). Every sample is
  kept; a failed operation is counted per arm, never dropped.

Off Windows both arms are identical by design (nothing waits there): it refuses to run without
``--allow-non-windows``, which only smoke-tests the harness. Standard library only.
"""

import argparse
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import types

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/modules"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_change_clock as change_clock  # noqa: E402

SESSION = "00000000-0000-4000-8000-000000000001"
ARMS = ("off", "on")
KILL_GRACE_S = 60     # A process still running this long after the cap is killed (its samples are lost, and said so).
SETTLE_WAIT_S = 2.0   # The harness's own untimed wait for a steady stamp; far past any tick.
_SETTLED = change_clock.settled   # Unpatched, captured before an arm patches it: the harness's steady-state question.


def timer_resolution():
    """``NtQueryTimerResolution`` in ms: coarsest, finest and current system timer interval (None off Windows)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        values = [ctypes.c_ulong() for _ in range(3)]
        status = ctypes.WinDLL("ntdll").NtQueryTimerResolution(*(ctypes.byref(value) for value in values))
        if status != 0:
            return {"error": "NTSTATUS 0x%08x" % (status & 0xFFFFFFFF)}
        first, second, current = (value.value / 10_000 for value in values)
        return {"coarsest_ms": max(first, second), "finest_ms": min(first, second), "current_ms": current}
    except (OSError, AttributeError) as error:
        return {"error": str(error)}


def observed_clock_step(seconds=0.25):
    """The median step of ``GetSystemTimeAsFileTime`` (the clock NTFS stamps with) over ``seconds``, in ms."""
    if os.name != "nt":
        return None
    steps, last, until = [], change_clock.system_time(), time.perf_counter() + seconds
    while time.perf_counter() < until:
        now = change_clock.system_time()
        if now != last:
            steps.append((now - last) / 10_000)
            last = now
    return {"median_step_ms": statistics.median(steps) if steps else None, "steps_seen": len(steps)}


ENERGY_KEYS = ("Requested Period", "Requesting Process ID", "Requesting Process Path",
               "Current Timer Resolution (100ns units)", "Maximum Timer Period (100ns units)")


def parse_timer_requests(report):
    """From a ``powercfg /energy`` HTML report: the platform timer resolution and every outstanding request to raise
    it (the processes holding the tick finer), as ``{"platform": {...}, "requests": [{...}, ...]}``."""
    import html
    import re
    lines = [line.strip() for line in html.unescape(re.sub(r"<[^>]+>", "\n", report)).splitlines() if line.strip()]
    found, current = {"platform": {}, "requests": []}, None
    for index, line in enumerate(lines):
        if line.startswith("Platform Timer Resolution:"):
            current = {} if "Outstanding Timer Request" in line else found["platform"]
            if "Outstanding Timer Request" in line:
                found["requests"].append(current)
        elif re.match(r"[A-Z][A-Za-z /]+:[A-Z]", line):
            current = None   # Another category's finding ("USB Suspend:...") began.
        elif current is not None and line in ENERGY_KEYS and index + 1 < len(lines):
            current[line] = lines[index + 1]
    return found


def timer_holders(seconds=5):
    """Who holds the system timer resolution raised, read-only: a ``powercfg /energy`` trace of ``seconds``
    (administrator; the validation VM runs as LocalSystem). None when it cannot be read. Never asks for another
    resolution (no ``timeBeginPeriod``) and changes no setting."""
    if os.name != "nt":
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="angelo-energy-") as folder:
            report = Path(folder) / "energy.html"
            subprocess.run(["powercfg", "/energy", "/duration", str(seconds), "/output", str(report)],
                           capture_output=True, timeout=seconds + 120)
            if not report.is_file():
                return None
            return parse_timer_requests(report.read_text(encoding="utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def commit():
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20)
        return done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def host_facts():
    windows = sys.getwindowsversion() if hasattr(sys, "getwindowsversion") else None
    return {"python": sys.version.split()[0], "python_full": sys.version, "executable_name": Path(sys.executable).name,
            "platform": platform.platform(), "windows_build": None if windows is None else
            {"major": windows.major, "minor": windows.minor, "build": windows.build},
            "cpu_count": os.cpu_count(), "process_cpu_count": getattr(os, "process_cpu_count", os.cpu_count)(),
            "coarse": change_clock.COARSE, "settle_limit_s": change_clock.LIMIT_S, "settle_step_s": change_clock.STEP_S}


class NativeWitness:
    """The stand-in native witness the capture publication tests bind (``_bpy._angelo_checkpoint.capture_facts``),
    with native's content binding (``capture_hasher``/``capture_bind``, ``fake_native_binding``): reset per capture."""

    def __init__(self):
        self.capability, self.owner, self.facts = object(), None, None
        self.binding = None

    def begin_binding(self):
        from fake_native_binding import FakeContentBinding
        self.binding = FakeContentBinding()
        self.capture_hasher, self.capture_bind = self.binding.capture_hasher, self.binding.capture_bind
        return self.binding

    def capture_facts(self, capability, owner):
        if capability is not self.capability or owner is not self.owner:
            raise ValueError("Not the bound native witness")
        return dict(self.facts)


def apply_arm(arm):
    """``off``: the three calls the fix added return what the pre-fix code had, at once. ``on``: untouched."""
    if arm == "off":
        change_clock.settle = lambda witness, clock=None, **_options: witness
        change_clock.settle_all = lambda witnesses, clock=None, **_options: None
        change_clock.settled = lambda witness, clock=None, **_options: True
        change_clock.cache_key = change_clock.settled_key = lambda witness, key, **_options: key


def write_all(descriptor, data):
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view):]


def wait_settled(witnesses):
    """Untimed: until the file-system clock is past every witness's stamp (the unpatched helper's question)."""
    limit = time.monotonic() + SETTLE_WAIT_S
    while not all(_SETTLED(witness()) for witness in witnesses):
        if time.monotonic() > limit:
            raise RuntimeError("A stamp stayed in the current tick for %.1f s" % SETTLE_WAIT_S)
        time.sleep(0.001)


def path_witnesses(path):
    """A path's stat (modification time) and an open handle's (the change time, on CPython 3.13 Windows)."""
    def opened():
        with open(path, "rb") as handle:
            return os.fstat(handle.fileno())
    return [lambda: os.stat(path), opened]


class Bench:
    def __init__(self, temp, args, native):
        from angelo_agent import checkpoint_capture, checkpoint_filesystem, checkpoint_recovery, export_snapshot
        from angelo_agent import session_journal, worker_journal_owner, worker_mirror_journal
        from angelo_agent.checkpoints import CheckpointStore
        from angelo_agent.files import FileWorkspace
        self.capture_api, self.filesystem, self.recovery = checkpoint_capture, checkpoint_filesystem, checkpoint_recovery
        self.journal, self.Store, self.snapshot = session_journal, CheckpointStore, export_snapshot
        self.mirror, self.journal_owner = worker_mirror_journal, worker_journal_owner
        self.temp, self.args, self.native = temp, args, native
        self.workspace_root = temp / "workspace"
        self.workspace_root.mkdir()
        self.files = FileWorkspace(self.workspace_root.resolve())
        self.payloads = {"small": (os.urandom(args.small_bytes), os.urandom(args.small_bytes)),
                         "large": (os.urandom(args.large_bytes), os.urandom(args.large_bytes))}
        self.text = ("Angelo change clock bench line.\n" * 128).encode()   # 4 KiB of UTF-8 text.
        self.chat = (json.dumps({"version": 1, "messages": [{"role": "user", "text": "hello"},
                                                           {"role": "assistant", "text": "hi"}]}) + "\n").encode()
        self.samples, self.errors, self.cleanup_failures = [], {}, 0

    def timed(self, op, timed, function):
        """Run ``function`` (it returns the ``[(op, ns)]`` it timed itself, excluding harness waits); record each in
        ms with the settle counters' change over the call (the harness's own waits never count there)."""
        waits, waited = change_clock.waits, change_clock.waited_s
        started = time.perf_counter_ns()
        try:
            spent = function()
        except Exception as error:   # Counted per operation and arm; the rest of the repetition goes on.
            if timed:
                entry = self.errors.setdefault(op, {"count": 0, "first": "%s: %s" % (type(error).__name__, error)})
                entry["count"] += 1
            return False
        elapsed = time.perf_counter_ns() - started
        if timed:
            for entry in ([(op, elapsed)] if spent is None else spent):
                # (op, ns), or (op, ns, waits, waited_s) for a part that counted its own settles (``.publish``).
                own = entry[2:] or (change_clock.waits - waits, change_clock.waited_s - waited)
                self.samples.append({"op": entry[0], "ms": entry[1] / 1e6, "waits": own[0], "waited_ms": own[1] * 1000})
        return True

    def capture(self, store, base, runtime, steady, op):
        """One pair capture; returns [(op, total ns without the harness wait), (op.publish_finish, ns)]."""
        started = time.perf_counter_ns()
        owner = self.capture_api.CaptureOwner.reserve(store)
        try:
            identity, _path = owner._native_begin()
            descriptors = []
            binding = self.native.begin_binding()
            for role, data in (("BASE", base), ("RUNTIME", runtime)):
                descriptor = owner._native_output(role)
                if role == "BASE":
                    owner._native_binding()   # Native asks before writing BASE; a pair binds both whatever it says.
                write_all(descriptor, data)
                binding.wrote(self.native.capability, role, data)
                descriptors.append(descriptor)
            self.native.owner = owner
            self.native.facts = {"capture_id": identity, "scene_bytes": len(base), "runtime_bytes": len(runtime),
                                 "owner_count": 2, "format_version": 1, "post_state": "pending"}
            written = time.perf_counter_ns()
            if steady:
                wait_settled([lambda fd=fd: self.filesystem.descriptor_witness(fd) for fd in descriptors])
            publishing = time.perf_counter_ns()
            waits, waited = change_clock.waits, change_clock.waited_s
            owner.publish(self.native.capability)
            published = time.perf_counter_ns()
            publish_waits, publish_waited = change_clock.waits - waits, change_clock.waited_s - waited
            self.native.facts["post_state"] = "complete"
            record = owner.finish(self.native.capability)
            owner._native_end()
            owner.abandon()
            finished = time.perf_counter_ns()
        finally:
            if owner._resource is not None and owner._resource.native_active:
                owner._native_end()
            owner.abandon()
        if record["capture_validation"] != "VALIDATED":
            raise ValueError("The capture finished %s, not VALIDATED" % record["capture_validation"])
        self.last_capture = identity
        total = (written - started) + (finished - publishing)
        return [(op, total), (op + ".publish_finish", finished - publishing),
                (op + ".publish", published - publishing, publish_waits, publish_waited)]

    def restore(self, store, steady, op):
        identity = self.last_capture
        if steady:
            folder = store.folder(identity)
            wait_settled(path_witnesses(folder / "scene.blend") + path_witnesses(folder / "sculpt.runtime"))
        started = time.perf_counter_ns()
        owner = self.recovery.CheckpointRecovery.prepare(store, identity)
        spent = time.perf_counter_ns() - started
        try:
            if owner.path.stat().st_size != owner.record["scene_bytes"]:
                raise ValueError("The recovery working copy has the wrong size")
        finally:
            owner.close()
        return [(op, spent)]

    def file_op(self, kind, name, steady, op):
        """A file tool's read or write precondition, or a chat load, of a file just written (or settled)."""
        path = self.workspace_root / name
        path.write_bytes(self.chat if kind in ("journal_load", "mirror_read", "mirror_load") else self.text)
        if steady:
            wait_settled(path_witnesses(path))
        started = time.perf_counter_ns()
        if kind == "snapshot_take":
            target = path.with_name(path.name + ".copy")
            ok = self.snapshot.take(str(path), str(target)) is not None
            spent = time.perf_counter_ns() - started
            self.remove(target)
            if not ok:
                raise ValueError("The snapshot copy has no identity")
            return [(op, spent)]
        if kind in ("mirror_read", "mirror_load"):
            store = object.__new__(self.mirror.MirrorStore)   # Only what _read_whole keeps: the real read's code.
            store._tail, store.whole_reads, store._settle, store.session_id = None, 0, False, "bench"
            hosted, self.journal_owner.hosted = self.journal_owner.hosted, (lambda _id: kind == "mirror_read")
            try:
                raw = self.mirror.MirrorStore._read_whole(store, path)
            finally:
                self.journal_owner.hosted = hosted
            ok = len(raw["messages"]) == 2 and store._tail is not None
        elif kind == "file_read":
            done = self.files.execute("angelo_read_file", {"path": name, "start": 0, "length": len(self.text)})
            ok = done["bytes"] == len(self.text)
        elif kind == "write_precondition":
            ok = self.files.precondition("angelo_write_file", {"path": name}) != ("missing",)
        else:
            raw, info = self.journal.read(path)
            ok = len(raw["messages"]) == 2 and info.identity is not None   # Trusted: no change seen during it.
        spent = time.perf_counter_ns() - started
        if not ok:
            raise ValueError("The %s did not observe the file it was given" % kind)
        return [(op, spent)]

    def remove(self, path):
        def retry(function, target, _error):
            os.chmod(target, 0o700)
            function(target)
        try:
            if path.is_dir() and sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=retry)
            elif path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError:
            self.cleanup_failures += 1

    def rep(self, index, timed):
        tag = "%s-%d" % ("timed" if timed else "warm", index)
        profile = self.temp / ("profile-" + tag)
        store = self.Store(profile, SESSION)
        tiny = (b"B" * 64, b"R" * 64)   # Priming, untimed: the profile's seal key, ledgers and recoveries folder.
        if self.timed("prime", False, lambda: self.capture(store, *tiny, False, "prime")):
            self.timed("prime", False, lambda: self.restore(store, False, "prime"))
        for size in ("small", "large"):
            for when in ("fresh", "steady"):
                steady = when == "steady"
                name = "capture_%s_%s" % (size, when)
                if self.timed(name, timed, lambda: self.capture(store, *self.payloads[size], steady, name)):
                    self.timed("restore_prep_%s_%s" % (size, when), timed,
                               lambda: self.restore(store, steady, "restore_prep_%s_%s" % (size, when)))
        names = []
        for when in ("fresh", "steady"):
            steady = when == "steady"
            for kind in ("file_read", "write_precondition", "journal_load", "mirror_read", "mirror_load",
                         "snapshot_take"):
                json_file = kind in ("journal_load", "mirror_read", "mirror_load")
                name = "%s-%s-%s.%s" % (kind, tag, when, "json" if json_file else "md")
                names.append(name)
                self.timed(kind + "_" + when, timed, lambda: self.file_op(kind, name, steady, kind + "_" + when))
        if os.name == "nt":   # A full listing's cost (every volume asked, in parallel), on a thread in the app.
            self.timed("volume_listing", timed, lambda: (change_clock._answers.clear(), change_clock._volume_steps(),
                                                         None)[2])
            # A re-check's (a share seen again a minute later): the names are listed, no volume is asked.
            self.timed("volume_recheck", timed, lambda: (change_clock._volume_steps(), None)[1])
        self.remove(profile)
        for name in names:
            self.remove(self.workspace_root / name)


def child(args):
    from angelo_agent import change_clock as alias
    if alias is not change_clock:
        raise SystemExit("angelo_agent.change_clock is not angelo_change_clock: the arms would not differ")
    native = NativeWitness()
    sys.modules["_bpy"] = types.SimpleNamespace(_angelo_checkpoint=native)
    apply_arm(args.arm)
    change_clock.prime()     # As the add-on's registration does: the volumes are known before the first settle.
    change_clock.listed(30)
    started, stopped_early, done = time.time(), False, 0
    with tempfile.TemporaryDirectory(prefix="angelo-clock-bench-", dir=args.work_dir or None,
                                     ignore_cleanup_errors=True) as temp:
        bench = Bench(Path(temp).resolve(), args, native)
        for index in range(args.warmup):
            bench.rep(index, timed=False)
        for index in range(args.reps):
            if time.time() >= args.deadline:
                stopped_early = True
                break
            bench.rep(index, timed=True)
            done += 1
    result = {"arm": args.arm, "reps_done": done, "stopped_early": stopped_early, "wall_s": time.time() - started,
              "samples": bench.samples, "errors": bench.errors, "cleanup_failures": bench.cleanup_failures,
              "waits_total": change_clock.waits, "waited_s_total": change_clock.waited_s}
    Path(args.child_out).write_text(json.dumps(result), encoding="utf-8")


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summarize(samples):
    values = [sample["ms"] for sample in samples]
    waits = sum(sample["waits"] for sample in samples)
    return {"n": len(values), "median_ms": statistics.median(values), "p95_ms": percentile(values, 0.95),
            "p25_ms": percentile(values, 0.25), "p75_ms": percentile(values, 0.75), "min_ms": min(values),
            "max_ms": max(values), "mean_ms": statistics.fmean(values),
            "settle": {"waits": waits, "waited_ms": sum(sample["waited_ms"] for sample in samples),
                       "waits_per_op": waits / len(values),
                       "ops_that_waited": sum(1 for sample in samples if sample["waits"])},
            "samples_ms": values}


def aggregate(processes):
    arms = {}
    for arm in ARMS:
        by_op, per_process = {}, {}
        for process in processes:
            if process["arm"] != arm or not process.get("samples"):
                continue
            mine = {}
            for sample in process["samples"]:
                by_op.setdefault(sample["op"], []).append(sample)
                mine.setdefault(sample["op"], []).append(sample["ms"])
            for op, values in mine.items():
                per_process.setdefault(op, []).append(statistics.median(values))
        arms[arm] = {op: dict(summarize(samples), per_process_median_ms=per_process[op])
                     for op, samples in sorted(by_op.items())}
        errors = {}
        for process in (process for process in processes if process["arm"] == arm):
            for op, entry in (process.get("errors") or {}).items():
                errors.setdefault(op, {"count": 0, "first": entry["first"]})["count"] += entry["count"]
        arms[arm + "_errors"] = errors
    deltas = {}
    for op in sorted(set(arms["off"]) & set(arms["on"])):
        off, on = arms["off"][op], arms["on"][op]
        deltas[op] = {key + "_delta_ms": on[key + "_ms"] - off[key + "_ms"] for key in ("median", "p95")}
        for key in ("median", "p95"):
            base = off[key + "_ms"]
            deltas[op][key + "_delta_pct"] = 100 * (on[key + "_ms"] - base) / base if base else None
        deltas[op]["n_off"], deltas[op]["n_on"] = off["n"], on["n"]
    return arms, deltas


def summary_text(report):
    lines = ["Angelo change clock bench: %s, Python %s, %s CPUs, commit %s" % (
        report["host"]["platform"], report["host"]["python"], report["host"]["cpu_count"], report["commit"] or "?")]
    lines.append("timer resolution %s; GetSystemTimeAsFileTime step %s" % (
        report["host"]["timer_resolution_before"], report["host"]["clock_step_before"]))
    if report["identical_arms_by_design"]:
        lines.append("NOT WINDOWS: both arms are identical by design; this was only a harness smoke test.")
    lines.append("%-36s %9s %9s %9s %8s %9s %9s %9s %7s" % (
        "operation (ms)", "off med", "on med", "d med", "d med%", "off p95", "on p95", "d p95", "waits"))
    for op, delta in report["deltas"].items():
        off, on = report["arms"]["off"][op], report["arms"]["on"][op]
        pct = delta["median_delta_pct"]
        lines.append("%-36s %9.3f %9.3f %+9.3f %7s %9.3f %9.3f %+9.3f %7.2f" % (
            op, off["median_ms"], on["median_ms"], delta["median_delta_ms"], "-" if pct is None else "%+.1f" % pct,
            off["p95_ms"], on["p95_ms"], delta["p95_delta_ms"], on["settle"]["waits_per_op"]))
    lines.append("waits = settles that waited per operation in 'on' (0 in 'off' by construction); n per arm: %s" % (
        ", ".join("%s %d" % (arm, sum(p["reps_done"] for p in report["processes"] if p["arm"] == arm and p["ok"]))
                  for arm in ARMS) + " repetitions"))
    holders = report["host"].get("timer_holders")
    if holders is not None and not holders["platform"] and not holders["requests"]:
        # powercfg ran but nothing was parsed (a localized Windows, a changed layout): say so, never "nobody".
        lines.append("timer resolution holders: powercfg's report was unreadable (no platform or request entries)")
    elif holders:
        lines.append("timer resolution held raised by: %s; platform %s" % (
            "; ".join("PID %s %s (%s x100ns)" % (entry.get("Requesting Process ID", "?"),
                                                  entry.get("Requesting Process Path", "?"),
                                                  entry.get("Requested Period", "?")) for entry in holders["requests"])
            or "nobody", holders["platform"] or "?"))
    current = (report["host"].get("timer_resolution_before") or {}).get("current_ms")
    for op, on in sorted(report["arms"]["on"].items()):
        if op.endswith(".publish") and current:
            # A settle waits at most one tick, so its cost at any resolution is about waits x that tick.
            lines.append("%s: %.2f waits per publish x %.3f ms tick = up to %.2f ms (measured %.2f ms waited)" % (
                op, on["settle"]["waits_per_op"], current, on["settle"]["waits_per_op"] * current,
                on["settle"]["waited_ms"] / max(1, on["n"])))
    for arm in ARMS:
        if report["arms"][arm + "_errors"]:
            lines.append("ERRORS in %s: %s" % (arm, json.dumps(report["arms"][arm + "_errors"])))
    failed = [p for p in report["processes"] if not p["ok"]]
    if failed:
        lines.append("FAILED processes: %s" % "; ".join("%s #%d: %s" % (p["arm"], p["pair"], p["error"]) for p in failed))
    if report["capped"]:
        lines.append("CAPPED at --max-seconds %d: %s" % (report["settings"]["max_seconds"], report["cap_note"]))
    return "\n".join(lines)


def run_process(args, arm, pair, deadline, scratch):
    out = scratch / ("%s-%d.json" % (arm, pair))
    command = [sys.executable, str(Path(__file__).resolve()), "--arm", arm, "--child-out", str(out),
               "--deadline", repr(deadline), "--reps", str(args.reps), "--warmup", str(args.warmup),
               "--small-bytes", str(args.small_bytes), "--large-bytes", str(args.large_bytes)]
    if args.work_dir:
        command += ["--work-dir", args.work_dir]
    if args.allow_non_windows:
        command.append("--allow-non-windows")
    entry = {"arm": arm, "pair": pair, "ok": False, "error": None, "reps_done": 0, "stopped_early": False}
    started = time.time()
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=max(1.0, deadline - time.time()) + KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        entry.update(error="killed %d s after the cap; its samples are lost" % KILL_GRACE_S, wall_s=time.time() - started)
        return entry
    entry["wall_s"] = time.time() - started
    if done.returncode != 0 or not out.is_file():
        entry["error"] = "exit %d: %s" % (done.returncode, (done.stderr or done.stdout)[-2000:])
        return entry
    entry.update(json.loads(out.read_text(encoding="utf-8")), ok=True)
    return entry


def parent(args):
    started = time.time()
    host = dict(host_facts(), timer_resolution_before=timer_resolution(), clock_step_before=observed_clock_step(),
                timer_holders=timer_holders())   # Read-only: who keeps the tick finer (powercfg /energy), if readable.
    deadline = time.time() + args.max_seconds   # After the host facts (powercfg traces >= 5 s): the cap is the runs'.
    processes, capped, cap_note = [], False, ""
    with tempfile.TemporaryDirectory(prefix="angelo-clock-bench-parent-") as scratch:
        for pair in range(args.pairs):
            for arm in ARMS:   # Interleaved off, on, off, on ...: drift reaches both arms alike.
                if time.time() >= deadline:
                    capped, cap_note = True, "pair %d %s and later never started" % (pair, arm)
                    break
                entry = run_process(args, arm, pair, deadline, Path(scratch))
                processes.append(entry)
                print("%s pair %d: %s, %d reps, %.1f s%s" % (arm, pair, "ok" if entry["ok"] else "FAILED",
                      entry["reps_done"], entry.get("wall_s", 0), " (stopped at the cap)" if entry["stopped_early"]
                      else ""), flush=True)
                if entry["stopped_early"] and not capped:
                    capped, cap_note = True, "%s pair %d stopped after %d reps" % (arm, pair, entry["reps_done"])
            if capped:
                break
    host.update(timer_resolution_after=timer_resolution(), clock_step_after=observed_clock_step())
    arms, deltas = aggregate(processes)
    report = {"schema": "angelo-change-clock-bench/1", "commit": args.commit or commit(), "argv": sys.argv[1:],
              "started_unix": started, "wall_s": time.time() - started, "host": host,
              "identical_arms_by_design": os.name != "nt",
              "settings": {"pairs": args.pairs, "reps": args.reps, "warmup": args.warmup, "order": "off,on per pair",
                           "small_bytes": args.small_bytes, "large_bytes": args.large_bytes,
                           "max_seconds": args.max_seconds, "work_dir": args.work_dir or "(the system temp folder)",
                           "arms": {"off": "settle -> witness, settle_all -> None, settled -> True, "
                                           "cache_key/settled_key -> key (pre-fix)",
                                    "on": "angelo_change_clock as shipped"}},
              "capped": capped, "cap_note": cap_note,
              "processes": [{key: value for key, value in entry.items() if key != "samples"} for entry in processes],
              "arms": arms, "deltas": deltas}
    text = summary_text(report)
    report["summary"] = text
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    out.with_suffix(".summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    print("wrote %s" % out)
    clean = processes and all(entry["ok"] for entry in processes) and not any(arms[arm + "_errors"] for arm in ARMS)
    return 0 if clean else 1   # The JSON is written either way; a failed process or operation is named in it.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "change_clock.json"))
    parser.add_argument("--pairs", type=int, default=6)
    parser.add_argument("--reps", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--small-bytes", type=int, default=64 * 1024)
    parser.add_argument("--large-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-seconds", type=int, default=900)
    parser.add_argument("--work-dir", default="", help="Where profiles and files are written (default: the temp dir)")
    parser.add_argument("--commit", default="", help="The commit under test (default: git rev-parse HEAD)")
    parser.add_argument("--allow-non-windows", action="store_true", help="A harness smoke test: the arms are identical")
    parser.add_argument("--arm", choices=ARMS, help=argparse.SUPPRESS)
    parser.add_argument("--child-out", help=argparse.SUPPRESS)
    parser.add_argument("--deadline", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if os.name != "nt" and not args.allow_non_windows:
        print("refused: this measures a Windows-only wait; off Windows both arms are identical by design "
              "(pass --allow-non-windows for a harness smoke test)", file=sys.stderr)
        return 2
    if args.arm:
        child(args)
        return 0
    return parent(args)


if __name__ == "__main__":
    sys.exit(main())
