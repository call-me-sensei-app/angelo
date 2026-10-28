# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An object's size counts each evaluated component's radius exactly where it draws, on every object type (background).

A radius is part of an object's size only where it is a drawn extent, and that depends on how each component of the
evaluated geometry draws, which is the same for every original object type: points draw as spheres of their radius
and Grease Pencil strokes as thick strokes, so both keep it; Geometry Nodes curves draw as tubes or hair only as a
Curves object's own data, and on every other object (mesh, empty, point cloud, volume, Grease Pencil, a legacy curve's
or a text's own curves) as a wire of their positions, so there they read without it. The maintainer's audits of
e25d51566617 (P2) and 7413ee59dd7 found the rule chosen by the original type: a text whose nodes leave one point of
radius 2 read zero-sized instead of 4 m across, while a mesh whose nodes leave a 1 m curve of radius 2 read 5 x 4 x 4.

Every original type (mesh, curve, text, surface, empty, curves, point cloud, volume, Grease Pencil; a surface takes no
nodes modifier, which is recorded) runs every shape: one point of radius 2; a curve line of radius 2; that line joined
with a point; two points of different radii (the per-point radius path); a point of negative radius beside one of radius
1 (a radius only widens a box). A legacy curve and a text outline (Fill Mode None) are joined with a point, so their own
curves and the points sit in one geometry; a Grease Pencil object's own strokes have radius 2. Every reader (the
evaluated object's dimensions and exact bound box, the original's dimensions, Angelo's world_bounds where it covers the
type) must state the exact expected min and max.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.gettempdir())
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import object_bounds  # noqa: E402

TOLERANCE = 1e-3
KINDS = ('MESH', 'CURVE', 'FONT', 'SURFACE', 'EMPTY', 'CURVES', 'POINTCLOUD', 'VOLUME', 'GREASEPENCIL')
LINE = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 2.0)   # A 1 m Geometry Nodes curve line of radius 2.
FAR_POINT = ((10.0, 0.0, 0.0), 2.0)
report = {"checks": {}, "readings": {}}


def node_group(name, points=(), line=None, keep_input=False, strokes=False):
    """Nodes whose output joins the modifier's input (``keep_input``), one point per (position, radius), and a curve
    line (start, end, radius), turned into Grease Pencil strokes with ``strokes``."""
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    join = tree.nodes.new('GeometryNodeJoinGeometry')
    tree.links.new(join.outputs[0], tree.nodes.new('NodeGroupOutput').inputs[0])
    if keep_input:
        tree.links.new(tree.nodes.new('NodeGroupInput').outputs[0], join.inputs[0])
    for position, radius in points:
        node = tree.nodes.new('GeometryNodePoints')
        node.inputs['Count'].default_value = 1
        node.inputs['Position'].default_value = position
        node.inputs['Radius'].default_value = radius
        tree.links.new(node.outputs[0], join.inputs[0])
    if line is not None:
        start, end, radius = line
        curve = tree.nodes.new('GeometryNodeCurvePrimitiveLine')
        curve.inputs['Start'].default_value = start
        curve.inputs['End'].default_value = end
        set_radius = tree.nodes.new('GeometryNodeSetCurveRadius')
        set_radius.inputs['Radius'].default_value = radius
        tree.links.new(curve.outputs[0], set_radius.inputs['Curve'])
        last = set_radius
        if strokes:
            last = tree.nodes.new('GeometryNodeCurvesToGreasePencil')
            last.inputs['Instances as Layers'].default_value = False   # Plain curves: one layer of strokes.
            tree.links.new(set_radius.outputs[0], last.inputs[0])
        tree.links.new(last.outputs[0], join.inputs[0])
    return tree


def data_for(kind, name):
    if kind == 'MESH':
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(0, 0, 0), (0.1, 0, 0), (0, 0.1, 0)], [], [(0, 1, 2)])
        return mesh
    if kind == 'CURVE':   # A poly line from the origin to (1, 0, 0), control-point radius 1 (the default).
        curve = bpy.data.curves.new(name, 'CURVE')
        curve.dimensions = '3D'
        spline = curve.splines.new('POLY')
        spline.points.add(1)
        spline.points[0].co = (0, 0, 0, 1)
        spline.points[1].co = (1, 0, 0, 1)
        return curve
    if kind == 'FONT':
        text = bpy.data.curves.new(name, 'FONT')
        text.body = "A"
        return text
    if kind == 'SURFACE':
        surface = bpy.data.curves.new(name, 'SURFACE')
        spline = surface.splines.new('NURBS')
        spline.points.add(3)
        for index, point in enumerate(spline.points):
            point.co = (index * 0.1, 0, 0, 1)
        return surface
    if kind == 'EMPTY':
        return None
    if kind == 'CURVES':
        return bpy.data.hair_curves.new(name)
    if kind == 'POINTCLOUD':
        return bpy.data.pointclouds.new(name)
    if kind == 'VOLUME':
        return bpy.data.volumes.new(name)
    if kind == 'GREASEPENCIL':
        return bpy.data.grease_pencils.new(name)
    raise ValueError(kind)


