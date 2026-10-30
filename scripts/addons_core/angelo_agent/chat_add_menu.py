# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu: Angelo's main navigation (attach a file, open Media Studio or a Generation Flow, choose a
tool Angelo performs before the agent starts, and whatever else registers in ``chat_destinations``).

Drawn on the chat canvas as the chat's popover kind ``"add"`` (``chat_view``
owns opening, closing, Esc and outside clicks): sections, rows with a tile, a
title and a why line, group rows that open and close (closed until the person
opens one, remembered in ``chat-add-menu.json`` beside the chat's state), an
opt-in fit toggle, a scrolling list and a footer that says what happens. The
rows come from ``chosen_tools.menu_rows``; ``layout`` is pure and host-tested.
Strings are translated here, at display time.
"""

from . import chosen_tools, ui_tokens
from .ui_text import iface_

# The menu grows with its chat: as wide as the column allows up to MAX_WIDTH, and as tall as COVER of the chat (never
# under MIN_HEIGHT) before its list scrolls, so it never covers the whole transcript. 1.0.5 added Attach, Media Studio
# and Flows rows in 1.0.4's fixed 352 x 520, which left 3.5 base meshes in view (6.5 before) and ellipsized why lines.
MAX_WIDTH = 480
MIN_HEIGHT, COVER = 520, 0.7
HEIGHTS = {"section": 28, "note": 22, "toggle": 36, "row": 46, "group": 46}
LINE = 14   # A why line too long for one line wraps to a second, and its row grows by this much.
PAD, FOOTER = 8, 46
INDENT = 14   # A group's rows, under its header.
TEXT_X = 50   # A row's title column, beside its tile; a row with no glyph (a template, a flow) keeps the column, no tile.
ACTIONABLE = ("row", "toggle", "group")
MEMORY_FILE = "chat-add-menu.json"
_memory = None
TILE_FILL = (0.255, 0.255, 0.255, 1)


def _label(value):
    if not value:
        return ""
    return iface_(value[0], **value[1]) if isinstance(value, tuple) else iface_(value)


def _shown(row, key):
    """A row's label or subtitle as drawn: a label that is a name (an asset's, a flow's: ``translate`` False) as it
    is; everything else translated."""
    value = row.get(key, "")
    return value if key == "label" and row.get("translate") is False else _label(value)


def memory():
    """Which groups the person opened (``chat_destinations.GroupMemory``), read once per session."""
    global _memory
    if _memory is None:
        from . import chat_destinations
        try:
            from .ui import state_directory
            import os
            path = os.path.join(state_directory(), MEMORY_FILE)
        except Exception:   # No state folder (a test host): remembered for this session only.
            path = None
        _memory = chat_destinations.GroupMemory(path)
    return _memory


def draw_glyph(view, glyph, tx, ty):
    """A row's 32 px tile: attach (+), base_mesh (a head on shoulders), studio (a framed spark), flow (three linked
    nodes), new (+ in teal), web (a globe), link (two chain links), anything else a dot. Shapes and strokes only: no
    icon font, nothing mirrored."""
    if glyph == "add":   # New Flow: a 16 px ADD glyph, no tile (studio_entry_points.rst, section 2).
        view.stroke(tx + 9, ty + 16, tx + 23, ty + 16, ui_tokens.TEAL, 1.8)
        view.stroke(tx + 16, ty + 9, tx + 16, ty + 23, ui_tokens.TEAL, 1.8)
        return
    if glyph == "attach":
        view.shape(tx, ty, 32, 32, TILE_FILL, 8)
        view.icon("plus", tx + 16, ty + 16, view.TEXT)
        return
    view.shape(tx, ty, 32, 32, ui_tokens.TEAL_SOFT, 8)
    teal = ui_tokens.TEAL
    if glyph == "base_mesh":
        view.shape(tx + 11, ty + 13, 10, 12, teal, 5)
        view.shape(tx + 7, ty + 5, 18, 7, teal, 3)
    elif glyph == "studio":
        for a, b in (((8, 8), (24, 8)), ((24, 8), (24, 21)), ((24, 21), (8, 21)), ((8, 21), (8, 8))):
            view.stroke(tx + a[0], ty + a[1], tx + b[0], ty + b[1], teal, 1.6)
        for dx, dy in ((0, 5), (5, 0), (0, -5), (-5, 0)):
            view.stroke(tx + 22, ty + 22, tx + 22 + dx, ty + 22 + dy, teal, 1.8)
    elif glyph in {"flow", "new"}:
        for (ax, ay), (bx, by) in (((10, 16), (22, 22)), ((10, 16), (22, 10))):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.4)
        for cx, cy in ((10, 16), (22, 22), (22, 10)):
            view.shape(tx + cx - 3.5, ty + cy - 3.5, 7, 7, teal, 2)
        if glyph == "new":
            view.shape(tx + 20, ty + 1, 11, 11, ui_tokens.INK, 5.5)
            view.icon("plus", tx + 25.5, ty + 6.5, teal)
    elif glyph == "web":
        view.shape(tx + 7, ty + 7, 18, 18, teal, 9)
        view.shape(tx + 8.6, ty + 8.6, 14.8, 14.8, ui_tokens.TEAL_SOFT, 7.4)
        view.stroke(tx + 8, ty + 16, tx + 24, ty + 16, teal, 1.4)
        for (ax, ay), (bx, by) in (((16, 8), (12.5, 16)), ((12.5, 16), (16, 24)), ((16, 8), (19.5, 16)),
                                   ((19.5, 16), (16, 24))):
            view.stroke(tx + ax, ty + ay, tx + bx, ty + by, teal, 1.3)
    elif glyph == "link":   # Two chain links: the Godot live link (rims first, then both openings).
        for dx, dy in ((-4, 2.5), (4, -2.5)):
            view.shape(tx + 16 + dx - 6.5, ty + 16 + dy - 4, 13, 8, teal, 4)
        for dx, dy in ((-4, 2.5), (4, -2.5)):
            view.shape(tx + 16 + dx - 4, ty + 16 + dy - 1.6, 8, 3.2, ui_tokens.TEAL_SOFT, 1.6)
    else:
        view.shape(tx + 12, ty + 12, 8, 8, teal, 4)


def wrap_subtitles(view, rows, list_width):
    """Each row's why line as drawn (``subtitle_lines``) and the row's height to hold it: a why line wraps to a second
    line before it is elided."""
    for row in rows:
        if row["kind"] in {"row", "group"}:
            avail = list_width - (INDENT if row.get("depth") else 0) - 74
            row["subtitle_lines"] = subtitle_lines(view, _shown(row, "subtitle"), avail)
            row["height"] = HEIGHTS[row["kind"]] + LINE * max(0, len(row["subtitle_lines"]) - 1)


def draw_row(view, row, rect, glyphs=None):
    """A row's tile (``glyphs``, default ``draw_glyph``; none for a row without a glyph), title and why lines, and its
    chevron (a group) or check (a chosen option), inside ``rect``. Angelo's Flows popup (``flow_menu``) draws its rows
    with it too, so the two menus' rows never drift apart."""
    rx, ry, rw, rh = rect
    glyph, thumbnail = row.get("glyph", "dot"), row.get("thumbnail")
    tx = rx + TEXT_X
    if glyph or thumbnail:   # A tile is a picture: never mirrored in a right-to-left language, only placed (P7).
        from . import base_mesh_thumbs, canvas_rtl
        with canvas_rtl.upright(rx + 8, 32) as gx:
            # A row with its own picture (a base mesh's preview, SD3) shows it; else its drawn glyph.
            if not (thumbnail and base_mesh_thumbs.draw(view, thumbnail, gx, ry + (rh - 32) / 2)) and glyph:
                (glyphs or draw_glyph)(view, glyph, gx, ry + (rh - 32) / 2)
    lines = row.get("subtitle_lines", [])
    view.text(view.elide(_shown(row, "label"), rx + rw - 24 - tx, 13), tx, ry + (rh - 21 if lines else 17), 13)
    for n, line in enumerate(lines):
        view.text(line, tx, ry + rh - 37 - LINE * n, 11.5, view.MUTED)
    if row["kind"] == "group":   # Open: the chevron points down at its rows; closed: it points on.
        view.icon("chevron" if row.get("open") else "next", rx + rw - 16, ry + rh / 2, view.MUTED)
    elif row.get("selected"):
        view.icon("check", rx + rw - 16, ry + rh / 2, ui_tokens.TEAL)


