# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the marble wait costs in the running app, for ``test_blender_chat_waiting``.

* **Main-thread share** (``LoopRate``): with event simulation Blender's main loop never sleeps, so the number of
  loop iterations a second is what is left after every other piece of main-thread work. A timer that runs on
  every iteration counts them in alternating windows with the animation on and off (``marble_host.enabled``);
  the share the animation takes is ``1 - rate_on / rate_off``. It covers everything a marble frame costs: the
  timer's tick, the region's draw (Blender's console text, the kept picture, the marks), the window's
  composite and the buffer swap.
* **Orbit frame time** (``Orbit``): a middle-mouse orbit of the 3D view driven by simulated events, one move per
  loop iteration, with the time between the view's draws recorded by a draw handler on it, in alternating
  windows with the animation on and off.
* **One frame** (``frame_cost``): the chat region's redraw, composite and swap (``wm.redraw_timer`` ``DRAW_SWAP``),
  repeated, as a marble frame over the kept picture or as the chat's full draw, in main-thread CPU time.
"""

import ctypes
import statistics
import time

import bpy


class _TimeValue(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_int), ("microseconds", ctypes.c_int)]


class _ThreadBasicInfo(ctypes.Structure):  # <mach/thread_info.h>, THREAD_BASIC_INFO
    _fields_ = [("user_time", _TimeValue), ("system_time", _TimeValue), ("cpu_usage", ctypes.c_int),
                ("policy", ctypes.c_int), ("run_state", ctypes.c_int), ("flags", ctypes.c_int),
                ("suspend_count", ctypes.c_int), ("sleep_time", ctypes.c_int)]


_mach = None


def thread_cpu():
    """The calling thread's CPU time in seconds (user + system, microseconds), from Mach's ``thread_info``:
    Blender's bundled Python has no ``time.thread_time`` on macOS."""
    global _mach
    if _mach is None:
        _mach = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        _mach.mach_thread_self.restype = ctypes.c_uint
        _mach.thread_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        _mach.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
        _mach.mach_task_self_ = ctypes.c_uint.in_dll(_mach, "mach_task_self_")
    info, count = _ThreadBasicInfo(), ctypes.c_uint(ctypes.sizeof(_ThreadBasicInfo) // 4)
    thread = _mach.mach_thread_self()
    try:
        if _mach.thread_info(thread, 3, ctypes.byref(info), ctypes.byref(count)) != 0:
            raise OSError("thread_info failed")
    finally:
        _mach.mach_port_deallocate(_mach.mach_task_self_.value, thread)
    return (info.user_time.seconds + info.system_time.seconds
            + (info.user_time.microseconds + info.system_time.microseconds) / 1e6)


def median(values):
    return round(statistics.median(values), 4) if values else None


def percentile(values, share):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(share * len(values)))], 4) if values else None


class LoopRate:
    """Main-loop iterations a second, counted by a timer that runs on every one of them."""

    def __init__(self):
        self.count, self.running = 0, False

    def _step(self):
        self.count += 1
        return 0.0 if self.running else None

    def start(self):
        self.running = True
        bpy.app.timers.register(self._step, first_interval=0.0)

    def stop(self):
        self.running = False


class Windows:
    """Alternating measurement windows: ``begin(on)`` sets the animation, ``sample()`` closes the window."""

    SETTLE = 0.2  # Seconds after a switch before counting (the switch itself redraws the chat in full).

    def __init__(self, read):
        self.read, self.samples, self.open = read, {True: [], False: []}, None

    def begin(self, on):
        from angelo_agent import marble_host
        marble_host.enabled = on
        self.open = (on, time.perf_counter() + self.SETTLE, None)

    def tick(self):
        """Start counting once settled; returns True while a window is open."""
        if self.open is None:
            return False
        on, settle, start = self.open
        if start is None and time.perf_counter() >= settle:
            self.open = (on, settle, (time.perf_counter(), self.read()))
        return True

    def close(self):
        on, _, start = self.open
        if start is not None:
            self.samples[on].append((start, (time.perf_counter(), self.read())))
        self.open = None


def rates(samples):
    """Loop iterations a second of each window (its reading's first value)."""
    return [(end[1][0] - begin[1][0]) / (end[0] - begin[0]) for begin, end in samples if end[0] > begin[0]]


def alternate(marks, name, read, first_at, count, t):
    """Alternating on/off windows of one second from ``first_at`` (``marks[name]``); True once ``count`` closed."""
    windows = marks.get(name)
    if windows is None:
        windows = marks[name] = Windows(read)
        windows.begin(True)
        marks[name + "_next"], marks[name + "_done"] = first_at + 1.0, 0
    windows.tick()
    if t >= marks[name + "_next"] and marks[name + "_done"] < count:
        windows.close()
        marks[name + "_done"] += 1
        if marks[name + "_done"] < count:
            windows.begin(marks[name + "_done"] % 2 == 0)
            marks[name + "_next"] += 1.0
    return marks[name + "_done"] >= count


def same_picture(window, region, view_state, out, differing, debug):
    """Screenshots with the kept picture and drawn straight as before (``marble_frames.bypass``): the share of
    the chat's pixels that differ outside the moving marks. ``debug`` gets which path each screenshot took."""
    from angelo_agent import marble_frames
    shots = []
    for name, bypass in (("kept", False), ("direct", True), ("kept-again", False)):
        marble_frames.bypass, marble_frames.trace = bypass, []
        shots.append(out / ("picture-%s.png" % name))
        before = dict(marble_frames.stats)
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            bpy.ops.screen.screenshot(filepath=str(shots[-1]))
        from angelo_agent import chat_view, marble_host
        debug.append({"shot": name, "kept": len(marble_frames._kept), "requested": len(marble_frames._requested),
                      "chat_error": chat_view.last_error, "marble_error": marble_host.last_error,
                      "full": marble_frames.stats["full"] - before["full"],
                      "reused": marble_frames.stats["reused"] - before["reused"], "trace": marble_frames.trace})
    marble_frames.bypass, marble_frames.trace = False, None
    holes = [(region.x + r["rect"][0], region.y + r["rect"][1], r["rect"][2], r["rect"][3])
             for r in view_state.get("marble_drawn", {}).values()]
    rect = (region.x, region.y, region.width, region.height)
    return {"kept": differing(shots[0], shots[1], rect, holes), "kept_again": differing(shots[2], shots[1], rect, holes)}


