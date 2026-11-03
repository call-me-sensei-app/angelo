# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reads one armature and the meshes skinned to it into the plain facts the engine-skeleton check takes
(``skeleton_check.check``). Main thread; reads only: nothing in the scene, its selection or Undo changes.

* Bones at rest, in world space (``Bone.matrix_local`` through the armature's world matrix), whatever the pose.
* Per skinned mesh (an Armature modifier using this armature), a histogram of how many deforming bones weight each
  vertex (weights above 0), read from the mesh's own vertex groups, and which bones move anything at all: a weight on
  any vertex, or an object parented to the bone (a rigid part).
* Enabled constraints with influence and drivers on pose bones, B-Bone segments, the armature's world rotation and
  scale, and the scene's unit scale.
"""

import math

import numpy as np

from .skeleton_roles import Rig

MAX_BONES = 2048
MAX_VERTICES = 500_000   # vertices counted for influences per call; beyond it the influence check says so


class FactsError(ValueError):
    pass


def skinned_meshes(scene, armature):
    return [obj for obj in scene.objects if obj.type == "MESH" and any(
        m.type == "ARMATURE" and m.object == armature for m in obj.modifiers)]


def armatures(scene):
    return [obj for obj in scene.objects if obj.type == "ARMATURE"]


def _rotation_degrees(matrix):
    _, rotation, _ = matrix.decompose()
    return [math.degrees(a) for a in rotation.to_euler("XYZ")]


def gather(scene, armature):
    bones = list(armature.data.bones)
    if len(bones) > MAX_BONES:
        raise FactsError("The armature has more than %d bones" % MAX_BONES)
    if not bones:
        raise FactsError("The armature has no bones")
    world = np.array(armature.matrix_world, dtype=np.float64)
    linear = world[:3, :3]
    index = {b.name: i for i, b in enumerate(bones)}
    rest = np.array([np.array(b.matrix_local, dtype=np.float64) for b in bones])
    heads = rest[:, :3, 3] @ linear.T + world[:3, 3]
    tails = np.array([tuple(b.tail_local) for b in bones], dtype=np.float64) @ linear.T + world[:3, 3]
    axes = np.einsum("ij,njk->nik", linear, rest[:, :3, :3])
    axes /= np.maximum(np.linalg.norm(axes, axis=1, keepdims=True), 1e-12)   # unit columns: X, Y, Z
    deform = [b.use_deform for b in bones]
    meshes, weighted, note = _influences(scene, armature, bones, index, deform)
    carried = [index[obj.parent_bone] for obj in scene.objects   # a rigid part parented to a bone uses it too
               if obj.parent == armature and obj.parent_type == "BONE" and obj.parent_bone in index]
    if carried:
        weighted = np.zeros(len(bones), dtype=bool) if weighted is None else weighted
        weighted[carried] = True
    rig = Rig([b.name for b in bones], [index[b.parent.name] if b.parent else -1 for b in bones], heads, tails, axes,
              deform, weighted, world[:3, 3])
    live, driven = _driven(armature, index)
    _, _, scale = armature.matrix_world.decompose()
    facts = {"armature": armature.name, "rig": rig,
             "object": {"rotation": _rotation_degrees(armature.matrix_world), "scale": list(scale)},
             "unit_scale": float(scene.unit_settings.scale_length) if scene.unit_settings.system != "NONE" else 1.0,
             "constraints": live, "drivers": driven,
             "bbones": {i: b.bbone_segments for i, b in enumerate(bones) if b.bbone_segments > 1},
             "meshes": meshes}
    if note:
        facts["note"] = note
    return facts


def _influences(scene, armature, bones, index, deform):
    """([{name, histogram}], weighted per bone or None, note) for the meshes skinned to ``armature``."""
    meshes = skinned_meshes(scene, armature)
    if not meshes:
        return [], None, ""
    weighted = np.zeros(len(bones), dtype=bool)
    out, counted = [], 0
    for obj in meshes:
        data = obj.data
        if data.is_editmode:
            return [], None, "a skinned mesh is in Edit Mode: its weights were not read"
        counted += len(data.vertices)
        if counted > MAX_VERTICES:
            return [], None, "the skinned meshes have more than %d vertices: influences were not counted" % MAX_VERTICES
        groups = {g.index: index.get(g.name) for g in obj.vertex_groups}
        deforming = {gi: bi for gi, bi in groups.items() if bi is not None and deform[bi]}
        histogram = {}
        for vertex in data.vertices:
            n = 0
            for g in vertex.groups:
                bone = deforming.get(g.group)
                if bone is not None and g.weight > 0.0:
                    n += 1
                    weighted[bone] = True
            histogram[n] = histogram.get(n, 0) + 1
        out.append({"name": obj.name, "histogram": histogram})
    return out, weighted, ""


def _driven(armature, index):
    """({bone index: live constraint count}, {bone indices with a driver})."""
    live = {}
    for pose_bone in armature.pose.bones:
        count = sum(1 for c in pose_bone.constraints if c.enabled and c.influence > 0)
        if count and pose_bone.name in index:
            live[index[pose_bone.name]] = count
    driven = set()
    for driver in getattr(armature.animation_data, "drivers", None) or ():
        path = driver.data_path
        if path.startswith('pose.bones["'):
            name = path[len('pose.bones["'):].split('"]', 1)[0]
            if name in index:
                driven.add(index[name])
    return live, driven
