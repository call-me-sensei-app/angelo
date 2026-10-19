# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stand-ins for the host tests of ``angelo_view``'s pictures beyond the viewport (doc/angelo/view_sources.rst).

Only what the sources read is modelled, as Blender behaves: an Image's
``pixels`` returns its buffer as floats (8-bit values divided by 255, float
buffers as stored) and *loads the image* when its buffer is not cached, which
the tests assert never happens; the native read helper reports a cached
buffer's metadata or None. ``picture_fakes`` stands in for ``imbuf``, so the
delivered PNG is the real ``view_picture`` code's.
"""

import contextlib
import struct
import zlib
from types import ModuleType, SimpleNamespace

import numpy

from pathlib import Path

from picture_fakes import imbuf_module, png_bytes


class Pixels:
    def __init__(self, image):
        self.image = image

    def foreach_get(self, values):
        image = self.image
        if not image.cached:
            image.loaded = True  # What Blender would do: acquire the buffer, loading the file.
        image.reads += 1
        values[:] = image.buffer.reshape(-1)


class Image:
    """bpy.types.Image with a buffer (height, width, channels; bottom row first) as Blender holds it."""

    def __init__(self, name, buffer, *, cached=True, is_float=False, color_space="sRGB", is_data=False,
                 alpha_mode="STRAIGHT", source="GENERATED", kind="IMAGE", dirty=False, tiles=1, view_as_render=False):
        self.name, self.type, self.source, self.alpha_mode = name, kind, source, alpha_mode
        self.buffer = numpy.asarray(buffer, dtype=numpy.float32)
        self.cached, self.is_float, self.loaded, self.reads = cached, is_float, False, 0
        self.colorspace_settings = SimpleNamespace(name=color_space, is_data=is_data)
        self.is_dirty, self.use_view_as_render = dirty, view_as_render
        self.tiles = [SimpleNamespace(number=1001 + index) for index in range(tiles)]
        self.pixels = Pixels(self)

    def metadata(self):
        if not self.cached:
            return {"frame_duration": 1}  # The helper's answer for an image with no buffer in memory.
        height, width, channels = self.buffer.shape
        return {"size": [width, height], "channels": channels, "is_float": self.is_float, "frame_duration": 1}


class Images(list):
    def get(self, name):
        return next((image for image in self if image.name == name), None)


def byte_image(name, rows, **options):
    """An 8-bit image from uint8 RGBA rows given top row first, as a person would draw them."""
    rows = numpy.asarray(rows, dtype=numpy.uint8)
    return Image(name, rows[::-1].astype(numpy.float32) / 255.0, **options)


class UVLayer:
    def __init__(self, name, uvs):
        self.name = name
        self.uv = SimpleNamespace(foreach_get=lambda _attribute, values: values.__setitem__(
            slice(None), numpy.asarray(uvs, dtype=numpy.float32).reshape(-1)))


class Collection(list):
    def __init__(self, items=(), **arrays):
        super().__init__(items)
        self.arrays = arrays

    def foreach_get(self, attribute, values):
        values[:] = numpy.asarray(self.arrays[attribute]).reshape(-1)


def uv_mesh(name, faces, uvs, *, material=None, edit=None):
    """A mesh object whose faces are lists of corner indices into ``uvs`` (one UV per face corner)."""
    starts, totals, offset = [], [], 0
    for face in faces:
        starts.append(offset)
        totals.append(len(face))
        offset += len(face)
    layer = UVLayer("UVMap", uvs)
    mesh = SimpleNamespace(is_editmode=edit is not None, uv_layers=SimpleNamespace(active=layer),
                           loops=[None] * offset, polygons=Collection([None] * len(faces), loop_start=starts,
                                                                    loop_total=totals), edit=edit)
    return SimpleNamespace(name=name, type="MESH", data=mesh, active_material=material)


def material(*images, active=None):
    """A material whose node tree holds one Image Texture node per image; ``active`` indexes the active node."""
    nodes = [SimpleNamespace(bl_idname="ShaderNodeTexImage", image=image) for image in images]
    return SimpleNamespace(node_tree=SimpleNamespace(nodes=_Nodes(nodes, nodes[active] if active is not None else None)))


class _Nodes(list):
    def __init__(self, nodes, active):
        super().__init__(nodes)
        self.active = active


class Blender:
    """The stand-in application for picture sources: ``bpy``, ``_bpy`` and ``imbuf`` modules."""

    def __init__(self):
        self.images = Images()
        self.scene = SimpleNamespace(name="Scene", display_settings=SimpleNamespace(display_device="sRGB"),
                                     view_settings=SimpleNamespace(view_transform="AgX", look="None", exposure=0.0,
                                                                   gamma=1.0))
        self.window = SimpleNamespace(scene=self.scene, view_layer=SimpleNamespace(update=lambda: None),
                                      screen=SimpleNamespace(areas=[]), modal_operators=[])
        self.objects = {}
        self.bpy = ModuleType("bpy")
        self.bpy.data = SimpleNamespace(images=self.images)
        self.bpy.app = SimpleNamespace(background=False, is_job_running=lambda _kind: False)
        self.bpy.context = SimpleNamespace(temp_override=self.override,
                                           window_manager=SimpleNamespace(windows=[self.window]))
        self.bpy.ops = SimpleNamespace(screen=SimpleNamespace(screenshot=self.screenshot))
        self.overridden, self.screenshots = [], []
        self.native = ModuleType("_bpy")
        self.native._angelo_read = SimpleNamespace(image_metadata=lambda image: image.metadata())
        self.imbuf = imbuf_module()
        self.queries = SimpleNamespace(document_epoch=lambda: "e" * 32, resolve_object=self.resolve)

    def resolve(self, _window, handle):
        return self.objects[handle]

    @contextlib.contextmanager
    def override(self, **context):
        self.overridden.append(context)
        yield

    def screenshot(self, filepath, check_existing=True):
        """screen.screenshot: the overridden window's pixels as Blender draws them, written as a PNG."""
        window = self.overridden[-1]["window"]
        self.screenshots.append(window)
        Path(filepath).write_bytes(png_bytes(window.pixels))
        return {"FINISHED"}

    def modules(self):
        return {"bpy": self.bpy, "_bpy": self.native, "imbuf": self.imbuf}