def popup_width(width):
    return min(MAX_WIDTH, max(200, width - 24))


def row_height(row):
    return row.get("height") or HEIGHTS[row["kind"]]


def subtitle_lines(view, text, width):
    """A why line as drawn: one line, or two when it is longer (the second elided if it is longer still)."""
    if not text:
        return []
    lines = [(line[0] if isinstance(line, tuple) else line).rstrip() for line in view._flow_wrap(text, width, 11.5)]
    if len(lines) <= 2:
        return lines or [text]
    first = lines[0]
    rest = text[len(first):].lstrip() if text.startswith(first) else " ".join(lines[1:])
    return [first, view.elide(rest, width, 11.5)]


def layout(rows, width, height, bottom, scroll=0):
    """Popup rect, list rect, each row's rect (region px, y up) and the clamped scroll, for the menu above ``bottom``."""
    w = popup_width(width)
    content = sum(row_height(row) for row in rows)
    available = max(120, min(max(MIN_HEIGHT, COVER * height), height - bottom - 12))
    h = min(available, content + 2 * PAD + FOOTER)
    x, y = 12, bottom
    list_rect = (x + PAD, y + FOOTER, w - 2 * PAD, h - FOOTER - PAD)
    max_scroll = max(0, content - list_rect[3])
    scroll = max(0, min(max_scroll, scroll))
    rects, top = [], y + h - PAD + scroll
    for row in rows:
        rh = row_height(row)
        top -= rh
        rects.append((list_rect[0], top, list_rect[2], rh))
    return {"rect": (x, y, w, h), "list_rect": list_rect, "rows": rects, "scroll": scroll, "max_scroll": max_scroll,
            "content_height": content}


