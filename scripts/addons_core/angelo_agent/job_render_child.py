# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A heavy Cycles render job in a child Angelo: the interface keeps its frame rate, and Cancel frees its GPU work.

Contract: doc/angelo/long_jobs.rst, "Renders in a separate process". A render inside the application shares its GPU,
memory and main loop with the interface: Blender's presentation waits on a GPU the render keeps busy, and a stop only
sets a flag that GPU work already queued never reads, so that work and its memory stay until it finishes. Ending a
process ends both. ``job_render_route`` decides which renders come here; this module runs them.

* **Copy** (``write_snapshot``): edit modes flushed as F12 does, then the job's scene and everything it reaches written
  with ``bpy.data.libraries.write`` (no save handlers run, every path made absolute) into a private folder, with the
  job's own settings applied (its output paths lead into its staging folder). The person's settings are put back at
  once (``restore_now``): their scene is theirs while the child renders the copy, and an edit meanwhile never
  reaches the render. The folder goes when the job ends.
* **Child** (``render_child.py``): this app's binary in the background, factory settings, auto-execution off, offline,
  on the copy, with Cycles on the devices this app's preferences enable. It ends itself when this app is gone, and
  every child is ended when the app quits (``_end_all``).
* **Watchdog** (``Child``): a reader thread takes the child's lines (progress, memory, device, its end) and a watch
  thread enforces the stop (terminate, then a kill ``STOP_SECONDS`` later), the time budget on its own clock
  (``budget_fired``) and memory (``job_render_route.memory_low`` while the child itself uses at least the reserve:
  stopped, ``memory``); none of them after the child reported its render's end. Nothing waits on the main
  thread, which a starved GPU can hold up.
* **The job** (``ChildPhase``, mixed into ``RenderRunner``): the same plan, staging, frames, publication and recovery
  as a render in the application; only who renders differs. Its record says ``process`` (``child`` or ``app``, with
  ``process_why``) and the copy's ``snapshot_s``, local facts never sent to a model.
"""

import atexit
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import job_render_route

CHILD = Path(__file__).with_name("render_child.py")
MARK = "ANGELO-RENDER "    # The child's own lines: JSON (render_child.MARK).
PREFIX = "angelo-render-"  # Its private folder (private_roots.SCRATCH_PREFIXES).
STOP_SECONDS = 3.0         # A stopped child that has not ended is killed this long after.
WATCH_SECONDS = 0.05       # How often the watchdog looks.
MEMORY_SECONDS = 0.5       # How often it reads memory.
KILL_WAIT = 2.0            # How long a closing document or a quit waits for a killed child to be gone.
TAIL = 40                  # The child's last plain lines, kept on the record (local) when it fails.
STALE_SECONDS = 3600       # A copy left this long by a crashed app is removed: its child read it within seconds.

MEMORY = ("Angelo stopped the render: this computer had {free:,} MB of its {total:,} MB of memory left, less than "
          "the {reserve:,} MB kept free, while the render's process used {used:,} MB. Ask the person whether to close "
          "other applications, or to render at a lower resolution")
CRASHED = "The render's process ended unexpectedly (exit status {code}{why})"

# Blender's background status line, e.g. "Fra:3 Mem:... | Rendered 1/4 Tiles, Sample 12/128": the frame's progress.
_SAMPLE = re.compile(r"Sample (\d+)/(\d+)")
_TILES = re.compile(r"Rendered (\d+)/(\d+) Tiles")

# Children that may still run: ended when the app quits (``_end_all``). Kept on ``sys`` so Reload Scripts, which makes
# this module again, still sees a child the old one started (``writing``; break-it review 2026-11-04, F10).
_running = sys.__dict__.setdefault("_angelo_render_children", set())
_swept = False             # Old copies are looked for once per process (``sweep``).


def status_fraction(line):
    """How far one frame is, from Blender's background status line; None for any other line."""
    sample = _SAMPLE.search(line)
    if sample is None or int(sample.group(2)) <= 0:
        return None
    within = min(1.0, int(sample.group(1)) / int(sample.group(2)))
    tiles = _TILES.search(line)
    if tiles is not None and int(tiles.group(2)) > 0:   # The finished tiles, then this one's samples.
        done, count = int(tiles.group(1)), int(tiles.group(2))
        return min(1.0, (min(done, count - 1) + within) / count)
    return within