def bmesh_module(faces):
    """``bmesh`` whose edit mesh has ``faces`` given as lists of UV corners: what ``from_edit_mesh`` returns."""
    module = ModuleType("bmesh")

    class Layer:  # Hashable, as BMesh layer keys are.
        name = "UVMap"
    layer = Layer()
    edit_faces = [SimpleNamespace(loops=[{layer: SimpleNamespace(uv=tuple(uv))} for uv in face]) for face in faces]

    class Layers:
        active = layer

    edit = SimpleNamespace(faces=edit_faces, loops=SimpleNamespace(layers=SimpleNamespace(uv=Layers())))
    module.from_edit_mesh = lambda mesh: edit
    return module


def area(kind, x, y, width, height, ui_type=None):
    return SimpleNamespace(type=kind, ui_type=ui_type or kind, x=x, y=y, width=width, height=height)


def editor_window(width, height, areas, colours, status=(255, 255, 0)):
    """A window whose screenshot fills each editor with its colour and the strip below them (the status bar)."""
    pixels = numpy.zeros((height, width, 4), dtype=numpy.uint8)
    pixels[...] = status + (255,)
    for editor, colour in zip(areas, colours):
        top = height - (editor.y + editor.height)
        pixels[top:top + editor.height, editor.x:editor.x + editor.width] = colour + (255,)
    return SimpleNamespace(screen=SimpleNamespace(areas=list(areas)), pixels=pixels, modal_operators=[],
                           scene=None, view_layer=None)


# OpenEXR files as Blender and other renderers write them ------------------------------------------------------

