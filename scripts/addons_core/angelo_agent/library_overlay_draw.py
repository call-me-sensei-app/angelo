# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library overlay's layout and drawing in a 3D view (``library_overlay`` owns state and input).

Three shapes of one surface, drawn over the view's main region in card-kit colours:

* **Pill** (closed): a search pill at the top centre of the view, "Search the library", below any header drawn over
  the view.
* **Search** (open): the pill widens into a field and a results panel opens under it: kind chips, a thumbnail grid,
  and a footer with the selected item's source, licence and size (and its resolutions for texture sets and HDRIs).
* **Browse**: the same panel docked along the view's left edge at full height, with more columns.

Layout is pure (``layout``) so it is tested without Blender; every hit rectangle is in region pixels and recorded in
``state["hits"]`` as ``(rect, action, value)`` for the input handler.
"""

import math

from . import card_kit as kit
from .ui_text import iface_, n_

PILL_W, PILL_H, MARGIN = 236.0, 34.0, 14.0
SEARCH_W, PANEL_MAX_H = 640.0, 500.0
BROWSE_MIN, BROWSE_MAX = 380.0, 680.0
TILE, TITLE_H, GAP, PAD = 112.0, 30.0, 10.0, 12.0
CHIPS_H, FOOTER_H = 28.0, 52.0
KIND_CHIPS = ((None, n_("All")), ("model", n_("Models")), ("texture", n_("Materials")), ("hdri", n_("HDRIs")),
              ("vfx", n_("VFX")))
KIND_NAMES = {"model": n_("Model"), "texture": n_("Material"), "hdri": n_("HDRI"), "vfx": n_("VFX")}
# Where items are used (library_catalog.CATEGORIES), as the second row of chips once a kind is chosen.
CATEGORY_LABELS = {
    "model": {"science": n_("Science & nature studies"), "vehicles": n_("Vehicles"), "characters": n_("Characters"),
              "furniture": n_("Furniture"), "food": n_("Food & tableware"), "tools": n_("Tools & weapons"),
              "architecture": n_("Architecture"), "nature": n_("Nature & rocks"), "history": n_("History & art"),
              "props": n_("Props")},
    "texture": {"fabric": n_("Fabric"), "metal": n_("Metal"), "wood": n_("Wood"), "brick": n_("Brick"),
                "tiles": n_("Tiles"), "paving": n_("Roads & paving"), "concrete": n_("Concrete & plaster"),
                "ground": n_("Ground"), "stone": n_("Stone"), "other": n_("Other")},
    "hdri": {"studio": n_("Studio"), "night": n_("Night"), "sunset": n_("Sunrise & sunset"), "indoor": n_("Indoor"),
             "skies": n_("Skies"), "urban": n_("City"), "nature": n_("Nature")},
    "vfx": {"fire": n_("Fire & explosions"), "smoke": n_("Smoke & dust"), "magic": n_("Magic & energy"),
            "impacts": n_("Impacts & shots"), "other": n_("Other")}}
ALL_OF = {"model": n_("All models"), "texture": n_("All materials"), "hdri": n_("All HDRIs"), "vfx": n_("All VFX")}


def kind_chips(lock):
    """The kind chips a surface shows: every kind in the 3D view, only its own where a surface locks one (the
    Material properties show materials, the World properties HDRIs)."""
    return [(kind, label) for kind, label in KIND_CHIPS if lock is None or kind == lock]


def active_kind(state):
    """The kind the results are of: a surface's lock wins over the chips."""
    return state.get("lock") or state.get("kind")
SHADOW = (0.0, 0.0, 0.0, 0.32)


