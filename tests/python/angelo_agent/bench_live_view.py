# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a live view costs Angelo per worker save (``live_view``): measured, never assumed.

A worker's save is read again into a private Main of its own (``_bpy._angelo_live``) and evaluated there, so the cost
should be the part's alone, whatever the size of Angelo's document. This bench builds a document of ``--objects`` mesh
objects (each with a Bevel modifier), starts one real worker and times, for ``--saves`` worker saves each:

* ``refresh``: the read and evaluation of the part (``live_view.refresh``);
* ``document_after``: the document's next evaluation after it (``view_layer.update``), the cost the window pays;
* ``control``: the same document evaluation after a worker save with NO live view attached (the baseline).

Run in a disposable background instance (``--background --factory-startup``), on the machine whose numbers you
need; report the machine, its load and the build. No provider or paid call.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--objects", type=int, default=2000)
parser.add_argument("--saves", type=int, default=12)
parser.add_argument("--subdivision", type=int, default=None, help="live_view.SUBDIVISION for this run")
parser.add_argument("--gpu-subdivision", action="store_true", help="live_view.GPU_SUBDIVISION for this run")
parser.add_argument("--heavy", type=int, default=0,
                    help="the part first gets this many subdivided spheres (Subdivision level 3): a part that costs")
parser.add_argument("--out", default=str(Path(tempfile.gettempdir(), "angelo-live-view-bench")))
args = parser.parse_args(argv)
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"),
                  ANGELO_ENABLE_PARALLEL_AGENTS="1")   # Background: the machine check never passes (parallel_capability).
from angelo_agent import live_view, parallel_sessions, ui, worker_routing  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from parallel_workers_qa import WorkerChat, primitive, program  # noqa: E402


def stats(values):
    values = [v * 1000 for v in values]
    return {"n": len(values), "p50": round(statistics.median(values), 3), "max": round(max(values), 3),
            "mean": round(statistics.fmean(values), 3)} if values else None


