# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks of the Angelo Library's downloaded files before Blender reads them (doc/angelo/library.rst, "Safety").

The files passed their size and checksum in ``library_fetch``, which proves they are what the index lists, not
that they are safe: the catalog holds files from many open sources. So the same bounds as the team library apply
(``team_library_files``: format from the content, size caps, decompression bounds) plus what that module never
admits:

* **Multi-file glTF** (Poly Haven models): every ``uri`` of a buffer or image must be one of the item's own listed
  file names (all downloaded into one private folder, checked by ``library_catalog.safe_name``) or a bounded
  ``data:`` URI; images must be PNG, JPEG or WebP within the pixel bounds; required extensions must be ones
  Blender's importer reads; accessors decode to at most ``MAX_DECODED``.
* **KTX2-textured GLB** (ToonLab's first-party rocks, ``KHR_texture_basisu``, which Blender can't read): a copy with
  the KTX2 images and their texture references removed is imported instead, and the rock's PNG maps (listed beside
  it) become its material (``library_import``).
* **Radiance HDR** (HDRIs): the header must be ``#?RADIANCE`` or ``#?RGBE`` with a standard resolution line, within
  ``MAX_SIDE`` and ``MAX_PIXELS``.

Standard library only; no bpy. Called on worker threads.
"""

import json
import re
import struct
from pathlib import Path
from urllib.parse import unquote

from . import gltf_checks as gltf, team_library_files as checks
from .team_library_files import Refused

HDR_PIXELS = 16384 * 8192
HDR_LINE = 127   # Longest header line read alike here and by OpenImageIO (128-byte pieces with the newline).
READABLE_EXTENSIONS = frozenset({
    "KHR_materials_emissive_strength", "KHR_materials_transmission", "KHR_materials_volume", "KHR_materials_ior",
    "KHR_materials_specular", "KHR_materials_clearcoat", "KHR_materials_sheen", "KHR_materials_unlit",
    "KHR_texture_transform", "KHR_mesh_quantization", "KHR_draco_mesh_compression", "EXT_meshopt_compression",
    "KHR_lights_punctual", "KHR_materials_anisotropy", "KHR_materials_iridescence", "KHR_materials_variants"})
PLAIN_GLB = "_plain.glb"   # Never an index name: those start with a letter or digit (library_catalog.safe_name).


def check_item(item, paths):
    """The checked facts of an item's downloaded files (``paths``: ``{name: Path}``); raises ``Refused``."""
    roles = {}
    for name, path in paths.items():
        role = next(f["role"] for f in _files(item, paths) if f["name"] == name)
        roles.setdefault(role, []).append((name, Path(path)))
    if item["kind"] == "model":
        name, path = roles["model"][0]
        maps = {role: path for role, entries in roles.items() if role not in {"model", "resource"}
                for _name, path in entries[:1]}
        for role, map_path in maps.items():
            _image(map_path, "The %s map" % role.replace("_", " "))
        if name.lower().endswith(".gltf"):
            return {"model": path, "format": "gltf", "maps": maps,
                    **check_gltf(path, {n: p for n, p in roles.get("resource", [])})}
        return {"model": _plain_glb(path), "format": "glb", "maps": maps}
    if item["kind"] == "texture":
        maps = {}
        for role, entries in roles.items():
            _image(entries[0][1], "The %s map" % role.replace("_", " "))
            maps[role] = entries[0][1]
        return {"maps": maps}
    if item["kind"] == "hdri":
        path = roles["hdri"][0][1]
        width, height = check_hdr(path)
        return {"hdri": path, "width": width, "height": height}
    path = roles["image"][0][1]
    width, height = _image(path, "This sprite")
    return {"image": path, "width": width, "height": height}


def _files(item, paths):
    names = set(paths)
    return [f for group in [item["files"]] + list((item.get("resolutions") or {}).values()) for f in group
            if f["name"] in names]


