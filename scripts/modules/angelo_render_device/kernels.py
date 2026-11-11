# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles compiling its render kernels, read from Cycles' own status text.

Cycles says "Loading render kernels (may take a few minutes the first time)" (``Scene::load_kernels``, every backend)
and, on Metal, "N / M render kernels loaded (may take a few minutes the first time)" until its kernels are ready
(``MetalDevice::is_ready``). A warm load reads the kernels Cycles archived and is quick. A cold one compiles: the
first render after Blender's kernels changed (an update: 5.2.4 changed them, 5.3 too), on a new GPU or a new macOS.
It took 134 s on an M4 Pro (81 kernel archives) and is paid once per user: Cycles keys its archives in
``~/.cache/cycles`` by the kernel source, the macOS version and the GPU (never the application's build), and a child
render shares them.

That one-time cost is not the render's: a render job's budget credits it (``credited``, at most ``CAP_S``), estimates
leave it out (``facts.observe``), and the job's card and the agent say what the wait is. The handlers are registered
by Cycles' register in a windowed application (``session.register``) and by ``render_child.main`` in a child render
(a background run, where Cycles sets none of this up); they ship with the application, so no render-extension check
counts them.

What counts (break-it review 2026-11-11, side-session-notes/cold-kernel-budget-break-it-2026-11-11.md):
- Only Cycles' exact status (``is_loading``): its status text also carries mesh, view-layer and file names, so a name
  holding the same words never starts the watch. The denoiser's "Loading denoising kernels (...)" comes after the
  first sample and is not a render-kernel compile.
- A render's end closes the watch (render_complete, render_cancel, render_post), whatever its last status said, and
  every render and every job resets it (render_init, ``JobManager._start``).
- A load shorter than ``MIN_COMPILE_S`` is a warm one: not counted, never said.
- Reads and writes hold one lock: Cycles' session thread writes while the main thread reads.
"""

import math
import re
import threading
import time

MARK = "may take a few minutes the first time"   # Cycles' words while its render kernels load (see above).
LOADING_STATUS = "Loading render kernels (%s)" % MARK                    # intern/cycles/scene/scene.cpp
_METAL_STATUS = re.compile(r"\d+ / \d+ render kernels loaded \(%s\)" % re.escape(MARK))   # device/metal/device_impl.mm
_PREFIX = re.compile(r"(Remaining: [^|]*|Mem: \d+M)")   # What a background session puts before its status.
# The most compile time a render job's budget is credited. A child render that exceeds it is ended by its watchdog;
# in the application the job's break check is asked to stop, and a compile Cycles runs synchronously (CUDA, OptiX)
# ends only when it returns, as any render step does.
CAP_S = 600.0
# The shortest kernel load counted (and said as "loading or compiling"). A warm load reads Cycles' kernel archives,
# usually well under a second; a budget moves in whole seconds; compiling even one kernel takes seconds. A slow
# warm read (a cold disk cache, a network home) can pass it: it is then credited as a load, bounded by the cap, and
# left out of estimates, being no part of the scene's cost either (r2 re-review R4).
MIN_COMPILE_S = 2.0


def is_loading(text):
    """Whether Cycles' status ``text`` (as render_stats gets it) is its own render-kernel loading status."""
    fields = (text or "").split(" | ")
    while fields and _PREFIX.fullmatch(fields[0]):
        fields.pop(0)
    status = fields[0] if fields else ""
    return status == LOADING_STATUS or _METAL_STATUS.fullmatch(status) is not None


def from_state(open_, closed_s, open_s):
    """``(loading, compile_s)`` from a watch's raw state: a load counts only once it lasted ``MIN_COMPILE_S``."""
    counted = open_ and open_s >= MIN_COMPILE_S
    return counted, closed_s + (open_s if counted else 0.0)


def said(report, now):
    """A child render's watch as it said it (``render_child.report_kernels``: ``[open, closed_s, open_s]``), with the
    time ``now`` it was read, or None for anything else: a NaN, an infinity or a negative never reaches the record
    (break-it F10)."""
    if not (isinstance(report, list) and len(report) == 3 and isinstance(report[0], bool)
            and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in report[1:])):
        return None
    return report[0], float(report[1]), float(report[2]), now


def carried(state, now):
    """``(loading, compile_s)`` from a child's ``said`` state at ``now``. A child says its watch only when Cycles'
    status changes, and CUDA, OptiX and HIP show one status for their whole compile, so an open load is carried on
    with this side's clock (break-it F8); the cap bounds the credit, and the watchdog the child."""
    if state is None:
        return False, 0.0
    open_, closed, open_s, at = state
    return from_state(open_, closed, open_s + (max(0.0, now - at) if open_ else 0.0))


class Watch:
    """The render-kernel compile of the render running now: when Cycles' status started saying it loads kernels, and
    how long the counted loads took. One render runs at a time (Blender starts no second one), so one watch serves
    it; each render and each job resets it."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.since, self.spans = None, []   # The open load's start; the counted loads, as (start, end).

    def reset(self):
        with self.lock:
            self.since, self.spans = None, []

    def _close(self, now):
        if self.since is not None and now - self.since >= MIN_COMPILE_S:
            self.spans.append((self.since, now))
        self.since = None

    def stats(self, text):
        loading = is_loading(text)
        with self.lock:
            now = self.clock()
            if loading:
                if self.since is None:
                    self.since = now
            else:
                self._close(now)

    def close(self):
        """The render ended (or was cancelled): an open load ends here, whatever its last status said."""
        with self.lock:
            self._close(self.clock())

    def state(self):
        """``(open, closed_s, open_s)``: raw, before the threshold (a child says it; its parent applies it)."""
        with self.lock:
            now, since = self.clock(), self.since
            closed = sum(end - start for start, end in self.spans)
            return since is not None, closed, (now - since if since is not None else 0.0)

    def loading(self):
        return from_state(*self.state())[0]

    def compile_s(self, until=None):
        """Seconds of counted compile, all of it or only what came before monotonic time ``until``."""
        with self.lock:
            now, since, spans = self.clock(), self.since, list(self.spans)
        if since is not None and now - since >= MIN_COMPILE_S:
            spans.append((since, now))
        end = now if until is None else until
        return sum(max(0.0, min(stop, end) - start) for start, stop in spans)


WATCH = Watch()


def credited(seconds):
    """The part of ``seconds`` of kernel compile a budget is credited: never negative, never over ``CAP_S``; nothing
    for a value that is not a finite number."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return 0.0
    return min(max(0.0, value), CAP_S) if math.isfinite(value) else 0.0


def _on_stats(*args):
    WATCH.stats(next((value for value in args if isinstance(value, str)), ""))


def _on_init(*_args):
    WATCH.reset()


def _on_end(*_args):
    WATCH.close()


_HANDLERS = (("render_stats", _on_stats), ("render_init", _on_init), ("render_complete", _on_end),
             ("render_cancel", _on_end), ("render_post", _on_end))


def register():
    import bpy
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name, None)   # None: a build or stand-in without that list.
        function._bpy_persistent = None   # bpy.app.handlers.persistent: a file load keeps it.
        if handlers is not None and function not in handlers:
            handlers.append(function)


def unregister():
    import bpy
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and function in handlers:
            handlers.remove(function)
