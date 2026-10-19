# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Team-library files are untrusted: a format allow-list, size caps and decompression bounds.

Every downloaded file passes ``inspect`` on the worker thread that fetched it,
before Blender reads a byte of it:

* **Format from the content, never the name.** ``sniff`` reads the first bytes;
  the library's MIME type is only a hint. Admitted: PNG, JPEG and WebP images;
  GLB, binary FBX (7.1-7.7), OBJ and USD (``usdc``, ``usda``, ``usdz``) models.
  Refused with a reason: ``.blend`` (it can carry scripts and drivers), ``.gltf``
  with side files, ASCII FBX, GIF and anything else.
* **Size caps** per kind (``MAX_BYTES``), checked against the declared size, while
  downloading (``team_library_transfer``) and here.
* **Decompression bounds.** Every image, including the textures embedded in a GLB,
  FBX or USDZ, is at most ``MAX_SIDE`` pixels a side and ``MAX_PIXELS`` in all,
  read from its header, and a model's embedded textures total at most
  ``MAX_TOTAL_PIXELS``. GLB accessors (Draco and meshopt streams decode into
  them) total at most ``MAX_DECODED`` bytes. Every zlib-compressed FBX array is
  inflated here with a hard output bound and must decode to exactly its declared
  length; their total is at most ``MAX_DECODED``. USDZ entries must be stored
  uncompressed, as the format requires, so a USDZ can't inflate.
* **No path traversal.** A GLB may reference nothing outside itself (every
  ``uri`` is a ``data:`` URI); an OBJ ``mtllib`` is a bare file name (nothing
  beside the download exists, so it resolves to nothing); an FBX texture path
  may not climb out of its folder; USDZ entry names are relative and never
  climb. USD layers' asset paths are checked in Blender with ``pxr``
  (``team_library_import.check_usd``).

Standard library only; no bpy.
"""

import base64
import json
import os
import re
import struct
import zipfile
import zlib

MiB = 1024 * 1024
MAX_BYTES = {"image": 64 * MiB, "model": 256 * MiB}
MAX_SIDE = 16384
MAX_PIXELS = 8192 * 8192
MAX_TOTAL_PIXELS = 4 * MAX_PIXELS  # All textures embedded in one model (about 1 GB decoded as RGBA).
MAX_DECODED = 1024 * MiB
MAX_JSON = 64 * MiB
MAX_FBX_NODES = 2_000_000
MAX_FBX_DEPTH = 48
MAX_ZIP_ENTRIES = 4096
HEADER_BYTES = 4 * MiB  # Enough of any image to read its dimensions (JPEG markers may follow large EXIF data).

IMAGE_FORMATS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}
MODEL_FORMATS = {"glb": ".glb", "fbx": ".fbx", "obj": ".obj", "usdc": ".usdc", "usda": ".usda", "usdz": ".usdz"}
FORMAT_NAMES = {"png": "PNG", "jpeg": "JPEG", "webp": "WebP", "glb": "GLB", "fbx": "FBX", "obj": "OBJ",
                "usdc": "USD", "usda": "USD", "usdz": "USDZ"}
SUPPORTED = "PNG, JPEG or WebP images and GLB, binary FBX, OBJ or USD models"
FBX_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
_ARRAY_ITEM = {ord("f"): 4, ord("d"): 8, ord("l"): 8, ord("i"): 4, ord("b"): 1}
_SCALAR = {ord("Y"): 2, ord("C"): 1, ord("I"): 4, ord("F"): 4, ord("D"): 8, ord("L"): 8}
_FBX_PATHS = (b"RelativeFilename", b"FileName", b"Filename")
_OBJ_STATEMENT = re.compile(r"^\s*(#|v|vt|vn|vp|f|l|p|o|g|s|mg|mtllib|usemtl|cstype|deg|curv|surf|parm|end)(\s|$)")
_ACCESSOR_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_COMPONENT_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}


class Refused(ValueError):
    """A file that is not admitted; ``str`` is a fixed sentence for the person and the model."""


class NotImported(ValueError):
    """An import refused on the main thread before Blender read the file or changed the scene."""


class ImportFailed(RuntimeError):
    """Blender's import failed after it began changing the scene.

    ``scene_unchanged`` is True only when the rollback was verified: nothing the
    import created remains and the scene reads exactly as before it.
    """

    def __init__(self, message, *, scene_unchanged):
        super().__init__(message)
        self.scene_unchanged = scene_unchanged


def kind_of(fmt):
    return "image" if fmt in IMAGE_FORMATS else "model" if fmt in MODEL_FORMATS else ""


def extension(fmt):
    return IMAGE_FORMATS.get(fmt) or MODEL_FORMATS[fmt]


def sniff(head):
    """The format of a file from its first bytes (64 KiB is plenty)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"glTF"):
        return "glb"
    if head.startswith(FBX_MAGIC):
        return "fbx"
    if head.startswith(b"PXR-USDC"):
        return "usdc"
    if head.startswith(b"#usda "):
        return "usda"
    if head.startswith(b"PK\x03\x04"):
        if len(head) >= 30:
            name_length = struct.unpack_from("<H", head, 26)[0]
            if head[30:30 + name_length].lower().endswith((b".usdc", b".usda", b".usd")):
                return "usdz"
        raise Refused("This archive isn't a USDZ model; only %s are imported." % SUPPORTED)
    if head.startswith(b"; FBX"):
        raise Refused("ASCII FBX files aren't supported; export the model as binary FBX or GLB.")
    if head.startswith(b"BLENDER") or head.startswith(b"\x1f\x8b") or head.startswith(b"\x28\xb5\x2f\xfd"):
        raise Refused(".blend files are never imported from the team library (they can carry scripts and drivers); "
                      "save the model as GLB, FBX, OBJ or USD.")
    if head.startswith((b"GIF87a", b"GIF89a")):
        raise Refused("GIF images aren't imported; use PNG, JPEG or WebP.")
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped[:1] == b"{" and b'"asset"' in head:
        raise Refused("Multi-file .gltf models aren't imported; save the model as one .glb file.")
    if _looks_like_obj(head):
        return "obj"
    raise Refused("Only %s are imported from the team library." % SUPPORTED)