def _attribute(name, kind, value):
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<i", len(value)) + value


def _predicted(raw):
    """OpenEXR's byte interleave, then its predictor: what ZIP, ZIPS and RLE compress."""
    data = numpy.frombuffer(raw, dtype=numpy.uint8)
    ordered = numpy.concatenate([data[0::2], data[1::2]])
    out = ordered.astype(numpy.int16)
    out[1:] = (ordered[1:].astype(numpy.int16) - ordered[:-1].astype(numpy.int16) + 128 + 256) & 255
    return out.astype(numpy.uint8).tobytes()


def _rle(data):
    """OpenEXR's run-length code: a repeated byte as (count - 1, byte), other bytes as (-count, bytes)."""
    out, index, literal = bytearray(), 0, bytearray()

    def flush():
        if literal:
            out.extend(bytes([256 - len(literal)]) + literal)
            literal.clear()
    while index < len(data):
        end = index
        while end + 1 < len(data) and data[end + 1] == data[index] and end - index < 127:
            end += 1
        if end - index + 1 >= 3:
            flush()
            out.extend(bytes([end - index, data[index]]))
            index = end + 1
        else:
            literal.append(data[index])
            index += 1
            if len(literal) == 127:
                flush()
    flush()
    return bytes(out)


COMPRESSIONS = {"NONE": (0, 1), "RLE": (1, 1), "ZIPS": (2, 1), "ZIP": (3, 16), "PIZ": (4, 32)}


def exr_bytes(parts, width, height, compression="ZIP", multipart=True, flags=0, sampling=(1, 1)):
    """An OpenEXR file. ``parts``: ``[(part name, {channel name: (rows top first, "HALF" or "FLOAT"), ...})]``."""
    code, lines = COMPRESSIONS[compression]
    headers, blocks = [], []
    for name, channels in parts:
        names = sorted(channels)
        chlist = b"".join(channel.encode() + b"\0" + struct.pack("<iB3xii", 1 if channels[channel][1] == "HALF" else 2,
                                                                  0, *sampling) for channel in names) + b"\0"
        window = struct.pack("<4i", 0, 0, width - 1, height - 1)
        count = -(-height // lines)
        header = (_attribute("channels", "chlist", chlist) + _attribute("compression", "compression", bytes([code]))
                  + _attribute("dataWindow", "box2i", window) + _attribute("displayWindow", "box2i", window)
                  + _attribute("lineOrder", "lineOrder", b"\0")
                  + _attribute("cryptomatte/manifest", "string", b"{" + b" " * 2000 + b"}"))
        if multipart:
            header += (_attribute("name", "string", name.encode()) + _attribute("type", "string", b"scanlineimage")
                       + _attribute("chunkCount", "int", struct.pack("<i", count)))
        headers.append(header + b"\0")
        chunks = []
        for y in range(0, height, lines):
            rows = range(y, min(height, y + lines))
            raw = b"".join(numpy.ascontiguousarray(channels[channel][0][row], dtype="<f2" if channels[channel][1] == "HALF"
                                                   else "<f4").tobytes() for row in rows for channel in names)
            if code in (2, 3):
                data = zlib.compress(_predicted(raw))
            elif code == 1:
                data = _rle(_predicted(raw))
            else:
                data = raw
            chunks.append((y, data if len(data) < len(raw) else raw))  # OpenEXR keeps what compression cannot shrink.
        blocks.append(chunks)
    head = b"\x76\x2f\x31\x01" + struct.pack("<I", 2 | flags | (0x1000 if multipart else 0)) + b"".join(headers)
    if multipart:
        head += b"\0"
    offset = len(head) + 8 * sum(len(chunks) for chunks in blocks)
    tables, body = b"", b""
    for index, chunks in enumerate(blocks):
        for y, data in chunks:
            tables += struct.pack("<Q", offset + len(body))
            body += (struct.pack("<i", index) if multipart else b"") + struct.pack("<ii", y, len(data)) + data
    return head + tables + body
