# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread latency of the parallel merge's privacy check on a large part (doc/angelo/agent_integration.rst).

The check runs once per merge, on the main thread, over the datablocks the merge keeps: it writes them as a save
would and searches the bytes for Angelo's private folders. This times exactly the merge's own call on a large part
(a 2.25 million vertex mesh, a packed 4K image, a 4 MiB text of ordinary paths, materials) in whichever check the
build has: ``worker_merge_verify.check`` (since the F-L-2 reviews 7-14) or ``worker_merge_verify.leaks`` (before).

    Angelo --background --factory-startup --python bench_merge_check.py -- OUT_FOLDER [RUNS]

One untimed run first (a warm cache on both sides), then RUNS timed ones (default 5); prints ``MERGE_CHECK {...}``
with the part's saved size, each run's seconds, the median and what the check found (nothing, on this part).
"""

import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

import addon_utils
import bpy

args = sys.argv[sys.argv.index("--") + 1:]
OUT, RUNS = Path(args[0]), int(args[1]) if len(args) > 1 else 5
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import worker_merge_paths, worker_merge_verify  # noqa: E402

KINDS = ("objects", "meshes", "materials", "images", "texts", "collections")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=1500, y_subdivisions=1500, size=10)
random.seed(7)
image = bpy.data.images.new("Big", 4096, 4096, alpha=True)
image.pixels.foreach_set([random.random() for _ in range(4096 * 4096 * 4)])
image.pack()
text = bpy.data.texts.new("Notes")
line = "render/%05d/frame_%04d.png see /usr/share/textures/brick_%03d.png and C:\\Assets\\tile_%02d.tga\n"
text.write("".join(line % (index, index % 10000, index % 1000, index % 100) for index in range(48000)))
for index in range(40):
    material = bpy.data.materials.new("Paint %d" % index)
    material.use_nodes = True
    bpy.context.object.data.materials.append(material)
part = OUT / "angelo-part-b3n8c1" / "part.blend"
part.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)

private = worker_merge_paths.PrivateFolders([part])
kept = [item for kind in KINDS for item in getattr(bpy.data, kind) if item.library is None]
call = getattr(worker_merge_verify, "check", None) or worker_merge_verify.leaks
name = call.__name__


def once():
    started = time.perf_counter()
    found = call(bpy, kept, private)
    return time.perf_counter() - started, found


_seconds, found = once()   # Untimed: the same warm cache for both builds.
seconds = [round(once()[0], 4) for _ in range(RUNS)]
print("MERGE_CHECK " + json.dumps({"check": name, "part_bytes": os.path.getsize(part), "kept": len(kept),
                                   "seconds": seconds, "median": statistics.median(seconds),
                                   "found": json.loads(json.dumps(found, default=str)), "blender": bpy.app.version_string}))
