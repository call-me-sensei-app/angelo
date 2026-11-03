# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scene extras with binary parts, for a glTF tree (the exporter's) or a written .glb (after gltfpack). No Blender.

Angelo's look (``web_look.extras``) carries the World's picture as raw ``bytes`` where a bufferView index belongs.
``for_exporter`` hands the exporter its ``BinaryData`` instead, which it stores in the file's buffer and replaces
with the bufferView's index, like any of its own data. gltfpack rewrites every buffer and keeps no extras, so after
it ``add_scene_extras`` appends the bytes to the .glb's binary chunk itself and sets the extras again.
"""

import json
import struct

from .web_budget import BIN_CHUNK, GLB_MAGIC, JSON_CHUNK, read_glb


def _replace_bytes(value, convert):
    if isinstance(value, bytes):
        return convert(value)
    if isinstance(value, dict):
        return {key: _replace_bytes(item, convert) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_bytes(item, convert) for item in value]
    return value


def for_exporter(extras, binary_data):
    """``extras`` with each ``bytes`` value wrapped in the exporter's ``binary_data`` class (``BinaryData``)."""
    return _replace_bytes(extras, binary_data)


def _pad(data, fill):
    return data + fill * (-len(data) % 4)


def add_scene_extras(data, extras):
    """The .glb ``data`` with ``extras`` merged into its default scene's extras; ``bytes`` in them become bufferViews
    appended to the binary chunk (buffer 0, which a .glb stores). Returns the new file's bytes."""
    document, binary = read_glb(data)
    buffers = document.setdefault("buffers", [])
    if buffers and ("uri" in buffers[0] or buffers[0].get("extensions")):
        raise ValueError("the .glb's first buffer is not its binary chunk")
    binary = bytearray(binary)
    views = document.setdefault("bufferViews", [])

    def append(blob):
        offset = len(binary)
        binary.extend(_pad(blob, b"\0"))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(blob)})
        return len(views) - 1

    extras = _replace_bytes(extras, append)
    if not buffers:
        buffers.append({"byteLength": 0})
    buffers[0]["byteLength"] = len(binary)
    scenes = document.setdefault("scenes", [{"nodes": []}])
    scene = scenes[document.get("scene", 0)]
    scene["extras"] = dict(scene.get("extras") or {}, **extras)
    text = _pad(json.dumps(document, separators=(",", ":")).encode("utf-8"), b" ")
    chunks = struct.pack("<II", len(text), JSON_CHUNK) + text
    if binary:
        chunks += struct.pack("<II", len(binary), BIN_CHUNK) + bytes(binary)
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(chunks)) + chunks
