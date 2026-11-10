# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything that follows a path with no segment reads defined, finite values.

A path whose points all sit at one place has a single bevel point, so ``BKE_where_on_path`` places nothing and
returns false. Text on curve ignored that and laid its characters out from uninitialised stack values: on Windows
5.3 a text following such a path got an auto texture space of (-inf, -inf, 1), so its exact revision was refused
(``test_blender_borrowed_materials_final_review``'s text_follow_curve step, under the native runner only). The
function now defines every output when it fails, and text keeps its straight layout there.

Checks, in the real binary (background; writes results.json under ANGELO_QA_DIR), with paths of two coincident
points and of one point:

* a text following it has a finite texture space and finite evaluated geometry, the same as with no path;
* held out: a Spline IK chain, a zero-length bone included, and a Follow Path constraint on it pose finitely;
* a text following a real path still bends along it.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
import tempfile
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-path-without-segment-qa"))
OUT.mkdir(parents=True, exist_ok=True)


def finite(values):
    return all(math.isfinite(value) for value in values)


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def path(name, points):
    data = bpy.data.curves.new(name, "CURVE")
    spline = data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, co in zip(spline.points, points):
        point.co = (*co, 1.0)
    return link(bpy.data.objects.new(name, data))


def text(name, follow=None):
    data = bpy.data.curves.new(name, "FONT")
    data.body = "Hi there"
    data.follow_curve = follow
    return link(bpy.data.objects.new(name, data))


def evaluated_bounds(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        coordinates = [value for vertex in mesh.vertices for value in vertex.co]
        return len(mesh.vertices), coordinates
    finally:
        evaluated.to_mesh_clear()


def text_case(follow):
    obj = text("Text", follow)
    bpy.context.view_layer.update()
    count, coordinates = evaluated_bounds(obj)
    texspace = list(obj.data.texspace_location) + list(obj.data.texspace_size)
    return obj, {"vertices": count, "finite_geometry": finite(coordinates), "texspace": texspace,
                 "finite_texspace": finite(texspace),
                 "box": [round(min(coordinates[axis::3]), 5) for axis in range(2)]
                 + [round(max(coordinates[axis::3]), 5) for axis in range(2)] if coordinates else None}


def rig_case(follow):
    armature = bpy.data.armatures.new("Rig")
    rig = link(bpy.data.objects.new("Rig", armature))
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    previous, height = None, 0.0
    for index, length in enumerate((0.5, 1e-7, 0.5)):   # The middle bone is shorter than FLT_EPSILON.
        bone = armature.edit_bones.new("Bone%d" % index)
        bone.head, bone.tail = (0.0, 0.0, height), (0.0, 0.0, height + length)
        bone.parent, bone.use_connect = previous, previous is not None
        previous, height = bone, height + length
    bpy.ops.object.mode_set(mode="OBJECT")
    spline_ik = rig.pose.bones["Bone2"].constraints.new("SPLINE_IK")
    spline_ik.target, spline_ik.chain_count = follow, 3
    follower = link(bpy.data.objects.new("Follower", None))
    follow_path = follower.constraints.new("FOLLOW_PATH")
    follow_path.target, follow_path.use_curve_follow = follow, True
    bpy.context.view_layer.update()
    pose = [value for bone in rig.pose.bones for row in bone.matrix for value in row]
    placed = [value for row in follower.matrix_world for value in row]
    bpy.data.objects.remove(rig)
    bpy.data.objects.remove(follower)
    return {"finite_pose": finite(pose), "finite_follow_path": finite(placed)}


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    checks, observed = {}, {}
    plain, observed["no path"] = text_case(None)
    bpy.data.objects.remove(plain)
    for name, points in (("coincident", [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]), ("single", [(0.4, 0.2, 0.0)])):
        follow = path("Path " + name, points)
        obj, row = text_case(follow)
        observed["text on " + name] = row
        checks["text_on_%s_path_finite_texspace" % name] = row["finite_texspace"]
        checks["text_on_%s_path_finite_geometry" % name] = row["finite_geometry"]
        checks["text_on_%s_path_keeps_its_layout" % name] = row["box"] == observed["no path"]["box"]
        bpy.data.objects.remove(obj)
        rig = rig_case(follow)
        observed["rig on " + name] = rig
        checks["spline_ik_on_%s_path_finite" % name] = rig["finite_pose"]
        checks["follow_path_on_%s_path_finite" % name] = rig["finite_follow_path"]
        bpy.data.objects.remove(follow)
    follow = path("Path real", [(0.0, 0.0, 0.0), (2.0, 2.0, 0.0), (4.0, 0.0, 0.0)])
    obj, row = text_case(follow)
    observed["text on a real path"] = row
    checks["text_on_a_real_path_bends"] = row["finite_texspace"] and row["box"] != observed["no path"]["box"]
    state = {"success": all(checks.values()), "checks": checks, "observed": observed}
    (OUT / "results.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    print("ANGELO_PATH_WITHOUT_SEGMENT", json.dumps({"success": state["success"],
                                                     "failed": [k for k, v in checks.items() if not v]}), flush=True)


main()
