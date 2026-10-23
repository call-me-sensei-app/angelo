# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Asset Browser's empty and no-results states (1.0.5, maintainer: a real empty state, not an always-on pill).

The browser's counts come from Angelo's own RNA (``SpaceFileBrowser.angelo_listed_count``, ``angelo_total_count``,
``angelo_list_ready``: ``ED_fileselect_angelo_list_counts``), so a state shows only when it is true:

* **empty**: nothing is listed and no search is typed (an empty library or catalog): the Studio glyph, "Nothing here
  yet" and a teal **Generate in Media Studio**;
* **no results**: the library has assets but the search lists none: "No matches for "{query}"", a **Clear search**
  pill that clears only the search, and **Generate in Media Studio**.

Nothing shows while the list is still being read (its counts can still grow) or on a build without the counts. The
block is centred in the list, at most 360 px wide, and every line wraps, never cut. ``asset_browser_entry`` draws the
header pills (they stay in both states), runs the clicks and calls ``draw`` from its list-region callback; Media Studio
opens through the shared opener with the browser's kind (AUTO). What was drawn is recorded (``drawn``) for GUI suites.
"""

from . import card_kit as kit, ui_tokens as tk
from .ui_text import iface_, n_

EMPTY = n_("Nothing here yet")
# Angelo Media Studio's own library, empty: what will appear there (part D's words for its empty shelf).
STUDIO_EMPTY = n_("Your generated images and models appear here")
NO_MATCHES = n_("No matches for “{query}”")
GENERATE = n_("Generate in Media Studio")
CLEAR = n_("Clear search")
MAX_WIDTH, SIDE, GLYPH, GAP, PAD = 360, 16, 32, 12, 16
drawn = {}        # area pointer -> {"state", "lines", "buttons": [label, ...]} as last drawn.
pointer = {}      # area pointer -> (x, y) in its list region, from the input handler (hover).
_glyph = []       # Media Studio's spark-frame glyph as a GPU texture, built once at its design size.


def state(space):
    """("empty" | "no_results", query) for an Asset Browser whose list is final and shows nothing, else (None, "")."""
    if getattr(space, "browse_mode", "") != 'ASSETS' or not getattr(space, "angelo_list_ready", False):
        return None, ""   # Still reading, not an Asset Browser, or a build without the counts.
    if space.angelo_listed_count > 0:
        return None, ""
    params = space.params
    query = (getattr(params, "filter_search", "") or "").strip("* ").strip() if params else ""
    return ("no_results" if query and space.angelo_total_count > 0 else "empty"), query


def empty_title(space, studio_library):
    """The empty state's title: Media Studio's own library (``studio_library``, its name in the Preferences, which
    the person may have changed; None when it isn't registered) says what appears there, any other says it is empty."""
    params = getattr(space, "params", None)
    shown = getattr(params, "asset_library_reference", None) if params is not None else None
    return STUDIO_EMPTY if studio_library and shown == studio_library else EMPTY


def _studio_library():
    try:
        from .media_shelf import library_name
    except ImportError:
        return None
    return library_name()


def _rows(text, width, size, font):
    from . import canvas_wrap
    return [row.strip() for row, _ in canvas_wrap.iter_wrap(text, width, size, font, True, 1.0)] or [""]


def _glyph_texture():
    """The glyph at studio_toggle's 32 px design size (its geometry is drawn for it); larger scales stretch it."""
    import gpu
    if not _glyph:
        from .studio_toggle import ICON_SIZE, frame_icon
        _glyph.append(gpu.types.GPUTexture((ICON_SIZE, ICON_SIZE), format='RGBA16F', data=gpu.types.Buffer(
            'FLOAT', [ICON_SIZE * ICON_SIZE * 4], frame_icon(False))))
    return _glyph[0]


def _draw_glyph(x, y, size):
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('IMAGE_COLOR')
    quad = {"pos": [(x, y), (x + size, y), (x + size, y + size), (x, y + size)],
            "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]}
    batch = batch_for_shader(shader, 'TRIS', quad, indices=[(0, 1, 2), (0, 2, 3)])
    gpu.state.blend_set('ALPHA_PREMULT')   # frame_icon is premultiplied.
    shader.bind()
    shader.uniform_sampler("image", _glyph_texture())
    shader.uniform_float("color", kit.TEXT)
    batch.draw(shader)
    gpu.state.blend_set('ALPHA')


def _button(view, rect, rows, size, style, hover, base):
    """A pill (secondary: PANEL fill with a 1 px LINE border) or a teal button; its label may take several rows, and
    then it stays a rounded rectangle of its one-row radius (``base``: its one-row height)."""
    x, y, w, h = rect
    radius = min(h, base) / 2
    if style == "primary":
        view.shape(x, y, w, h, kit.TEAL_HOVER if hover else kit.TEAL, radius)
        color = kit.INK
    else:
        view.shape(x, y, w, h, kit.LINE, radius)
        view.shape(x + 1, y + 1, w - 2, h - 2, kit.PANEL_HOVER if hover else kit.PANEL, max(0, radius - 1))
        color = kit.TEXT
    line = size * 1.35
    top = y + h / 2 + line * len(rows) / 2
    for index, row in enumerate(rows):
        tw = view.measure(row, size, view._font)
        view.text(row, x + (w - tw) / 2, top - line * (index + 1) + (line - size * 0.72) / 2, size, color)


def draw(area, region, space, scale):
    """Draw the state over the list region (region pixels), and return its clickable rects [(rect, action)]."""
    which, query = state(space)
    if which is None:
        drawn.pop(area.as_pointer(), None)
        return []
    from .flow_window import chat_view_view
    view = chat_view_view()
    font = view._font
    width = min(MAX_WIDTH * scale, region.width - 2 * SIDE * scale)
    if width < 80 * scale:
        return []
    heading, body = tk.TYPE_HEADING * scale, tk.TYPE_SMALL * scale
    title = iface_(empty_title(space, _studio_library())) if which == "empty" else iface_(NO_MATCHES, query=query)
    title_rows = _rows(title, width, heading, font)
    buttons = ([("clear_search", iface_(CLEAR), "secondary", tk.BUTTON_HEIGHT_SMALL)] if which == "no_results" else [])
    buttons.append(("studio", iface_(GENERATE), "primary", tk.BUTTON_HEIGHT))
    laid = []
    for action, label, style, height in buttons:
        rows = _rows(label, width - 2 * PAD * scale, body, font)
        text_w = max(view.measure(row, body, font) for row in rows)
        h = max(height * scale, body * 1.35 * len(rows) + 2 * 8 * scale)
        laid.append((action, rows, style, min(width, text_w + 2 * PAD * scale), h, height * scale))
    glyph = GLYPH * scale if which == "empty" else 0
    total = (glyph + GAP * scale if glyph else 0) + heading * 1.3 * len(title_rows) \
        + sum(GAP * scale + h for *_, h, _ in laid)
    y = (region.height + total) / 2
    cx = region.width / 2
    if glyph:
        size = int(round(glyph))
        _draw_glyph(cx - size / 2, y - size, size)
        y -= glyph + GAP * scale
    for row in title_rows:
        y -= heading * 1.3
        view.text(row, cx - view.measure(row, heading, font) / 2, y + heading * 0.2, heading, kit.TEXT)
    hits = []
    hover_at = pointer.get(area.as_pointer())
    for action, rows, style, w, h, base in laid:
        y -= GAP * scale + h
        rect = (cx - w / 2, y, w, h)
        hover = bool(hover_at) and rect[0] <= hover_at[0] < rect[0] + w and y <= hover_at[1] < y + h
        _button(view, rect, rows, body, style, hover, base)
        hits.append((rect, action))
    drawn[area.as_pointer()] = {"state": which, "lines": title_rows, "buttons": [rows for _, rows, *_ in laid],
                                "rects": [rect for rect, _ in hits]}
    return hits


def clear_search(space):
    """Clear only the search text (catalog, type and library stay as they are)."""
    if space.params is not None:
        space.params.filter_search = ""