def actionable(rows):
    return [i for i, row in enumerate(rows) if row["kind"] in ACTIONABLE]


def draw(view, state, controller, width, height, window_fit_target):
    """Draw the open menu; ``window_fit_target`` is the selected mesh's name or ''."""
    import gpu
    popup = state["popover"]
    chosen = list(getattr(controller, "chosen_tools", ()) or ())
    from .worker_routing import worker_snapshots   # A worker's chat is offered only what its Send runs.
    rows = chosen_tools.menu_rows(chosen, window_fit_target, memory().open, worker=bool(worker_snapshots(controller)))
    if not window_fit_target:
        popup["fit"] = False
    for row in rows:
        if row["kind"] == "toggle":
            row["on"] = bool(popup.get("fit"))
    wrap_subtitles(view, rows, popup_width(width) - 2 * PAD)   # A why line wraps before it is elided.
    popup["rows"] = rows
    bottom = state.get("add_bottom", 120)
    geometry = layout(rows, width, height, bottom, popup.get("scroll", 0))
    if not popup.get("revealed"):   # Opening: the asked-for group (popup["reveal"]) at the top of the list, else
        popup["revealed"] = True    # the chosen row (a picked base mesh) in view, never below the fold.
        wanted = popup.get("reveal")
        target = next((i for i, row in enumerate(rows) if wanted and row.get("value") == wanted), None)
        if target is None:
            target = next((i for i, row in enumerate(rows) if row.get("selected")), None)
        if target is not None:
            ly, lh = geometry["list_rect"][1], geometry["list_rect"][3]
            ry, rh = geometry["rows"][target][1], geometry["rows"][target][3]
            if wanted:   # The group's header at the top, its rows under it.
                geometry = layout(rows, width, height, bottom, geometry["scroll"] + ly + lh - (ry + rh))
            elif ry < ly:
                geometry = layout(rows, width, height, bottom, geometry["scroll"] + ly - ry + (lh - rh) / 2)
    popup["layout"], popup["scroll"] = geometry, geometry["scroll"]
    x, y, w, h = geometry["rect"]
    state["popup_rect"] = geometry["rect"]
    view.shape(x, y, w, h, view.LINE, 15)
    view.shape(x + 1, y + 1, w - 2, h - 2, view.POPOVER, 14)
    old_scissor = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    lx, ly, lw, lh = geometry["list_rect"]
    gpu.state.scissor_set(*view._scissor(lx, ly, lw, lh))
    cursor = popup.get("cursor") if popup.get("keyboard") else None
    for i, (row, rect) in enumerate(zip(rows, geometry["rows"])):
        rx, ry, rw, rh = rect
        if ry + rh <= ly or ry >= ly + lh:
            continue
        if row.get("depth"):
            rx, rw = rx + INDENT, rw - INDENT
        kind = row["kind"]
        if kind == "section":
            view.text(view.elide(_label(row["label"]).upper(), rw - 16, 11), rx + 8, ry + 8, 11, view.MUTED)
            continue
        if kind == "note":
            view.text(view.elide(_label(row["label"]), rw - 16, 11.5), rx + 8, ry + 6, 11.5, view.MUTED)
            continue
        if popup.get("hover") == (row["action"], row["value"]) or cursor == i:
            view.shape(rx, ry + 1, rw, rh - 2, view.LINE, 9)
        if kind == "toggle":
            track = (rx + 8, ry + 9, 30, 17)
            view.shape(*track, ui_tokens.TEAL if row["on"] else (0.29, 0.29, 0.29, 1), 8)
            view.shape(track[0] + (15 if row["on"] else 2), track[1] + 2, 13, 13,
                       ui_tokens.INK if row["on"] else ui_tokens.TEXT, 6)
            view.text(view.elide(_label(row["label"]), rw - 56, 12), rx + 48, ry + 13, 12)
        else:
            draw_row(view, row, (rx, ry, rw, rh))
        bottom_y, top_y = max(ly, ry), min(ly + lh, ry + rh)
        _hit(view, state, (rx, bottom_y, rw, top_y - bottom_y), row["action"], row["value"], width, height)
    gpu.state.scissor_set(*view._scissor(x + 1, y + 1, w - 2, h - 2))
    if geometry["max_scroll"]:
        thumb_h = max(22, lh * lh / geometry["content_height"])
        thumb_y = ly + lh - thumb_h - (lh - thumb_h) * geometry["scroll"] / geometry["max_scroll"]
        view.shape(x + w - 5, thumb_y, 2, thumb_h, view.MUTED, 1)
    view.shape(x + 1, y + FOOTER - 1, w - 2, 1, view.LINE)
    for n, line in enumerate(view._flow_wrap(iface_(chosen_tools.FOOTER), w - 32, 11.5)[:2]):
        value = line[0] if isinstance(line, tuple) else line
        view.text(value.rstrip(), x + 16, y + FOOTER - 20 - n * 15, 11.5, view.MUTED)
    gpu.state.scissor_set(*old_scissor)


