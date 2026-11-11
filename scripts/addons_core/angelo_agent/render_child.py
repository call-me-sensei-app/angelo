# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run inside a background child Angelo: render one job's copy of its scene (``job_render_child``).

Never imported by the add-on. The parent starts ``Angelo --background --factory-startup --disable-autoexec
--offline-mode <copy> --python <this file> -- <request JSON>``. The copy already carries the job's settings (its
output paths lead into the job's staging folder), so this file only sets Cycles' devices as the parent's preferences
enable them, renders the frames, and reports on lines starting ``ANGELO-RENDER `` (JSON): the device its session
uses, each frame's start and progress (from the render-stats callback), each finished frame's peak memory, and the
end. The child ends itself when the parent is gone, so a crashed Angelo never leaves a render behind.
"""

import json
import os
import re
import sys
import threading
import time

import bpy

MARK = "ANGELO-RENDER "
PARENT_SECONDS = 0.5
PROGRESS_SECONDS = 0.2   # The least time between two progress reports.
# Cycles' status text, e.g. "Rendered 1/4 Tiles, Sample 12/128": as job_render_child.status_fraction reads it (this file
# cannot import the add-on).
_SAMPLE = re.compile(r"Sample (\d+)/(\d+)")
_TILES = re.compile(r"Rendered (\d+)/(\d+) Tiles")
_last = [0.0]


def say(**report):
    print(MARK + json.dumps(report), flush=True)


def parent_alive(pid):
    """Whether the Angelo that started this child still runs (as ``video_encode_child.parent_alive``)."""
    if not pid:
        return True
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    if os.getppid() != int(pid):   # Re-parented: the parent ended.
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent(pid):
    """End this process within ``PARENT_SECONDS`` of the parent's end, whatever the render is doing."""
    def watch():
        while parent_alive(pid):
            time.sleep(PARENT_SECONDS)
        os._exit(3)   # The parent's next start keeps the finished frames (job_recovery).
    threading.Thread(target=watch, name="angelo-parent-watch", daemon=True).start()


def use_devices(chosen):
    """Cycles' compute backend and devices as the parent's preferences enable them (none: the CPU)."""
    if not chosen:
        return
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for name, value in (chosen.get("settings") or {}).items():   # The parent's other Cycles preferences.
        try:
            setattr(prefs, name, set(value) if isinstance(value, list) else value)
        except (AttributeError, TypeError, ValueError):
            print("Angelo: the Cycles preference", name, "could not be set", flush=True)
    prefs.compute_device_type = chosen["type"]
    (getattr(prefs, "refresh_devices", None) or prefs.get_devices)()
    wanted = set(chosen["ids"])
    for device in prefs.devices:
        device.use = device.id in wanted


def report_device(scene):
    try:
        from angelo_render_device import facts
        say(device=[facts.facts(scene), facts.shown(scene)])
    except Exception as error:   # noqa: BLE001 - a fact, never a reason not to render.
        print("Angelo: the render device could not be read:", type(error).__name__, error, flush=True)


def report_progress(*args):
    """The frame's progress from Blender's render-stats callback, which every render path fires with Cycles' status
    text: printed status lines reach the parent only from some paths (an operator render printed none while it
    rendered, the C6 Metal suite run of 2026-11-04), so the job's card sat at 0%."""
    text = next((value for value in args if isinstance(value, str)), "")
    report_kernels()
    sample = _SAMPLE.search(text)
    if sample is None or int(sample.group(2)) <= 0:
        return
    within = min(1.0, int(sample.group(1)) / int(sample.group(2)))
    tiles = _TILES.search(text)
    if tiles is not None and int(tiles.group(2)) > 0:
        done, count = int(tiles.group(1)), int(tiles.group(2))
        within = min(1.0, (min(done, count - 1) + within) / count)
    now = time.monotonic()
    if now - _last[0] >= PROGRESS_SECONDS or within >= 1.0:
        _last[0] = now
        say(progress=within)


_kernels = [None, 0.0]   # The kernel watch last said and when: said as it opens or closes, else each PROGRESS_SECONDS.


def report_kernels(*_args):
    """The render's kernel watch, raw (angelo_render_device.kernels, whose own handlers read the same status text and
    the render's end): ``[open, closed_s, open_s]``. The parent carries an open load on with its own clock, applies the
    warm-load threshold, credits the compile to the job's budget and says what the wait is."""
    try:
        from angelo_render_device import kernels
    except ImportError:
        return
    open_, closed, open_s = kernels.WATCH.state()
    state = [open_, round(closed, 1), round(open_s, 1)]
    now = time.monotonic()
    if _kernels[0] is None or state[0] != _kernels[0][0] or (state[0] and now - _kernels[1] >= PROGRESS_SECONDS):
        _kernels[0], _kernels[1] = state, now
        say(kernels=state)


def report_used_device():
    """The device the render's Cycles session really used (``angelo_render_device.session``), once it has one: a
    fallback at render time is said, not the device predicted before it (review round 2, F7)."""
    try:
        from angelo_render_device import session
        used = session._used
    except Exception:   # noqa: BLE001 - a fact, never a reason not to render.
        return
    if used is not None:
        say(device=[used[2], used[3]])


def report_memory(scene, *_args):
    try:
        from _bpy import _angelo_jobs
        stats = _angelo_jobs.render_stats(scene) or {}
    except (ImportError, AttributeError, TypeError, ReferenceError, RuntimeError):
        return
    if isinstance(stats.get("mem_peak_mb"), int):
        say(mem_peak_mb=stats["mem_peak_mb"], frame=scene.frame_current)
    report_used_device()


def main():
    request = json.loads(sys.argv[sys.argv.index("--") + 1])
    watch_parent(request.get("parent"))
    scene = bpy.data.scenes[request["scene"]]
    use_devices(request.get("devices"))
    report_device(scene)
    try:   # A background run's Cycles register sets none of Angelo's device module up, so the kernel watch is here.
        from angelo_render_device import kernels
        kernels.register()   # First: its handlers update the watch before this child's own read it.
    except ImportError:
        pass
    bpy.app.handlers.render_post.append(report_memory)
    bpy.app.handlers.render_post.append(report_kernels)   # After the watch closed at the frame's end (kernels).
    bpy.app.handlers.render_stats.append(report_progress)
    # Each frame starts at no progress: the last frame's 1.0 never reads as part of the next (review F7).
    bpy.app.handlers.render_pre.append(lambda *_args: say(progress=0.0))
    start, end, _step = request["frames"]   # The copy's own frame step is the job's.
    bpy.ops.render.render(animation=True, frame_start=start, frame_end=end, scene=scene.name, use_viewport=False)
    report_memory(scene)
    say(done=True)


main()
