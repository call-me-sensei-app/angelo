# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The camera preview card's geometry (doc/angelo/camera_preview.rst). Pure: no bpy; region pixels, y up.

The card stands in one of the 3D view's corners (``CORNERS``), ``offset`` pixels in from it, inside the part of the
view no toolbar, sidebar, header or shelf covers (``Box``), clear of Angelo's own corner controls (``avoid``). It is
the camera's picture at the render's aspect over a strip with the camera's name (the switcher) and a close button;
a grip at the corner away from the anchor resizes it. Its width is the person's (points, scaled), clamped so it
never takes more than a share of the view. A drop snaps to the nearest corner and keeps where it was dropped as an
offset from there, so a window resize keeps it in its corner.
"""

from collections import namedtuple

CORNERS = ("BOTTOM_LEFT", "BOTTOM_RIGHT", "TOP_LEFT", "TOP_RIGHT")
DEFAULT_CORNER = "BOTTOM_LEFT"   # Blender's 3D view keeps this corner free (text at the top left, axes at the top right).
DEFAULT_WIDTH = 256    # Points, at a UI scale of 1.
MIN_WIDTH = 160
NARROW_MIN = 112        # A tall picture's card at least: its strip still holds the name and the close button.
MAX_WIDTH = 720
MAX_WIDTH_SHARE = 0.5   # Of the free box's width ...
MAX_HEIGHT_SHARE = 0.6  # ... and height.
MARGIN = 16             # From the free box's edges (ui_tokens.SPACE_4).
INSET = 4               # The picture's inset from the card's edge.
STRIP = 28              # The strip under the picture: the camera's name and the close button.
CLOSE = 20
GRIP = 16               # A 16-point pointer target, as every control (the UI audit's rule).
CHIP_H = 22             # A state chip on the picture ("Preparing materials…", "Paused · Refresh").
ROW = 28                # A switcher row.
SWITCHER_ROWS_MAX = 8
RENDER_MAX = 512        # The offscreen's long side at most, in pixels: a small picture is drawn small.
QUANTUM = 8             # Offscreen sizes step by this many pixels, so a resize drag reuses its picture.
ASPECT_MIN, ASPECT_MAX = 0.25, 4.0
MIN_VIEW = (2 * MIN_WIDTH, 240)   # Points: a view smaller than this shows no preview (the card would crowd it).

Box = namedtuple("Box", "left right bottom top")
Card = namedtuple("Card", "corner card picture strip chip close grip scale rtl", defaults=(False,))
Menu = namedtuple("Menu", "panel rows content scroll view track", defaults=(None, None))
Pill = namedtuple("Pill", "rect body close glyph_x text_x")


def aspect(res_x, res_y, pixel_x=1.0, pixel_y=1.0):
    """Width over height of the camera's picture: the render resolution with its pixel aspect, clamped."""
    try:
        value = (res_x * pixel_x) / (res_y * pixel_y)
    except (TypeError, ZeroDivisionError):
        return 16 / 9
    if value != value or value <= 0:   # NaN or nothing.
        return 16 / 9
    return min(ASPECT_MAX, max(ASPECT_MIN, value))


def fits(box, scale):
    """Whether the free box has room for the card at its smallest beside the rest of the view (else it hides)."""
    return (box.right - box.left >= MIN_VIEW[0] * scale and box.top - box.bottom >= MIN_VIEW[1] * scale)


def _size(width_pt, ratio, scale, box):
    """``(card width, picture width, picture height)`` in pixels for a requested width in points. A picture too tall
    for the view's height share is sized by that height; its card is at least ``NARROW_MIN`` wide (the picture
    centred in it), so a portrait camera in a short view never leaves it."""
    room_w = (box.right - box.left) * MAX_WIDTH_SHARE
    room_h = (box.top - box.bottom) * MAX_HEIGHT_SHARE
    width = min(max(MIN_WIDTH, min(MAX_WIDTH, width_pt)) * scale, max(MIN_WIDTH * scale, room_w))
    chrome_h = (STRIP + INSET) * scale
    if (width - 2 * INSET * scale) / ratio + chrome_h > room_h:   # A tall picture: its height decides.
        picture_h = max(1.0, room_h - chrome_h)
        picture_w = picture_h * ratio
        return max(NARROW_MIN * scale, picture_w + 2 * INSET * scale), picture_w, picture_h
    picture_w = width - 2 * INSET * scale
    return width, picture_w, picture_w / ratio