def _hit(view, state, rect, action, value, width, height):
    from .chat_hit_testing import clip_rect
    visible = clip_rect(rect, (0, 0, width, height))
    if visible:
        state["popup_hits"].append((visible, action, value))


def dispatch(context, controller, state, action, value):
    """A click (or Return) on one of the menu's rows."""
    import bpy
    popup = state.get("popover") or {}
    if action == "popup_add_attach":
        state.pop("popover", None)
        bpy.ops.angelo.attach_reference('INVOKE_DEFAULT')
    elif action == "popup_add_fit":
        popup["fit"] = not popup.get("fit")
        controller.chosen_tools = [dict(choice, fit_selected=popup["fit"]) if choice["tool"] == value else choice
                                   for choice in getattr(controller, "chosen_tools", []) or []]
    elif action == "popup_add_group":   # Open or close a group; the menu stays open, at the same row.
        memory().toggle(value)
        popup["cursor"] = None
    elif action == "popup_add_open":
        row = next((r for r in popup.get("rows") or () if r.get("action") == action and r.get("value") == value), None)
        state.pop("popover", None)
        if row and row.get("operator"):
            group, name = row["operator"].split(".", 1)
            # undo=True: called from Python, an operator pushes no undo step otherwise (WM_operator_call_py), so what a
            # row creates (New Flow, a template) would be lost to the next Undo. Operators without UNDO push nothing.
            getattr(getattr(bpy.ops, group), name)('INVOKE_DEFAULT', True, **dict(row.get("properties") or ()))
    elif action == "popup_add_choose":   # A tool with one option (Work on hands): one click chooses it.
        controller.chosen_tools = chosen_tools.choose(getattr(controller, "chosen_tools", []) or [], value,
                                                      chosen_tools.tool(value).options[0][0])
        state.pop("popover", None)
    elif action == "popup_add_pick":
        tool_id, option = value
        controller.chosen_tools = chosen_tools.choose(getattr(controller, "chosen_tools", []) or [], tool_id, option,
                                                      fit_selected=bool(popup.get("fit")))
        state.pop("popover", None)
    controller.version += 1
    if context.area:
        context.area.tag_redraw()


def key(popup, event):
    """Arrow keys move over the actionable rows; returns the row to activate on Return, else None."""
    rows = popup.get("rows") or []
    stops = actionable(rows)
    if not stops:
        return None
    cursor = popup.get("cursor") if popup.get("cursor") in stops else None
    if event.type in {'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'}:
        if event.type == 'HOME' or (cursor is None and event.type == 'DOWN_ARROW'):
            cursor = stops[0]
        elif event.type == 'END' or cursor is None:
            cursor = stops[-1]
        else:
            index = stops.index(cursor) + (-1 if event.type == 'UP_ARROW' else 1)
            cursor = stops[max(0, min(len(stops) - 1, index))]
        popup.update(cursor=cursor, keyboard=True, hover=None)
        geometry = popup.get("layout")
        if geometry:   # Keep the cursor's row in view.
            _, ry, _, rh = geometry["rows"][cursor]
            _, ly, _, lh = geometry["list_rect"]
            if ry < ly:
                popup["scroll"] = geometry["scroll"] + (ly - ry)
            elif ry + rh > ly + lh:
                popup["scroll"] = max(0, geometry["scroll"] - (ry + rh - ly - lh))
        return None
    if event.type in {'RET', 'NUMPAD_ENTER'} and cursor is not None:
        return rows[cursor]
    return None
