# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Skinned hand scenes for the RIG suites and trials, built in Blender from ``hand_fixtures``' bone layouts.

The skin is a metaball body converted to a mesh: an ellipsoid palm as thick as a hand's, a thenar mound at the
thumb's base and a capsule along every digit bone, blended into webs where they meet, as a sculpted or generated
hand is one surface. It is bound with automatic (heat) weights, as an artist or an agent binds a mesh. Layouts:

* ``HANGING``: arms down, palms toward the thighs (the rigs' rest pose), both hands.
* ``T_POSE``: arms out, palms down, both hands.
* ``CARTOON``: hanging, three fingers and a thumb, chubby.
* ``MITTEN``: hanging, the four fingers one blob (as image-to-3D makes them).
* ``THIGH``: hanging, a thigh a few millimetres from the left palm, bound with the rest (heat weights leak).

Every builder empties the file's objects first and returns {"armature": obj, "mesh": obj}.
"""

import math

import bpy
import numpy as np

import hand_fixtures as F

LAYOUTS = ("HANGING", "T_POSE", "CARTOON", "MITTEN", "THIGH")


def _armature(builders, name="Rig"):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    for b in builders:
        for i, bone in enumerate(b.names):
            if bone in data.edit_bones:
                continue
            edit = data.edit_bones.new(bone)
            edit.head, edit.tail = b.heads[i], b.tails[i]
            edit.use_deform = b.deform[i]
        for i, bone in enumerate(b.names):
            if b.parents[i] >= 0:
                data.edit_bones[bone].parent = data.edit_bones[b.names[b.parents[i]]]
                data.edit_bones[bone].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def _quaternion(x_axis, z_hint):
    """(w, x, y, z) of the rotation taking local x to ``x_axis`` with local z toward ``z_hint``."""
    from mathutils import Matrix
    x = np.asarray(x_axis, float) / np.linalg.norm(x_axis)
    z = np.asarray(z_hint, float) - x * float(np.dot(z_hint, x))
    if np.linalg.norm(z) < 1e-9:
        z = np.cross(x, (0.0, 0.0, 1.0)) if abs(x[2]) < 0.9 else np.cross(x, (1.0, 0.0, 0.0))
    z /= np.linalg.norm(z)
    y = np.cross(z, x)
    return Matrix(np.stack([x, y, z], axis=1).tolist()).to_quaternion()


def _metaball_hand(builder, name, finger_r, palm_thick, fused=False):
    """One arm's hand and forearm as a metaball object (its own family, so it never blends with another's)."""
    names = builder.names
    data = bpy.data.metaballs.new(name)
    data.resolution = data.render_resolution = finger_r * 0.25
    data.threshold = 0.6
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)

    def at(n, end="head"):
        return (builder.heads if end == "head" else builder.tails)[names.index(n)]
    side = next(n for n in names if n.endswith("Hand"))[:-4]
    wrist, knuckle_mid = at(side + "Hand"), at(side + "Hand", "tail")
    along = (knuckle_mid - wrist) / np.linalg.norm(knuckle_mid - wrist)
    fingers = [n for n in names if n.startswith(side + "Hand") and n.endswith("1") and "Thumb" not in n]
    across = at(fingers[-1]) - at(fingers[0])
    width = float(np.linalg.norm(across))
    across /= width
    normal = np.cross(across, along)
    thumb_side = -across
    if np.dot(at(side + "HandThumb2") - wrist, normal) < 0:   # out of the palm, the side the thumb sits forward on
        normal = -normal

    def capsule(a, b, r):
        e = data.elements.new(type="CAPSULE")
        e.co = tuple((a + b) / 2)
        e.radius = r
        e.size_x = float(np.linalg.norm(b - a)) / 2
        e.rotation = _quaternion(b - a, normal)
        return e

    def ellipsoid(centre, sx, sy, sz, r):
        """Semi-axes sx, sy, sz (metres): an ellipsoid's sizes scale its radius."""
        e = data.elements.new(type="ELLIPSOID")
        e.co = tuple(centre)
        e.radius = r
        e.size_x, e.size_y, e.size_z = sx / r, sy / r, sz / r
        e.rotation = _quaternion(across, normal)
        return e

    capsule(at(side + "ForeArm"), wrist, palm_thick * 1.05)
    palm = (wrist + knuckle_mid) / 2 - normal * palm_thick * 0.1
    # A metaball's surface lies well inside its nominal size (SHRINK, measured on these elements), so each size is
    # its wanted surface extent over that.
    shrink = 0.72
    ellipsoid(palm, (width / 2 + finger_r * 1.2) / shrink, float(np.linalg.norm(knuckle_mid - wrist)) * 0.55 / shrink,
              palm_thick * 0.5 / shrink, palm_thick)
    capsule(at(fingers[0]), at(fingers[-1]), finger_r * 1.35)   # the knuckle row, bridging palm and fingers
    ellipsoid(at(side + "HandThumb1") * 0.5 + at(side + "HandThumb2") * 0.5 + normal * palm_thick * 0.1,
              palm_thick * 0.45 / shrink, palm_thick * 0.6 / shrink, palm_thick * 0.4 / shrink, palm_thick)
    for n in names:
        if not (n.startswith(side + "Hand") and n.endswith("1")):
            continue
        thumb = "Thumb" in n
        chain = [n, n[:-1] + "2", n[:-1] + "3"]
        r = finger_r * (1.2 if thumb else 1.0) * (2.2 if fused and not thumb else 1.0)
        for i, bone in enumerate(chain):
            a, b = at(bone), at(bone, "tail")
            if i == len(chain) - 1:
                b = a + (b - a) * 0.8   # rounded tip: the capsule's cap reaches the bone's tail
            capsule(a, b, r * (0.92 if i == 2 else 1.0))
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target="MESH")
    mesh = bpy.context.view_layer.objects.active
    mesh.name = name
    return mesh