def layout(region_w, region_h, top, left, right, mode, scale):
    """``{"pill", "panel", "grid", "chips", "footer"}`` rectangles (x, y, w, h) in region pixels, or None where the
    view is too small. ``top``: the highest usable y (below headers drawn over the view); ``left`` and ``right``: the
    free span between a toolbar and a sidebar drawn over it. Pure."""
    s = scale
    span = right - left
    if span < 420 * s or top < 260 * s:
        return None
    if mode == "browse":
        width = max(BROWSE_MIN * s, min(BROWSE_MAX * s, span * 0.45, span - 2 * MARGIN * s))
        panel = (left + MARGIN * s, MARGIN * s, width, top - 2 * MARGIN * s)
        pill = (panel[0] + PAD * s, panel[1] + panel[3] - PAD * s - PILL_H * s, width - 2 * PAD * s, PILL_H * s)
        chips = (pill[0], pill[1] - 8 * s - CHIPS_H * s, pill[2], CHIPS_H * s)
    else:
        width = PILL_W * s if mode is None else min(SEARCH_W * s, span - 2 * MARGIN * s)
        pill = (left + (span - width) / 2, top - MARGIN * s - PILL_H * s, width, PILL_H * s)
        if mode is None:
            return {"pill": pill}
        height = min(PANEL_MAX_H * s, pill[1] - 2 * MARGIN * s)
        panel = (pill[0], pill[1] - 6 * s - height, width, height)
        chips = (panel[0] + PAD * s, panel[1] + panel[3] - PAD * s - CHIPS_H * s, width - 2 * PAD * s, CHIPS_H * s)
    footer = (panel[0], panel[1], panel[2], FOOTER_H * s)
    grid_top = chips[1] - 8 * s
    grid = (panel[0] + PAD * s, footer[1] + footer[3], panel[2] - 2 * PAD * s, grid_top - footer[1] - footer[3])
    return {"pill": pill, "panel": panel, "chips": chips, "grid": grid, "footer": footer}


