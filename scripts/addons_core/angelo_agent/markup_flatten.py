# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The marked picture: the original with every mark and numbered pin drawn on it, at the original's resolution
(capped at ``markup_message.MAX_FLAT``) (2.1.0, doc/angelo/attachment_markup.rst).

Drawn on the GPU in one offscreen pass with the viewer's own drawing (``markup_draw.marks``), so the picture the
model gets is the one the person saw, then read back once. ``render`` runs on the main thread (a GPU context);
``markup_message`` describes the same marks to the model. PNG encoding (``view_png``) touches no ``bpy`` and runs on a
worker (``markup_overlay``).
"""

import gpu
from mathutils import Matrix

from . import markup_draw, markup_doc as md, markup_message as mm, markup_view as mv


def _ortho(width, height):
    return Matrix(((2.0 / width, 0, 0, -1.0), (0, 2.0 / height, 0, -1.0), (0, 0, 1, 0), (0, 0, 0, 1)))


# Under a picture with transparency (a Remove BG result): a neutral grey, so a dark subject and a light one both stay
# visible in the flattened picture (break-it of 2298d608dd1, N5); black hid dark subjects.
BACKDROP = (0.5, 0.5, 0.5, 1.0)

def render(doc, texture):
    """``(rgba8 bytes bottom row first, width, height)`` of the original (``texture``, already at ``flat_size``)
    with the document's marks drawn on it."""
    width, height = mm.flat_size(doc.size)
    view = mv.View(doc.size, (0, 0, width, height), margins=(0, 0, 0, 0))
    view.fill()
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            gpu.state.active_framebuffer_get().clear(color=BACKDROP)
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(_ortho(width, height))
                gpu.state.viewport_set(0, 0, width, height)
                markup_draw.picture(texture, (0, 0, width, height))
                markup_draw.marks(view, doc, mm.PIN_PX * md.scale_of(doc.size) * view.scale)
                gpu.state.blend_set('NONE')
            pixels = offscreen.texture_color.read()
        pixels.dimensions = width * height * 4
        return bytes(pixels), width, height
    finally:
        offscreen.free()


def erase_source(size, strokes, texture):
    """The picture Erase sends: the original at ``flat_size`` with the brushed areas painted solid
    ``markup_edit.ERASE_HEX`` (``markup_edit``'s prompt names that colour)."""
    from . import markup_edit
    width, height = mm.flat_size(size)
    view = mv.View(size, (0, 0, width, height), margins=(0, 0, 0, 0))
    view.fill()
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            gpu.state.active_framebuffer_get().clear(color=BACKDROP)
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(_ortho(width, height))
                gpu.state.viewport_set(0, 0, width, height)
                markup_draw.picture(texture, (0, 0, width, height))
                for stroke in strokes:
                    points = [view.to_region(x, y) for x, y in stroke["points"]]
                    markup_draw.polyline(points, markup_edit.ERASE_RGBA, 2 * stroke["radius"] * view.scale)
                gpu.state.blend_set('NONE')
            pixels = offscreen.texture_color.read()
        pixels.dimensions = width * height * 4
        return bytes(pixels), width, height
    finally:
        offscreen.free()
