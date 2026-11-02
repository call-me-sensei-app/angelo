# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a region render pixel run crashes, and whether stock Blender crashes there too (diagnostic, not a suite).

Run with any Python 3 against a built Angelo, one background launch per variant so a crash ends only its own::

    python render_region_probe.py <Angelo executable> <output folder> [variant ...]

Each variant gets its own QA folder and temporary folder (where Blender writes ``*.crash.txt``). The summary
(``probe.json`` and stdout) gives every variant's exit code, the last step it began (``ANGELO_RR_STEP``), its last
``render.region`` log lines and the head of any crash file (the module list there includes Angelo's base address).

* ``stock-f12``: four full Cycles renders of the product scene with denoising: no region code at all.
* ``stock-border``: Blender's own border render (not cropped) with the scene's Persistent Data, the region's size and
  denoising, three times, then a full render: the engine kept between border renders, as a region render keeps it.
* ``cycles``: the pixel suite's product scene in Cycles, as the suite runs it.
* ``cycles-nodenoise`` / ``cycles-persistent`` / ``cycles-1thread``: the same with denoising off, with the scene's own
  Persistent Data on, and on one render thread.
* ``eevee``: the product scene in EEVEE.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE / "test_blender_render_region_pixels.py"
LOG_ARGS = ["--log", "render.region", "--log-level", "debug"]
VARIANTS = {
    "stock-f12": ([], ["--stock", "f12"]),
    "stock-border": ([], ["--stock", "border"]),
    "cycles": ([], ["--scenes", "product", "--engines", "CYCLES"]),
    "cycles-nodenoise": ([], ["--scenes", "product", "--engines", "CYCLES", "--denoise", "off"]),
    "cycles-persistent": ([], ["--scenes", "product", "--engines", "CYCLES", "--persistent", "on"]),
    "cycles-1thread": (["-t", "1"], ["--scenes", "product", "--engines", "CYCLES"]),
    "eevee": ([], ["--scenes", "product", "--engines", "BLENDER_EEVEE"]),
}
TIMEOUT = 600


def stock(mode):
    """Inside Blender: the stock-Blender controls (no region render operator)."""
    import bpy
    sys.path.insert(0, str(HERE))
    import render_region_scenes as scenes
    scene, _material = scenes.product()
    scenes.configure(scene, "CYCLES", denoise=True)
    if mode == "f12":
        for index in range(4):
            print("ANGELO_RR_STEP stock-f12 render %d" % index, flush=True)
            bpy.ops.render.render()
    else:
        render = scene.render
        render.use_persistent_data = True
        for index, (left, bottom, right, top) in enumerate([(0.3, 0.25, 0.7, 0.75), (0.3, 0.25, 0.7, 0.75),
                                                            (0.1, 0.1, 0.4, 0.5)]):
            render.use_border, render.use_crop_to_border = True, False
            render.border_min_x, render.border_min_y, render.border_max_x, render.border_max_y = (
                left, bottom, right, top)
            print("ANGELO_RR_STEP stock-border border %d" % index, flush=True)
            bpy.ops.render.render()
        render.use_border = False
        render.use_persistent_data = False
        print("ANGELO_RR_STEP stock-border full", flush=True)
        bpy.ops.render.render()
    print("ANGELO_RR_PROBE_STOCK_DONE", flush=True)


def run(executable, out, name):
    blender_args, suite_args = VARIANTS[name]
    folder = out / name
    tmp = folder / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ANGELO_QA_DIR=str(folder), TEMP=str(tmp), TMP=str(tmp), TMPDIR=str(tmp))
    command = [executable, "-b", "--factory-startup", *blender_args, *LOG_ARGS, "--python-exit-code", "1",
               "--python", str(Path(__file__).resolve() if "--stock" in suite_args else SUITE), "--", *suite_args]
    started = time.monotonic()
    with open(folder / "run.log", "w", encoding="utf-8", errors="replace") as log:
        try:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    lines = (folder / "run.log").read_text(encoding="utf-8", errors="replace").splitlines()
    steps = [line for line in lines if line.startswith("ANGELO_RR_STEP")]
    crashes = sorted(tmp.rglob("*.crash.txt"))
    return {
        "exit": code if isinstance(code, str) else "%d (0x%08X)" % (code, code & 0xFFFFFFFF),
        "seconds": round(time.monotonic() - started, 1),
        "last_step": steps[-1] if steps else None,
        "region_log": [line for line in lines if "render.region" in line][-6:],
        "tail": lines[-25:],
        "crash_head": crashes[0].read_text(encoding="utf-8", errors="replace").splitlines()[:60] if crashes else None,
    }


def main():
    executable, out = sys.argv[1], Path(sys.argv[2])
    names = sys.argv[3:] or list(VARIANTS)
    results = {}
    for name in names:
        results[name] = run(executable, out, name)
        print(name, results[name]["exit"], results[name]["seconds"], "s, last:", results[name]["last_step"],
              flush=True)
        (out / "probe.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    if "--" in sys.argv and "--stock" in sys.argv:
        stock(sys.argv[sys.argv.index("--stock") + 1])
    else:
        main()