class Orbit:
    """A middle-mouse orbit of the first 3D view, one simulated move per loop iteration."""

    def __init__(self, window):
        self.window = window
        self.area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.draws, self.running, self.step, self.handler = [], False, 0, None
        self.rotation = None

    def _drawn(self):
        self.draws.append(time.perf_counter())

    def _move(self):
        if not self.running:
            return None
        self.step += 1
        x = self.region.x + self.region.width // 2 + (self.step % 120 - 60) * 3
        self.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=self.region.y + self.region.height // 2)
        return 0.0

    def start(self):
        self.rotation = tuple(self.area.spaces.active.region_3d.view_rotation)
        self.handler = bpy.types.SpaceView3D.draw_handler_add(self._drawn, (), 'WINDOW', 'POST_PIXEL')
        x, y = self.region.x + self.region.width // 2, self.region.y + self.region.height // 2
        self.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        self.window.event_simulate(type='MIDDLEMOUSE', value='PRESS', x=x, y=y)
        self.running = True
        bpy.app.timers.register(self._move, first_interval=0.0)

    def stop(self):
        self.running = False
        x, y = self.region.x + self.region.width // 2, self.region.y + self.region.height // 2
        self.window.event_simulate(type='MIDDLEMOUSE', value='RELEASE', x=x, y=y)
        if self.handler is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self.handler, 'WINDOW')
            self.handler = None
        return tuple(self.area.spaces.active.region_3d.view_rotation) != self.rotation

    def frame_times(self, begin, end):
        """Milliseconds between the view's draws within ``begin``-``end`` (perf_counter seconds)."""
        times = [t for t in self.draws if begin <= t <= end]
        return [(b - a) * 1000 for a, b in zip(times, times[1:])]


def frame_cost(window, area, region, marble, count=40):
    """One chat redraw with the window's composite and swap: a marble frame, or the chat's full draw.

    ``cpu_ms`` is the main thread's CPU time per frame (``thread_cpu`` over all ``count`` frames, whose
    microsecond resolution is too coarse for one), which leaves out the swap's wait for the display
    (back-to-back swaps wait for vertical sync, about 8 ms at 120 Hz); ``wall_ms`` is the median with it.
    """
    from angelo_agent import marble_frames

    def frame():
        if marble:
            marble_frames.request(area)
        with bpy.context.temp_override(window=window, area=area, region=region):
            begin = time.perf_counter()
            bpy.ops.wm.redraw_timer(type='DRAW_SWAP', iterations=1)
            return (time.perf_counter() - begin) * 1000
    wall, started_cpu = [], thread_cpu()
    for _ in range(count):
        wall.append(frame())
    cost = {"cpu_ms": round((thread_cpu() - started_cpu) * 1000 / count, 4), "wall_ms": median(wall)}
    cost["regions_drawn_per_frame"] = regions_drawn(window, frame)  # After the timed frames: never in cpu_ms.
    return cost


def regions_drawn(window, frame, count=10):
    """Observation only: which regions of ``window`` draw in each of ``count`` more frames (``"area/region"`` ->
    draws per frame), so a frame cost can be attributed to the regions that redraw with the chat. Counted by a
    POST_PIXEL handler on every region type of every editor in the window and of the top and status bars; a type that
    takes none is skipped."""
    counts, handlers = {}, []

    def seen(key):
        counts[key] = counts.get(key, 0) + 1
    pairs = {(type(area.spaces.active).__name__, r.type) for area in window.screen.areas for r in area.regions}
    # The top bar and status bar are the window's own areas, outside its screen's list.
    pairs |= {("SpaceTopBar", "HEADER"), ("SpaceTopBar", "WINDOW"), ("SpaceStatusBar", "HEADER")}
    for space_name, region_type in sorted(pairs):
        space = getattr(bpy.types, space_name, None)
        key = "%s/%s" % (space_name, region_type)
        try:
            handlers.append((space, space.draw_handler_add(seen, (key,), region_type, 'POST_PIXEL'), region_type))
        except (TypeError, ValueError, AttributeError):
            continue
    try:
        for _ in range(count):
            frame()
    finally:
        for space, handle, region_type in handlers:
            space.draw_handler_remove(handle, region_type)
    return {key: round(value / count, 2) for key, value in sorted(counts.items())}


def paired_shares(samples):
    """``1 - rate_on / rate_off`` for each "on" window against the mean of the "off" windows beside it in time,
    so a drift in machine load cancels; ``samples`` are ``Windows.samples``."""
    windows = sorted([(begin[0], True, rate) for (begin, _), rate in zip(samples[True], rates(samples[True]))]
                     + [(begin[0], False, rate) for (begin, _), rate in zip(samples[False], rates(samples[False]))])
    shares = []
    for index, (_, on, rate) in enumerate(windows):
        beside = [windows[k][2] for k in (index - 1, index + 1) if 0 <= k < len(windows) and not windows[k][1]]
        if on and beside:
            shares.append(1 - rate / (sum(beside) / len(beside)))
    return shares
