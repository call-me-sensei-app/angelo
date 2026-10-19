# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic .blend byte layouts and snapshot histories for storage tests.

The byte layouts follow the block layouts in source/blender/blenloader_core
(header and BHead structs) closely enough to exercise segmentation.
``build_history`` writes a profile of stored request snapshots exactly as
``CheckpointStore.reserve``/``save`` leave them, quickly, for scaling tests and
the snapshot benchmark's history phase. They are storage fixtures only; Blender
never opens them. test_blender_snapshot_dedup.py covers real documents.
"""

import json
import os
import random
import struct
import time
import uuid

LARGE_BHEAD8 = struct.Struct("<4siQqq")


def blend_v1(blocks, *, version=b"0502", trailer=b""):
    """Blender 5.x layout. ``blocks`` holds (code, data, old pointer) tuples."""
    parts = [b"BLENDER17-01v" + version]
    for code, data, old in blocks:
        parts.append(LARGE_BHEAD8.pack(code, 7, old, len(data), 1) + data)
    parts.append(LARGE_BHEAD8.pack(b"ENDB", 0, 0, 0, 0))
    return b"".join(parts) + trailer


def blend_v0(blocks, *, pointer_size=8, big_endian=False):
    """Pre-5.0 layout: 12-byte header with BHead4 or SmallBHead8 blocks."""
    order = ">" if big_endian else "<"
    layout = struct.Struct(order + ("4siIii" if pointer_size == 4 else "4siQii"))
    parts = [b"BLENDER" + (b"_" if pointer_size == 4 else b"-") + (b"V" if big_endian else b"v") + b"405"]
    for code, data, old in blocks:
        parts.append(layout.pack(code, len(data), old, 7, 1) + data)
    parts.append(layout.pack(b"ENDB", 0, 0, 0, 0))
    return b"".join(parts)


def scene_blocks(objects=40, *, seed=1, array_bytes=3 * 1024 * 1024, moved=(), mesh_offset=None):
    """A small "document": object/mesh IDs with small DATA blocks and one large array.

    ``moved`` changes the given objects' transform bytes; ``mesh_offset`` edits
    a range of the large array, like moving some vertices.
    """
    rng = random.Random(seed)
    blocks = [(b"GLOB", rng.randbytes(256), 1)]
    for index in range(objects):
        transform = struct.pack("<16f", *[float(index)] * 16)
        if index in moved:
            transform = struct.pack("<16f", *[float(index) + 1.0] * 16)
        blocks.append((b"OB\0\0", b"object-%04d" % index + transform + bytes(1200), 1000 + index))
        blocks.append((b"DATA", rng.randbytes(64), 5000 + index))
        blocks.append((b"ME\0\0", b"mesh-%04d" % index + bytes(600), 9000 + index))
        blocks.append((b"DATA", struct.pack("<24f", *[index * 0.5] * 24), 13000 + index))
    array = bytearray(rng.randbytes(array_bytes))
    if mesh_offset is not None:
        array[mesh_offset:mesh_offset + 4096] = bytes(4096)
    blocks.append((b"DATA", bytes(array), 99999))
    blocks.append((b"DNA1", b"SDNA" + bytes(4000), 2))
    return blocks


HISTORY_SCENE = b"BLENDER17-01v0502 synthetic stored snapshot"


def build_history(root, count, per_chat=7):
    """A profile at ``root`` holding ``count`` READY full-copy request snapshots.

    ``per_chat`` snapshots per chat folder, ordered boundaries 1..count and the
    matching timeline clock, as a long session leaves them (the stress replay
    used seven per chat). Returns the resolved root.
    """
    from angelo_agent.checkpoint_timeline import NAME, legacy_fingerprint
    root = os.path.abspath(root)
    os.makedirs(root, mode=0o700, exist_ok=False)
    created = time.time() - count
    for index in range(count):
        if index % per_chat == 0:
            session = os.path.join(root, "checkpoints", str(uuid.uuid4()))
            os.makedirs(session, mode=0o700)
        identity = str(uuid.uuid4())
        folder = os.path.join(session, identity)
        os.mkdir(folder, 0o700)
        record = {"version": 3, "boundary": index + 1, "id": identity, "original_path": "",
                  "created": created + index, "files": [], "state": "READY", "purpose": "request",
                  "capture_mode": "EVERY_REQUEST", "protected": False, "scene_bytes": len(HISTORY_SCENE),
                  "capture_ms": 1.0}
        for name, data in (("scene.blend", HISTORY_SCENE), ("checkpoint.json", json.dumps(record).encode())):
            descriptor = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
    clock = {"version": 1, "sequence": count, "boundary": count, "legacy_fingerprint": legacy_fingerprint([])}
    with open(os.path.join(root, NAME), "w", encoding="utf-8") as handle:
        json.dump(clock, handle)
    os.chmod(os.path.join(root, NAME), 0o600)
    return os.path.realpath(root)
