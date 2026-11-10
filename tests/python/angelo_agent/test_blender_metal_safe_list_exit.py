# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Metal's buffer pool releases a safe-free list's chained chunks when Blender exits.

A safe-free list holds 8,192 freed GPU buffers per chunk and chains another chunk past that
(``MTLSafeFreeList::insert_buffer``). ``MTLBufferPool::free`` released only each list's first chunk, so a list that
had overflowed was still partly allocated at exit: "Not freed memory blocks", ``MTLSafeFreeList len: 65624`` per
chunk (the voxel remesh guard's GUI suite on the 1.0.9 cut freed a 12M-vertex mesh just before quitting).

Checks, in the real binary (background, Metal only; writes results.json under ANGELO_QA_DIR): ``BUFFERS`` small
vertex buffers, each drawn once into an offscreen so the GPU holds it, then all freed together before Blender
quits, which overflows the list into chained chunks with no frame in between to recycle them. The runner fails
the suite on any leak line at exit (native_suite_checks.allocator_leak), which is the check that matters here.
Before the fix, the 1.0.9 cut's app leaked "3 blocks, 0.187752 MB" here (3 x 65,624 B), every run.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import tempfile
from pathlib import Path

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

BUFFERS = 3 * 8192   # Three chunks' worth: at least two chained chunks.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-metal-safe-list-qa"))
OUT.mkdir(parents=True, exist_ok=True)
if bpy.app.background:
    gpu.init()   # A background Blender starts its GPU backend only when asked.
state = {"success": False, "backend": gpu.platform.backend_type_get()}
if state["backend"] == "METAL":
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    offscreen = gpu.types.GPUOffScreen(8, 8)
    batches = []
    with offscreen.bind():
        shader.bind()
        shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        for index in range(BUFFERS):
            offset = (index % 7) * 0.01
            batch = batch_for_shader(shader, "TRIS", {"pos": [(offset, 0.0), (1.0, 0.0), (0.0, 1.0)]})
            batch.draw(shader)
            batches.append(batch)
    state["drawn"] = len(batches)
    batches.clear()   # Every buffer freed at once, into the safe-free list, just before the exit.
    offscreen.free()
    state["success"] = True
else:
    state["note"] = "Not Metal: the safe-free list is Metal's; nothing to check here."
    state["success"] = True
(OUT / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
print("ANGELO_METAL_SAFE_LIST_EXIT", json.dumps(state), flush=True)