def padded(low, high, radius):
    return [value - radius for value in low], [value + radius for value in high]


def union(*boxes):
    return ([min(box[0][axis] for box in boxes) for axis in range(3)],
            [max(box[1][axis] for box in boxes) for axis in range(3)])


def line_box(kind):
    """The line draws as hair (radius 2 an extent) only on a Curves object; everywhere else as a wire."""
    start, end, radius = LINE
    low = [min(start[axis], end[axis]) for axis in range(3)]
    high = [max(start[axis], end[axis]) for axis in range(3)]
    return padded(low, high, radius) if kind == 'CURVES' else (low, high)


def point_box(position, radius):
    return padded(position, position, radius)


# Each shape: (label, node-group keyword arguments, expected (min, max) for an original type).
SHAPES = (
    ("one point of radius 2", {"points": [((0.0, 0.0, 0.0), 2.0)]},
     lambda kind: point_box((0.0, 0.0, 0.0), 2.0)),
    ("curve line of radius 2", {"line": LINE}, line_box),
    ("curve line joined with a point", {"line": LINE, "points": [FAR_POINT]},
     lambda kind: union(line_box(kind), point_box(*FAR_POINT))),
    ("two points of radius 0.5 and 3", {"points": [((0.0, 0.0, 0.0), 0.5), ((10.0, 0.0, 0.0), 3.0)]},
     lambda kind: union(point_box((0.0, 0.0, 0.0), 0.5), point_box((10.0, 0.0, 0.0), 3.0))),
    # A radius only widens: a negative one never shrinks the box past its point (REVIEW-C3-GN-RADIUS-2 L1).
    ("a point of radius -2 and one of radius 1", {"points": [((0.0, 0.0, 0.0), -2.0), ((10.0, 0.0, 0.0), 1.0)]},
     lambda kind: union(point_box((0.0, 0.0, 0.0), 0.0), point_box((10.0, 0.0, 0.0), 1.0))),
)


def local_box(evaluated):
    corners = [tuple(corner) for corner in evaluated.bound_box]
    return [min(c[axis] for c in corners) for axis in range(3)], [max(c[axis] for c in corners) for axis in range(3)]


def close(found, expected):
    return found is not None and all(abs(a - b) <= TOLERANCE for a, b in zip(found, expected))


def record(name, reader, ok, found):
    key = "%s: %s" % (name, reader)
    report["checks"][key] = bool(ok)
    report["readings"][key] = found


def check_object(name, obj, depsgraph, expected):
    """Every reader states ``expected`` (min, max), at the origin with identity transforms."""
    low, high = expected
    size = [high[axis] - low[axis] for axis in range(3)]
    evaluated = obj.evaluated_get(depsgraph)
    box = local_box(evaluated)
    record(name, "evaluated bound box min/max", close(box[0], low) and close(box[1], high),
           [[round(v, 4) for v in box[0]], [round(v, 4) for v in box[1]]])
    dims = list(evaluated.dimensions)
    record(name, "evaluated dimensions", close(dims, size), [round(v, 4) for v in dims])
    dims = list(obj.dimensions)
    record(name, "original dimensions", close(dims, size), [round(v, 4) for v in dims])
    if obj.type in object_bounds.GEOMETRY_TYPES:   # world_bounds covers geometry object types (no empty).
        found = object_bounds.world_bounds(obj, depsgraph=depsgraph)
        record(name, "world_bounds min/max", found is not None and close(found[0], low) and close(found[1], high),
               found)
    report["readings"]["%s: expected min/max" % name] = [low, high]


def add_object(scene, name, data, group):
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    if group is not None:
        modifier = obj.modifiers.new("Nodes", 'NODES')
        if modifier is None:
            bpy.data.objects.remove(obj)
            return None
        modifier.node_group = group
    return obj


