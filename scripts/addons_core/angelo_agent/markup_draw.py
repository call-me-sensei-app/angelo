# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws the picture viewer: the scrim, the picture, its marks and comment pins, what is being drawn, the selection,
the inline text editor, then the chrome (``markup_chrome``) (2.1.0, doc/angelo/attachment_markup.rst).

Called from the overlay's POST_PIXEL handler (``markup_overlay``) with what it already holds: the session, the laid
out chrome, the picture's texture (or the chip's thumbnail while the full picture loads, or nothing) and the pointer
state. Nothing here reads a file, decodes or uploads. Marks are drawn from ``markup_geometry`` in picture pixels mapped
through the view, at the widths the flattened picture gets, so what the person sees is what the model gets.
"""

import gpu
from gpu_extras.batch import batch_for_shader

from . import canvas_rtl, chat_view, markup_chrome, markup_doc as md, markup_geometry as mg, markup_message as mm
from . import ui_tokens as tk
from .ui_kit_field import draw_field
from .ui_text import iface_, n_

MIN_LINE_PX = 1.5
SELECTION_PX = 1.5
NOTE_PLACEHOLDER = n_("Write a note…")
TEXT_PLACEHOLDER = n_("Type here…")
_shaders = {}


def _shader(name):
    if name not in _shaders:
        _shaders[name] = gpu.shader.from_builtin(name)
    return _shaders[name]


def polyline(points, colour, width, closed=False):
    """A thick line through region-pixel ``points`` (Blender's polyline shader: joins and width in pixels)."""
    if len(points) < 2:
        if points:
            chat_view.shape(points[0][0] - width / 2, points[0][1] - width / 2, width, width, colour, width / 2)
        return
    coordinates = [(x, y) for x, y in points] + ([tuple(points[0])] if closed else [])
    shader = _shader('POLYLINE_UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coordinates})
    shader.bind()
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", max(MIN_LINE_PX, width))
    shader.uniform_float("color", colour)
    batch.draw(shader)


def picture(texture, rect):
    x, y, w, h = rect
    shader = _shader('IMAGE')
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": ((x, y), (x + w, y), (x + w, y + h), (x, y + h)),
                                                 "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)


def _region(view, polyline_points):
    return [view.to_region(px, py) for px, py in polyline_points]


def _mark(view, size, mark, offset=(0.0, 0.0)):
    colour, width = md.COLOURS[mark["colour"]], mg.width(mark, size) * view.scale
    if mark["kind"] == "text":
        x, y = view.to_region(mark["at"][0] + offset[0], mark["at"][1] + offset[1])
        height = md.TEXT_PX * md.scale_of(size) * (0.75 + 0.25 * mark["width"]) * view.scale
        chat_view.text(mark["text"], x, y - height, height, colour, shaped=True)
        return
    for points, closed in mg.lines(mark, size, offset):
        polyline(_region(view, points), colour, width, closed)


def marks(view, doc, pin_radius):
    """Every mark, then every comment region and numbered pin: the viewer's and the flattened picture's one drawing."""
    for mark in doc.marks:
        _mark(view, doc.size, mark)
    _pins(view, doc, pin_radius)


def _pins(view, doc, radius):
    for pin in mm.pins(doc):
        if pin["box"]:
            points = _region(view, mg.rectangle(pin["box"]))
            polyline(points, mm.REGION, max(MIN_LINE_PX, mm.STROKE_PX[1] * md.scale_of(doc.size) * view.scale), True)
        cx, cy = view.to_region(*pin["at"])
        chat_view.shape(cx - radius, cy - radius, 2 * radius, 2 * radius, mm.REGION, radius)
        label = str(pin["n"])
        size = radius
        chat_view.text(label, cx - chat_view.measure(label, size) / 2, cy - size * 0.36, size, tk.INK, shaped=False)


ERASE_SHOWN = (1.0, 0.0, 1.0, 0.55)   # The brushed areas, see-through so the person sees what they cover.


def _erase(view, session):
    strokes = list(session.erase_strokes)
    pending = session.pending
    if pending and pending["kind"] == "brush":
        radius = session.erase_radius()
        strokes.append({"points": pending["points"], "radius": radius})
    for stroke in strokes:
        polyline(_region(view, stroke["points"]), ERASE_SHOWN, 2 * stroke["radius"] * view.scale)


def _crop(view, session, scale):
    """Resize's crop: the picture outside it dimmed, its edge in teal."""
    pending = session.pending
    box = (mg.box_of(pending["start"], pending["end"]) if pending and pending["kind"] == "crop" else session.crop)
    if not box:
        return
    px, py, pw, ph = view.picture_rect()
    left, top = view.to_region(box[0], box[1])
    right, bottom = view.to_region(box[0] + box[2], box[1] + box[3])
    for rect in ((px, py, left - px, ph), (right, py, px + pw - right, ph), (left, py, right - left, bottom - py),
                 (left, top, right - left, py + ph - top)):
        chat_view.shape(*rect, tk.SCRIM)
    polyline([(left, top), (right, top), (right, bottom), (left, bottom)], tk.TEAL, 1.5 * scale, True)


def _selection(view, size, mark, offset):
    x, y, w, h = mg.bounds({**mark, **_moved(mark, offset)} if offset != (0.0, 0.0) else mark, size)
    pad = view.image_length(6)
    points = _region(view, mg.rectangle([x - pad, y - pad, w + 2 * pad, h + 2 * pad]))
    polyline(points, tk.TEAL, SELECTION_PX, True)


def _moved(mark, offset):
    dx, dy = offset
    moved = {}
    for key in ("start", "end", "at"):
        if key in mark:
            moved[key] = [mark[key][0] + dx, mark[key][1] + dy]
    if "box" in mark:
        moved["box"] = [mark["box"][0] + dx, mark["box"][1] + dy] + list(mark["box"][2:])
    if "points" in mark:
        moved["points"] = [[x + dx, y + dy] for x, y in mark["points"]]
    return moved


def _editor(view, session, scale, ui, now):
    """The inline field of a text mark or a comment's note, beside its point."""
    editing = session.editing
    if editing["kind"] == "text":
        anchor, placeholder = editing["at"], TEXT_PLACEHOLDER
    else:
        comment = session.doc.comment(editing["id"])
        anchor, placeholder = (comment.get("box") or comment["at"])[:2], NOTE_PLACEHOLDER
    x, y = view.to_region(*anchor)
    width, height = 260 * scale, 36 * scale
    region_w, region_h = view.region[2], view.region[3]
    x = min(max(view.region[0] + 8 * scale, x + 16 * scale), view.region[0] + region_w - width - 8 * scale)
    y = min(max(view.region[1] + 8 * scale, y - height - 8 * scale), view.region[1] + region_h - height - 8 * scale)
    draw_field(chat_view_view(), (x, y, width, height), scale, fs=session.field, placeholder=iface_(placeholder),
               action="editor", time=now)


_card_view = None


def chat_view_view():
    global _card_view
    if _card_view is None:
        from .card_view import View
        _card_view = View()
    return _card_view


def draw(session, plan, picture_texture, scale, ui, now, region_size, notice=None, busy=False, card=None):
    """One frame of the viewer over its region (``region_size`` = width, height in region pixels); ``notice``,
    ``busy`` and ``card`` as ``markup_chrome.draw`` takes them."""
    width, height = region_size
    view, doc = session.view, session.doc
    chat_view.shape(0, 0, width, height, tk.SCRIM)
    rect = view.picture_rect()
    if picture_texture is not None:
        picture(picture_texture, rect)
    else:   # Loading: the picture's place, so marks and pins never float on the scrim.
        chat_view.shape(*rect, tk.WELL)
    with gpu.matrix.push_pop():
        pending = session.pending
        moving = pending if pending and pending["kind"] == "move" else None
        for mark in doc.marks:
            offset = (moving["dx"], moving["dy"]) if moving and moving["id"] == mark["id"] else (0.0, 0.0)
            _mark(view, doc.size, mark, offset)
        if pending and pending["kind"] not in ("move",):
            drawn = {**pending, "colour": session.colour, "width": session.width}
            if pending["kind"] == "comment":
                polyline(_region(view, mg.rectangle(mg.box_of(pending["start"], pending["end"]))), mm.REGION,
                         MIN_LINE_PX, True)
            else:
                _mark(view, doc.size, drawn)
        _pins(view, doc, 12 * scale)
        _erase(view, session)
        if session.mode == "resize":
            _crop(view, session, scale)
        if session.selected:
            try:
                mark = doc.mark(session.selected)
            except KeyError:
                session.selected = None
            else:
                offset = (moving["dx"], moving["dy"]) if moving and moving["id"] == mark["id"] else (0.0, 0.0)
                _selection(view, doc.size, mark, offset)
        if session.editing:
            _editor(view, session, scale, ui, now)
    canvas_rtl.begin(width)   # The chrome mirrors for a right-to-left language; the picture above never does.
    try:
        markup_chrome.draw(plan, session, scale, ui, now, notice, busy, card)
    finally:
        canvas_rtl.end()
    gpu.state.blend_set('NONE')