def command(binary, snapshot, request, cycles_device=None):
    """The child's command line: the copy is loaded first, then ``render_child.py`` renders it."""
    tail = ["--cycles-device", cycles_device] if cycles_device else []
    return [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
            "--python-exit-code", "1", str(snapshot), "--python", str(CHILD), "--", json.dumps(request)] + tail


class Child:
    """One child Angelo rendering one job, watched from two daemon threads (never the main thread)."""

    def __init__(self, arguments, owner, deadline=None, *, folder=None, popen=None, clock=time.monotonic,
                 footprint=None, memory_low=None, total_mb=None):
        from . import child_process
        self.owner, self.deadline, self.folder, self.clock = owner, deadline, folder, clock
        # Its budget's end before any compile credit; its kernel watch as it last said (``kernels``).
        self.base_deadline, self.kernels_state = deadline, None
        self.footprint = job_render_route.footprint_mb if footprint is None else footprint
        self.memory_low = job_render_route.memory_low if memory_low is None else memory_low
        self.total_mb = job_render_route.physical_mb() if total_mb is None else total_mb
        self.lock = threading.Lock()
        self.tail = collections.deque(maxlen=TAIL)
        self.fraction, self.peak_mb, self.device, self.done = 0.0, None, None, None
        self.why = self.used = None   # Why this side ended it ("cancel", "time_budget", "memory"); the memory then.
        self.kill_at, self.killed = None, False
        self.process = (popen or child_process.popen)(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                      stdin=subprocess.DEVNULL, text=True, env=dict(os.environ))
        _running.add(self)
        self.reader = threading.Thread(target=self._read, name="angelo-render-read", daemon=True)
        self.watcher = threading.Thread(target=self._watch, name="angelo-render-watch", daemon=True)
        self.reader.start()
        self.watcher.start()

    def kernels(self):
        """``(loading, compile_s)`` of the child's render kernels, an open load carried on (``kernels.carried``)."""
        from angelo_render_device import kernels
        with self.lock:
            state, now = self.kernels_state, self.clock()
        return kernels.carried(state, now)

    def credit(self, seconds):
        """Move the watchdog's end by ``seconds`` of the child's kernel compile, at most ``kernels.CAP_S``."""
        from angelo_render_device import kernels
        with self.lock:
            if self.base_deadline is not None:
                self.deadline = self.base_deadline + kernels.credited(seconds)

    # Threads -------------------------------------------------------------------------------------------------

    def _read(self):
        """Every line until the child's output ends; the pipe is closed here, never under a read on the main thread."""
        try:
            for line in self.process.stdout:
                self._line(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.process.stdout.close()
            except (OSError, AttributeError):
                pass

    def _line(self, line):
        if not line.startswith(MARK):
            if line.strip():
                self.tail.append(line[:200])
            fraction = status_fraction(line)
            if fraction is not None:
                with self.lock:
                    self.fraction = fraction
            return
        try:
            report = json.loads(line[len(MARK):])
        except ValueError:
            return
        if not isinstance(report, dict):
            return
        with self.lock:
            if isinstance(report.get("progress"), (int, float)):
                self.fraction = min(1.0, max(0.0, float(report["progress"])))
            if isinstance(report.get("mem_peak_mb"), int) and report["mem_peak_mb"] > (self.peak_mb or 0):
                self.peak_mb = report["mem_peak_mb"]
            if isinstance(report.get("device"), list) and len(report["device"]) == 2:
                self.device = tuple(report["device"])
            if report.get("done"):
                self.done = report
            from angelo_render_device import kernels
            said = kernels.said(report.get("kernels"), self.clock())   # Its kernel watch (render_child).
            if said is not None:
                self.kernels_state = said

    def _watch(self):
        next_memory = self.clock()
        while self.process.poll() is None:
            now = self.clock()
            with self.lock:
                finished = self.done is not None   # Its render ended on its own: only Blender's teardown is left, which
            if not finished and self.deadline is not None and now >= self.deadline:   # never costs it its output.
                self.stop("time_budget", unless_done=True)
            if not finished and self.total_mb and now >= next_memory:
                next_memory = now + MEMORY_SECONDS
                self._check_memory()
            with self.lock:
                due = self.kill_at is not None and not self.killed and now >= self.kill_at
                if due:
                    self.killed = True
            if due:
                self._signal(self.process.kill)
            time.sleep(WATCH_SECONDS)

    def _check_memory(self):
        """Stop the child when the system is about to run out of memory and the child is a large part of why: what the
        system can still give is under the reserve, and the child uses at least the reserve itself. The app's own copy
        of the document is never counted against the child (break-it review 2026-11-04, F5)."""
        reserve = job_render_route.reserve_mb(self.total_mb)
        low, free = self.memory_low(self.total_mb, reserve)
        if not low:
            return
        used = self.footprint(self.process.pid)
        if used is not None and used >= reserve:
            with self.lock:
                self.used = (used, free if free is not None else 0, reserve)
            self.stop("memory", unless_done=True)

    # Main thread ---------------------------------------------------------------------------------------------

    def _signal(self, action):
        try:
            action()
        except OSError:
            pass   # It ended meanwhile.

    def stop(self, why="cancel", unless_done=False):
        """Ask the child to end now (terminate; on Windows its whole Job Object) and kill it ``STOP_SECONDS`` later if
        it has not. The first reason stays. ``unless_done``: the watchdog's own reasons never end a child that reported
        its render's end meanwhile (review round 2, F4). Returns at once; never raises."""
        with self.lock:
            if unless_done and self.done is not None:
                return
            if self.why is None:
                self.why = why
            first = self.kill_at is None
            if first:
                self.kill_at = self.clock() + STOP_SECONDS
        if first:
            self._signal(self.process.terminate)

    def running(self):
        return self.process.poll() is None

    def state(self):
        with self.lock:
            return self.fraction

    def reason(self):
        with self.lock:
            return self.why

    def finished(self):
        """Whether the child reported that its render ended on its own."""
        with self.lock:
            return self.done is not None

    def kill_now(self, wait=KILL_WAIT):
        """End the child at once and wait up to ``wait`` seconds for it to be gone (a closing document, a quit)."""
        with self.lock:
            if self.why is None:
                self.why = "cancel"
            self.killed = True
        self._signal(self.process.kill)
        try:
            self.process.wait(wait)
        except subprocess.TimeoutExpired:
            pass

    def outcome(self):
        """What the ended child left: its exit status, why this side ended it, its end report and last lines."""
        self.reader.join(1.0)   # Its last lines (the peak, the end) are read before anything is decided.
        with self.lock:
            return {"code": self.process.poll(), "why": self.why, "used": self.used, "done": self.done,
                    "tail": list(self.tail)[-5:]}

    def close(self):
        """Forget the child once it has ended, and remove its copy. A child that cannot be ended stays known until the
        app quits (``_end_all``); a crash's copy goes at a later start (``sweep``)."""
        if self.running():
            return False
        _running.discard(self)
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = None
        return True


def crashed(code):
    """The model's reason for an unexpected exit: its status and, where the status says, its kind (no local paths)."""
    why = {-9: "; the system killed it, often for memory", -11: "; it crashed", -6: "; it aborted",
           1: "; its render script failed", 3: "; it lost its parent"}.get(code, "")
    if not why and isinstance(code, int) and code > 255:   # Windows: an NTSTATUS such as 0xC0000005.
        why = "; it crashed (0x%08X)" % (code & 0xFFFFFFFF)
    return CRASHED.format(code=code, why=why)


def writing(owner):
    """Whether a render child of job ``owner`` may still write into its staging folder (job_render_video.writing)."""
    return owner is not None and any(child.owner == owner and child.running() for child in list(_running))


def _end_all():
    """The app quits (no ``bpy`` here): kill every child and wait briefly, so none outlives it."""
    for child in list(_running):
        child.kill_now(KILL_WAIT / 2)
        child.close()


atexit.register(_end_all)


def sweep(root=None, now=None):
    """Remove the copies a crashed app left (``PREFIX`` folders in the scratch root older than ``STALE_SECONDS``): each
    holds a whole document. A child reads its copy once, at its start, so an old copy is never in use."""
    global _swept
    if root is None:
        if _swept:
            return
        _swept = True
        from . import private_roots
        root = private_roots.scratch_root()
    now = time.time() if now is None else now
    try:
        entries = [entry for entry in os.scandir(root) if entry.name.startswith(PREFIX)]
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False) and now - entry.stat(follow_symlinks=False).st_mtime > STALE_SECONDS:
                shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass


