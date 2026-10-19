# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How operator_run's phase-5 operators scale: each one at n and 4n elements, everything selected.

Run alone, in a disposable instance with the exact Angelo binary::

    Angelo --background --factory-startup --disable-autoexec --python-exit-code 1 \\
      --python tests/python/angelo_agent/benchmark_blender_operator_run_work.py -- [--json OUT] [--only PREFIX,...] [--scale FACTOR]

Each operator runs once through the kernel (``apply_operation``: selection
pass, digests, mode switches and the operator itself) on fresh private staged
copies of each input at both sizes; the ratio of the two times is the growth
(about 4 is linear, about 16 quadratic). Inputs, as in native_coverage.rst
("operator_run work bounds"): meshes as a grid, a random wire tree and loose
points; Grease Pencil strokes and hair curves of ten points each; text
characters; constraint stacks; armature bones; curve points; lattice points.
Proportional editing also runs with half the vertices selected. Times are
wall clock on a shared machine: compare ratios, not single runs.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import action_operator_families as families  # noqa: E402
from angelo_agent.action_kernels import StagedActionContext, apply_operation  # noqa: E402
from angelo_agent.action_operator_spec import WORK_BOUNDS, target_types  # noqa: E402

PROPERTIES = {"transform.translate": {"value": [0, 0, .1]}, "transform.rotate": {"value": .3, "orient_axis": "Z"},
              "transform.resize": {"value": [1.1, 1.1, 1.1]}, "transform.mirror": {"constraint_axis": [True, False, False]},
              "transform.push_pull": {"value": .1}, "transform.tosphere": {"value": .5}, "transform.trackball": {"value": [.2, .1]},
              "transform.shrink_fatten": {"value": .1}, "transform.edge_slide": {"value": .2},
              "transform.edge_crease": {"value": .5}, "transform.edge_bevelweight": {"value": .5},
              "transform.vert_crease": {"value": .5}, "transform.skin_resize": {"value": [1.1, 1.1, 1.1]},
              "transform.rotate_normal": {"value": .2}, "transform.vertex_random": {"offset": .01}, "transform.tilt": {"value": .2},
              "transform.bbone_resize": {"value": [1.1, 1.1, 1.1]}, "grease_pencil.stroke_subdivide": {"number_cuts": 1},
              "curves.subdivide": {"number_cuts": 1}, "font.text_insert": {"text": "Q"}, "font.case_set": {"case": "UPPER"},
              "font.style_set": {"style": "BOLD"}, "grease_pencil.set_uniform_thickness": {"thickness": .2},
              "grease_pencil.cyclical_set": {"type": "CLOSE"}, "grease_pencil.move_to_layer": {"target_layer_name": "Other"}}
PROPORTIONAL = {"use_proportional_edit": True, "proportional_size": .2}


def stage():
    scene = bpy.data.scenes.new("Work Stage")
    collection = bpy.data.collections.new("Work Stage Objects")
    scene.collection.children.link(collection)
    return StagedActionContext(scene, collection)


def adopt(context, data, alias):
    context.track_id(data)
    return context.add_object(context.track_id(bpy.data.objects.new(alias, data)), alias)


def mesh(context, shape, vertices, skin=False):
    data, bm, rng = bpy.data.meshes.new(shape), bmesh.new(), random.Random(7)
    if shape == "grid":
        side = max(int(vertices ** .5) - 1, 1)
        bmesh.ops.create_grid(bm, x_segments=side, y_segments=side, size=10)
    else:
        verts = [bm.verts.new((rng.random() * 10, rng.random() * 10, rng.random())) for _ in range(vertices)]
        if shape == "tree":
            for index in range(1, len(verts)):
                bm.edges.new((verts[index], verts[rng.randrange(index)]))
    if skin:
        bm.verts.layers.skin.verify()
    bm.to_mesh(data)
    bm.free()
    obj = adopt(context, data, "mesh")
    obj.vertex_groups.new(name="Group")
    return obj


