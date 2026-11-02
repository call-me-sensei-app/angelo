# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Create column shows what the person attached as pictures, never as file names (media first).

One tile per attached frame, reference image or reference video, in every tab (Image, 3D, Video): the picture
itself (a video: its first frame as a poster, a play mark and its length), its label beneath, and a remove mark in
its corner. ``tiles`` lays a list of them out in rows that fit the column and returns the height it used.

A tile's picture comes ready from ``studio_tile_previews``: a draw only asks for it, and the first draws of a new
attachment show the empty tile while a main-thread timer prepares it (never a load, decode, resize or upload here:
1.0.8 audit). Its texels are the file's stored values, drawn with the plain ``IMAGE`` shader.
"""

from . import chat_view as canvas
from .ui_text import iface_

LABEL_HEIGHT = 18   # The label line under a tile's picture.
GAP = 8
_shader = None


def _picture(path, rect):
    """The prepared picture at ``path`` cropped to fill ``rect`` (a tile is a frame, not an inspector); False while
    it is being prepared or when it can't be shown."""
    global _shader
    from . import studio_tile_previews
    found = studio_tile_previews.previews().texture(path) if path else None
    if found is None:
        return False
    import gpu
    from gpu_extras.batch import batch_for_shader
    from . import canvas_rtl
    if _shader is None:
        _shader = gpu.shader.from_builtin('IMAGE')   # Stored values: what the screen shows.
    x, y, w, h = rect
    # Fill the tile: crop the longer side of the picture around its centre.
    scale = max(w / found.width, h / found.height)
    u, v = min(1.0, w / (found.width * scale)), min(1.0, h / (found.height * scale))
    u0, v0 = (1 - u) / 2, (1 - v) / 2
    with canvas_rtl.upright(x, w) as x:   # A picture is never mirrored (right-to-left layouts).
        batch = batch_for_shader(_shader, 'TRIS', {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
            "texCoord": [(u0, v0), (u0 + u, v0), (u0 + u, v0 + v), (u0, v0 + v)]}, indices=[(0, 1, 2), (0, 2, 3)])
        _shader.bind()
        _shader.uniform_sampler("image", found.texture)
        batch.draw(_shader)
    return True


def tile(state, rect, item):
    """One attachment: ``item`` {path, label, remove: (action, value), seconds (a video's length, optional)}."""
    from .studio import FIELD, LINE, MUTED, SCRIM, TEXT
    from .studio_draw import hit
    x, y, w, h = rect
    py, ph = y + LABEL_HEIGHT, h - LABEL_HEIGHT
    canvas.shape(x, py, w, ph, LINE, 8)
    canvas.shape(x + 1, py + 1, w - 2, ph - 2, FIELD, 7)
    _picture(item.get("path"), (x + 1, py + 1, w - 2, ph - 2))   # Unreadable: the empty field stays.
    if item.get("seconds") is not None:   # A video: a play mark and its length, on a dark chip so any frame reads.
        canvas.shape(x + w / 2 - 14, py + ph / 2 - 14, 28, 28, SCRIM, 14)
        cx, cy = x + w / 2 + 2, py + ph / 2
        for a, b in (((-4, -6), (-4, 6)), ((-4, 6), (6, 0)), ((6, 0), (-4, -6))):   # Play.
            canvas.stroke(cx + a[0], cy + a[1], cx + b[0], cy + b[1], TEXT, 1.6)
        if item["seconds"] > 0:
            length = iface_("{seconds} s", seconds=round(item["seconds"], 1))
            lw = canvas.measure(length, 10) + 10
            canvas.shape(x + w - lw - 5, py + 5, lw, 16, SCRIM, 6)
            canvas.text(length, x + w - lw, py + 9, 10, TEXT)
    canvas.shape(x + w - 25, py + ph - 25, 20, 20, SCRIM, 10)   # Remove, in the picture's corner.
    canvas.icon("close", x + w - 15, py + ph - 15, TEXT)
    hit(state, (x + w - 28, py + ph - 28, 26, 26), *item["remove"])
    canvas.text(canvas.elide(item["label"], w - 2, 10), x + 1, y + 4, 10, MUTED)


def tiles(state, items, px, cw, top, columns=2, aspect=9 / 16):
    """Lay ``items`` out under ``top`` in rows of ``columns``; returns the new ``top`` (below the last row)."""
    if not items:
        return top
    width = (cw - GAP * (columns - 1)) / columns
    height = width * aspect + LABEL_HEIGHT
    for index, item in enumerate(items):
        row, column = divmod(index, columns)
        tile(state, (px + column * (width + GAP), top - GAP - height - row * (height + GAP), width, height), item)
    rows = (len(items) + columns - 1) // columns
    return top - rows * (height + GAP)


def reference_tiles(state, px, cw, top):
    """The Create column's reference images (every tab), three to a row; returns the new ``top``."""
    items = [{"path": r.get("path"), "label": r["name"], "remove": ("remove_reference", r["id"])}
             for r in state["references"]]
    return tiles(state, items, px, cw, top, columns=3, aspect=1.0)