def build(layout="HANGING", seed=0):
    """A scene with one armature and the skinned mesh of the layout (the file's objects removed first)."""
    if layout not in LAYOUTS:
        raise ValueError("layout must be one of " + ", ".join(LAYOUTS))
    for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.metaballs):
        for item in list(collection):
            collection.remove(item)
    cartoon = layout == "CARTOON"
    builders = [F.hand_bones(side=s, fingers=3 if cartoon else 4, t_pose=layout == "T_POSE", roll_seed=seed + k,
                             **({"middle": 0.045, "palm": 0.05} if cartoon else {}))
                for k, s in enumerate(("Left", "Right"))]
    arm = _armature(builders)
    meshes = []
    for b in builders:
        meshes.append(_metaball_hand(b, "Hand" + b.names[2][:4], 0.0095 if cartoon else 0.0075, 0.024,
                                     fused=layout == "MITTEN"))
    if layout == "THIGH":
        wrist = builders[0].heads[builders[0].names.index("LeftHand")]
        palm_x = min(v.co.x for v in meshes[0].data.vertices if abs(v.co.z - (wrist[2] - 0.035)) < 0.004)
        data = bpy.data.metaballs.new("Thigh")
        data.resolution = 0.004
        thigh = bpy.data.objects.new("Thigh", data)
        bpy.context.scene.collection.objects.link(thigh)
        e = data.elements.new(type="CAPSULE")
        e.radius, e.size_x = 0.07, 0.15
        e.co = (palm_x - 0.003 - 0.07 * 0.62, 0.0, wrist[2] - 0.06)   # a metaball's surface sits inside its radius
        e.rotation = _quaternion((0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
        bpy.context.view_layer.update()
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = thigh
        thigh.select_set(True)
        bpy.ops.object.convert(target="MESH")
        meshes.append(bpy.context.view_layer.objects.active)
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = body.data.name = "Body"
    bpy.ops.object.shade_smooth()
    body.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    return {"armature": arm, "mesh": body}