# The job ------------------------------------------------------------------------------------------------------

def write_snapshot(scene, window, folder):
    """The job's scene and everything it reaches, as it is now (its job settings applied), into ``folder``."""
    import bpy
    path = Path(folder) / "render.blend"
    with bpy.context.temp_override(window=window):
        bpy.ops.ed.flush_edits()   # As F12 does: edit-mode and sculpt changes reach the data that is written.
    bpy.data.libraries.write(str(path), {scene}, path_remap="ABSOLUTE", fake_user=False, compress=False)
    return path


def devices():
    """The Cycles devices this app's preferences enable: ``{"type", "ids"}`` (the child sets the same)."""
    import bpy
    addon = bpy.context.preferences.addons.get("cycles")
    if addon is None:
        return None
    prefs = addon.preferences
    # Every other Cycles preference a render reads (MetalRT, kernel optimization, ...): the child starts with the
    # factory ones (review round 2, F8).
    settings = {}
    for prop in prefs.bl_rna.properties:
        if prop.identifier in {"rna_type", "compute_device_type", "devices"} or prop.is_readonly:
            continue
        if prop.type in {"BOOLEAN", "INT", "FLOAT", "ENUM", "STRING"} and not getattr(prop, "is_array", False):
            value = getattr(prefs, prop.identifier)
            settings[prop.identifier] = sorted(value) if isinstance(value, set) else value
    return {"type": prefs.compute_device_type, "ids": [device.id for device in prefs.devices if device.use],
            "settings": settings}