def sketch(context, points):
    data = bpy.data.grease_pencils.new("Work Sketch")
    drawing = data.layers.new("Ink", set_active=True).frames.new(context.scene.frame_current).drawing
    data.layers.new("Other")
    drawing.add_strokes([10] * (points // 10))
    rng = random.Random(7)
    for point in drawing.attributes["position"].data:
        point.vector = (rng.random(), rng.random(), 0)
    return adopt(context, data, "sketch")


def hair(context, points):
    data = bpy.data.hair_curves.new("Work Hair")
    data.add_curves([10] * (points // 10))
    rng = random.Random(7)
    for point in data.points:
        point.position = (rng.random(), rng.random(), rng.random())
    return adopt(context, data, "hair")


def text(context, characters):
    data = bpy.data.curves.new("Work Text", "FONT")
    data.body = ("Angelo " * (characters // 7 + 1))[:characters]
    return adopt(context, data, "text")


def owner(context, count):
    obj = mesh(context, "loose", 8)
    for index in range(count):
        item = obj.constraints.new(("LIMIT_LOCATION", "COPY_ROTATION", "CHILD_OF", "LIMIT_DISTANCE")[index % 4])
        item.name = "C%03d" % index
    return obj


def armature(context, bones):
    from angelo_agent.action_operator_modes import mode_override
    rig = adopt(context, bpy.data.armatures.new("Work Rig"), "rig")
    with mode_override(context, rig, "EDIT"):
        for index in range(bones):
            bone = rig.data.edit_bones.new("B%04d" % index)
            bone.head, bone.tail = (index * .1, 0, 0), (index * .1, 0, 1)
    return rig


def curve(context, points):
    data = bpy.data.curves.new("Work Curve", "CURVE")
    data.dimensions = "3D"
    for _ in range(points // 100):
        spline = data.splines.new("BEZIER")
        spline.bezier_points.add(99)
    return adopt(context, data, "curve")


def lattice(context, points):
    data = bpy.data.lattices.new("Work Lattice")
    side = max(round(points ** (1 / 3)), 2)
    data.points_u = data.points_v = data.points_w = min(side, 64)
    return adopt(context, data, "lattice")


def inputs(idname):
    """(label, builder(context, size), base size) for every input an operator scales with."""
    kind = target_types(idname)[0]
    if idname.startswith("constraint."):
        return [("stack", owner, 16)]
    if idname == "object.skin_root_mark":  # Bounded at 2,048 vertices (families.RECURSION_BOUNDS).
        return [("grid", lambda context, n: mesh(context, "grid", n, skin=True), 512)]
    if idname.startswith("object.skin_") or idname == "transform.skin_resize":
        return [("grid", lambda context, n: mesh(context, "grid", n, skin=True), 20000)]
    if kind == "MESH":
        return [(shape, lambda context, n, shape=shape: mesh(context, shape, n), 20000) for shape in ("grid", "tree", "loose")]
    return {"GREASEPENCIL": [("strokes", sketch, 5000)], "CURVES": [("hair", hair, 20000)], "FONT": [("text", text, 1000)],
            "ARMATURE": [("bones", armature, 256)], "CURVE": [("curve", curve, 5000)],
            "LATTICE": [("lattice", lattice, 1000)]}.get(kind, [])


def timed(idname, builder, size, properties, half=False):
    context = stage()
    try:
        target = builder(context, size)
        name = next(alias for alias, obj in context.aliases.items() if obj == target)
        if half and target.type == "MESH":
            flags = [index % 2 == 0 for index in range(len(target.data.vertices))]
            target.data.vertices.foreach_set("select", flags)
        parameters = {"operator": idname, "properties": properties}
        if idname.startswith("constraint."):
            parameters["properties"] = dict(properties, constraint="C001")
        if half:
            parameters["select"] = "KEEP"
        started = time.perf_counter()
        try:
            apply_operation(context, {"kind": "operator_run", "target": name, "parameters": parameters})
            outcome = "OK"
        except (ValueError, RuntimeError) as error:
            outcome = str(error).split(":")[0].replace("operator_run ", "")[:40]
        return round(time.perf_counter() - started, 4), outcome
    finally:
        bpy.data.batch_remove(ids=list(set(context.owned_ids) | set(context.created_ids)))


def measure(idname, scale=1):
    rows = []
    variants = [("", PROPERTIES.get(idname, {}), False)]
    if idname in {"transform.translate", "transform.resize"}:
        variants += [("proportional", dict(PROPERTIES[idname], **PROPORTIONAL), False),
                     ("proportional_half", dict(PROPERTIES[idname], **PROPORTIONAL), True),
                     ("connected_half", dict(PROPERTIES[idname], **PROPORTIONAL, use_proportional_connected=True), True)]
    for label, builder, size in inputs(idname):
        size = int(size * scale)
        for variant, properties, half in variants:
            small, outcome = timed(idname, builder, size, properties, half)
            large, outcome_large = timed(idname, builder, size * 4, properties, half)
            rows.append({"input": label + ("+" + variant if variant else ""), "n": size, "seconds_n": small,
                         "seconds_4n": large, "ratio": round(large / small, 1) if small > 1e-4 else None,
                         "outcome": outcome_large})
    return rows


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    parser.add_argument("--only", default="")
    parser.add_argument("--scale", type=float, default=1, help="multiply every base size")
    args = parser.parse_args(argv)
    bpy.data.curves.remove(bpy.data.curves.new("Font warm-up", "FONT"))
    for name in families.WORK_BOUNDS:  # Measure the operator itself, not the bound that refuses it.
        WORK_BOUNDS[name] = float("inf")
    report = {}
    for idname in sorted(families.ALLOWED):
        if idname.startswith(tuple(args.only.split(","))):
            report[idname] = measure(idname, args.scale)
            print("ANGELO_OPERATOR_WORK", idname, json.dumps(report[idname]), flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True))


main()
