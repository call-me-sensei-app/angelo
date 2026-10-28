# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The size budget of a written .glb, read from the file itself: what a web page downloads and draws. No Blender.

``measure(path)`` returns bytes, triangles, draw calls (one per primitive of each mesh instance in the default
scene; a GPU-instanced node draws once), textures (count, bytes, pixel sizes) and the extensions the file requires
(so a page knows which decoders it needs). Compressed meshes keep their accessor counts in the JSON, so the counts
are exact for meshopt and Draco files alike.
"""

import json
import struct

GLB_MAGIC = 0x46546C67
JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942
TRIANGLES, TRIANGLE_STRIP, TRIANGLE_FAN = 4, 5, 6


class BudgetError(ValueError):
    """The file is not a binary glTF this module can read."""


def read_glb(data):
    """(json document, binary chunk or b"") of a .glb's bytes."""
    if len(data) < 20:
        raise BudgetError("too short for a .glb")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2 or length > len(data):
        raise BudgetError("not a glTF 2.0 binary")
    offset, document, binary = 12, None, b""
    while offset + 8 <= length:
        size, kind = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + size]
        if kind == JSON_CHUNK:
            document = json.loads(chunk.decode("utf-8"))
        elif kind == BIN_CHUNK:
            binary = chunk
        offset += 8 + size + (-size % 4)
    if document is None:
        raise BudgetError("no JSON chunk")
    return document, binary


def image_size(blob):
    """(width, height) of a PNG, JPEG, WebP or KTX2 image's bytes, or (0, 0) when unknown."""
    if blob[:8] == b"\x89PNG\r\n\x1a\n" and len(blob) >= 24:
        return struct.unpack(">II", blob[16:24])
    if blob[:12] == b"\xabKTX 20\xbb\r\n\x1a\n" and len(blob) >= 28:
        return struct.unpack_from("<II", blob, 20)
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP" and len(blob) >= 30:
        kind = blob[12:16]
        if kind == b"VP8 ":
            width, height = struct.unpack_from("<HH", blob, 26)
            return width & 0x3FFF, height & 0x3FFF
        if kind == b"VP8L":
            bits = int.from_bytes(blob[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if kind == b"VP8X":
            return int.from_bytes(blob[24:27], "little") + 1, int.from_bytes(blob[27:30], "little") + 1
    if blob[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(blob):
            if blob[offset] != 0xFF:
                break
            marker, size = blob[offset + 1], struct.unpack_from(">H", blob, offset + 2)[0]
            if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
                height, width = struct.unpack_from(">HH", blob, offset + 5)
                return width, height
            offset += 2 + size
    return 0, 0


def _triangles(document, primitive):
    accessors = document.get("accessors", [])
    index = primitive.get("indices")
    count = accessors[index]["count"] if index is not None else \
        accessors[primitive.get("attributes", {}).get("POSITION", 0)]["count"] if accessors else 0
    mode = primitive.get("mode", TRIANGLES)
    if mode == TRIANGLES:
        return count // 3
    if mode in {TRIANGLE_STRIP, TRIANGLE_FAN}:
        return max(count - 2, 0)
    return 0   # Points and lines draw no triangles.


def _instances(document):
    """{mesh index: (instances, draws)} over the default scene's node tree; a GPU-instanced node draws once."""
    nodes = document.get("nodes", [])
    scenes = document.get("scenes", [])
    scene = scenes[document.get("scene", 0)] if scenes else {"nodes": list(range(len(nodes)))}
    counts, stack, seen = {}, list(scene.get("nodes", [])), set()
    while stack:
        index = stack.pop()
        if index in seen or index >= len(nodes):
            continue
        seen.add(index)
        node = nodes[index]
        if "mesh" in node:
            copies = 1
            instancing = node.get("extensions", {}).get("EXT_mesh_gpu_instancing")
            accessor = next(iter(instancing.get("attributes", {}).values()), None) if instancing else None
            if accessor is not None:
                copies = document["accessors"][accessor]["count"]
            instances, draws = counts.get(node["mesh"], (0, 0))
            counts[node["mesh"]] = (instances + copies, draws + 1)
        stack.extend(node.get("children", []))
    return counts


def measure_bytes(data):
    """The budget of a .glb given as bytes (``measure`` reads the file)."""
    document, binary = read_glb(data)
    meshes = document.get("meshes", [])
    triangles = draw_calls = 0
    for index, (copies, draws) in _instances(document).items():
        primitives = meshes[index].get("primitives", []) if index < len(meshes) else []
        triangles += copies * sum(_triangles(document, primitive) for primitive in primitives)
        draw_calls += draws * len(primitives)
    views = document.get("bufferViews", [])
    textures = []
    for image in document.get("images", []):
        view = image.get("bufferView")
        blob = b""
        if view is not None and view < len(views):
            start = views[view].get("byteOffset", 0)
            blob = binary[start:start + views[view]["byteLength"]]
        width, height = image_size(blob)
        textures.append({"name": image.get("name", ""), "mime": image.get("mimeType", ""), "bytes": len(blob),
                         "width": width, "height": height})
    return {
        "bytes": len(data),
        "triangles": triangles,
        "draw_calls": draw_calls,
        "meshes": len(meshes),
        "materials": len(document.get("materials", [])),
        "textures": {"count": len(textures), "bytes": sum(item["bytes"] for item in textures),
                     "largest": max((max(item["width"], item["height"]) for item in textures), default=0),
                     "images": textures},
        "extensions_required": sorted(document.get("extensionsRequired", [])),
        "extensions_used": sorted(document.get("extensionsUsed", [])),
    }


def measure(path):
    with open(path, "rb") as handle:
        return measure_bytes(handle.read())
