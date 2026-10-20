# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu: attach a file, or choose a tool Angelo performs before the agent starts.

Drawn on the chat canvas as the chat's popover kind ``"add"`` (``chat_view``
owns opening, closing, Esc and outside clicks): sections, rows with a tile, a
title and a why line, an opt-in fit toggle, a scrolling list and a footer that
says what happens. The rows come from ``chosen_tools.menu_rows``; ``layout`` is
pure and host-tested. Strings are translated here, at display time.
"""

from . import chosen_tools, ui_tokens
from .ui_text import iface_

WIDTH = 352
MAX_HEIGHT = 520   # Taller chats scroll the list rather than cover the whole transcript.
HEIGHTS = {"section": 28, "note": 22, "toggle": 36, "row": 46}
PAD, FOOTER = 8, 46
ACTIONABLE = ("row", "toggle")
TILE_FILL = (0.255, 0.255, 0.255, 1)


def _label(value):
    return iface_(value[0], **value[1]) if isinstance(value, tuple) else iface_(value)


def layout(rows, width, height, bottom, scroll=0):
    """Popup rect, list rect, each row's rect (region px, y up) and the clamped scroll, for the menu above ``bottom``."""
    w = min(WIDTH, max(200, width - 24))
    content = sum(HEIGHTS[row["kind"]] for row in rows)
    available = max(120, min(MAX_HEIGHT, height - bottom - 12))
    h = min(available, content + 2 * PAD + FOOTER)
    x, y = 12, bottom
    list_rect = (x + PAD, y + FOOTER, w - 2 * PAD, h - FOOTER - PAD)
    max_scroll = max(0, content - list_rect[3])
    scroll = max(0, min(max_scroll, scroll))
    rects, top = [], y + h - PAD + scroll
    for row in rows:
        rh = HEIGHTS[row["kind"]]
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
    rows = chosen_tools.menu_rows(chosen, window_fit_target)
    if not window_fit_target:
        popup["fit"] = False
    for row in rows:
        if row["kind"] == "toggle":
            row["on"] = bool(popup.get("fit"))
    popup["rows"] = rows
    bottom = state.get("add_bottom", 120)
    geometry = layout(rows, width, height, bottom, popup.get("scroll", 0))
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
            tx, ty = rx + 8, ry + 7
            if row["glyph"] == "attach":
                view.shape(tx, ty, 32, 32, TILE_FILL, 8)
                view.icon("plus", tx + 16, ty + 16, view.TEXT)
            else:   # A base mesh: a head on shoulders, in the brand teal.
                view.shape(tx, ty, 32, 32, ui_tokens.TEAL_SOFT, 8)
                view.shape(tx + 11, ty + 13, 10, 12, ui_tokens.TEAL, 5)
                view.shape(tx + 7, ty + 5, 18, 7, ui_tokens.TEAL, 3)
            label = row["label"] if row["action"] == "popup_add_pick" else _label(row["label"])
            view.text(view.elide(label, rw - 74, 13), rx + 50, ry + 25, 13)
            view.text(view.elide(_label(row["subtitle"]), rw - 74, 11.5), rx + 50, ry + 9, 11.5, view.MUTED)
            if row.get("selected"):
                view.icon("check", rx + rw - 16, ry + rh / 2, ui_tokens.TEAL)
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
