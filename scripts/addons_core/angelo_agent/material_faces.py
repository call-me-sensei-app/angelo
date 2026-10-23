# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which materials an object's faces render, read cheaply and only where its base faces decide it.

An assigned material is a rendered one: a slot no face uses leaves the object looking unchanged. Recorded (1.0.5 A/B,
kitchen, 16 of 16 trials): ``also`` objects got the material as a new slot and kept every face on their old one,
and the host's checks, which read the slots only, passed. The base faces decide what a mesh renders unless something
evaluated maps faces to other slots (Geometry Nodes, particle hair, Solidify/Bevel/Wireframe material offsets, Array
caps, a mesh cache) or the mesh is in Edit Mode; then only the slots can be read, and a check says so in its scope.
"""

# Modifiers that keep every face's material index (deform, smooth, subdivide, mirror, weights, UVs, normals).
_INDEX_KEEPING = frozenset({
    "ARMATURE", "CAST", "CLOTH", "COLLISION", "CORRECTIVE_SMOOTH", "CURVE", "DECIMATE", "DISPLACE", "EDGE_SPLIT",
    "HOOK", "LAPLACIANDEFORM", "LAPLACIANSMOOTH", "LATTICE", "MESH_DEFORM", "MIRROR", "MULTIRES", "NORMAL_EDIT",
    "SHRINKWRAP", "SIMPLE_DEFORM", "SMOOTH", "SOFT_BODY", "SUBSURF", "SURFACE_DEFORM", "TRIANGULATE", "UV_PROJECT",
    "UV_WARP", "VERTEX_WEIGHT_EDIT", "VERTEX_WEIGHT_MIX", "VERTEX_WEIGHT_PROXIMITY", "WARP", "WAVE",
    "WEIGHTED_NORMAL",
    # Remove or merge faces, or move points, and keep each kept face's index; Data Transfer moves no material index.
    "BUILD", "DATA_TRANSFER", "DYNAMIC_PAINT", "MASK", "MESH_CACHE", "SURFACE", "WELD"})


def _keeps_indices(modifier):
    kind = modifier.type
    if kind in _INDEX_KEEPING:
        return True
    if kind == "SOLIDIFY":
        return not modifier.material_offset and not modifier.material_offset_rim
    if kind == "BEVEL":
        return modifier.material == -1
    if kind == "WIREFRAME":
        return not modifier.material_offset
    if kind == "ARRAY":
        return modifier.start_cap is None and modifier.end_cap is None
    return False


def faces_decide(obj):
    """Whether ``obj``'s base faces show which materials it renders: a mesh with faces, out of Edit Mode, without
    particle systems and with only modifiers that keep each face's material index."""
    try:
        data = obj.data if obj.type == "MESH" else None
        return (data is not None and len(data.polygons) > 0 and getattr(obj, "mode", "OBJECT") != "EDIT"
                and not getattr(data, "is_editmode", False) and not len(getattr(obj, "particle_systems", ()))
                and all(_keeps_indices(modifier) for modifier in obj.modifiers))
    except (AttributeError, TypeError, ReferenceError):
        return False


def face_slots(obj):
    """Each face's effective slot (an index past the last slot renders with the last one), as a numpy array."""
    import numpy
    polygons = obj.data.polygons
    indices = numpy.empty(len(polygons), dtype=numpy.int32)
    polygons.foreach_get("material_index", indices)
    return numpy.clip(indices, 0, max(len(obj.material_slots) - 1, 0))


def rendered_faces(obj, slots):
    """How many of ``obj``'s faces render one of ``slots``, or None when only the slots can be read
    (``faces_decide``)."""
    if not faces_decide(obj):
        return None
    import numpy
    return int(numpy.isin(face_slots(obj), list(slots)).sum())


def base_faces_on(obj, slots):
    """How many of ``obj``'s base faces use one of ``slots``, whatever evaluation does with them: what decides whether
    an assignment moves faces (an assignment writes base faces, as the target's always did)."""
    import numpy
    if not len(obj.data.polygons):
        return 0
    return int(numpy.isin(face_slots(obj), list(slots)).sum())


def give_faces(obj, mesh, slots):
    """Every face on ``slots[0]`` unless a base face already uses one of ``slots``: an object given a material renders
    it, and one that already shows it keeps its faces (its other materials too)."""
    if len(mesh.polygons) and not base_faces_on(obj, slots):
        all_faces_to(mesh, slots[0])


def live_identity(material):
    """The live material a staged slot stands for: an unedited staged clone carries its source's session UID
    (program_objects.MATERIAL_SOURCE); anything else is itself. Two objects' private clones of one live material
    are one material."""
    from .node_edit_spec import SOURCE   # program_objects.MATERIAL_SOURCE
    if material is None:
        return None
    return int(material[SOURCE]) if SOURCE in material.keys() else int(material.session_uid)


def slots_holding(obj, material):
    """The slots of ``obj`` that hold ``material``'s live material."""
    wanted = live_identity(material)
    return [index for index, slot in enumerate(obj.material_slots)
            if slot.material is not None and live_identity(slot.material) == wanted]


def all_faces_to(mesh, slot):
    """Every face of ``mesh`` on ``slot`` (one bulk write)."""
    mesh.polygons.foreach_set("material_index", [slot] * len(mesh.polygons))
