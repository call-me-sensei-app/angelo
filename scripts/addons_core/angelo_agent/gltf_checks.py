# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks of glTF documents shared by every place Angelo imports a GLB or glTF it didn't write itself.

Each check reads the document the way Blender's importer will (``io_scene_gltf2``), never a friendlier way:

* **URIs.** The importer decodes a URI as data only when it starts with ``data:`` and contains ``;base64,`` (what
  follows the first ``;base64,``, decoded without validation); it opens every other URI as a path,
  ``join(dirname(model), normpath(unquote(uri)))``. ``data_payload`` admits exactly the form it decodes and returns
  the same bytes; ``check_references`` admits any other URI only for a person's own file and only when that path
  stays inside the model's folder (``importer_path``), and never for a downloaded file (``self_contained``).
* **Images are read from the buffer their view names**: the GLB's binary chunk, a ``data:`` buffer or a side file.
* **Buffer view extensions.** Meshopt-compressed views (``EXT_``/``KHR_meshopt_compression``) are decoded into
  ``count`` x ``byteStride`` bytes up front: those count toward ``MAX_DECODED``; any other view extension is
  refused, and an image's view may carry none.

Standard library only; no bpy (also imported as a plain module by Media Studio's background writer).
"""

import base64
import json
import os
from urllib.parse import unquote

MiB = 1024 * 1024
MAX_DECODED = 1024 * MiB
MESHOPT = frozenset({"EXT_meshopt_compression", "KHR_meshopt_compression"})
ACCESSOR_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
COMPONENT_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_SEPARATOR = ";base64,"


class Refused(ValueError):
    """Shared with ``team_library_files.Refused`` (it re-exports this class)."""


def items(document, field, what="This model"):
    value = document.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise Refused("%s's description is malformed." % what)
    return value


def is_data_uri(uri):
    return isinstance(uri, str) and uri.startswith("data:")


def data_payload(uri, what):
    """The bytes the importer decodes from a ``data:`` URI; refuses any ``data:`` URI it would open as a path."""
    index = uri.find(_SEPARATOR) if is_data_uri(uri) else -1
    if index < 0:
        raise Refused("%s refers to a file through a malformed data: link; it wasn't imported." % what)
    try:
        return base64.b64decode(uri[index + len(_SEPARATOR):])   # Non-validating, as the importer decodes it.
    except ValueError:
        raise Refused("%s holds malformed embedded data; it wasn't imported." % what) from None


def decoded_size(document, what="This model"):
    """Bytes the importer allocates for accessors and meshopt views; refuses an unbounded view extension."""
    total = 0
    for accessor in items(document, "accessors", what):
        count, kind, component = accessor.get("count"), accessor.get("type"), accessor.get("componentType")
        width = ACCESSOR_WIDTH.get(kind) if isinstance(kind, str) else None
        component = COMPONENT_BYTES.get(component) if type(component) is int else None
        if type(count) is not int or count < 0 or not width or not component:
            raise Refused("%s has a malformed accessor; it wasn't imported." % what)
        total += count * width * component
    for view in items(document, "bufferViews", what):
        extensions = view.get("extensions") or {}
        if not isinstance(extensions, dict):
            raise Refused("%s has a malformed buffer view; it wasn't imported." % what)
        for name, value in extensions.items():
            if name not in MESHOPT or not isinstance(value, dict):
                raise Refused("%s uses a buffer extension Angelo doesn't check (%s); it wasn't imported."
                              % (what, str(name)[:40]))
            meshopt_view(value, what)
            total += value["count"] * value["byteStride"]
    if total > MAX_DECODED:
        raise Refused("%s decodes to %.0f MB of geometry; up to %d MB is imported."
                      % (what, total / MiB, MAX_DECODED // MiB))
    return total


_FILTER_STRIDES = {"NONE": None, "OCTAHEDRAL": (4, 8), "QUATERNION": (8,), "EXPONENTIAL": "x4", "COLOR": (4, 8)}


def meshopt_view(value, what):
    """Refuse meshopt parameters outside the extension's specification (the native decoder trusts them)."""
    count, stride, mode, kind = value.get("count"), value.get("byteStride"), value.get("mode"), value.get("filter")
    if not isinstance(mode, str) or not (kind is None or isinstance(kind, str)):
        raise Refused("%s has a malformed compressed buffer view; it wasn't imported." % what)
    bad = (type(count) is not int or type(stride) is not int or count < 0 or not 0 < stride <= 256
           or count * stride > MAX_DECODED
           or (mode == "ATTRIBUTES" and stride % 4)
           or (mode == "TRIANGLES" and (stride not in (2, 4) or count % 3))
           or (mode == "INDICES" and stride not in (2, 4))
           or mode not in ("ATTRIBUTES", "TRIANGLES", "INDICES"))
    if not bad and kind not in (None, "NONE"):
        allowed = _FILTER_STRIDES.get(kind, ())
        bad = mode != "ATTRIBUTES" or (allowed == "x4" and stride % 4) or \
            (isinstance(allowed, tuple) and stride not in allowed)
    if bad:
        raise Refused("%s has a malformed compressed buffer view; it wasn't imported." % what)


def check_views(document, what="This model"):
    """Every compressed buffer view within the specification (any path; the total bound is ``decoded_size``)."""
    for view in items(document, "bufferViews", what):
        extensions = view.get("extensions") or {}
        if not isinstance(extensions, dict):
            raise Refused("%s has a malformed buffer view; it wasn't imported." % what)
        for name, value in extensions.items():
            if name in MESHOPT:
                if not isinstance(value, dict):
                    raise Refused("%s has a malformed compressed buffer view; it wasn't imported." % what)
                meshopt_view(value, what)


def image_bytes(document, index, image, readers, limit, what):
    """The first ``limit`` bytes of image ``index`` as the importer will read them. ``readers[i](offset, length)``
    reads buffer ``i`` (None: that buffer can't be located, which refuses the model)."""
    uri = image.get("uri")
    if uri is not None:
        raise Refused("%s is not inside the model's buffers." % what)   # Callers handle uri images first.
    views = items(document, "bufferViews")
    view = image.get("bufferView")
    if type(view) is not int or not 0 <= view < len(views):
        raise Refused("%s is malformed; the model wasn't imported." % what)
    record = views[view]
    if record.get("extensions"):
        raise Refused("%s is stored compressed; the model wasn't imported." % what)
    buffer, offset, length = record.get("buffer"), record.get("byteOffset", 0), record.get("byteLength")
    if (type(buffer) is not int or not 0 <= buffer < len(readers) or readers[buffer] is None or type(offset) is not int
            or type(length) is not int or offset < 0 or length < 1):
        raise Refused("%s can't be located; the model wasn't imported." % what)
    data = readers[buffer](offset, min(length, limit))
    if data is None or len(data) < min(length, limit):
        raise Refused("%s lies outside its buffer; the model wasn't imported." % what)
    return data


def bytes_reader(data):
    return lambda offset, length: data[offset:offset + length] if offset + length <= len(data) else None


def file_reader(path, start=0, size=None):
    """Reads ``path`` from ``start`` (the GLB binary chunk's data) within ``size`` bytes."""
    def read(offset, length):
        if size is not None and offset + length > size:
            return None
        with open(path, "rb") as handle:
            handle.seek(start + offset)
            data = handle.read(length)
        return data if len(data) == length else None
    return read


def importer_path(model_path, uri, path=os.path):
    """The absolute path the importer opens for a non-``data:`` URI of the model at ``model_path``: exactly its
    ``join(dirname(model), normpath(unquote(uri)))`` with this platform's path rules (``path``: tests pass ``ntpath``)."""
    return path.abspath(path.join(path.dirname(str(model_path)), path.normpath(unquote(uri.replace("\\", "/")))))


def _inside(folder, target, path):
    try:
        return path.commonpath([folder, target]) == folder
    except ValueError:   # Another drive, or a mix of absolute and relative: outside.
        return False


def check_references(document, model_path=None, *, self_contained, what="This model"):
    """Refuse a buffer or image URI the importer would read from outside the model.

    ``self_contained``: a downloaded file, whose URIs may only be data (``data_payload``'s form). Otherwise (a
    person's own file) a URI may also name a file inside ``model_path``'s folder, resolved as the importer does,
    links followed."""
    try:
        _check_references(document, model_path, self_contained, what)
    except (OSError, ValueError, TypeError) as error:
        if isinstance(error, Refused):
            raise
        raise Refused("%s refers to a file Angelo can't check; it wasn't imported." % what) from None


def _check_references(document, model_path, self_contained, what, path=os.path):
    check_views(document, what)
    folder = path.abspath(path.dirname(str(model_path))) if model_path else None
    for field in ("buffers", "images"):
        for record in items(document, field, what):
            uri = record.get("uri")
            if uri is None:
                continue
            if not isinstance(uri, str):
                raise Refused("%s's description is malformed." % what)
            if is_data_uri(uri):
                data_payload(uri, what)
                continue
            if self_contained or folder is None:
                raise Refused("%s refers to other files; only a self-contained model is imported." % what)
            target = importer_path(model_path, uri, path)
            # Lexically first: a path outside is refused without touching it (a network share is never opened);
            # then with links resolved, so a link inside that leads outside is refused too.
            if "\0" in target or not _inside(folder, target, path) or \
                    not _inside(path.realpath(folder), path.realpath(target), path):
                raise Refused("%s refers to a file outside its folder; it wasn't imported." % what)


def read_document(path):
    """The JSON description of a GLB or glTF file (``.gltf`` JSON, or a GLB's JSON chunk)."""
    with open(path, "rb") as handle:
        head = handle.read(20)
        if head[:4] == b"glTF":
            if len(head) < 20 or head[16:20] != b"JSON":
                raise Refused("This GLB file is malformed or not glTF 2.0.")
            length = int.from_bytes(head[12:16], "little")
            if length > 64 * MiB:
                raise Refused("This GLB file's description is too large.")
            raw = handle.read(length)
        else:
            handle.seek(0)
            raw = handle.read(64 * MiB + 1)
            if len(raw) > 64 * MiB:
                raise Refused("This glTF file's description is too large.")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("This model's description is unreadable; it wasn't imported.") from None
    if not isinstance(document, dict):
        raise Refused("This model's description is unreadable; it wasn't imported.")
    return document


def check_file(path, *, self_contained):
    """``check_references`` on the GLB or glTF file at ``path``, right before Blender's importer reads it."""
    check_references(read_document(path), path, self_contained=self_contained)
