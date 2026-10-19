# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test helpers for the team-library tools: crafted files and a signed-in account on the fake console.

Files are built in memory from their format specifications (PNG chunks, JPEG
markers, WebP headers, GLB chunks, binary FBX 7.4/7.5 node records, OBJ text,
stored USDZ zips), including hostile variants: decompression bombs, oversized
dimensions, external references and path traversal. Host tests only.
"""

import io
import json
import struct
import sys
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.view_png import encode_png  # noqa: E402

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def png(width=4, height=3):
    return encode_png(bytes(width * height * 4), width, height)


def png_header(width, height):
    """A PNG whose IHDR claims ``width`` x ``height`` (the rest need not decode)."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))


def jpeg_header(width, height, exif=0):
    app1 = b"\xff\xe1" + struct.pack(">H", exif + 2) + bytes(exif) if exif else b""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app1 + sof + b"\xff\xd9"


def webp_header(width, height):
    body = b"VP8X" + struct.pack("<I", 10) + bytes(4) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def glb(document=None, binary=b"", images=()):
    """A GLB with an optional binary chunk; ``images``: embedded image bytes (one bufferView each)."""
    document = json.loads(json.dumps(document or {"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0}))
    blob = bytearray(binary)
    for data in images:
        while len(blob) % 4:
            blob.append(0)
        document.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        document.setdefault("images", []).append({"bufferView": len(document["bufferViews"]) - 1, "mimeType": "image/png"})
        blob += data
    if blob:
        document.setdefault("buffers", [{}])[0]["byteLength"] = len(blob)
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    if blob:
        chunks += struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def _fbx_property(value):
    kind, data = value
    if kind in "fdlib":
        item = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "?"}[kind]
        if isinstance(data, tuple):  # ("zlib", raw values, declared count override, compressed bytes override)
            _, values, declared, payload = data
        else:
            values, declared, payload = data, None, None
        raw = struct.pack("<%d%s" % (len(values), item), *values)
        compressed = payload if payload is not None else zlib.compress(raw)
        return (kind.encode() + struct.pack("<III", declared if declared is not None else len(values), 1, len(compressed))
                + compressed)
    if kind in "SR":
        return kind.encode() + struct.pack("<I", len(data)) + data
    if kind == "I":
        return b"I" + struct.pack("<i", data)
    raise ValueError(kind)


def fbx(nodes, version=7400):
    """Binary FBX: ``nodes`` = [(name, [(kind, value)...], [children])]."""
    wide = version >= 7500
    record = struct.Struct("<QQQB" if wide else "<IIIB")
    null = bytes(record.size)
    out = io.BytesIO()
    out.write(b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", version))

    def write(node):
        name, properties, children = node
        start = out.tell()
        props = b"".join(_fbx_property(p) for p in properties)
        out.write(bytes(record.size))  # Patched below once the end offset is known.
        out.write(name.encode())
        out.write(props)
        for child in children:
            write(child)
        if children:
            out.write(null)
        end = out.tell()
        out.seek(start)
        out.write(record.pack(end, len(properties), len(props), len(name)))
        out.seek(end)

    for node in nodes:
        write(node)
    out.write(null)
    out.write(bytes(160))  # Footer padding; readers stop at the null record.
    return out.getvalue()


def obj(extra=""):
    return ("# test\nmtllib model.mtl\no Box\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n" + extra).encode()


def usdz(entries, compress=False):
    """A USDZ-like zip of ``[(name, bytes)]``; stored unless ``compress``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


USDA = b'#usda 1.0\n(\n    defaultPrim = "Root"\n)\n\ndef Xform "Root"\n{\n}\n'


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def signed_in(case, console):
    """A TeamAccount signed in on ``console`` (a FakeConsole); closed when the test ends."""
    from angelo_agent.team_account import TeamAccount
    account = TeamAccount(console.url)
    case.addCleanup(account.close)
    url = account.begin_sign_in(device="Test computer")
    with DIRECT.open(console.callback(url), timeout=5) as response:  # The allow screen's redirect (code, state, iss).
        case.assertEqual(response.status, 200)
    wait_for(lambda: account.view().signed_in)
    return account
