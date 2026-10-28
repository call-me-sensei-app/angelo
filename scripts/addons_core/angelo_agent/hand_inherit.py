# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How Blender composes a pose bone's matrix from its parent's, for the RIG tools' forward kinematics: a numpy port
of ``BKE_bone_parent_transform_calc_from_matrices`` and ``BKE_bone_parent_transform_apply`` (blenkernel
armature.cc), with ``orthogonalize_m4_stable`` and ``mat4_to_size_fix_shear`` (blenlib math_matrix_c.cc).

Every setting Blender applies there is honoured: Inherit Rotation (``use_inherit_rotation``, off = a hinge), every
Inherit Scale mode, Local Location (``use_local_location``) and Connected (``use_connect``: the location channel is
ignored, as ``BKE_pchan_to_mat4`` ignores it). Relative Parenting changes only objects parented to the bone, never
a pose bone; constraints and drivers run after this composition and are refused by the reader (``rig_read``), since
a bone_set rotation under them would be overridden. A prediction made here is Blender's evaluated pose for the
same bases, so the tools never report a pose a bone's settings would not give.

Matrices are numpy 4x4 in armature space; a Blender matrix's ``m[i]`` (an axis) is column ``i`` here.
"""

import math

import numpy as np

from .hand_math import rotation_part

INHERIT_SCALE = ("FULL", "FIX_SHEAR", "ALIGNED", "AVERAGE", "NONE", "NONE_LEGACY")
FLT_EPSILON = 1.1920929e-07


def _normalized(v):
    length = float(np.linalg.norm(v))
    return (v / length, length) if length > 1.0e-35 else (np.zeros(3), 0.0)


def orthogonalize_stable(m, normalize):
    """``orthogonalize_m4_stable(m, 1, normalize)`` on a 3x3 (columns are the axes; Y is the primary)."""
    v1, v2, v3 = m[:, 1].copy(), m[:, 0].copy(), m[:, 2].copy()
    len_sq = float(v1 @ v1)
    if len_sq > 0.0:
        v2 = v2 - v1 * (float(v2 @ v1) / len_sq)
        v3 = v3 - v1 * (float(v3 @ v1) / len_sq)
        if normalize:
            v1 = v1 / math.sqrt(len_sq)
    n2, l2 = _normalized(v2)
    n3, l3 = _normalized(v3)
    cos_angle = float(n2 @ n3)
    if 1e-4 < abs(cos_angle) < 1.0 - FLT_EPSILON:
        # Each secondary axis turns by half the correction, so both move by the same angle.
        angle = math.acos(cos_angle)
        target = angle + (math.pi / 2 - angle) / 2
        n2 = n2 - n3 * cos_angle
        n2 = n2 * (math.sin(target) / float(np.linalg.norm(n2))) + n3 * math.cos(target)
        n3 = _normalized(np.cross(np.cross(n2, n3), n2))[0]
        if not normalize:
            factor = math.sqrt(math.sin(angle))
            v2, v3 = n2 * l2 * factor, n3 * l3 * factor
    if normalize:
        v2, v3 = n2, n3
    out = np.empty((3, 3))
    out[:, 0], out[:, 1], out[:, 2] = v2, v1, v3
    return out


def _sizes(m):
    return np.linalg.norm(m[:3, :3], axis=0)


def _sizes_fix_shear(m):
    size = _sizes(m)
    volume = float(np.prod(size))
    if volume != 0.0:
        size = size * np.cbrt(abs(float(np.linalg.det(m[:3, :3])) / volume))
    return size


def parent_transform(rest, parent_rest, parent_pose, inherit_rotation=True, inherit_scale="FULL",
                     local_location=True):
    """(rotscale 4x4, loc 4x4, post_scale 3) of a bone with rest matrix ``rest`` under a parent with rest matrix
    ``parent_rest`` posed at ``parent_pose`` (both None for a root bone), as Blender computes them."""
    post = np.ones(3)
    if parent_pose is None:
        rotscale = rest.copy()
        if local_location:
            return rotscale, rotscale.copy(), post
        loc = np.eye(4)
        loc[:3, 3] = rest[:3, 3]
        return rotscale, loc, post
    if inherit_scale not in INHERIT_SCALE:
        raise ValueError("Unknown inherit scale mode %r" % inherit_scale)
    offs = np.linalg.inv(parent_rest) @ rest    # Blender's offs_bone: bone_mat, head and the parent's length.
    full = inherit_rotation and inherit_scale == "FULL"
    if full:
        rotscale = parent_pose @ offs
    else:
        t = (parent_pose if inherit_rotation else parent_rest).copy()
        if inherit_rotation:
            if inherit_scale in ("NONE", "AVERAGE"):
                t[:3, :3] = orthogonalize_stable(t[:3, :3], True)
            elif inherit_scale == "ALIGNED":
                t[:3, :3] = orthogonalize_stable(t[:3, :3], False)
                post = _sizes(t)
                t[:3, :3] = t[:3, :3] / np.where(post != 0.0, post, 1.0)
            elif inherit_scale == "NONE_LEGACY":
                size = _sizes(t)
                t[:3, :3] = t[:3, :3] / np.where(size != 0.0, size, 1.0)
        elif inherit_scale == "FULL":
            t[:3, :3] = t[:3, :3] * _sizes(parent_pose)
        elif inherit_scale == "FIX_SHEAR":
            t[:3, :3] = t[:3, :3] * _sizes_fix_shear(parent_pose)
        elif inherit_scale == "ALIGNED":
            post = _sizes_fix_shear(parent_pose)
        if inherit_scale == "AVERAGE":
            t[:3, :3] = t[:3, :3] * np.cbrt(abs(float(np.linalg.det(parent_pose[:3, :3]))))
        rotscale = t @ offs
        if inherit_scale == "FIX_SHEAR":
            rotscale[:3, :3] = orthogonalize_stable(rotscale[:3, :3], False)
    if not local_location:
        loc = np.eye(4)
        loc[:3, :3] = parent_pose[:3, :3]
        loc[:3, 3] = (parent_pose @ np.append(offs[:3, 3], 1.0))[:3]
    elif not full:
        loc = parent_pose @ offs
    else:
        loc = rotscale.copy()
    return rotscale, loc, post


def settings(skel, bone):
    """The keyword settings of ``parent_transform`` for one bone of a ``hand_rig.Skeleton``."""
    return {"inherit_rotation": bool(skel.inherit_rotation[bone]), "inherit_scale": skel.inherit_scale[bone],
            "local_location": bool(skel.local_location[bone])}


def pose_matrix(skel, bone, parent_pose, basis):
    """The bone's posed matrix for pose basis ``basis`` under its parent posed at ``parent_pose`` (Blender's
    ``BKE_armature_mat_bone_to_pose``, before constraints)."""
    p = skel.parents[bone]
    basis = np.asarray(basis, dtype=np.float64)
    if p >= 0 and skel.plain[bone]:
        return parent_pose @ skel.offsets[bone] @ basis   # What the general path below gives, as one product.
    if skel.connect[bone]:
        basis = basis.copy()
        basis[:3, 3] = 0.0   # A connected bone's location channel is not part of its basis.
    rotscale, loc, post = parent_transform(skel.rest[bone], skel.rest[p] if p >= 0 else None,
                                           parent_pose if p >= 0 else None, **settings(skel, bone))
    out = rotscale @ basis
    out[:3, 3] = (loc @ np.append(basis[:3, 3], 1.0))[:3]
    out[:3, :3] = out[:3, :3] * post
    return out


def _delta(skel, bone, posed):
    """The rotation taking a bone from its rest to its posed orientation (identity for none)."""
    if bone < 0:
        return np.eye(3)
    return rotation_part(posed[bone]) @ skel.rest_rotation[bone].T


def relative(skel, bone, posed_bone, parent_posed):
    """A joint's rotation relative to its parent, in the rest frame: the parent's pose delta undone from the bone's
    own. Under full inheritance this is R B R^-1 of the bone's basis B; a hinge also undoes its parent's turn."""
    p = skel.parents[bone]
    parent = _delta(skel, p, parent_posed) if p >= 0 else np.eye(3)
    return parent.T @ rotation_part(posed_bone) @ skel.rest_rotation[bone].T


def basis_for(skel, bone, rel, parent_pose):
    """The pose-basis rotation (3x3, bone space) giving joint rotation ``rel`` under the parent posed at
    ``parent_pose``: the bone's posed orientation is the parent's delta, then ``rel``, applied to its rest."""
    p = skel.parents[bone]
    if p >= 0 and skel.plain[bone]:
        r = skel.rest_rotation[bone]
        return r.T @ rel @ r   # Full inheritance: the parent's turn comes with it.
    parent = rotation_part(parent_pose) @ skel.rest_rotation[p].T if p >= 0 else np.eye(3)
    wanted = parent @ rel @ skel.rest_rotation[bone]
    frame = pose_matrix(skel, bone, parent_pose, np.eye(4))   # Where an identity basis puts the bone.
    return rotation_part(frame).T @ wanted


def _with_rotation(basis, rotation):
    """A pose basis with ``rotation`` replacing its rotation, keeping its location and scale."""
    out = np.eye(4)
    out[:3, :3] = rotation * np.linalg.norm(basis[:3, :3], axis=0)
    out[:3, 3] = basis[:3, 3]
    return out


def forward_kinematics(skel, root, rels, bases=None):
    """Posed matrices (n, 4, 4) with the joints in ``rels`` ({bone index: 3x3 rest-frame rotation relative to the
    parent}) replaced and every bone below ``root`` recomputed as Blender poses it (``pose_matrix``). Bones outside
    ``root``'s subtree, and ``root`` itself, keep their current pose. ``bases``, a dict, receives each replaced
    joint's basis rotation (what a bone_set writes)."""
    posed = skel.posed.copy()
    below = set(skel.subtree(root))
    for i in skel.order:
        if i == root or i not in below:
            continue
        p = skel.parents[i]
        basis = skel.basis[i]
        if i in rels:
            rotation = basis_for(skel, i, rels[i], posed[p])
            if bases is not None:
                bases[i] = rotation
            basis = _with_rotation(basis, rotation)
        posed[i] = pose_matrix(skel, i, posed[p], basis)
    return posed