def text_outline(name):
    text = bpy.data.curves.new(name, 'FONT')
    text.body, text.fill_mode = "I", 'NONE'   # The outline stays as the text's own curves.
    return text


def wire_box(obj, depsgraph):
    """The positions the outline draws as a wire, read through its evaluated mesh (no radius involved)."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        positions = [tuple(vertex.co) for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
    return ([min(p[axis] for p in positions) for axis in range(3)],
            [max(p[axis] for p in positions) for axis in range(3)]) if positions else None


def run():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj)
    cases = []
    for label, arguments, expect in SHAPES:
        group = node_group("GN Radius " + label, **arguments)
        for kind in KINDS:
            name = "%s, %s" % (kind, label)
            try:
                obj = add_object(scene, name, data_for(kind, name), group)
            except Exception:   # noqa: BLE001 - a construction miss fails its check, by name.
                record(name, "built", False, traceback.format_exc()[-400:])
                continue
            if obj is None:   # Blender takes no nodes modifier on this type (a surface): nothing to measure.
                report["readings"]["%s: takes no Geometry Nodes modifier" % kind] = True
                continue
            cases.append((name, obj, expect(kind)))
    # A legacy curve's own poly line (0..1 m, control-point radius 1, a bevel scale) joined with a point.
    joined = node_group("GN Radius join", points=[FAR_POINT], keep_input=True)
    curve = add_object(scene, "CURVE own line joined with a point", data_for('CURVE', "Joined Curve"), joined)
    cases.append(("CURVE own line joined with a point", curve,
                  union(([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]), point_box(*FAR_POINT))))
    # A text outline joined with a point: the outline's part is its drawn wire, read from the control's mesh.
    outline = add_object(scene, "FONT outline joined with a point", text_outline("Joined Outline"), joined)
    control = add_object(scene, "FONT outline, no nodes", text_outline("Control Outline"), None)
    # A Grease Pencil object's own strokes, radius 2: they draw that thick.
    strokes = add_object(scene, "GREASEPENCIL own strokes of radius 2", data_for('GREASEPENCIL', "Strokes"),
                         node_group("GN Radius strokes", line=LINE, strokes=True))
    cases.append(("GREASEPENCIL own strokes of radius 2", strokes, padded(LINE[0], LINE[1], LINE[2])))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    wire = wire_box(control, depsgraph)
    record("FONT outline, no nodes", "has a wire", wire is not None, wire)
    if wire is not None:
        check_object("FONT outline, no nodes", control, depsgraph, wire)
        cases.append(("FONT outline joined with a point", outline, union(wire, point_box(*FAR_POINT))))
    for name, obj, expected in cases:
        check_object(name, obj, depsgraph, expected)
    # The premise of the curves rule (REVIEW-C3-GN-RADIUS-2 L2): on an object that is not a Curves object, the curves
    # its nodes leave draw as an instance of a temporary legacy curve with no mesh (a wire). Should upstream draw
    # them as hair, this fails and the rule must follow.
    for kind in ('MESH', 'EMPTY'):
        owner = scene.objects.get("%s, curve line of radius 2" % kind)
        if owner is None:
            continue
        drawn = [instance.object.type for instance in depsgraph.object_instances
                 if instance.is_instance and instance.parent is not None and instance.parent.original == owner]
        record("%s, curve line of radius 2" % kind, "draws its curves as a legacy curve instance",
               drawn == ['CURVE'], drawn)
    for kind in ('MESH', 'EMPTY', 'VOLUME'):   # Recorded: which components an object keeps (no expectation).
        obj = scene.objects.get("%s, curve line joined with a point" % kind)
        if obj is not None:
            geometry = obj.evaluated_get(depsgraph).evaluated_geometry()
            report["readings"]["%s keeps" % kind] = sorted(
                part for part in ("mesh", "curves", "pointcloud", "volume", "grease_pencil")
                if getattr(geometry, part, None) is not None)
    report["success"] = bool(report["checks"]) and all(report["checks"].values())


try:
    run()
except Exception:   # noqa: BLE001 - recorded, the run fails.
    report["error"] = traceback.format_exc()
    report["success"] = False
(OUT / "gn-radius-bounds.json").write_text(json.dumps(report, indent=2))
print("ANGELO_GN_RADIUS_BOUNDS", json.dumps({"success": report["success"]}), flush=True)
if not report["success"]:
    sys.exit(1)