def _overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _clamp_into(x, y, w, h, box, scale):
    margin = MARGIN * scale
    x = min(max(x, box.left + margin), box.right - margin - w)
    y = min(max(y, box.bottom + margin), box.top - margin - h)
    return x, y


def place(corner, offset, width_pt, ratio, scale, box, avoid=(), rtl=False):
    """The card's rectangles ``(x, y, w, h)`` at ``corner``, moved ``offset`` (dx, dy >= 0) in from it. ``rtl``: a
    right-to-left interface reads the strip from the right (the close button at its left end); the card stays where
    the person put it, as the 3D view under it is not mirrored."""
    corner = corner if corner in CORNERS else DEFAULT_CORNER
    width, picture_w, picture_h = _size(width_pt, ratio, scale, box)
    height = picture_h + (STRIP + INSET) * scale
    margin = MARGIN * scale
    dx, dy = max(0.0, offset[0]), max(0.0, offset[1])
    left, bottom = corner.endswith("LEFT"), corner.startswith("BOTTOM")
    x = box.left + margin + dx if left else box.right - margin - width - dx
    y = box.bottom + margin + dy if bottom else box.top - margin - height - dy
    x, y = _clamp_into(x, y, width, height, box, scale)
    for _ in range(len(avoid) + 1):   # Slide along the corner's vertical edge, away from it, past each obstacle.
        blocking = [rect for rect in avoid if _overlaps((x, y, width, height), rect)]
        if not blocking:
            break
        if bottom:
            y = max(rect[1] + rect[3] for rect in blocking) + margin / 2
        else:
            y = min(rect[1] for rect in blocking) - margin / 2 - height
        x, y = _clamp_into(x, y, width, height, box, scale)
    return _parts(corner, x, y, width, height, picture_w, picture_h, scale, rtl)


def _parts(corner, x, y, width, height, picture_w, picture_h, scale, rtl=False):
    inset, strip_h = INSET * scale, STRIP * scale
    picture = (x + (width - picture_w) / 2, y + strip_h, picture_w, picture_h)
    strip = (x + inset, y, width - 2 * inset, strip_h)
    close_size = CLOSE * scale
    close_y = y + (strip_h - close_size) / 2
    if rtl:
        close = (x + inset + 2 * scale, close_y, close_size, close_size)
        chip_x = close[0] + close_size + 8 * scale
        chip = (chip_x, y + 4 * scale, max(0.0, x + width - inset - 2 * scale - chip_x), strip_h - 8 * scale)
    else:
        close = (x + width - inset - close_size - 2 * scale, close_y, close_size, close_size)
        chip = (x + inset + 2 * scale, y + 4 * scale, max(0.0, close[0] - (x + inset + 2 * scale) - 8 * scale),
                strip_h - 8 * scale)
    grip_size = GRIP * scale
    gx = x + width - grip_size if corner.endswith("LEFT") else x
    # Away from the anchor: the picture's top corner in a bottom corner; in a top corner the picture's bottom one
    # (the strip below it holds the name and the close button).
    gy = y + height - grip_size if corner.startswith("BOTTOM") else y + strip_h
    return Card(corner, (x, y, width, height), picture, strip, chip, close, (gx, gy, grip_size, grip_size), scale, rtl)


def _chip_room(card, scale):
    """``(left, right, y)`` of the room for a state chip along the picture's top edge, clear of the grip (which
    stands at that edge's left end in a right corner, its right end in a left corner)."""
    px, py, pw, ph = card.picture
    y = py + ph - (CHIP_H + 6) * scale
    left, right = px + 6 * scale, px + pw - 6 * scale
    gx, gy, gw, gh = card.grip
    if gy < y + CHIP_H * scale and y < gy + gh:   # The grip is on the chip's line.
        if gx + gw / 2 < px + pw / 2:
            left = max(left, gx + gw + 4 * scale)
        else:
            right = min(right, gx - 4 * scale)
    return left, max(left, right), y


