# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parallel merge's privacy check in a large file of the person's (doc/angelo/agent_integration.rst).

A merge's check writes what a save writes and reads the kept datablocks' bytes. This times the merge's own call
(``worker_merge_verify.check``) for a small kept part (a few objects, meshes and a material) while the person's open
file holds GIGABYTES of their own data (meshes of random vertex positions, which zstd hardly shrinks), saved compressed
first as a person's file is, and records how many bytes the check wrote (its largest copy) and the free disk it took.

    Angelo --background --factory-startup --python-exit-code 1 --python bench_merge_check_person.py -- OUT GB [RUNS] [MODE]

``MODE`` (default ``object``): ``edit``, ``dyntopo`` or ``multires`` also keeps a large mesh of the person's in that
mode while the merge is checked (a 2000 x 2000 grid in Edit Mode or in Sculpt Mode with dynamic topology; a 500 x 500
grid with a level 2 Multires in Sculpt Mode, about 4 million vertices each): a save flushes such edits, and the
merge's check must not pay for or change what the person is editing (port re-read S1).

One untimed run first, then RUNS timed ones (default 3); prints ``MERGE_CHECK_PERSON {...}``.
"""

import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

import addon_utils
import bpy
import numpy

args = sys.argv[sys.argv.index("--") + 1:]
OUT, GIGABYTES, RUNS = Path(args[0]), float(args[1]), int(args[2]) if len(args) > 2 else 3
MODE = args[3] if len(args) > 3 else "object"
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import worker_merge_paths, worker_merge_verify  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
OUT.mkdir(parents=True, exist_ok=True)
scene = bpy.context.scene
generator = numpy.random.default_rng(7)
remaining, index = int(GIGABYTES * (1 << 30)), 0
while remaining >= 12:   # The person's own data, in meshes of up to 50 million vertices (600 MB of positions each).
    count = min(remaining // 12, 50_000_000)
    mesh = bpy.data.meshes.new("Person Mesh %d" % index)
    mesh.vertices.add(count)
    mesh.vertices.foreach_set("co", generator.random(count * 3, dtype=numpy.float32))
    scene.collection.objects.link(bpy.data.objects.new("Person %d" % index, mesh))
    remaining, index = remaining - count * 12, index + 1
person = OUT / "person.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(person), compress=True)
if MODE != "object":   # The person's large mesh, in the mode they are working in.
    import bmesh
    grid = bpy.data.meshes.new("Person Grid")
    built = bmesh.new()
    size = 500 if MODE == "multires" else 2000
    bmesh.ops.create_grid(built, x_segments=size, y_segments=size, size=10.0)
    built.to_mesh(grid)
    built.free()
    editing = bpy.data.objects.new("Person Editing", grid)
    scene.collection.objects.link(editing)
    bpy.context.view_layer.objects.active = editing
    editing.select_set(True)
    if MODE == "multires":
        editing.modifiers.new("Multires", "MULTIRES")
        for _level in range(2):
            bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
    bpy.ops.object.mode_set(mode="EDIT" if MODE == "edit" else "SCULPT")
    if MODE == "dyntopo":
        bpy.ops.sculpt.dynamic_topology_toggle()
    assert editing.mode == ("EDIT" if MODE == "edit" else "SCULPT"), editing.mode

before = {item.as_pointer() for kind in ("objects", "meshes", "materials") for item in getattr(bpy.data, kind)}
material = bpy.data.materials.new("Part Paint")
for number in range(3):   # What a merge keeps: a worker's few objects.
    mesh = bpy.data.meshes.new("Part Mesh %d" % number)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(material)
    scene.collection.objects.link(bpy.data.objects.new("Part %d" % number, mesh))
kept = [item for kind in ("objects", "meshes", "materials") for item in getattr(bpy.data, kind)
        if item.as_pointer() not in before]
part = OUT / "angelo-part-p4r7s2" / "part.blend"
part.parent.mkdir(parents=True, exist_ok=True)
private = worker_merge_paths.PrivateFolders([part])

written = []
copy = getattr(worker_merge_verify, "saved_copy", None)
if copy is not None:   # The size of each copy the check writes, read before its folder goes.
    def recorded(*args):   # (bpy, path) for the whole-file copy before; (path, ids) for the kept datablocks only.
        copy(*args)
        written.append(os.path.getsize(next(arg for arg in args if isinstance(arg, str))))
    worker_merge_verify.saved_copy = recorded


def once():
    free = shutil.disk_usage(OUT).free
    started = time.perf_counter()
    found = worker_merge_verify.check(bpy, kept, private)
    return time.perf_counter() - started, found, free


_seconds, found, _free = once()   # Untimed: a warm cache.
seconds = [round(once()[0], 3) for _ in range(RUNS)]
print("MERGE_CHECK_PERSON " + json.dumps({
    "person_saved_bytes": os.path.getsize(person), "person_raw_gb": GIGABYTES, "mode": MODE, "kept": len(kept),
    "seconds": seconds, "median": statistics.median(seconds), "largest_copy_bytes": max(written or [0]),
    "found": json.loads(json.dumps(found, default=str)), "blender": bpy.app.version_string}))
