# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ray-cast capture split across background Blender processes, byte-identical to ``scenario_capture.capture``.

``Scene.ray_cast`` tests every object for every ray, so one process needs
rays x objects time: a 10,000-part scene took over an hour on one core. This
splits the same work, unchanged, across processes. ``install()`` (run by
``scenario_capture_boot`` before a capture job) points the job's
``scenario_capture.capture`` at ``capture_split``. That saves a copy of the
scene as it stands (``capture-state.blend``) and starts ``worker_count()``
background Blender processes. Each worker opens the copy, sets the job's frame
and runs the unchanged ``scenario_capture.capture`` on one row band at a time,
with ``rays`` narrowed to the band's slice of the view's full ray grid. Every
pixel therefore gets the same ray and the same ``Scene.ray_cast`` call as in
one process. The job puts the bands back in camera and row order and numbers
the hit names in first-hit order, as ``capture()`` does. Depths cross the
process boundary as JSON, which writes a float's shortest exact repr, so every
value round-trips bit for bit.

None of the files in a scenario's reference digest change
(``scenario_specs.SHARED_SOURCES``): the jobs still call ``capture.capture``,
and the references keep their folders.

``worker_count`` is one fixed rule from ``os.cpu_count()``, the same for every
side, scenario and reference. Work is a queue of row bands
(``CHUNKS_PER_WORKER`` per worker): each worker claims the next unclaimed band
(an exclusive-create file per band), so efficiency cores, busy cores and costly
views even out without any tuning. A worker
exits when the job that started it is gone (``scenario_capture_worker``), so
a timed-out job leaves no orphaned ray casters behind.
"""

import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE / "scenario_capture_worker.py"
HEADROOM = 2             # Cores left for the job's own process and the system.
CHUNKS_PER_WORKER = 8    # Row bands per worker: small enough that slow cores and costly views even out.
POLL_S = 0.5


def worker_count(cpu_count=None, chunks=None):
    """``os.cpu_count()`` minus ``HEADROOM`` (at least one), and never more than there are chunks."""
    cores = os.cpu_count() if cpu_count is None else cpu_count
    count = max(1, (cores or 1) - HEADROOM)
    return count if chunks is None else max(1, min(count, chunks))


def plan(views, height, workers):
    """``[(view, start, stop)]``: row bands that cover every row of every view exactly once, band-major."""
    views = list(views)
    bands = max(1, min(height, math.ceil(CHUNKS_PER_WORKER * workers / max(1, len(views)))))
    edges = [round(band * height / bands) for band in range(bands + 1)]
    return [(view, edges[band], edges[band + 1]) for band in range(bands) for view in views
            if edges[band] < edges[band + 1]]


def assemble(views, width, height, parts):
    """``({view: (depths, hit names)}, parents)`` in camera order from the workers' parts.

    A part is ``{"chunks": [[view, start, stop, {"names", "parents", "views"}], ...]}``, where the last
    item is what ``capture()`` returned for that band. Gaps, overlaps and wrong sizes are refused.
    """
    rows = {view: [None] * height for view in views}
    parents = {}
    for part in parts:
        for view, start, stop, data in part["chunks"]:
            if view not in rows or not 0 <= start < stop <= height:
                raise ValueError("capture chunk outside the plan: %r rows %d-%d" % (view, start, stop))
            maps = data["views"][view]
            depth, ids = maps["depth"], maps["ids"]
            if len(depth) != (stop - start) * width or len(ids) != len(depth):
                raise ValueError("capture chunk %r rows %d-%d has %d depths and %d ids for %d pixels"
                                 % (view, start, stop, len(depth), len(ids), (stop - start) * width))
            hits = [None if number < 0 else data["names"][number] for number in ids]
            parents.update(data["parents"])
            for row in range(start, stop):
                if rows[view][row] is not None:
                    raise ValueError("capture row %d of %r arrived twice" % (row, view))
                offset = (row - start) * width
                rows[view][row] = (depth[offset:offset + width], hits[offset:offset + width])
    traced = {}
    for view in views:
        missing = [row for row, value in enumerate(rows[view]) if value is None]
        if missing:
            raise ValueError("capture of %r is missing %d rows (first %d)" % (view, len(missing), missing[0]))
        traced[view] = ([value for row in rows[view] for value in row[0]],
                        [name for row in rows[view] for name in row[1]])
    return traced, parents


def index_hits(traced):
    """``(names, views)`` as ``capture()`` numbers them: names in first-hit order over the views, ``-1`` misses."""
    names, index = [], {}
    views = {}
    for view, (depth, hits) in traced.items():
        ids = []
        for name in hits:
            if name is None:
                ids.append(-1)
                continue
            if name not in index:
                index[name] = len(names)
                names.append(name)
            ids.append(index[name])
        views[view] = {"depth": list(depth), "ids": ids}
    return names, views


def worker_command(binary, plan_path, worker, part_path):
    return [binary, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(WORKER), "--",
            str(plan_path), str(worker), str(part_path)]


def _tail(path, lines=30):
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


def run_workers(commands, logs, timeout=None):
    """Runs every command at once; returns when all exit 0, raises on the first failure or at ``timeout``."""
    handles, processes = [], []
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        for command, log in zip(commands, logs):
            handle = open(log, "w")
            handles.append(handle)
            processes.append(subprocess.Popen(command, cwd=HERE, stdout=handle, stderr=subprocess.STDOUT,
                                              stdin=subprocess.DEVNULL))
        while True:
            codes = [process.poll() for process in processes]
            for worker, code in enumerate(codes):
                if code not in (None, 0):
                    raise RuntimeError("capture worker %d exited %s:\n%s" % (worker, code, _tail(logs[worker])))
            if all(code == 0 for code in codes):
                return
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("capture workers still running after %s s" % timeout)
            time.sleep(POLL_S)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        for handle in handles:
            handle.close()


def capture_split(bpy, scene, cameras, scratch, resolution=None, workers=None, timeout=None):
    """``scenario_capture.capture(bpy, scene, cameras, resolution)``, computed by ``workers`` background processes.

    Call it where ``capture()`` would be called, with the scene prepared
    (render visibility matched, frame set). ``scratch`` holds the scene copy,
    the plan, the parts and the worker logs; it is removed on success and
    kept on failure.
    """
    import scenario_capture as capture
    width, height = resolution or capture.CAPTURE_RESOLUTION
    views = list(cameras)
    workers = worker_count(chunks=len(views) * height) if workers is None else workers
    if workers <= 1:
        return getattr(capture.capture, "serial", capture.capture)(bpy, scene, cameras, (width, height))
    scratch = Path(scratch)
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    state = scratch / "capture-state.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(state), copy=True, compress=False)
    chunks = plan(views, height, workers)
    workers = min(workers, len(chunks))
    plan_path = scratch / "capture-plan.json"
    (scratch / "claims").mkdir()
    plan_path.write_text(json.dumps({
        "blend": str(state), "scene": scene.name, "frame": scene.frame_current, "subframe": scene.frame_subframe,
        "cameras": cameras, "resolution": [width, height], "parent": os.getpid(), "claims": str(scratch / "claims"),
        "chunks": [list(chunk) for chunk in chunks]}))
    parts = [scratch / ("capture-part-%d.json" % worker) for worker in range(workers)]
    logs = [scratch / ("capture-worker-%d.log" % worker) for worker in range(workers)]
    started = time.monotonic()
    run_workers([worker_command(bpy.app.binary_path, plan_path, worker, part) for worker, part in enumerate(parts)],
                logs, timeout)
    traced, parents = assemble(views, width, height, [json.loads(part.read_text()) for part in parts])
    names, maps = index_hits(traced)
    print("CAPTURE_SPLIT", json.dumps({"workers": workers, "chunks": len(chunks),
                                       "seconds": round(time.monotonic() - started, 2)}), flush=True)
    shutil.rmtree(scratch, ignore_errors=True)
    return {"resolution": [width, height], "names": names, "views": maps,
            "parents": {name: parents[name] for name in names}}


def install(scratch, workers=None, timeout=None):
    """Makes ``scenario_capture.capture`` in this process the split capture (scratch folders under ``scratch``)."""
    import scenario_capture as capture
    if getattr(capture.capture, "split", False):
        return
    serial = capture.capture
    calls = []

    def split_capture(bpy, scene, cameras, resolution=capture.CAPTURE_RESOLUTION):
        calls.append(None)
        data = capture_split(bpy, scene, cameras, Path(scratch) / ("capture-split-%d" % len(calls)), resolution,
                             workers, timeout)
        try:
            Path(scratch).rmdir()   # Only when empty: a failed capture's folder stays for its logs.
        except OSError:
            pass
        return data
    split_capture.split, split_capture.serial = True, serial
    capture.capture = split_capture


def job_arguments(argv):
    """``(job script, argv as the job expects it)`` from the boot's argv: its own first ``--`` argument removed."""
    cut = argv.index("--")
    return argv[cut + 1], argv[:cut + 1] + argv[cut + 2:]


def scratch_of(arguments):
    """Where a job's split capture keeps its scratch folders: under its ``--out``."""
    out = arguments[arguments.index("--out") + 1] if "--out" in arguments[:-1] else "."
    return Path(out) / ".capture-split"