def chip_width(text_width, scale, dot=True):
    return text_width + (28 if dot else 16) * scale


def chip(card, text_width, scale, dot=True):
    """The state chip's ``(x, y, w, h)``: at the picture's top, clear of the grip, as wide as its text where it fits
    (the text is elided to the rest); from the right end in a right-to-left interface."""
    left, right, y = _chip_room(card, scale)
    width = min(chip_width(text_width, scale, dot), right - left)
    return (right - width if card.rtl else left, y, width, CHIP_H * scale)


def action_button(card, text_width, scale):
    """A button on the picture (a paused picture's Refresh): centred in the room below the state chip, a 28-point
    pill as wide as its words, narrower only in a picture too small for them (the words are then elided)."""
    px, py, pw, ph = card.picture
    height = 28 * scale
    width = min(text_width + 32 * scale, pw - 12 * scale)
    chip_bottom = py + ph - (CHIP_H + 6) * scale
    top = chip_bottom - 4 * scale
    y = min(py + (ph - height) / 2, top - height)
    y = max(py + 4 * scale, y)
    return (px + (pw - width) / 2, y, width, height)


def pill(card, text_width, scale, close):
    """A pill standing in for the card ("Add camera from this view", "Choose a camera", "Back to your view"): 32
    points tall in the card's corner, its glyph, words and close button (``close``) in the interface's reading order.
    The card passed is placed at the corner itself (``camera_preview.card_for(at_corner=True)``)."""
    height = 32 * scale
    width = text_width + (52 + (28 if close else 0)) * scale
    x0, y0, w0, h0 = card.card
    x = x0 if card.corner.endswith("LEFT") else x0 + w0 - width
    y = y0 if card.corner.startswith("BOTTOM") else y0 + h0 - height
    button = None
    if card.rtl:
        if close:
            button = (x + 8 * scale, y + 6 * scale, 20 * scale, 20 * scale)
        glyph_x = x + width - 28 * scale
        text_x = glyph_x - 8 * scale - text_width
        body = (x + (28 * scale if close else 0), y, width - (28 * scale if close else 0), height)
    else:
        if close:
            button = (x + width - 28 * scale, y + 6 * scale, 20 * scale, 20 * scale)
        glyph_x = x + 12 * scale
        text_x = x + 36 * scale
        body = (x, y, width - (28 * scale if close else 0), height)
    return Pill((x, y, width, height), body, button, glyph_x, text_x)


