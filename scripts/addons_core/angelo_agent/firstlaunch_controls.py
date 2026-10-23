# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Controls the first-launch cards draw that ``card_kit`` does not have yet: banner, dropdown, segmented control,
toggle row and scene tile.

API (for ``setup_card`` and ``start_card``; the kit stream may fold these into ``ui_kit*``):

* ``Builder`` is ``card_kit.Builder`` (with the kit's methods) plus ``custom(x, top, w, h, painter, *args, hit=None,
  focus=True, overlay=False)``: a card-relative rectangle drawn by ``painter(view, rect, state, scale, *args)``
  beneath the card's text (or above everything, ``overlay``), with an optional ``(action, value)`` hit. It is
  drawn as the kit component ``"firstlaunch"`` (``ui_kit.register_component``), so ``card_draw`` needs nothing.
* ``popup(x, top, w, h, close)``: an open menu's opaque panel, above the card's text and controls, with a
  click-away catcher over the whole card (``close``). Its items are ``custom(..., overlay=True)`` after it. A
  painter marked ``popup`` (``menu_panel``) is always drawn in that top layer, whatever ``custom`` was told: a menu
  panel beneath the card's controls lets them show through the list (1.0.5 screenshot review, the setup card).
* Painters: ``banner``, ``dropdown_field`` and ``menu_panel`` / ``menu_item`` (a multi-column language list),
  ``row_panel`` (a bordered row whose text wraps), ``tile`` (a scene thumbnail). ``state`` is
  ``(hover, pressed, focus, enabled)`` like every card control. The segmented controls and the switch are the
  kit's (``ui_kit_controls``).
* ``thumbnail(path)`` reads a ``.blend`` file's preview pixels once, outside drawing; ``release()`` frees them.

Every label reaching a painter is already translated. GPU only while drawing; no file or Blender data access.
"""

from . import card_kit as kit, ui_kit, ui_tokens as tokens

# Private tokens this surface needed (listed in the stream report for ui_tokens).
SWATCH_DARK = (0.235, 0.240, 0.250, 1)     # The Dark look's swatch dot (lifted from its window grey to read on a track).
SWATCH_LIGHT = (0.851, 0.851, 0.851, 1)    # The Light look's window grey.
TRACK = (0.067, 0.075, 0.090, 1)           # Thumbnail wells, below PANEL.
BANNER_FALLBACK = ((0.035, 0.137, 0.161, 1), (0.055, 0.075, 0.090, 1))  # The art's teal-to-navy, without the asset.

BANNER_ASPECT = 480 / 1520   # The welcome art's proportions (welcome_screen.BANNER_ASPECT).

_thumbs = {}   # path -> {"pixels", "size", "texture"}
_shader = None


class Builder(kit.Builder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.customs = []   # (x, top, w, h, painter, args, hit)
        self.overlay_hits = []

    def custom(self, x, top, w, h, painter, *args, hit=None, focus=True, overlay=False):
        """``overlay``: above the card's text and controls (an open menu); otherwise beneath its text."""
        overlay = overlay or getattr(painter, "popup", False)
        self.customs.append((x, top, w, h, painter, args, hit, overlay))
        if hit is not None:
            (self.overlay_hits if overlay else self.hits).append((x, top, w, h) + tuple(hit))
            if focus:
                self.focus.append(tuple(hit))

    def popup(self, x, top, w, h, close):
        """An open menu: a click-away catcher over the card, then the menu's opaque panel, both above everything."""
        self.custom(0, 0, self.width, self.height(), _nothing, hit=(close, None), focus=False, overlay=True)
        self.custom(x, top, w, h, menu_panel, overlay=True)

    def place(self, x0, y_top, modal, region):
        plan = super().place(x0, y_top, modal, region)
        s, ui = self.s, self.ui
        under, over = [], []
        for x, top, w, h, painter, args, hit, overlay in self.customs:
            state = kit._state(ui, hit[0], hit[1], True) if hit else (False, False, False, True)
            (over if overlay else under).append(("kit", "firstlaunch", (x0 + x, y_top - top - h, w, h),
                                                 {"painter": painter, "state": state, "args": tuple(args)}, None, s))
        # Beneath the text, just above the card's own background (the last op before its content).
        base = next(index for index, op in enumerate(plan.ops) if op[0] == "shape" and op[1] == plan.card) + 1
        plan.ops[base:base] = under
        plan.ops.extend(over)
        for x, top, w, h, action, value in self.overlay_hits:   # Hit last, so an open menu wins its clicks.
            plan.hits.append(((x0 + x, y_top - top - h, w, h), action, value))
        return plan


def _nothing(*args):
    pass


