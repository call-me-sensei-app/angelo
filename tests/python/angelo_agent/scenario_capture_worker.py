# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One worker of ``scenario_capture_split``: the unchanged capture of its row bands, in stock Blender.

    Blender -b --factory-startup --python-exit-code 1 --python scenario_capture_worker.py -- PLAN WORKER PART

Opens the job's scene copy (``load_ui=False``, as the capture job opens a
side's scene) and sets the job's frame. It then claims bands from the plan's
queue one at a time (``claim``: an exclusive-create file per band, so no band
runs twice). For each ``(view, start, stop)`` band it claims, it runs ``scenario_capture.capture`` on that one
view, with ``scenario_capture.rays`` narrowed to the band's slice of the full
ray grid (``band_rays``). Each pixel gets the same ray and the same
``Scene.ray_cast`` code as in a single process. It writes
``{"chunks": [[view, start, stop, capture result], ...]}`` as JSON, where
floats keep their exact repr. A watchdog thread ends the worker as soon as the
job process that started it is gone.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

import scenario_capture as capture  # noqa: E402


def watch_parent(pid, period=1.0):
    def loop():
        while True:
            if os.getppid() != pid:
                os._exit(3)
            time.sleep(period)
    threading.Thread(target=loop, daemon=True).start()


def claim(folder, index):
    """Whether this worker got band ``index``: the first to create its claim file does."""
    try:
        os.close(os.open(os.path.join(folder, str(index)), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True


def band_rays(full_rays, start, stop):
    """``rays`` for rows ``start..stop`` only: the full grid's rays, sliced, so every value is the full grid's."""
    def rays(spec, width, height):
        return full_rays(spec, width, height)[start * width:stop * width]
    return rays


def capture_band(bpy, scene, view, spec, resolution, start, stop):
    full_rays = capture.rays
    capture.rays = band_rays(full_rays, start, stop)
    try:
        return capture.capture(bpy, scene, {view: spec}, tuple(resolution))
    finally:
        capture.rays = full_rays


def main():
    import bpy
    plan_path, _worker, part_path = sys.argv[sys.argv.index("--") + 1:][:3]
    plan = json.loads(Path(plan_path).read_text())
    watch_parent(plan["parent"])
    bpy.ops.wm.open_mainfile(filepath=plan["blend"], load_ui=False)
    scene = bpy.data.scenes[plan["scene"]]
    scene.frame_set(plan["frame"], subframe=plan["subframe"])
    chunks = []
    for index, (view, start, stop) in enumerate(plan["chunks"]):
        if not claim(plan["claims"], index):
            continue
        data = capture_band(bpy, scene, view, plan["cameras"][view], plan["resolution"], start, stop)
        chunks.append([view, start, stop, data])
    temporary = Path(str(part_path) + ".tmp")
    temporary.write_text(json.dumps({"chunks": chunks}))
    os.replace(temporary, part_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