def document(count):
    scene = bpy.context.scene
    mesh = bpy.data.meshes.new("Bench Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], [],
                     [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    side = max(1, int(count ** 0.5))
    for index in range(count):
        obj = bpy.data.objects.new("Doc %05d" % index, mesh)
        obj.location = (index % side * 2.0, index // side * 2.0, 0)
        obj.modifiers.new("Bevel", 'BEVEL')
        scene.collection.objects.link(obj)
    bpy.context.view_layer.update()


steps = []


calls = []   # Seconds each worker tool call held Angelo's main thread (break-it P1-2 H3: calls block).


def heavy_part(chat, count):
    """``count`` subdivided spheres (level 3), built as a chat builds, 16 per program (a program's 32 operations)."""
    from parallel_workers_qa import op
    for first in range(0, count, 16):
        operations = []
        for index in range(first, min(count, first + 16)):
            alias = "s%d" % index
            operations += [primitive("UV_SPHERE", "Heavy %d" % index, alias, radius=0.4,
                                     location=[index % 6, index // 6, 0]),
                           op("modifier_add", "$" + alias, type="SUBSURF", name="Smooth", settings={"levels": 3})]
        result = chat.call("angelo_scene_program", program(operations))
        result = result[0] if isinstance(result, tuple) else result
        assert result.get("status") == "committed", result


def saves(chat, label, count, live):
    refresh, after = [], []
    for index in range(count):
        before = list(parallel_sessions.refresh_seconds)
        started = time.perf_counter()
        chat.call("angelo_scene_program", program([primitive("CUBE", "%s %d" % (label, index), "c", size=0.2,
                                                             location=[index * 0.3, 0, 0])]))
        calls.append(time.perf_counter() - started)
        parallel_sessions.flush()
        if live:
            refresh.extend(parallel_sessions.refresh_seconds[len(before):])
        started = time.perf_counter()
        bpy.context.view_layer.update()
        after.append(time.perf_counter() - started)
    return refresh, after


def footprint(pid):
    """Bytes the OS charges ``pid`` (macOS phys_footprint; Windows private bytes), or None: bench_parallel_workers'
    measure, copied because importing that bench runs it."""
    import subprocess
    try:
        if sys.platform == "darwin":
            text = subprocess.run(["footprint", "-p", str(pid), "-f", "bytes"], capture_output=True, text=True,
                                  timeout=30).stdout
            for line in text.splitlines():
                if "phys_footprint:" in line:
                    return int(line.split(":")[1].split()[0])
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                                                          "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                                          "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                                          "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
            handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
            counters = Counters(cb=ctypes.sizeof(Counters))
            try:
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return counters.PrivateUsage
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def gpu_release_probe():
    """Can a background Angelo give back the GPU memory a picture took (the worker-side picture policy's
    question)? Its own footprint before its first GPU use, after one 256 px draw, and after freeing the offscreen,
    clearing Python's garbage and waiting. Run first, before anything else in this process draws."""
    import gc
    rows = {"before": footprint(os.getpid())}
    import gpu
    gpu.init()
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    offscreen = gpu.types.GPUOffScreen(256, 160)
    bpy.context.view_layer.update()
    with bpy.context.temp_override(window=window, area=area, region=region):
        for _ in range(3):
            offscreen.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region,
                                  space.region_3d.view_matrix, space.region_3d.window_matrix)
    rows["after_draw"] = footprint(os.getpid())
    offscreen.free()
    del offscreen
    gc.collect()
    time.sleep(2.0)
    rows["after_free"] = footprint(os.getpid())
    return rows


def draw_part(session):
    """A 256 x 160 Solid picture of the part, as the rail draws it: first (GPU) and then p50 of 10."""
    import gpu
    from angelo_agent import rail_pictures
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    found = live_view.bounds(session)
    if found is None:
        return None
    view, projection = rail_pictures.framing(found, 1.6)[:2]
    offscreen = gpu.types.GPUOffScreen(256, 160)
    times = []
    for _ in range(11):
        started = time.perf_counter()
        with bpy.context.temp_override(window=window, area=area, region=region):
            live_view.native_draw(live_view.part(session), offscreen, area.spaces.active, view, projection)
        times.append(time.perf_counter() - started)
    offscreen.free()
    return {"first_ms": round(times[0] * 1000, 3), "after": stats(times[1:])}


_register(bpy, ui)
parallel_sessions.register()
if args.subdivision is not None:
    live_view.SUBDIVISION = args.subdivision
live_view.GPU_SUBDIVISION = args.gpu_subdivision
report_gpu = gpu_release_probe()
document(args.objects)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "main.blend"), check_existing=False)
report = {"parallel_edit_forced": True, "objects": args.objects, "saves": args.saves, "subdivision_cap": live_view.SUBDIVISION, "gpu_subdivision": live_view.GPU_SUBDIVISION, "build": bpy.app.version_string, "platform": sys.platform,
          "load": os.getloadavg() if hasattr(os, "getloadavg") else None, "gpu_release_probe_bytes": report_gpu}
try:
    # Control: a worker whose chat has no live view (detached at once) saves; the document evaluates.
    control = WorkerChat(OUT / "state", OUT / "control.blend", ROOT)
    parallel_sessions.dismiss(control.session)
    _r, report_after = saves(control, "Control", args.saves, live=False)
    report["control_document_after"] = stats(report_after[2:])
    control.stop()
    # Live: the same with the live view attached.
    chat = WorkerChat(OUT / "state", OUT / "live.blend", ROOT)
    if args.heavy:
        heavy_part(chat, args.heavy)
        parallel_sessions.flush()
    del calls[:]
    refresh, after = saves(chat, "Live", args.saves, live=True)
    if not refresh:   # Below the bar nothing of parallel edit runs: a bench that read nothing measured nothing.
        raise RuntimeError("no live reads were measured: is parallel edit on in this Angelo?")
    report["worker_call_blocks_main"] = stats(calls[2:])
    report["part_summary"] = {k: v for k, v in (live_view.summary(chat.session) or {}).items() if k != "objects"}
    report["part_draw_256"] = draw_part(chat.session)
    started = time.perf_counter()
    live_view.refresh(chat.session)   # Read again: new evaluated meshes, so the next draw extracts them again.
    report["refresh_before_redraw_ms"] = round((time.perf_counter() - started) * 1000, 3)
    report["part_draw_256_after_a_read"] = draw_part(chat.session)
    report["refresh"] = stats(refresh[2:])
    report["document_after"] = stats(after[2:])
    report["live_objects"] = len((live_view.summary(chat.session) or {}).get("objects") or [])
    chat.stop()
finally:
    worker_routing.stop_all()
(OUT / "bench-live-view.json").write_text(json.dumps(report, indent=2))
print("BENCH-LIVE-VIEW", json.dumps(report))