def picture_size(card):
    """The offscreen's ``(width, height)`` in pixels: the picture's, at most ``RENDER_MAX`` on its long side, stepped
    by ``QUANTUM`` above the smallest so a resize drag does not reallocate every pixel."""
    w, h = card.picture[2], card.picture[3]
    shrink = min(1.0, RENDER_MAX / max(w, h, 1.0))
    w, h = w * shrink, h * shrink
    if max(w, h) <= MIN_WIDTH:
        return max(16, round(w)), max(16, round(h))
    if w >= h:
        width = max(16, int(w // QUANTUM) * QUANTUM)
        return width, max(16, round(width * h / w))
    height = max(16, int(h // QUANTUM) * QUANTUM)
    return max(16, round(height * w / h)), height


def _inside(rect, x, y):
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def hit(card, x, y):
    """What a press at region pixel (x, y) does: ``close``, ``switch``, ``resize``, ``look`` (the picture), ``move``
    (the rest of the card), or None outside it."""
    if card is None or not _inside(card.card, x, y):
        return None
    for name, rect in (("close", card.close), ("resize", card.grip), ("switch", card.chip), ("look", card.picture)):
        if _inside(rect, x, y):
            return name
    return "move"


def anchor(x, y, width, height, box, scale):
    """``(corner, offset)`` for a card dropped with its lower left at (x, y): the corner nearest its centre, and how
    far in from that corner it stands (so ``place`` puts it back where it was dropped)."""
    margin = MARGIN * scale
    x, y = _clamp_into(x, y, width, height, box, scale)
    left = x + width / 2 < (box.left + box.right) / 2
    bottom = y + height / 2 < (box.bottom + box.top) / 2
    corner = ("BOTTOM" if bottom else "TOP") + ("_LEFT" if left else "_RIGHT")
    dx = x - (box.left + margin) if left else (box.right - margin - width) - x
    dy = y - (box.bottom + margin) if bottom else (box.top - margin - height) - y
    return corner, (max(0.0, dx), max(0.0, dy))


def resize(card, x, y, scale):
    """The width in points the grip dragged to region pixel (x, y) asks for (the anchor's side stays put)."""
    left_x = card.card[0]
    right_x = card.card[0] + card.card[2]
    width = x - left_x if card.corner.endswith("LEFT") else right_x - x
    return max(MIN_WIDTH, min(MAX_WIDTH, width / scale))


def switcher(card, count, scale, box, scroll=0.0, need=0.0):
    """The camera list opened from the strip (or the "Choose a camera" pill): ``count`` camera rows that scroll in
    ``view`` above a pinned "Add camera from this view" row (``rows[-1]``), above the card in a bottom corner and
    below it in a top one, at most ``SWITCHER_ROWS_MAX`` rows tall, with a scroll ``track`` when the rows scroll.
    As wide as the card, never narrower than a default card, and wider for rows that need it (``need``: the widest
    row's pixels), anchored on the card's side and kept inside the view."""
    row_h, inset, margin = ROW * scale, INSET * scale, MARGIN * scale
    shown = min(count, SWITCHER_ROWS_MAX - 1)
    height = (shown + 1) * row_h + 2 * inset
    room = max(card.card[2], box.right - box.left - 2 * margin)
    width = min(max(card.card[2], DEFAULT_WIDTH * scale, need + 2 * inset), room)
    x = card.card[0] if card.corner.endswith("LEFT") else card.card[0] + card.card[2] - width
    x = max(box.left + margin, min(x, box.right - margin - width)) if width <= box.right - box.left - 2 * margin \
        else x
    if card.corner.startswith("BOTTOM"):
        y = card.card[1] + card.card[3] + inset
        y = min(y, box.top - height)
    else:
        y = card.card[1] - inset - height
        y = max(y, box.bottom)
    view = (x + inset, y + inset + row_h, width - 2 * inset, shown * row_h)
    content = count * row_h
    scroll = max(0.0, min(scroll, content - view[3]))
    top = view[1] + view[3] + scroll
    rects = [(x + inset, top - (index + 1) * row_h, width - 2 * inset, row_h) for index in range(count)]
    rects.append((x + inset, y + inset, width - 2 * inset, row_h))
    track = None
    if content > view[3] + 0.5:
        bar_h = max(12 * scale, view[3] * view[3] / content)
        bar_y = view[1] + view[3] - bar_h - (scroll / (content - view[3])) * (view[3] - bar_h)
        bar_x = x + inset + scale if card.rtl else x + width - inset - 4 * scale
        track = (bar_x, bar_y, 3 * scale, bar_h)
    return Menu((x, y, width, height), rects, content, scroll, view, track)


def row_at(menu, x, y):
    """The index of the switcher row under (x, y) that shows inside the list (a camera row inside the scrolled view,
    or the pinned add row), or None."""
    if not _inside(menu.panel, x, y):
        return None
    last = len(menu.rows) - 1
    view = menu.view or menu.panel
    for index, rect in enumerate(menu.rows):
        if not _inside(rect, x, y):
            continue
        if index == last or (rect[1] >= view[1] - 0.5 and rect[1] + rect[3] <= view[1] + view[3] + 0.5):
            return index
    return None


def camera_for_view(lens, perspective, distance):
    """``(type, lens, ortho_scale)`` of a camera that frames what a 3D view shows. The view draws at twice a camera's
    zoom (``BKE_camera_params_from_view3d``: CAMERA_PARAM_ZOOM_INIT_PERSP), so a perspective camera gets half the
    view's lens and an orthographic one twice the view's scale (``distance * 36 / lens``, the sensor's width)."""
    if perspective == 'ORTHO':
        return 'ORTHO', 50.0, max(1e-3, 2.0 * distance * 36.0 / max(lens, 1e-3))
    return 'PERSP', max(1.0, lens / 2.0), 6.0
