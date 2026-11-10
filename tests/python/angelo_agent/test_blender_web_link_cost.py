# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a web-link delta costs to decide as copies grow (``web_link_live.changed_objects``; doc/angelo/web_link.rst,
"Live frames"; break-it round 9, H1). Run inside Angelo without a window:

    Angelo --background --factory-startup --python tests/python/angelo_agent/test_blender_web_link_cost.py -- --out DIR

Scenes: a typical one (120 boxes, each its own material), and a geometry-nodes scatter of 10,000, 100,000 and
1,000,000 instances of one pebble beside an unrelated box. The edit is the box's colour, which the scatter never
shows. For each: the time to decide what changed (median of 9 after 2 untimed), and the whole delta (median of 5
after 1 untimed) for the record. Checked: deciding stays flat as instances grow (a million costs at most twice ten
thousand plus 2 ms; it walked every instance before), the delta holds the box alone, and the pebble's own colour
still reaches the scatter; and with 1,000 and 10,000 empties instancing one collection, a member's colour reaches
every empty at a cost that grows linearly (round 10, M2: it grew with their square). Writes ``web-link-cost.json`` to DIR (``ANGELO_QA_DIR`` under run_native_suites.py),
whose ``success`` is the verdict; times are this machine's, compared only with each other.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(ARGS[ARGS.index("--out") + 1]) if "--out" in ARGS else Path(
    os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-web-link-cost-qa"))
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "timings": {}}
SIZES = (10_000, 100_000, 1_000_000)
INSTANCERS = (1_000, 10_000)   # Empties instancing one collection.


def check(name, value, detail=None):
    report["checks"][name] = bool(value)
    if not value and detail is not None:
        report.setdefault("details", {})[name] = detail


def box(name, location, size=0.5):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    paint = bpy.data.materials.new(name + "Paint")
    paint.use_nodes = True
    obj.data.materials.append(paint)
    return obj, paint


def scatter(count):
    """A geometry-nodes grid of ``count`` points, an instance of the pebble on each (never realized)."""
    host, _paint = box("Scatter", (0, 10, 0))
    tree = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out, grid = tree.nodes.new("NodeGroupOutput"), tree.nodes.new("GeometryNodeMeshGrid")
    place, info = tree.nodes.new("GeometryNodeInstanceOnPoints"), tree.nodes.new("GeometryNodeObjectInfo")
    side = int(round(count ** 0.5))
    grid.inputs["Vertices X"].default_value = grid.inputs["Vertices Y"].default_value = side
    grid.inputs["Size X"].default_value = grid.inputs["Size Y"].default_value = 50.0
    info.inputs["Object"].default_value = bpy.data.objects["Pebble"]
    info.inputs["As Instance"].default_value = True
    tree.links.new(grid.outputs["Mesh"], place.inputs["Points"])
    tree.links.new(info.outputs["Geometry"], place.inputs["Instance"])
    tree.links.new(place.outputs["Instances"], out.inputs[0])
    host.modifiers.new("Scatter", "NODES").node_group = tree
    return side * side


def median_ms(work, rounds, warm):
    for _ in range(warm):
        work()
    times = []
    for _ in range(rounds):
        started = time.perf_counter()
        work()
        times.append((time.perf_counter() - started) * 1000.0)
    return round(statistics.median(times), 3)


def measure(live, name, objects_changed):
    """Decide and export a delta of a colour change of ``objects_changed``'s material, as the link would."""
    server = SimpleNamespace(clients=lambda: {1: {}}, lagging=lambda client: False)
    link = SimpleNamespace(_state={"server": server, "last_ms": 0.0},
                           _followed=lambda window: (bpy.context.scene, bpy.context.view_layer))
    live._clients = lambda link_, found: [1]
    found, path = {"asset": "Cost", "number": len(report["timings"]) + 1, "params": {}}, str(OUT / "live.glb")
    version = [0]

    def frame():
        frames = live.live_frames(link, None, path, found, version[0])
        if frames:
            version[0] += 1
        return frames
    frame()   # The page's first, whole frame.
    target, paint = objects_changed
    scene, layer = bpy.context.scene, bpy.context.view_layer
    objects = list(scene.objects)

    def decide():
        live._session["dirty"], live._session["dirty_data"] = set(), {paint.session_uid}
        return live.changed_objects(objects, layer.depsgraph)
    decided = decide()
    shade = [0.0]

    def delta():
        shade[0] = (shade[0] + 0.07) % 1.0
        paint.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (shade[0], 0.5, 0.5, 1)
        layer.update()
        live._session["dirty"], live._session["dirty_data"] = set(), {paint.session_uid}
        return frame()
    sent = delta()
    report["timings"][name] = {"decide_ms": median_ms(decide, 9, 2), "delta_ms": median_ms(delta, 5, 1)}
    return decided, sent


