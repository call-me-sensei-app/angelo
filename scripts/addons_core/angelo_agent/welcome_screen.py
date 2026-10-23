# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's welcome screen, custom-drawn over the 3D view: not Blender's splash and not a stock dialog.

A banner (``assets/welcome-banner.png``, made by
``release/branding/angelo/welcome_banner.py``) carries the live title. Below
it: the two ways to work, each with a small diagram of its layout (Director is
marked as the current one), how to switch (the Director / Manual menu and the
shortcut as keycaps), demo tiles (``first_run.DEMOS``) and one primary button.

This module only lays out and draws; ``intro.py`` owns opening, input and the
saved state. Layout is measured with the chat's font and translated text, wraps
instead of truncating, and shrinks as a whole when the 3D view is small.
Drawing touches no files and no Blender data: the banner's pixels are read
once, outside drawing, by ``prepare_banner``.
"""

import math
import sys
from array import array
from pathlib import Path

import bpy

from . import canvas_rtl, first_run, welcome_slides
from .ui_text import iface_, join

CARD = (0.090, 0.098, 0.118, 1)  # welcome_banner.py fades its bottom edge into this.
PANEL = (0.122, 0.133, 0.157, 1)
PANEL_HOVER = (0.150, 0.163, 0.192, 1)
LINE = (0.200, 0.215, 0.245, 1)
TEXT = (0.950, 0.960, 0.970, 1)
MUTED = (0.620, 0.650, 0.690, 1)
TEAL = (0.169, 0.769, 0.749, 1)
TEAL_SOFT = (0.169, 0.769, 0.749, 0.22)
INK = (0.035, 0.055, 0.065, 1)
SCRIM = (0.020, 0.024, 0.030, 0.66)
BANNER_ASPECT = 480 / 1520
BANNER = Path(__file__).resolve().parent / "assets" / "welcome-banner.png"

_banner = {"pixels": None, "size": None, "texture": None, "failed": False}
_shader = None


def prepare_banner():
    """Read the banner's pixels once, outside drawing (the same pattern as native_preview_images)."""
    if _banner["pixels"] is not None or _banner["failed"]:
        return
    image = None
    try:
        image = bpy.data.images.load(str(BANNER), check_existing=False)
        image.colorspace_settings.name = 'Non-Color'
        width, height = image.size
        if not width or not height:
            raise ValueError("The welcome banner did not decode")
        pixels = array('f', [0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        _banner.update(pixels=pixels, size=(width, height))
    except (OSError, RuntimeError, ValueError):
        _banner["failed"] = True  # A missing or undownloaded asset leaves a plain banner, never an error.
    finally:
        if image is not None:
            bpy.data.images.remove(image)


def release():
    _banner.update(pixels=None, size=None, texture=None, failed=False)


def shortcut_keys():
    """The Director / Manual toggle as the person has it bound now (``shortcuts.DIRECTOR`` by default), as this
    platform labels it: macOS's modifier symbols in its order, else the key names."""
    from . import shortcuts
    chord = shortcuts.bound(shortcuts.DIRECTOR[0]) or {**shortcuts.DIRECTOR[1], "name": shortcuts.DIRECTOR[1]["type"]}
    return key_caps(chord, sys.platform == "darwin")


def key_caps(chord, mac):
    """A chord's key caps: ``("⌃", "⌥", "⇧", "D")`` on macOS, ``("Ctrl", "Alt", "Shift", "D")`` elsewhere."""
    names = (("ctrl", "⌃", "Ctrl"), ("alt", "⌥", "Alt"), ("shift", "⇧", "Shift"), ("oskey", "⌘", "Win"))
    caps = [symbol if mac else word for key, symbol, word in names if chord.get(key)]
    key = chord.get("type", "")
    return tuple(caps + [key if len(key) == 1 else chord.get("name") or key])


def _text_width(view, text, size):
    from .ui_kit import measure
    return measure(view, text, size)  # As ``view.text`` draws it.


def _wrap(view, text, width, size):
    # Measured at the size it is drawn at (13.5 px, not 13): a translation filling the width must not overflow.
    return [line.rstrip() for line, _ in view.wrap(text, max(40, int(width)), round(size, 2), view._font)]


def layout(view, width, height, scale, hover=None, here="director", slide=0, fade=1.0, pressed=None):
    """Every rectangle and line of the screen, in region pixels, plus clickable areas. ``here``: the mode the window
    is in ("director" or "standard"), which the screen marks *You're here*; opened from Help in Manual mode it said
    Director before 1.0.4, and offered Manual to a person already in it. ``slide`` is the slideshow's current slide,
    ``fade`` how far it has faded in, ``pressed`` the control under a held button (``welcome_slides``)."""
    plan = _fit(view, width, height, scale, hover, here, slide, fade, False, pressed)
    if plan["height"] > height - 32 * plan["scale"]:   # Too short even at the smallest scale: the slideshow
        small = _fit(view, width, height, scale, hover, here, slide, fade, True, pressed)   # drops its picture.
        if small["scale"] >= plan["scale"]:
            return small
    return plan


def _fit(view, width, height, scale, hover, here, slide, fade, compact, pressed=None):
    for _ in range(4):  # Shrink the whole screen until it fits the 3D view.
        plan = _plan(view, width, height, scale, hover, here, slide, fade, compact, pressed)
        if plan["height"] <= height - 32 * scale or scale <= 0.6:
            return plan
        scale *= max(0.6, (height - 32 * scale) / plan["height"])
    return plan


def _plan(view, region_w, region_h, s, hover, here="director", slide=0, fade=1.0, compact=False, pressed=None):
    W = min(780 * s, region_w - 40 * s)
    pad, gap = 30 * s, 14 * s
    banner_h = W * BANNER_ASPECT
    inner = W - 2 * pad
    ops, hits = [], []
    title = _wrap(view, iface_("Welcome to Angelo"), W * 0.52, 30 * s)
    subtitle = _wrap(view, iface_("Describe what you want to make, and Angelo builds it with you in Blender."), W * 0.50, 15 * s)
    side_by_side = W >= 600 * s
    card_w = (inner - gap) / 2 if side_by_side else inner
    # The second mode has one name everywhere: Manual, as the mode menu, the hint and the guide call it (1.0.3 and
    # earlier said "Standard editor" here only). Director's card says the menus are still there (1.0.4: Save and
    # Undo were thought to be Manual-only).
    modes = (("director", iface_("Director"), iface_("Just the 3D view and the chat, with Blender's menus at the top. "
                                                     "Orbit, select and move things as usual, and ask Angelo for "
                                                     "everything else.")),
             ("standard", iface_("Manual", "Angelo"), join([iface_("All of Blender: Properties, Outliner, Timeline and "
                                                                   "toolbars, with the chat docked beside the 3D view."),
                                                            # Corner Angelo (chat_launcher), the same in both modes.
                                                            iface_("Hide the chat and Angelo waits in the 3D view's "
                                                                   "corner; click him to bring it back.")], " ")))
    mode_blocks = []
    for key, name, body in modes:
        lines = _wrap(view, body, card_w - 36 * s, 13.5 * s)
        block_h = 18 * s + 58 * s + 14 * s + (19 * s) + len(lines) * 19 * s + (44 * s if key == "standard" != here else 0) + 14 * s
        mode_blocks.append((key, name, lines, block_h))
    # The switch line spans the card. Media Studio, Generation Flows and the rest are the slideshow's (1.0.5,
    # ``welcome_slides``), which replaced the single Media Studio card that sat beside this line.
    switch_lines = _wrap(view, iface_("Switch any time from the Director / Manual menu in the top-right corner, "
                                      "or with this shortcut:"), inner, 14 * s)
    switch_h = len(switch_lines) * 21 * s + 40 * s
    row2_h = switch_h
    slides_h = welcome_slides.layout(view, _wrap, 0, 0, inner, s, slide, compact=compact, here=here)[3]
    columns = 4 if W >= 700 * s else 2
    tile_w = (inner - gap * (columns - 1)) / columns
    # Wrapped in full, never elided (1.0.5 review: German and French titles were cut with "…"); every tile in the
    # grid is as tall as the one with the most lines.
    tiles = [(index, _wrap(view, iface_(title_), tile_w - 58 * s, 13 * s), url)
             for index, (title_, url) in enumerate(first_run.DEMOS)]
    tile_h = max(62 * s, max(len(lines) for _, lines, _ in tiles) * 17 * s + 28 * s)
    tile_rows = math.ceil(len(tiles) / columns)
    rows_h = max(b[3] for b in mode_blocks) if side_by_side else sum(b[3] for b in mode_blocks) + gap
    body_h = (26 * s + 22 * s + rows_h + 36 * s + row2_h + 36 * s + slides_h + 36 * s + 22 * s
              + tile_rows * tile_h + (tile_rows - 1) * gap + 30 * s + 46 * s + pad)
    H = banner_h + body_h
    x0 = (region_w - W) / 2
    top = min(region_h - 16 * s, (region_h + H) / 2)
    ops.append(("scrim", (0, 0, region_w, region_h)))
    ops.append(("shape", (x0, top - H, W, H), CARD, W * 0.0237))
    ops.append(("banner", (x0, top - banner_h, W, banner_h)))
    y = top - banner_h * 0.40 + (len(title) - 1) * 18 * s
    for line in title:
        ops.append(("text", line, x0 + pad + 8 * s, y, 30 * s, TEXT))
        y -= 38 * s
    y -= 4 * s
    for line in subtitle:
        ops.append(("text", line, x0 + pad + 8 * s, y, 15 * s, (0.86, 0.89, 0.91, 1)))
        y -= 21 * s
    close = (x0 + W - 46 * s, top - 46 * s, 32 * s, 32 * s)
    ops.append(("close", close, hover == ("close", None)))
    hits.append((close, "close", None))
    y = top - banner_h - 26 * s
    ops.append(("text", iface_("Two ways to work"), x0 + pad, y, 12.5 * s, MUTED))
    y -= 22 * s
    row_top = y
    for index, (key, name, lines, block_h) in enumerate(mode_blocks):
        cx = x0 + pad + (index * (card_w + gap) if side_by_side else 0)
        cy_top = row_top if side_by_side else row_top - sum(b[3] + gap for b in mode_blocks[:index])
        card_h = rows_h if side_by_side else block_h
        rect = (cx, cy_top - card_h, card_w, card_h)
        if key == here:
            ops.append(("shape", rect, TEAL, 12 * s))
            ops.append(("shape", (rect[0] + 1.5 * s, rect[1] + 1.5 * s, rect[2] - 3 * s, rect[3] - 3 * s), PANEL, 11 * s))
        else:
            ops.append(("shape", rect, PANEL, 12 * s))
        ops.append(("diagram", key, (cx + 18 * s, cy_top - 18 * s - 58 * s, 104 * s, 58 * s)))
        ty = cy_top - 18 * s - 58 * s - 14 * s - 13 * s
        ops.append(("text", name, cx + 18 * s, ty, 17 * s, TEXT))
        if key == here:
            badge = iface_("You're here")
            badge_w = _text_width(view, badge, 11.5 * s) + 18 * s
            ops.append(("shape", (cx + card_w - 18 * s - badge_w, ty - 5 * s, badge_w, 22 * s), TEAL_SOFT, 11 * s))
            ops.append(("text", badge, cx + card_w - 9 * s - badge_w, ty + 1.5 * s, 11.5 * s, TEAL))
        ly = ty - 26 * s
        for line in lines:
            ops.append(("text", line, cx + 18 * s, ly, 13.5 * s, MUTED))
            ly -= 19 * s
        if key == "standard" != here:
            label = iface_("Try Manual mode")
            button = (cx + 18 * s, cy_top - card_h + 16 * s, _text_width(view, label, 13 * s) + 28 * s, 30 * s)
            ops.append(("shape", button, PANEL_HOVER if hover == ("standard", None) else LINE, 15 * s))
            ops.append(("text", label, button[0] + 14 * s, button[1] + 10 * s, 13 * s, TEXT))
            hits.append((button, "standard", None))
    y = row_top - rows_h - 36 * s
    row2_top = y + 14 * s   # The switch line's cap height above its baseline.
    for line in switch_lines:
        ops.append(("text", line, x0 + pad, y, 14 * s, TEXT))
        y -= 21 * s
    kx = x0 + pad
    for key in shortcut_keys():
        key_w = max(30 * s, _text_width(view, key, 13 * s) + 18 * s)
        ops.append(("keycap", (kx, y - 22 * s, key_w, 28 * s), key))
        kx += key_w + 6 * s
    y = row2_top - 14 * s - row2_h - 36 * s
    slide_ops, slide_hits, slides_rect, _ = welcome_slides.layout(view, _wrap, x0 + pad, y + 18 * s, inner, s, slide,
                                                                  hover, fade, compact, pressed, here)
    ops += slide_ops
    hits += slide_hits
    y -= slides_h + 36 * s
    ops.append(("text", iface_("See it in action"), x0 + pad, y + 18 * s, 12.5 * s, MUTED))
    y -= 4 * s
    for index, lines, url in tiles:
        column, row = index % columns, index // columns
        rect = (x0 + pad + column * (tile_w + gap), y - (row + 1) * tile_h - row * gap, tile_w, tile_h)
        ops.append(("tile", rect, lines, hover == ("demo", index)))
        hits.append((rect, "demo", index))
    y -= tile_rows * tile_h + (tile_rows - 1) * gap + 30 * s
    start = iface_("Start creating")
    start_w = _text_width(view, start, 15 * s) + 44 * s
    button = (x0 + W - pad - start_w, y - 46 * s, start_w, 46 * s)
    ops.append(("shape", button, (0.24, 0.85, 0.82, 1) if hover == ("start", None) else TEAL, 23 * s))
    ops.append(("text", start, button[0] + 22 * s, button[1] + 17 * s, 15 * s, INK))
    hits.append((button, "start", None))
    note = _wrap(view, iface_("Open this again from Help or the Director / Manual menu."), W - 3 * pad - start_w, 12 * s)
    ny = button[1] + 26 * s if len(note) == 1 else button[1] + 34 * s
    for line in note:
        ops.append(("text", line, x0 + pad, ny, 12 * s, MUTED))
        ny -= 17 * s
    ops = [op for op in ops if op[0] != "slide_tip"] + [op for op in ops if op[0] == "slide_tip"]   # Tips on top.
    return {"ops": ops, "hits": hits, "card": (x0, top - H, W, H), "height": H, "scale": s, "slides": slides_rect,
            "slide": slide, "compact": compact}


def draw(view, plan):
    """Run the planned operations; GPU only, inside the 3D view's POST_PIXEL callback."""
    import gpu
    for op in plan["ops"]:
        kind = op[0]
        if kind != "text":
            gpu.state.blend_set('ALPHA')  # Text drawing resets blending; translucent shapes need it back.
        if kind == "scrim":
            view.shape(*op[1], SCRIM)
        elif kind == "shape":
            view.shape(*op[1], op[2], op[3])
        elif kind == "text":
            view.text(op[1], op[2], op[3], op[4], op[5])
        elif kind == "banner":  # Text-free art: mirrored with the layout in a right-to-left language.
            _draw_banner(gpu, op[1])
        elif kind == "close":
            x, y, w, h = op[1]
            view.shape(x, y, w, h, (1, 1, 1, 0.16 if op[2] else 0.08), w / 2)
            m = w * 0.34
            view.stroke(x + m, y + m, x + w - m, y + h - m, TEXT, 1.6)
            view.stroke(x + m, y + h - m, x + w - m, y + m, TEXT, 1.6)
        elif kind == "keycap":  # A shortcut reads left to right in every language: the row is placed, not mirrored.
            x, y, w, h = op[1]
            s = plan["scale"]
            row = [o[1] for o in plan["ops"] if o[0] == "keycap"]
            start, end = min(r[0] for r in row), max(r[0] + r[2] for r in row)
            with canvas_rtl.upright(start, end - start) as left:
                x = x - start + left
                view.shape(x, y - 2 * s, w, h, (0.06, 0.066, 0.08, 1), 6 * s)
                view.shape(x, y, w, h, LINE, 6 * s)
                view.text(op[2], x + (w - _text_width(view, op[2], 13 * s)) / 2, y + 9 * s, 13 * s, TEXT)
        elif kind == "tile":
            _draw_tile(view, op[1], op[2], op[3], plan["scale"])
        elif kind.startswith("slide_"):
            welcome_slides.draw(view, op, plan["scale"])
        elif kind == "diagram":  # Blender's real screen, which is not mirrored, so neither is its picture.
            with canvas_rtl.upright(op[2][0], op[2][2]) as x:
                _draw_diagram(view, op[1], (x,) + tuple(op[2][1:]))


def _draw_banner(gpu, rect):
    global _shader
    from gpu_extras.batch import batch_for_shader
    if _banner["pixels"] is None:
        return
    width, height = _banner["size"]
    if _banner["texture"] is None:
        _banner["texture"] = gpu.types.GPUTexture((width, height), format='RGBA16F',
                                                  data=gpu.types.Buffer('FLOAT', [width * height * 4], _banner["pixels"]))
    if _shader is None:
        _shader = gpu.shader.from_builtin('IMAGE')
    x, y, w, h = rect
    batch = batch_for_shader(_shader, 'TRIS', {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                                               "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]}, indices=[(0, 1, 2), (0, 2, 3)])
    _shader.bind()
    _shader.uniform_sampler("image", _banner["texture"])
    batch.draw(_shader)


def _draw_tile(view, rect, lines, hovered, s):
    x, y, w, h = rect
    view.shape(x, y, w, h, PANEL_HOVER if hovered else PANEL, 12 * s)
    cx, cy, r = x + 28 * s, y + h / 2, 15 * s
    view.shape(cx - r, cy - r, 2 * r, 2 * r, TEAL, r)
    # Play triangle drawn as three strokes: no glyph or icon font needed. It points right in every language.
    with canvas_rtl.upright(cx - r, 2 * r) as left:
        cx = left + r
        for a, b in (((cx - 4 * s, cy - 6 * s), (cx - 4 * s, cy + 6 * s)), ((cx - 4 * s, cy + 6 * s), (cx + 6 * s, cy)),
                     ((cx + 6 * s, cy), (cx - 4 * s, cy - 6 * s))):
            view.stroke(a[0], a[1], b[0], b[1], INK, 2.2 * s)
    ty = y + h / 2 + (len(lines) - 1) * 8 * s - 4 * s
    for line in lines:
        view.text(line, x + 52 * s, ty, 13 * s, TEXT)
        ty -= 17 * s


def _draw_diagram(view, key, rect):
    """A small picture of each layout: the chat column is teal in both."""
    x, y, w, h = rect
    view.shape(x, y, w, h, (0.06, 0.068, 0.082, 1), 6)
    chat = (x + w * 0.72, y + 4, w * 0.28 - 4, h - 8)
    if key == "director":
        view.shape(x + 4, y + 4, w * 0.72 - 7, h - 8, (0.20, 0.22, 0.26, 1), 4)
        cube = min(w, h) * 0.22
        view.shape(x + w * 0.36 - cube / 2, y + h / 2 - cube / 2, cube, cube, (0.55, 0.58, 0.62, 1), 2)
    else:
        view.shape(x + 4, y + h - 9, w * 0.72 - 7, 5, LINE, 2)          # Top bar
        view.shape(x + 4, y + 4, 6, h - 15, LINE, 2)                    # Toolbar
        view.shape(x + 13, y + 14, w * 0.40, h - 26, (0.20, 0.22, 0.26, 1), 3)  # 3D view
        view.shape(x + 16 + w * 0.40, y + h * 0.45, w * 0.72 - w * 0.40 - 23, h * 0.55 - 12, LINE, 2)  # Outliner
        view.shape(x + 16 + w * 0.40, y + 14, w * 0.72 - w * 0.40 - 23, h * 0.45 - 17, LINE, 2)  # Properties
        view.shape(x + 13, y + 4, w * 0.72 - 16, 7, LINE, 2)            # Timeline
    view.shape(*chat, TEAL_SOFT, 4)
    for index in range(3):
        bubble_w = chat[2] * (0.62 if index % 2 else 0.80)
        bx = chat[0] + (chat[2] - bubble_w - 4 if index % 2 else 4)
        view.shape(bx, chat[1] + chat[3] - 12 - index * 11, bubble_w, 6, TEAL, 3)


def hit(plan, x, y):
    """The action under a point, ("outside", None) beyond the card, or None inside it."""
    for (rx, ry, rw, rh), action, value in plan["hits"]:
        if rx <= x <= rx + rw and ry <= y <= ry + rh:
            return action, value
    cx, cy, cw, ch = plan["card"]
    return None if cx <= x <= cx + cw and cy <= y <= cy + ch else ("outside", None)