def request(runner):
    """What the child renders: the scene by name in the copy, the frames, this process (it ends with it)."""
    return {"scene": runner.scene.name, "frames": [runner.start_frame, runner.end_frame, runner.step],
            "parent": os.getpid(), "devices": devices()}


def _record(runner):
    from .job_manager import shared
    jobs = shared()
    return jobs.store.get(runner.identity) if jobs is not None and runner.identity else None


def _deadline(record, clock=time.monotonic, now=time.time):
    """The budget's end on the watchdog's clock: the budget counts from the job's start, as the manager's does."""
    if record is None or not record.get("started"):
        return None
    left = float(record["request"]["minutes"]) * 60 - (now() - record["started"])
    return clock() + max(0.1, left)


def begin(runner, binary=None, popen=None):
    """Start ``runner``'s render in a child when ``job_render_route`` routes it there: True when it started (the
    person's settings are then back), False to render in this application (its record says why)."""
    from . import job_deadline, private_roots
    record = _record(runner)
    why, folder, seconds = job_render_route.route(runner), None, None
    if why is None:
        sweep()
        try:
            folder = private_roots.allocate(PREFIX)
            started = time.perf_counter()
            snapshot = write_snapshot(runner.scene, runner.window, folder)
            seconds = round(time.perf_counter() - started, 3)
            if binary is None:
                import bpy
                binary = bpy.app.binary_path
            from . import render_device_facts
            arguments = command(binary, snapshot, request(runner), render_device_facts._override())
            runner.child = Child(arguments, runner.identity, _deadline(record), folder=folder, popen=popen)
        except Exception as error:   # noqa: BLE001 - any failure renders here instead, as before.
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)
            why = "its copy could not be written or its process started (%s)" % type(error).__name__
    if record is not None:
        found = job_render_route.projected_mb(getattr(runner, "facts", None) or {}, getattr(runner, "scene_uid", None))
        if found is not None:   # Local only: what the scene's last render here leads to expect (job_render_route).
            record["detail"]["projected_peak_mb"] = found[0]
        record["detail"]["process"] = "app" if why else "child"
        record["detail"].update({"process_why": why[:200]} if why else {"snapshot_s": seconds})
    if why:
        return False
    runner.restore_now()   # The child renders the copy: the person's own settings are back at once.
    if record is not None:   # The child keeps the budget itself; the scene's deadline would stop the person's F12.
        job_deadline.disarm(record, runner.scene)
        record["detail"].pop(job_deadline.ARMED, None)
    return True