def _looks_like_obj(head):
    text = head.decode("utf-8", "ignore") if head else ""
    lines = [line for line in text.splitlines()[:-1 if len(head) >= 65536 else None] if line.strip()][:400]
    if not lines or any(not _OBJ_STATEMENT.match(line) for line in lines[:60]):
        return False
    return any(line.lstrip().startswith("v ") for line in lines)


# Image headers ---------------------------------------------------------------

def image_size(data):
    """``(width, height)`` from an image's header, or None when it can't be read."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg_size(data)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if chunk == b"VP8L" and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
        if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF
    return None


def _jpeg_size(data):
    index, length = 2, len(data)
    for _ in range(10000):
        while index < length and data[index] == 0xFF:
            index += 1
        if index + 3 > length:
            return None
        marker, index = data[index], index + 1
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        segment = struct.unpack(">H", data[index:index + 2])[0]
        if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            if index + 7 > length:
                return None
            height, width = struct.unpack(">HH", data[index + 3:index + 7])
            return width, height
        if marker in {0xDA, 0xD9} or segment < 2:
            return None
        index += segment
        if index < length and data[index] != 0xFF:
            return None
    return None


def check_image(data, what="This image"):
    """Refuse an image whose header can't be read or whose pixels exceed the bounds; returns its size."""
    size = image_size(data)
    if size is None:
        raise Refused("%s has an unreadable header; it wasn't imported." % what)
    width, height = size
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE) or width * height > MAX_PIXELS:
        raise Refused("%s is %d x %d pixels; images up to %d pixels a side and %d megapixels are imported."
                      % (what, width, height, MAX_SIDE, MAX_PIXELS // 1_000_000))
    return size


def check_embedded_image(data, what, totals=None):
    """An embedded texture: an admitted image format within the pixel bounds.

    ``totals``: a dict whose ``"pixels"`` accumulates the model's textures, bounded by ``MAX_TOTAL_PIXELS``.
    """
    try:
        fmt = sniff(data[:65536])
    except Refused:
        fmt = ""
    if fmt not in IMAGE_FORMATS:
        raise Refused("%s isn't a PNG, JPEG or WebP image; the model wasn't imported." % what)
    width, height = check_image(data, what)
    if totals is not None:
        totals["pixels"] = totals.get("pixels", 0) + width * height
        if totals["pixels"] > MAX_TOTAL_PIXELS:
            raise Refused("This model's embedded textures total more than %d megapixels; it wasn't imported."
                          % (MAX_TOTAL_PIXELS // 1_000_000))
    return width, height


# Whole files -------------------------------------------------------------------

def inspect(path, expected_kind=""):
    """Check the downloaded file at ``path``; returns ``{"format", "kind", "bytes", ...}`` or raises ``Refused``.

    ``expected_kind``: the library item's kind; content of another kind is refused.
    """
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(65536)
    fmt = sniff(head)
    kind = kind_of(fmt)
    if expected_kind and kind != expected_kind:
        raise Refused("The file's content (%s) doesn't match its library kind (%s); it wasn't imported."
                      % (FORMAT_NAMES[fmt], expected_kind))
    if size > MAX_BYTES[kind]:
        raise Refused("%s files up to %d MB are imported; this one is %.0f MB."
                      % (FORMAT_NAMES[fmt], MAX_BYTES[kind] // MiB, size / MiB))
    facts = {"format": fmt, "kind": kind, "bytes": size}
    if kind == "image":
        with open(path, "rb") as handle:
            facts["width"], facts["height"] = check_image(handle.read(HEADER_BYTES))
    elif fmt == "glb":
        facts.update(_check_glb(path, size))
    elif fmt == "fbx":
        facts.update(_check_fbx(path, size))
    elif fmt == "obj":
        _check_obj(path)
    elif fmt == "usdz":
        facts.update(_check_usdz(path, size))
    return facts


def _check_glb(path, size):
    with open(path, "rb") as handle:
        header = handle.read(20)
        if len(header) < 20:
            raise Refused("This GLB file is truncated.")
        _magic, version, length, json_length, json_type = struct.unpack("<4sIIII", header)
        if version != 2 or length != size or json_type != 0x4E4F534A or not 0 < json_length <= min(MAX_JSON, size - 20):
            raise Refused("This GLB file is malformed or not glTF 2.0.")
        try:
            document = json.loads(handle.read(json_length))
        except (ValueError, UnicodeError, RecursionError):
            raise Refused("This GLB file's description is unreadable.") from None
        if not isinstance(document, dict):
            raise Refused("This GLB file's description is unreadable.")
        binary_at = 20 + json_length + (-json_length % 4)
        binary_length = 0
        handle.seek(binary_at)
        chunk = handle.read(8)
        if len(chunk) == 8:
            binary_length, chunk_type = struct.unpack("<II", chunk)
            if chunk_type != 0x004E4942 or binary_at + 8 + binary_length > size:
                raise Refused("This GLB file's binary data is malformed.")
        for field in ("buffers", "images"):
            for item in _items(document, field):
                uri = item.get("uri")
                if uri is not None and not (isinstance(uri, str) and uri.startswith("data:")):
                    raise Refused("This GLB file refers to other files (%s); only self-contained GLB files are "
                                  "imported." % field)
        decoded = 0
        for accessor in _items(document, "accessors"):
            count = accessor.get("count")
            width = _ACCESSOR_WIDTH.get(accessor.get("type"))
            component = _COMPONENT_BYTES.get(accessor.get("componentType"))
            if type(count) is not int or count < 0 or not width or not component:
                raise Refused("This GLB file has a malformed accessor.")
            decoded += count * width * component
        if decoded > MAX_DECODED:
            raise Refused("This GLB file decodes to %.0f MB of geometry; up to %d MB is imported."
                          % (decoded / MiB, MAX_DECODED // MiB))
        views = _items(document, "bufferViews")
        images = _items(document, "images")
        pixels = {}
        for index, image in enumerate(images):
            what = "Texture %d of this GLB" % (index + 1)
            if isinstance(image.get("uri"), str):
                check_embedded_image(_data_uri(image["uri"], what), what, pixels)
                continue
            view = image.get("bufferView")
            if type(view) is not int or not 0 <= view < len(views):
                raise Refused("%s is malformed." % what)
            offset, length = views[view].get("byteOffset", 0), views[view].get("byteLength")
            if (type(offset) is not int or type(length) is not int or offset < 0 or length < 1
                    or offset + length > binary_length):
                raise Refused("%s is malformed." % what)
            handle.seek(binary_at + 8 + offset)
            check_embedded_image(handle.read(min(length, HEADER_BYTES)), what, pixels)
    return {"textures": len(images), "meshes": len(_items(document, "meshes"))}


def _items(document, field):
    value = document.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise Refused("This GLB file's description is malformed.")
    return value


def _data_uri(uri, what):
    header, _, payload = uri.partition(",")
    if ";base64" not in header:
        raise Refused("%s isn't an embedded image." % what)
    payload = payload[:HEADER_BYTES * 4 // 3]
    try:
        return base64.b64decode(payload[:len(payload) - len(payload) % 4])
    except ValueError:
        raise Refused("%s is malformed." % what) from None


def _check_fbx(path, size):
    with open(path, "rb") as handle:
        header = handle.read(27)
        version = struct.unpack_from("<I", header, 23)[0] if len(header) == 27 else 0
        if not 7100 <= version <= 7700:
            raise Refused("FBX version %s isn't supported; export FBX 7.1 to 7.7 (binary) or GLB." % version)
        record = struct.Struct("<QQQB" if version >= 7500 else "<IIIB")
        totals = {"decoded": 0, "nodes": 0, "textures": 0}
        _fbx_nodes(handle, record, 27, size, 0, totals)
    return {"textures": totals["textures"]}


def _fbx_nodes(handle, record, start, stop, depth, totals):
    """Walk one node list in ``[start, stop)``, up to its closing null record."""
    if depth > MAX_FBX_DEPTH:
        raise Refused("This FBX file nests too deeply.")
    position = start
    while position + record.size <= stop:
        handle.seek(position)
        end, count, length, name_length = record.unpack(handle.read(record.size))
        if end == 0:
            return
        totals["nodes"] += 1
        if not position < end <= stop or totals["nodes"] > MAX_FBX_NODES:
            raise Refused("This FBX file is malformed.")
        name = handle.read(name_length)
        properties = position + record.size + name_length
        if properties + length > end:
            raise Refused("This FBX file is malformed.")
        _fbx_properties(handle, name, count, properties, properties + length, totals)
        if properties + length < end:
            _fbx_nodes(handle, record, properties + length, end, depth + 1, totals)
        position = end
    if depth:
        raise Refused("This FBX file is truncated.")


def _fbx_properties(handle, name, count, start, stop, totals):
    handle.seek(start)
    for _ in range(count):
        code = handle.read(1)
        if not code:
            raise Refused("This FBX file is truncated.")
        code = code[0]
        if code in _SCALAR:
            handle.seek(_SCALAR[code], os.SEEK_CUR)
        elif code in _ARRAY_ITEM:
            raw = handle.read(12)
            if len(raw) < 12:
                raise Refused("This FBX file is truncated.")
            length, encoding, compressed = struct.unpack("<III", raw)
            declared = length * _ARRAY_ITEM[code]
            totals["decoded"] += declared
            if totals["decoded"] > MAX_DECODED or handle.tell() + compressed > stop:
                raise Refused("This FBX file decodes to more than %d MB, or is malformed; it wasn't imported."
                              % (MAX_DECODED // MiB))
            if encoding == 1:
                _bounded_inflate(handle.read(compressed), declared)
            elif encoding == 0 and compressed == declared:
                handle.seek(compressed, os.SEEK_CUR)
            else:
                raise Refused("This FBX file has a malformed array.")
        elif code in (ord("S"), ord("R")):
            raw = handle.read(4)
            if len(raw) < 4:
                raise Refused("This FBX file is truncated.")
            length = struct.unpack("<I", raw)[0]
            if handle.tell() + length > stop:
                raise Refused("This FBX file is malformed.")
            if name == b"Content" and code == ord("R") and length > 0:
                data = handle.read(min(length, HEADER_BYTES))
                handle.seek(length - len(data), os.SEEK_CUR)
                totals["textures"] += 1
                check_embedded_image(data, "A texture embedded in this FBX file", totals)
            elif name in _FBX_PATHS and code == ord("S"):
                _check_fbx_path(handle.read(length))
            else:
                handle.seek(length, os.SEEK_CUR)
        else:
            raise Refused("This FBX file has an unknown property type.")
    if handle.tell() != stop:
        raise Refused("This FBX file is malformed.")


def _check_fbx_path(raw):
    text = raw.decode("utf-8", "replace")
    if ".." in re.split(r"[\\/]", text):
        raise Refused("This FBX file points at files outside its folder; it wasn't imported. Embed the textures "
                      "(or save as GLB).")


def _bounded_inflate(data, declared):
    inflater = zlib.decompressobj()
    try:
        output = inflater.decompress(data, declared + 1)
    except zlib.error:
        raise Refused("This FBX file has a corrupt compressed array.") from None
    if len(output) != declared or inflater.unconsumed_tail or not inflater.eof:
        raise Refused("This FBX file's compressed data doesn't match its declared size; it wasn't imported.")


def _check_obj(path):
    with open(path, "rb") as handle:
        for raw in handle:
            line = raw.strip()
            if not line.startswith(b"mtllib"):
                continue
            for name in line.split()[1:]:
                text = name.decode("utf-8", "replace")
                if "/" in text or "\\" in text or ".." in text or ":" in text:
                    raise Refused("This OBJ file points at material files elsewhere on disk; it wasn't imported. "
                                  "Save the model as GLB to keep its materials.")


def _check_usdz(path, size):
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise Refused("This USDZ file is malformed.") from None
    with archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ZIP_ENTRIES:
            raise Refused("This USDZ file is empty or has too many entries.")
        total, pixels = 0, {}
        for entry in entries:
            name = entry.filename
            parts = re.split(r"[\\/]", name)
            if (entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 0x1 or name.startswith(("/", "\\"))
                    or ".." in parts or ":" in name):
                raise Refused("This USDZ file isn't a valid package (compressed, encrypted or escaping entries); "
                              "it wasn't imported.")
            total += entry.file_size
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                with archive.open(entry) as member:
                    check_embedded_image(member.read(HEADER_BYTES), "A texture in this USDZ file", pixels)
        if total > size:
            raise Refused("This USDZ file is malformed.")
        layers = [e.filename for e in entries if e.filename.lower().endswith((".usd", ".usda", ".usdc"))]
    return {"entries": len(entries), "layers": layers}
