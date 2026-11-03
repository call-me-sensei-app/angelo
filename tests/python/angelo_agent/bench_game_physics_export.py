# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What game physics costs a glTF export (doc/angelo/game_physics.rst; doc/angelo/performance.rst).

    Angelo --background --factory-startup --python bench_game_physics_export.py -- OUT.json [OBJECTS] [ROUNDS]

Builds OBJECTS (default 2,000) small meshes in a hierarchy of ten groups and times ``export_scene.gltf`` to a .glb:

* ``off``: the exporter without the physics extension (``angelo_physics.export_extension`` returns an object
  with no hooks, the add-on as it was before game physics);
* ``unmarked``: the shipped exporter, nothing marked (the case that must not get slower);
* ``marked``: the shipped exporter with every tenth object a rigid body with a fitted box.

One untimed warm-up export per arm, then ROUNDS rounds (default 9) with the three arms in rotating order, so drift
and caches affect each alike. Reports each arm's median and spread in milliseconds. Run it under the billed lock on
a quiet machine; it makes no network request.
"""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(argv[0])
OBJECTS = int(argv[1]) if len(argv) > 1 else 2000
ROUNDS = int(argv[2]) if len(argv) > 2 else 9


class NoHooks:
    pass


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    groups = []
    for index in range(10):
        group = bpy.data.objects.new("Group%d" % index, None)
        bpy.context.scene.collection.objects.link(group)
        group.location = (index * 30, 0, 0)
        groups.append(group)
    bpy.ops.mesh.primitive_cube_add(size=1)
    template = bpy.context.object
    objects = []
    for index in range(OBJECTS):
        obj = template.copy()
        obj.data = template.data.copy()
        obj.name = "Prop%d" % index
        obj.location = (index % 20, (index // 20) % 20, index // 400)
        obj.parent = groups[index % 10]
        bpy.context.scene.collection.objects.link(obj)
        objects.append(obj)
    bpy.data.objects.remove(template)
    return objects


def export(path):
    start = time.perf_counter()
    result = bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_active_scene=True)
    elapsed = (time.perf_counter() - start) * 1000.0
    assert "FINISHED" in result
    return elapsed


def main():
    import io_scene_gltf2.angelo_physics as physics
    shipped = physics.export_extension
    objects = build()
    folder = Path(tempfile.mkdtemp(prefix="angelo-physics-bench-"))

    def arm(name):
        physics.export_extension = (lambda: NoHooks()) if name == "off" else shipped
        for index, obj in enumerate(objects):
            if index % 10 == 0:
                obj.angelo_physics.body = "RIGID" if name == "marked" else "NONE"
                obj.angelo_physics.shape = "BOX" if name == "marked" else "NONE"
        return export(folder / (name + ".glb"))

    arms = ("off", "unmarked", "marked")
    for name in arms:
        arm(name)   # Untimed warm-up.
    times = {name: [] for name in arms}
    for round_index in range(ROUNDS):
        order = arms[round_index % 3:] + arms[:round_index % 3]
        for name in order:
            times[name].append(arm(name))
    physics.export_extension = shipped
    report = {"objects": OBJECTS, "rounds": ROUNDS, "binary": bpy.app.binary_path, "version": bpy.app.version_string,
              "arms": {name: {"median_ms": statistics.median(values), "min_ms": min(values), "max_ms": max(values),
                              "all_ms": values} for name, values in times.items()}}
    report["unmarked_vs_off"] = report["arms"]["unmarked"]["median_ms"] / report["arms"]["off"]["median_ms"]
    report["marked_vs_off"] = report["arms"]["marked"]["median_ms"] / report["arms"]["off"]["median_ms"]
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ANGELO_PHYSICS_BENCH", json.dumps({key: report[key] for key in ("unmarked_vs_off", "marked_vs_off")}))


main()