def banner_ready():
    """The welcome art's pixels were read (``welcome_screen.prepare_banner``); a wash is drawn otherwise."""
    import sys
    module = sys.modules.get(__package__ + ".welcome_screen")
    return bool(module and module._banner["pixels"] is not None)


def _paint(view, rect, scale, ui, painter, state, args):
    painter(view, rect, state, scale, *args)


ui_kit.register_component("firstlaunch", _paint)


# -- painters ---------------------------------------------------------------------------------------------------
def _ring(view, rect, radius, s):
    x, y, w, h = rect
    d = 3 * s
    view.shape(x - d, y - d, w + 2 * d, h + 2 * d, kit.FOCUS, radius + d)
    view.shape(x - d + 2 * s, y - d + 2 * s, w + 2 * d - 4 * s, h + 2 * d - 4 * s, kit.CARD, radius + d - 2 * s)


def _label(view, text, x, rect, size, color):
    view.text(text, x, rect[1] + (rect[3] - size * 0.72) / 2, size, color)


def _chevron(view, cx, cy, s, color, down=True):
    d = 1 if down else -1
    view.stroke(cx - 4.5 * s, cy + 2.2 * s * d, cx, cy - 2.4 * s * d, color, 1.7 * s)
    view.stroke(cx, cy - 2.4 * s * d, cx + 4.5 * s, cy + 2.2 * s * d, color, 1.7 * s)


def banner(view, rect, state, s, ready):
    """The welcome art across the card's top; a teal-to-navy wash when the asset is missing."""
    import gpu
    from . import welcome_screen
    x, y, w, h = rect
    if ready:
        welcome_screen._draw_banner(gpu, rect)
        return
    top, bottom = BANNER_FALLBACK
    bands = 12
    for index in range(bands):
        t = index / (bands - 1)
        color = tuple(bottom[i] + (top[i] - bottom[i]) * t for i in range(3)) + (1,)
        view.shape(x, y + h * index / bands, w, h / bands + 1, color, 0)
    view.shape(x + w * 0.72, y + h * 0.18, h * 0.64, h * 0.64, (0.169, 0.769, 0.749, 0.10), h * 0.32)


def dropdown_field(view, rect, state, s, label, expanded, enabled=True):
    hover, pressed, focus, _ = state
    radius = tokens.RADIUS_CONTROL * s
    if focus:
        _ring(view, rect, radius, s)
    x, y, w, h = rect
    edge = kit.TEAL if expanded else kit.LINE_HOVER if hover else kit.LINE
    view.shape(x, y, w, h, edge, radius)
    view.shape(x + 1 * s, y + 1 * s, w - 2 * s, h - 2 * s, kit.PANEL_HOVER if hover or pressed else kit.PANEL, radius - 1 * s)
    _label(view, label, x + 14 * s, rect, tokens.TYPE_BODY * s, kit.TEXT)
    _chevron(view, x + w - 18 * s, y + h / 2, s, kit.TEXT if hover or expanded else kit.MUTED, down=not expanded)


def menu_panel(view, rect, state, s):
    x, y, w, h = rect
    for depth, alpha in ((10, 0.06), (5, 0.10), (2, 0.16)):
        d = depth * s
        view.shape(x - d, y - d - 2 * s, w + 2 * d, h + 2 * d, (0, 0, 0, alpha), tokens.RADIUS_PANEL * s + d)
    view.shape(x - 1, y - 1, w + 2, h + 2, kit.EDGE, tokens.RADIUS_PANEL * s + 1)
    view.shape(x, y, w, h, kit.PANEL, tokens.RADIUS_PANEL * s)


menu_panel.popup = True   # Builder.custom draws it above the card's controls, never beneath them.


def menu_item(view, rect, state, s, label, selected):
    hover, pressed, focus, _ = state
    x, y, w, h = rect
    if pressed or hover or focus:
        view.shape(x, y, w, h, kit.LINE_PRESSED if pressed else kit.LINE, tokens.RADIUS_CONTROL * s)
    if focus:
        view.shape(x, y, 2 * s, h, kit.FOCUS, 1 * s)
    color = kit.TEAL if selected else kit.TEXT if hover or focus else tokens.SUBTITLE
    _label(view, label, x + 12 * s, rect, 13 * s, color)
    if selected:
        cx, cy = x + w - 14 * s, y + h / 2
        view.stroke(cx - 5 * s, cy, cx - 1.5 * s, cy - 3.5 * s, kit.TEAL, 1.8 * s)
        view.stroke(cx - 1.5 * s, cy - 3.5 * s, cx + 5 * s, cy + 3.5 * s, kit.TEAL, 1.8 * s)