def columns(grid_w, scale):
    return max(1, int((grid_w + GAP * scale) // ((TILE + GAP) * scale)))


def tile_rects(grid, count, scroll, scale):
    """``[(index, rect of the thumbnail and title)]`` of the tiles visible in ``grid`` scrolled by ``scroll``."""
    s = scale
    cols = columns(grid[2], s)
    tile_w = (grid[2] - (cols - 1) * GAP * s) / cols
    tile_h = tile_w + TITLE_H * s
    first = max(0, int(scroll // (tile_h + GAP * s)) * cols)
    found = []
    for index in range(first, count):
        row, col = divmod(index, cols)
        y = grid[1] + grid[3] - (row + 1) * tile_h - row * GAP * s + scroll
        if y + tile_h < grid[1]:
            break
        if y > grid[1] + grid[3]:
            continue
        found.append((index, (grid[0] + col * (tile_w + GAP * s), y, tile_w, tile_h)))
    return found


def content_height(grid_w, count, scale):
    s = scale
    cols = columns(grid_w, s)
    tile_w = (grid_w - (cols - 1) * GAP * s) / cols
    rows = math.ceil(count / cols)
    return rows * (tile_w + TITLE_H * s) + max(0, rows - 1) * GAP * s


def human_bytes(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (size, unit)
        size /= 1024.0


# -- drawing --------------------------------------------------------------------------------------------------

class Painter:
    """Text and shapes in region pixels at one interface scale."""

    def __init__(self, scale):
        from . import chat_view
        self.s, self.canvas, self.font = scale, chat_view, chat_view._font

    def shape(self, rect, color, radius=0.0):
        self.canvas.shape(rect[0], rect[1], rect[2], rect[3], color, radius)

    def measure(self, value, size):
        return self.canvas.measure(value, size, scale=self.s) * self.s

    def text(self, value, x, y, size, color=kit.TEXT, width=None):
        import blf
        from . import canvas_rtl
        if width is not None:
            value = canvas_rtl.elide(value, lambda part: self.measure(part, size), width)
        blf.size(self.font, size * self.s)
        blf.position(self.font, x, y, 0)
        blf.color(self.font, *color)
        blf.draw(self.font, canvas_rtl.visual(value))

    def magnifier(self, x, y, color):
        s = self.s
        for i in range(20):
            a, b = math.radians(i * 18), math.radians((i + 1) * 18)
            self.canvas.stroke(x + 5 * s * math.cos(a), y + 5 * s * math.sin(a), x + 5 * s * math.cos(b),
                               y + 5 * s * math.sin(b), color, 1.5 * s)
        self.canvas.stroke(x + 3.6 * s, y - 3.6 * s, x + 8 * s, y - 8 * s, color, 1.7 * s)


def hit(state, rect, action, value=None):
    state["hits"].append((rect, action, value))


def draw_pill(paint, state, rect, focused, count):
    s = paint.s
    hovered = state.get("hover") == ("pill", None)
    paint.shape((rect[0], rect[1] - 2 * s, rect[2], rect[3]), SHADOW, rect[3] / 2)
    paint.shape(rect, kit.FOCUS if focused else kit.LINE, rect[3] / 2)
    paint.shape((rect[0] + s, rect[1] + s, rect[2] - 2 * s, rect[3] - 2 * s), kit.PANEL_HOVER if hovered else kit.PANEL,
                rect[3] / 2 - s)
    paint.magnifier(rect[0] + 20 * s, rect[1] + rect[3] / 2 + 1 * s, kit.TEXT if focused else kit.MUTED)
    x, baseline = rect[0] + 36 * s, rect[1] + rect[3] / 2 - 4.5 * s
    query = state.get("query", "")
    room = rect[2] - 50 * s
    if query:
        paint.text(query, x, baseline, 13, kit.TEXT, room)
    else:
        hint = iface_("Search the library") if not focused else iface_("Try “studio HDRI”, “oak chair”, “brick”")
        paint.text(hint, x, baseline, 13, kit.MUTED, room)
    if focused:
        caret_x = x + min(room, paint.measure(query[:state.get("caret", len(query))], 13))
        paint.shape((caret_x + 1 * s, rect[1] + 9 * s, 1.5 * s, rect[3] - 18 * s), kit.TEAL)
        if count is not None and query:
            label = iface_("{count} found", count="{:,}".format(count))
            paint.text(label, rect[0] + rect[2] - 16 * s - paint.measure(label, 11), baseline + 1 * s, 11, kit.MUTED)
    hit(state, rect, "pill")


def draw_panel(paint, state, rects, library, results, total, status=None):
    from gpu_extras.presets import draw_texture_2d
    import gpu
    s = paint.s
    panel = rects["panel"]
    paint.shape((panel[0], panel[1] - 3 * s, panel[2], panel[3]), SHADOW, 14 * s)
    paint.shape(panel, kit.LINE, 14 * s)
    paint.shape((panel[0] + s, panel[1] + s, panel[2] - 2 * s, panel[3] - 2 * s), kit.CARD, 13 * s)
    _chips(paint, state, rects["chips"])
    grid = rects["grid"]
    if active_kind(state) and library.catalog is not None:
        used = _categories(paint, state, rects["chips"], library.catalog.categories(active_kind(state)))
        grid = (grid[0], grid[1], grid[2], max(0.0, grid[3] - used))
        state["rects"]["grid"] = grid
    loading = library.status == "loading" or (library.catalog is None and library.status == "idle")
    if library.catalog is None or not results:
        if loading:
            message = iface_("Loading the library…")
        elif library.catalog is None:
            message = iface_("The library is offline. Check your network and try again.")
            retry = (grid[0] + grid[2] / 2 - 50 * s, grid[1] + grid[3] / 2 - 46 * s, 100 * s, 30 * s)
            _button(paint, state, retry, iface_("Try again"), "retry")
        else:
            message = iface_("No matches. Try fewer or different words.")
        width = paint.measure(message, 13)
        paint.text(message, grid[0] + max(0, (grid[2] - width) / 2), grid[1] + grid[3] / 2, 13, kit.MUTED, grid[2])
    else:
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(int(state["region_x"] + grid[0]), int(state["region_y"] + grid[1]),
                              int(grid[2]), int(grid[3]))
        selected = state.get("selected")
        for index, rect in tile_rects(grid, len(results), state.get("scroll", 0.0), s):
            item = results[index]
            hovered = state.get("hover") == ("tile", item["id"])
            chosen = selected == item["id"]
            image = (rect[0], rect[1] + TITLE_H * s, rect[2], rect[3] - TITLE_H * s)
            paint.shape(image, kit.FOCUS if chosen else kit.LINE_HOVER if hovered else kit.LINE, 9 * s)
            inner = (image[0] + 2 * s, image[1] + 2 * s, image[2] - 4 * s, image[3] - 4 * s) if chosen else \
                (image[0] + s, image[1] + s, image[2] - 2 * s, image[3] - 2 * s)
            paint.shape(inner, kit.PANEL_HOVER if hovered else kit.PANEL, 8 * s)
            texture = library.texture(item)
            if texture is not None:
                tw, th = texture.width, texture.height
                fit = min(inner[2] / tw, inner[3] / th)
                w, h = tw * fit, th * fit
                gpu.state.blend_set('ALPHA')
                draw_texture_2d(texture, (inner[0] + (inner[2] - w) / 2, inner[1] + (inner[3] - h) / 2), w, h)
            badge = iface_(KIND_NAMES[item["kind"]])
            bw = paint.measure(badge, 9) + 10 * s
            paint.shape((inner[0] + 5 * s, inner[1] + inner[3] - 21 * s, bw, 16 * s), kit.SCRIM, 5 * s)
            paint.text(badge, inner[0] + 10 * s, inner[1] + inner[3] - 17 * s, 9, kit.TEXT)
            paint.text(item["title"], rect[0] + 2 * s, rect[1] + 10 * s, 11, kit.TEXT if chosen else kit.MUTED,
                       rect[2] - 4 * s)
            hit(state, (rect[0], max(rect[1], grid[1]), rect[2], min(rect[1] + rect[3], grid[1] + grid[3]) - max(rect[1], grid[1])),
                "tile", item["id"])
        gpu.state.scissor_test_set(False)
    _footer(paint, state, rects["footer"], library, total, status)


def _chips(paint, state, rect):
    s = paint.s
    x = rect[0]
    for kind, label in kind_chips(state.get("lock")):
        text = iface_(label)
        width = paint.measure(text, 11) + 22 * s
        active = active_kind(state) == kind
        chip = (x, rect[1], width, rect[3])
        hovered = state.get("hover") == ("kind", kind)
        paint.shape(chip, kit.TEAL_SOFT if active else kit.PANEL_HOVER if hovered else kit.PANEL, rect[3] / 2)
        paint.text(text, x + 11 * s, rect[1] + rect[3] / 2 - 4 * s, 11, kit.TEXT if active else kit.MUTED)
        hit(state, chip, "kind", kind)
        x += width + 6 * s
    label = iface_("Close") if state.get("mode") == "browse" else iface_("Browse")
    width = paint.measure(label, 11) + 22 * s
    if x + width <= rect[0] + rect[2]:
        _button(paint, state, (rect[0] + rect[2] - width, rect[1], width, rect[3]), label, "browse")


def _categories(paint, state, chips, counts):
    """The kind's categories as chips under the kind chips, wrapped onto rows; returns the height they take."""
    s = paint.s
    kind = active_kind(state)
    entries = [(None, iface_(ALL_OF[kind]))] + [(c, iface_(CATEGORY_LABELS[kind][c])) for c, _n in counts]
    height, gap = 24 * s, 6 * s
    x, y, rows = chips[0], chips[1] - gap - height, 1
    for category, text in entries:
        width = paint.measure(text, 10) + 18 * s
        if x + width > chips[0] + chips[2] and x > chips[0]:
            x, y, rows = chips[0], y - gap - height, rows + 1
        chip = (x, y, width, height)
        active = state.get("category") == category
        hovered = state.get("hover") == ("category", category)
        paint.shape(chip, kit.TEAL_SOFT if active else kit.PANEL_HOVER if hovered else kit.CARD, height / 2)
        paint.shape((chip[0] + s, chip[1] + s, chip[2] - 2 * s, chip[3] - 2 * s),
                    kit.TEAL_SOFT if active else kit.PANEL_HOVER if hovered else kit.PANEL, height / 2 - s)
        paint.text(text, x + 9 * s, y + height / 2 - 3.5 * s, 10, kit.TEXT if active else kit.MUTED)
        hit(state, chip, "category", category)
        x += width + gap
    return rows * (height + gap)


def _button(paint, state, rect, label, action, value=None):
    s = paint.s
    hovered = state.get("hover") == (action, value)
    paint.shape(rect, kit.LINE_HOVER if hovered else kit.LINE, rect[3] / 2)
    paint.shape((rect[0] + s, rect[1] + s, rect[2] - 2 * s, rect[3] - 2 * s), kit.PANEL_HOVER if hovered else kit.PANEL,
                rect[3] / 2 - s)
    paint.text(label, rect[0] + (rect[2] - paint.measure(label, 11)) / 2, rect[1] + rect[3] / 2 - 4 * s, 11, kit.TEXT)
    hit(state, rect, action, value)


def _footer(paint, state, rect, library, total, status=None):
    """``status``: ``(fraction, label)`` of a download or a notice, shown in place of the footer's text (beside
    Credits) so it never covers the chips or the grid."""
    s = paint.s
    paint.shape((rect[0] + PAD * s, rect[1] + rect[3] - s, rect[2] - 2 * PAD * s, s), kit.LINE)
    item = library.get(state.get("selected")) if state.get("selected") else None
    x, top = rect[0] + PAD * s, rect[1] + rect[3] - 20 * s
    right = rect[0] + rect[2] - PAD * s
    credits = iface_("Credits")
    cw = paint.measure(credits, 11) + 22 * s
    _button(paint, state, (right - cw, rect[1] + (rect[3] - 26 * s) / 2, cw, 26 * s), credits, "credits")
    right -= cw + 8 * s
    if status:
        draw_progress(paint, (x, rect[1] + (rect[3] - 24 * s) / 2, right - x, 24 * s), *status)
        return
    if item is None:
        paint.text(iface_("Angelo Library, powered by ToonLab"), x, top, 12, kit.TEXT, right - x)
        count = library.catalog and len(library.catalog.items)
        line = iface_("{count} free assets. Drag one into the scene, or double-click to add it.",
                      count="{:,}".format(count)) if count else iface_("Free models, materials and HDRIs")
        paint.text(line, x, top - 18 * s, 11, kit.MUTED, right - x)
        return
    from . import library_catalog
    resolution = state.get("resolution", {}).get(item["id"])
    size = human_bytes(library_catalog.item_bytes(item, resolution))
    chips = [item.get("resolution")] + list(item.get("resolutions") or {}) if item.get("resolutions") else []
    chip_w = 34 * s
    for label in reversed(chips):
        chip = (right - chip_w, rect[1] + (rect[3] - 24 * s) / 2, chip_w, 24 * s)
        active = (resolution or item.get("resolution")) == label
        paint.shape(chip, kit.TEAL_SOFT if active else kit.PANEL, 7 * s)
        paint.text(label.upper(), chip[0] + 7 * s, chip[1] + 8 * s, 10, kit.TEXT if active else kit.MUTED)
        hit(state, chip, "resolution", label)
        right -= chip_w + 4 * s
    paint.text(item["title"], x, top, 12, kit.TEXT, right - x - 8 * s)
    cached = iface_("on this computer") if library.cached(item, resolution) else size
    detail = "%s · %s · %s" % (item["source"], item["license"], cached)
    paint.text(detail, x, top - 18 * s, 11, kit.MUTED, right - x - 8 * s)


def progress_rect(pill, scale):
    """Where a download or notice shows when only the pill is open: just under it."""
    return (pill[0], pill[1] - 30 * scale, pill[2], 24 * scale)


def draw_progress(paint, rect, fraction, label):
    """A labelled progress bar filling ``rect`` (24 px high)."""
    s = paint.s
    paint.shape(rect, kit.SCRIM, 8 * s)
    paint.shape((rect[0] + 8 * s, rect[1] + 3 * s, (rect[2] - 16 * s) * max(0.02, min(1.0, fraction)), 3 * s), kit.TEAL, 1.5 * s)
    paint.text(label, rect[0] + 8 * s, rect[1] + 9 * s, 10, kit.TEXT, rect[2] - 16 * s)


def draw_ghost(paint, x, y, texture, title):
    """What follows the pointer during a drag: the thumbnail and the title."""
    from gpu_extras.presets import draw_texture_2d
    import gpu
    s = paint.s
    size = 72 * s
    box = (x + 12 * s, y - size - 12 * s, size, size)
    paint.shape((box[0], box[1] - 2 * s, box[2], box[3]), SHADOW, 9 * s)
    paint.shape(box, kit.TEAL, 9 * s)
    paint.shape((box[0] + 2 * s, box[1] + 2 * s, box[2] - 4 * s, box[3] - 4 * s), kit.PANEL, 8 * s)
    if texture is not None:
        fit = min((size - 6 * s) / texture.width, (size - 6 * s) / texture.height)
        w, h = texture.width * fit, texture.height * fit
        gpu.state.blend_set('ALPHA')
        draw_texture_2d(texture, (box[0] + (size - w) / 2, box[1] + (size - h) / 2), w, h)
    paint.text(title, box[0], box[1] - 16 * s, 11, kit.TEXT, 220 * s)
