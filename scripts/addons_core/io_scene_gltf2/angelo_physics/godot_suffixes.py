# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
"""Godot's physics name suffixes, the fallback format (``Scene.angelo_physics_format``). No ``bpy``.

Godot reads OMI physics from 4.4 on; older pipelines name nodes instead, and the suffixes express less: no
kinematic or trigger body, no primitive from a mesh, nothing on an empty in glTF.
"""

MESH_SHAPES = ("CONVEX", "TRIMESH")

SUFFIX_NOTES = {
    "suffix_empty": "Godot reads collision suffixes on empties only from Collada files, not glTF (godot#32363), so it "
                    "has no physics",
    "suffix_body": "Godot has no name suffix for a kinematic or trigger body, so it has no physics",
    "suffix_hull": "Godot's suffix makes convex hulls from the mesh, not the chosen shape",
    "suffix_mesh": "Godot's suffix makes an exact mesh shape from the mesh, not the chosen shape",
}


def godot_suffix(body, shape, has_mesh, collision_only):
    """(Godot node-type suffix or None, a ``SUFFIX_NOTES`` code when it is approximate or missing, or None).

    Godot's importer (``ResourceImporterScene::_pre_fix_node``) builds -col and -convcol on a mesh as a static body
    below it, -colonly and -convcolonly as a static body in its place, and -rigid as a rigid body with convex hulls.
    It reads the -colonly forms on an empty only from Collada, never from glTF (godot#32363), and has no kinematic or
    trigger suffix."""
    if shape == "NONE" and body in ("NONE", "STATIC"):
        return None, None
    if not has_mesh:
        return None, "suffix_empty"
    if body in ("KINEMATIC", "TRIGGER"):
        return None, "suffix_body"
    if body == "RIGID":
        return "-rigid", None if shape == "CONVEX" else "suffix_hull"
    exact = shape == "TRIMESH"
    suffix = ("-colonly" if exact else "-convcolonly") if collision_only else ("-col" if exact else "-convcol")
    if shape in MESH_SHAPES:
        return suffix, None
    return suffix, "suffix_mesh" if exact else "suffix_hull"
