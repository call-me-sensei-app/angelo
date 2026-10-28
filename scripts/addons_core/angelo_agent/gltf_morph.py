# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own glTF imports weld files that have morph targets.

Blender's glTF importer keeps a file's split seam vertices apart unless Merge Vertices is on, and it cannot keep
glTF morph normals (shape keys store none). Once a shape key moves the surface, each split copy of a seam vertex
averages only its own side's faces, so every seam shades as its own patch; three.js, which uses the file's morph
normals, shows none. Merging co-located vertices removes it. Blender's import default stays as it is for people
and their scripts (the importer warns when such a file arrives unwelded, ``io_scene_gltf2``); every import path
Angelo owns asks for ``import_options(path)``. doc/angelo/generative_studio.rst, "Models with morph targets".

Standard library only: the file's JSON is read, never its buffers.
"""

import json
import struct

# A GLB's JSON chunk, or a whole .gltf, larger than this is not one we read (its buffers may be inline).
MAX_JSON = 64 * 1024 * 1024
GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A


def _document(path):
    """The glTF JSON of a .glb or .gltf file, or None when it cannot be read as one."""
    with open(path, "rb") as handle:
        head = handle.read(20)
        if head[:4] == GLB_MAGIC:
            if len(head) < 20:
                return None
            length, kind = struct.unpack_from("<II", head, 12)
            if kind != JSON_CHUNK or length > MAX_JSON:
                return None
            data = handle.read(length)
        else:
            data = head + handle.read(MAX_JSON + 1 - len(head))
            if len(data) > MAX_JSON:
                return None
    return json.loads(data.decode("utf-8"))


def has_morph_targets(path):
    """Whether any mesh primitive in the glTF file at ``path`` has morph targets; False for anything unreadable
    (the importer then reports its own error)."""
    try:
        document = _document(path)
    except (OSError, ValueError, UnicodeDecodeError, struct.error):
        return False
    meshes = document.get("meshes") if isinstance(document, dict) else None
    if not isinstance(meshes, list):
        return False
    return any(isinstance(primitive, dict) and bool(primitive.get("targets"))
               for mesh in meshes if isinstance(mesh, dict)
               for primitive in (mesh.get("primitives") if isinstance(mesh.get("primitives"), list) else []))


def import_options(path):
    """The ``import_scene.gltf`` options Angelo adds for the file at ``path``: Merge Vertices when it has morph
    targets, nothing otherwise (so every other file imports exactly as Blender's default does)."""
    return {"merge_vertices": True} if has_morph_targets(path) else {}