def main():
    import addon_utils
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import web_link_live as live
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for index in range(120):
        box("Box%03d" % index, (index % 12, index // 12, 0))
    bpy.context.view_layer.update()
    target = (bpy.data.objects["Box007"], bpy.data.materials["Box007Paint"])
    decided, _sent = measure(live, "typical_120_boxes", target)
    check("typical_scene_decides_the_box", decided == {bpy.data.objects["Box007"].session_uid}, sorted(decided))
    for count in SIZES:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        pebble, pebble_paint = box("Pebble", (0, -10, 0), size=0.05)
        made = scatter(count)
        unrelated = box("Unrelated", (5, -10, 0))
        bpy.context.view_layer.update()
        name = "scatter_%d" % made
        report.setdefault("instances", {})[name] = sum(   # What the depsgraph made, counted once, never timed.
            1 for instance in bpy.context.view_layer.depsgraph.object_instances if instance.is_instance)
        check("%s_makes_its_instances" % name, report["instances"][name] >= made, report["instances"][name])
        decided, _sent = measure(live, name, unrelated)
        check("%s_decides_the_box_alone" % name, decided == {unrelated[0].session_uid}, sorted(decided))
        live._session["dirty"], live._session["dirty_data"] = set(), {pebble_paint.session_uid}
        reach = live.changed_objects(list(bpy.context.scene.objects), bpy.context.view_layer.depsgraph)
        check("%s_pebble_colour_reaches_the_scatter" % name,
              {pebble.session_uid, bpy.data.objects["Scatter"].session_uid} <= reach, sorted(reach))
    small, large = (report["timings"]["scatter_%d" % size]["decide_ms"] for size in (SIZES[0], SIZES[-1]))
    check("deciding_stays_flat_as_instances_grow", large <= 2.0 * small + 2.0, {"10k": small, "1M": large})
    for count in INSTANCERS:   # Round 10, M2: many instancers of one collection, its member's colour changed.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        kit = bpy.data.collections.new("Kit")
        part, part_paint = box("Part", (0, 0, 0))
        for owner in list(part.users_collection):
            owner.objects.unlink(part)
        kit.objects.link(part)
        for index in range(count):
            empty = bpy.data.objects.new("Copy%05d" % index, None)
            empty.instance_type, empty.instance_collection = "COLLECTION", kit
            empty.location = (index % 100, index // 100, 0)
            bpy.context.scene.collection.objects.link(empty)
        bpy.context.view_layer.update()
        decided, _sent = measure(live, "instancers_%d" % count, (part, part_paint))
        check("instancers_%d_all_reached" % count, len(decided) == count + 1, len(decided))
    few, many = (report["timings"]["instancers_%d" % count]["decide_ms"] for count in INSTANCERS)
    ratio = INSTANCERS[1] / INSTANCERS[0]
    check("deciding_grows_linearly_with_instancers", many <= 1.5 * ratio * few + 5.0, {"few": few, "many": many})


try:
    main()
except Exception:
    report["traceback"] = traceback.format_exc()
ok = bool("traceback" not in report and report["checks"] and all(report["checks"].values()))
report["success"] = ok
(OUT / "web-link-cost.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("web link cost:", "PASS" if ok else "FAIL", json.dumps(report["timings"]), json.dumps(report["checks"]))
sys.exit(0 if ok else 1)
