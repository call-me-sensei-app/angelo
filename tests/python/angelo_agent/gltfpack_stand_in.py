# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in for gltfpack in test_blender_web_export_gltfpack.py: what the web export relies on, no KTX2 encoder.

``gltfpack_stand_in.py -i <in.glb> -o <out.glb> [flags]``. Like gltfpack it keeps no extras and rewrites the binary
buffer: only the bufferViews accessors and images use survive, renumbered, so the World's picture the export put
in the scene's extras is gone, and the export must put it back (``web_glb.add_scene_extras``). Each call's
arguments are appended as a JSON line to ``ANGELO_GLTFPACK_LOG``; ``ANGELO_GLTFPACK_FAIL`` makes it exit 1, as a
gltfpack without texture compression does on ``-tc``. Images keep their bytes: the KTX2 encoding itself is
gltfpack's and is not exercised here.
"""

import json
import os
import struct
import sys

JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942


def read(data):
    offset, document, binary = 12, None, b""
    while offset + 8 <= len(data):
        size, kind = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + size]
        document = json.loads(chunk) if kind == JSON_CHUNK else document
        binary = chunk if kind == BIN_CHUNK else binary
        offset += 8 + size + (-size % 4)
    return document, binary


def strip_extras(value):
    if isinstance(value, dict):
        return {key: strip_extras(item) for key, item in value.items() if key != "extras"}
    if isinstance(value, list):
        return [strip_extras(item) for item in value]
    return value


def repack(document, binary):
    views = document.get("bufferViews", [])
    used = sorted({item["bufferView"] for group in ("accessors", "images") for item in document.get(group, [])
                   if "bufferView" in item})
    renumber, packed, kept = {}, bytearray(), []
    for index in used:
        view = dict(views[index])
        start = view.get("byteOffset", 0)
        packed.extend(b"\0" * (-len(packed) % 4))
        view["byteOffset"] = len(packed)
        packed.extend(binary[start:start + view["byteLength"]])
        renumber[index] = len(kept)
        kept.append(view)
    for group in ("accessors", "images"):
        for item in document.get(group, []):
            if "bufferView" in item:
                item["bufferView"] = renumber[item["bufferView"]]
    document["bufferViews"] = kept
    document["buffers"] = [{"byteLength": len(packed)}]
    return document, bytes(packed)


def write(path, document, binary):
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(text), JSON_CHUNK) + text + struct.pack("<II", len(binary), BIN_CHUNK) + binary
    with open(path, "wb") as handle:
        handle.write(struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks)


def main(arguments):
    with open(os.environ["ANGELO_GLTFPACK_LOG"], "a") as log:
        log.write(json.dumps(arguments) + "\n")
    if os.environ.get("ANGELO_GLTFPACK_FAIL"):
        return 1
    source, target = arguments[arguments.index("-i") + 1], arguments[arguments.index("-o") + 1]
    with open(source, "rb") as handle:
        document, binary = read(handle.read())
    write(target, *repack(strip_extras(document), binary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