def progress(runner):
    """The child's progress through its current frame, or None once it has ended."""
    child = runner.child
    if child.running():
        return child.state()
    child.outcome()   # Its last lines (the end's peak memory, its device) are read before the job's end is handled.
    if child.reason() is not None and not child.finished():
        runner.stopping = True   # Stopped (Cancel, the budget, memory): never an encode after it.
    return None


def device_used(runner):
    """``(facts, shown)`` the child's Cycles session reported, or None (job_manager._device_used)."""
    child = getattr(runner, "child", None)
    return None if child is None else child.device


def peak_mb(runner):
    """The largest memory the child's renderer reported, in MB, or None (job_deadline.observe_memory)."""
    child = getattr(runner, "child", None)
    return None if child is None else child.peak_mb


class ChildPhase:
    """``RenderRunner``'s part (before ``EncodePhase``): a render whose ``child`` is set stops, ends and finishes
    through it, and never touches Blender's render job in this application (which may be the person's F12)."""
    child = None

    @property
    def budget_fired(self):
        """True once the child ended because its budget ran out (job_manager: recorded as a time_budget stop)."""
        child = self.child
        return (child is not None and not child.running() and child.reason() == "time_budget"
                and child.finished() is False)   # A render that reported its end keeps its output.

    def stop(self):
        if self.child is None:
            return super().stop()
        self.stopping = True
        if self.child.running():
            self.child.stop("cancel")
        elif self.encoder is not None:   # Its frames are done and the MP4 is being made.
            from . import job_render_video
            job_render_video.halt(self)

    def _end_child(self):
        if self.child is not None:
            self.child.kill_now()
            self.child.close()

    def abandon(self):
        self._end_child()
        super().abandon()

    def keep_frames(self):
        self._end_child()
        return super().keep_frames()

    def finish(self, stopped):
        """``Runner.finish`` (restore, publish or discard) once the child has ended, with the child's own ending said:
        a memory stop fails the job with the numbers (an animation keeps its finished frames), and an unexpected exit
        fails it with its status. Overrides ``finish``, never ``_finish``: ``RenderRunner`` defines its own
        ``_finish``, which comes first (break-it review 2026-11-04, F1)."""
        child = self.child
        if child is None:
            return super().finish(stopped)
        if child.running():   # Its poll failed and the job ends now: the child never outlives its job.
            child.kill_now()
        outcome = child.outcome()
        child.close()
        if outcome["why"] == "memory" and not stopped and not outcome["done"]:
            # Finished as an early end, which it is: a still says nothing was written, an animation keeps its whole
            # frames (its last checked strictly); only the reason is the memory's (review round 2, F5).
            status, _reason, receipt = super().finish(False)
            if status == "failed":
                used, free, reserve = outcome["used"] or (0, 0, 0)
                return "failed", MEMORY.format(free=free, total=child.total_mb or 0, reserve=reserve,
                                               used=used), receipt
            return status, _reason, receipt
        status, reason, receipt = super().finish(stopped)
        if status == "failed" and not stopped and outcome["code"] not in (0, None) and outcome["why"] is None:
            record = _record(self)
            if record is not None:   # Local only: the child's own last lines may name local paths.
                record["detail"]["process_tail"] = outcome["tail"]
            reason = crashed(outcome["code"])
        return status, reason, receipt