def row_panel(view, rect, state, s, active=False):
    hover, pressed, focus, enabled = state   # The switch inside carries the focus ring, as in the kit's toggle row.
    x, y, w, h = rect
    view.shape(x, y, w, h, kit.TEAL if active else kit.LINE, tokens.RADIUS_PANEL * s)
    fill = kit.PANEL_HOVER if (hover or pressed) and enabled else kit.PANEL
    view.shape(x + 1 * s, y + 1 * s, w - 2 * s, h - 2 * s, fill, tokens.RADIUS_PANEL * s - 1 * s)


def tile(view, rect, state, s, path, lines):
    """A recent scene: its saved thumbnail (or a drawn placeholder) over its name and folder."""
    import gpu
    hover, pressed, focus, _ = state
    x, y, w, h = rect
    radius = tokens.RADIUS_PANEL * s
    if focus:
        _ring(view, rect, radius, s)
    view.shape(x, y, w, h, kit.TEAL if hover or focus else kit.LINE, radius)
    view.shape(x + 1 * s, y + 1 * s, w - 2 * s, h - 2 * s, kit.PANEL_HOVER if hover or pressed else kit.PANEL, radius - 1 * s)
    pad = 6 * s
    image_h = (w - 2 * pad) * 0.6
    image = (x + pad, y + h - pad - image_h, w - 2 * pad, image_h)
    view.shape(*image, TRACK, tokens.RADIUS_CONTROL * s)
    thumb = _thumbs.get(path)
    if thumb and thumb.get("pixels") is not None:
        _image(gpu, thumb, _fit(image, thumb["size"]))
    else:
        cx, cy, r = image[0] + image[2] / 2, image[1] + image[3] / 2, image[3] * 0.18
        for ax, ay, bx, by in ((-1, .5, 0, 1), (0, 1, 1, .5), (1, .5, 0, 0), (0, 0, -1, .5), (-1, .5, -1, -.5),
                               (1, .5, 1, -.5), (0, 0, 0, -1), (-1, -.5, 0, -1), (1, -.5, 0, -1)):
            view.stroke(cx + ax * r, cy + ay * r, cx + bx * r, cy + by * r, kit.MUTED, 1.4 * s)
    ty = image[1] - 10 * s
    for index, (text, size, color) in enumerate(lines):
        ty -= size * s * 1.35
        view.text(text, x + 12 * s, ty + 2 * s, size * s, kit.TEXT if index == 0 and hover else color)


def _fit(rect, size):
    """``size`` scaled to fill ``rect``'s height or width, centred (letterboxed, never stretched)."""
    x, y, w, h = rect
    iw, ih = size
    scale = min(w / max(1, iw), h / max(1, ih))
    fw, fh = iw * scale, ih * scale
    return (x + (w - fw) / 2, y + (h - fh) / 2, fw, fh)


def _image(gpu, entry, rect):
    global _shader
    from gpu_extras.batch import batch_for_shader
    from . import canvas_rtl
    if entry.get("texture") is None:
        width, height = entry["size"]
        entry["texture"] = gpu.types.GPUTexture((width, height), format='RGBA16F',
                                                data=gpu.types.Buffer('FLOAT', [width * height * 4], entry["pixels"]))
    if _shader is None:
        _shader = gpu.shader.from_builtin('IMAGE')
    x, y, w, h = rect
    with canvas_rtl.upright(x, w) as x:   # A picture of the scene is never mirrored.
        batch = batch_for_shader(_shader, 'TRIS', {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                                                   "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]},
                                 indices=[(0, 1, 2), (0, 2, 3)])
        _shader.bind()
        _shader.uniform_sampler("image", entry["texture"])
        batch.draw(_shader)


# -- thumbnails ---------------------------------------------------------------------------------------------------
_previews = None


def thumbnail(path):
    """Read a saved scene's preview once, outside drawing. True when it has one."""
    global _previews
    entry = _thumbs.get(path)
    if entry is not None:
        return entry.get("pixels") is not None
    entry = _thumbs[path] = {"pixels": None, "size": None, "texture": None}
    try:
        import bpy.utils.previews
        if _previews is None:
            _previews = bpy.utils.previews.new()
        preview = _previews.get(path) or _previews.load(path, path, 'BLEND')
        width, height = preview.image_size
        if width and height:
            pixels = [0.0] * (width * height * 4)
            preview.image_pixels_float.foreach_get(pixels)
            entry.update(pixels=pixels, size=(width, height))
    except (RuntimeError, ValueError, KeyError, OSError, AttributeError, TypeError):
        pass   # No preview saved in the file: the tile draws a placeholder.
    return entry["pixels"] is not None


def release():
    global _previews
    _thumbs.clear()
    if _previews is not None:
        import bpy.utils.previews
        try:
            bpy.utils.previews.remove(_previews)
        except (RuntimeError, KeyError):
            pass
        _previews = None