def _image(path, what):
    with open(path, "rb") as handle:
        head = handle.read(checks.HEADER_BYTES)
    fmt = ""
    try:
        fmt = checks.sniff(head[:65536])
    except Refused:
        pass
    if fmt not in checks.IMAGE_FORMATS:
        raise Refused("%s isn't a PNG, JPEG or WebP image; it wasn't imported." % what)
    if Path(path).stat().st_size > checks.MAX_BYTES["image"]:
        raise Refused("%s is larger than %d MB; it wasn't imported." % (what, checks.MAX_BYTES["image"] // checks.MiB))
    return checks.check_image(head, what)


def check_hdr(path):
    """``(width, height)`` of a Radiance HDR file whose header is standard and within the bounds."""
    with open(path, "rb") as handle:
        head = handle.read(8192)
    if not (head.startswith(b"#?RADIANCE") or head.startswith(b"#?RGBE")):
        raise Refused("This environment isn't a Radiance HDR file; it wasn't imported.")
    blank = head.find(b"\n\n")
    if blank < 0:
        raise Refused("This HDR file's header is malformed; it wasn't imported.")
    if any(len(line) >= HDR_LINE for line in head[:blank].split(b"\n")):
        # OpenImageIO (Blender's reader) takes header lines in 128-byte pieces: a longer line ends early there and
        # the rest is read as more header, so the size line it finds is not the one read here.
        raise Refused("This HDR file's header has an overlong line; it wasn't imported.")
    line = head[blank + 2:blank + 64].split(b"\n", 1)[0].decode("ascii", "replace")
    match = re.fullmatch(r"([-+])([YX]) (\d{1,6}) ([-+])([YX]) (\d{1,6})", line.strip())
    if not match or match.group(2) == match.group(5):
        raise Refused("This HDR file's size line is malformed; it wasn't imported.")
    first, second = int(match.group(3)), int(match.group(6))
    height, width = (first, second) if match.group(2) == "Y" else (second, first)
    if not (1 <= width <= 16384 and 1 <= height <= 16384) or width * height > HDR_PIXELS:
        raise Refused("This HDR is %d x %d pixels; environments up to 16,384 pixels a side are imported."
                      % (width, height))
    if Path(path).stat().st_size > 512 * checks.MiB:
        raise Refused("This HDR file is larger than 512 MB; it wasn't imported.")
    seen = _reader_size(path)
    if seen is not None and seen != (width, height):
        raise Refused("This HDR file's size reads differently to Blender's reader; it wasn't imported.")
    return width, height


def _reader_size(path):
    """The size Blender's own image reader (OpenImageIO, bundled with Blender) finds in the header, or None where the
    module isn't importable (host tests); reads the header only, never pixels."""
    try:
        import OpenImageIO
    except ImportError:
        return None
    reader = OpenImageIO.ImageInput.open(str(path))
    if reader is None:
        raise Refused("Blender's reader can't open this HDR file; it wasn't imported.")
    try:
        spec = reader.spec()
        return spec.width, spec.height
    finally:
        reader.close()


def check_gltf(path, resources):
    """A multi-file glTF whose references are all its own listed files or strict ``data:`` URIs, read as Blender's
    importer reads them (``gltf_checks``); returns ``{"textures", "meshes"}``."""
    if Path(path).stat().st_size > checks.MAX_JSON:
        raise Refused("This glTF file is larger than Angelo reads; it wasn't imported.")
    try:
        document = json.loads(Path(path).read_bytes())
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("This glTF file is unreadable; it wasn't imported.") from None
    if not isinstance(document, dict) or not isinstance(document.get("asset"), dict) \
            or str(document["asset"].get("version", "")) != "2.0":
        raise Refused("This glTF file isn't glTF 2.0; it wasn't imported.")
    required = document.get("extensionsRequired") or []
    unknown = [e for e in required if e not in READABLE_EXTENSIONS] if isinstance(required, list) else ["?"]
    if unknown:
        raise Refused("This glTF model needs an extension Blender doesn't read (%s); it wasn't imported."
                      % ", ".join(str(e)[:40] for e in unknown[:3]))
    lookup = {name.lower(): p for name, p in resources.items()}

    def listed(uri, what):
        target = lookup.get(unquote(uri).replace("\\", "/").lower()) if isinstance(uri, str) else None
        if target is None:
            raise Refused("%s refers to a file outside its own; it wasn't imported." % what)
        return target
    readers = []
    for buffer in gltf.items(document, "buffers", "This glTF model"):
        uri, length = buffer.get("uri"), buffer.get("byteLength")
        if type(length) is not int or length < 0:
            raise Refused("This glTF model's binary data is malformed; it wasn't imported.")
        if gltf.is_data_uri(uri):
            readers.append(gltf.bytes_reader(gltf.data_payload(uri, "This glTF model")))
            continue
        target = listed(uri, "This glTF model")
        if target.stat().st_size < length:
            raise Refused("This glTF model's binary data is truncated; it wasn't imported.")
        readers.append(gltf.file_reader(target, 0, target.stat().st_size))
    gltf.decoded_size(document, "This glTF model")
    totals = {}
    images = gltf.items(document, "images", "This glTF model")
    for index, image in enumerate(images):
        what = "Texture %d of this model" % (index + 1)
        uri = image.get("uri")
        if gltf.is_data_uri(uri):
            data = gltf.data_payload(uri, what)[:checks.HEADER_BYTES]
        elif uri is not None:
            with open(listed(uri, what), "rb") as handle:
                data = handle.read(checks.HEADER_BYTES)
        else:
            data = gltf.image_bytes(document, index, image, readers, checks.HEADER_BYTES, what)
        checks.check_embedded_image(data, what, totals)
    return {"textures": len(images), "meshes": len(gltf.items(document, "meshes", "This glTF model"))}


def _plain_glb(path):
    """The GLB to import: checked as the team library checks one, after removing KTX2 textures when it has them."""
    path = Path(path)
    document, binary = _read_glb(path)
    required = document.get("extensionsRequired") or []
    used = document.get("extensionsUsed") or []
    if "KHR_texture_basisu" in required or "KHR_texture_basisu" in used:
        plain = path.with_name(PLAIN_GLB)
        _write_glb(plain, _without_basisu(document), binary)
        path = plain
    checks.inspect(str(path), "model")
    return path


def _read_glb(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise Refused("This model isn't a GLB file; it wasn't imported.")
    _magic, version, length, json_length, json_type = struct.unpack_from("<4sIIII", data)
    if version != 2 or length != len(data) or json_type != 0x4E4F534A or not 0 < json_length <= min(checks.MAX_JSON, len(data) - 20):
        raise Refused("This GLB file is malformed or not glTF 2.0.")
    try:
        document = json.loads(data[20:20 + json_length])
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("This GLB file's description is unreadable.") from None
    if not isinstance(document, dict):
        raise Refused("This GLB file's description is unreadable.")
    rest = 20 + json_length + (-json_length % 4)
    return document, data[rest:]


def _write_glb(path, document, binary_chunks):
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + binary_chunks
    Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def _without_basisu(document):
    """The document without KTX2 images: every texture reference in materials goes, then textures and images."""
    document = json.loads(json.dumps(document))
    for material in document.get("materials") or []:
        if not isinstance(material, dict):
            continue
        for key in ("normalTexture", "occlusionTexture", "emissiveTexture"):
            material.pop(key, None)
        pbr = material.get("pbrMetallicRoughness")
        if isinstance(pbr, dict):
            pbr.pop("baseColorTexture", None)
            pbr.pop("metallicRoughnessTexture", None)
        material.pop("extensions", None)
    for key in ("textures", "images", "samplers"):
        document.pop(key, None)
    for key in ("extensionsRequired", "extensionsUsed"):
        kept = [e for e in document.get(key) or [] if e != "KHR_texture_basisu"]
        if kept:
            document[key] = kept
        else:
            document.pop(key, None)
    return document
