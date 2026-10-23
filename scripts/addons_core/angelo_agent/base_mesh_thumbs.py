# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each bundled base mesh's picture in the + menu's base-mesh rows (1.0.5 review SD3).

The pictures are the base meshes' own Essentials asset previews, saved once at 96 px as
``assets/base_mesh_thumbs/<blend file stem>.png`` (a few KB each; made by reading every bundled .blend's asset
preview in a background Blender). They are read here without ``bpy.data`` (a loaded image would show up in the
person's file and image pickers): a small PNG reader turns each into a GPU texture once, and a row draws it as its
32 px tile. A picture that cannot be read leaves the row's drawn glyph (``chat_add_menu.draw_glyph``).
"""

import struct
import zlib
from pathlib import Path

FOLDER = Path(__file__).with_name("assets") / "base_mesh_thumbs"
_textures = {}   # stem -> GPUTexture, or None when the picture could not be read.
drawn = set()    # Options whose picture has been drawn (GUI suites read it).


def stem_for(option):
    """The picture's file stem for a base-mesh option (its asset name), or None."""
    from . import human_base_meshes
    for asset in human_base_meshes.table().get("assets", ()):
        if asset.get("name") == option:
            return Path(asset["file"]).stem
    return None


def path_for(option):
    stem = stem_for(option)
    return FOLDER / (stem + ".png") if stem else None


def decode_png(data):
    """(width, height, rows) of an 8-bit RGB or RGBA, non-interlaced PNG; rows top first, each a bytes of RGBA."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, width = 8, b"", 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour not in (2, 6) or interlace:
                raise ValueError("unsupported PNG")
            channels = 4 if colour == 6 else 3
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    raw, stride, rows, previous = zlib.decompress(idat), width * channels, [], bytearray(width * channels)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up, corner = previous[i], previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        previous = line
        if channels == 3:
            line = bytearray(b"".join(bytes(line[i:i + 3]) + b"\xff" for i in range(0, stride, 3)))
        rows.append(bytes(line))
    return width, height, rows


def _texture(stem):
    if stem in _textures:
        return _textures[stem]
    texture = None
    try:
        import gpu
        width, height, rows = decode_png((FOLDER / (stem + ".png")).read_bytes())
        pixels = []
        for row in reversed(rows):   # GPU textures start at the bottom row.
            for i in range(0, len(row), 4):
                a = row[i + 3] / 255.0   # Premultiplied, as the + menu's other pictures are drawn.
                pixels += (row[i] / 255.0 * a, row[i + 1] / 255.0 * a, row[i + 2] / 255.0 * a, a)
        texture = gpu.types.GPUTexture((width, height), format='RGBA16F',
                                       data=gpu.types.Buffer('FLOAT', width * height * 4, pixels))
    except (OSError, ValueError, zlib.error, struct.error, ImportError):
        texture = None   # A missing or unreadable picture: the row keeps its glyph.
    _textures[stem] = texture
    return texture


def draw(view, option, tx, ty, size=32):
    """The base mesh's picture as a row's ``size`` px tile on the tile colour; False when there is none to draw."""
    stem = stem_for(option)
    texture = _texture(stem) if stem else None
    if texture is None:
        return False
    import gpu
    from gpu_extras.presets import draw_texture_2d
    from . import chat_add_menu
    view.shape(tx, ty, size, size, chat_add_menu.TILE_FILL, 8)
    gpu.state.blend_set('ALPHA_PREMULT')
    draw_texture_2d(texture, (tx + 1, ty + 1), size - 2, size - 2)
    gpu.state.blend_set('ALPHA')
    drawn.add(option)
    return True


def release():
    _textures.clear()
