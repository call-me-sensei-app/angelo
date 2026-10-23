# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The start page a flow window or the Generation Flows tab shows while no flow is open (``flow_window``'s canvas).

The Flows popup's rows (``flow_menu.flow_rows``: New Flow, the templates, your flows, with the popup's section labels),
centred under the bar in the + menu's row style. Every why line shows in full; rows that don't fit the editor are left
out from the end of your flows and counted in a line that says where they are (Window > Generation Flows).
"""

from .ui_text import plural_

ROW_H, ROW_W, LINE_H, SECTION_H = 52, 440, 14, 28   # Rows (the + menu's row), why lines, section labels.

def start_rows(rows, wrap, available, s):
    """The start page's rows that fit ``available`` pixels, each with its why lines (all of them: never cut) and its
    height, and how many of the person's flows did not fit (they are in Window > Generation Flows and the + menu).
    ``wrap(text)`` returns a why line's lines. Pure."""
    from . import flow_open
    laid, used, left = [], 0.0, 0
    for index, row in enumerate(rows):
        if row.get("kind") == "section":   # The popup's section labels: Start from a template, Your flows.
            laid.append((row, [], SECTION_H * s))
            used += SECTION_H * s
            continue
        lines = wrap(row) if row.get("subtitle") else []
        height = (ROW_H + max(0, len(lines) - 1) * LINE_H) * s
        mine = row["value"].startswith(flow_open.TREE_ROW)
        if mine and used + height + (ROW_H * s if index < len(rows) - 1 else 0) > available:
            left = sum(1 for r in rows[index:] if r.get("value", "").startswith(flow_open.TREE_ROW))
            if laid and laid[-1][0].get("kind") == "section":
                laid.pop()   # A label with none of its rows under it says nothing.
            break
        laid.append((row, lines, height))
        used += height
    return laid, left


def draw(region, s, state):
    """The Generation Flows rows (``flow_open.menu_rows``) centred under the bar, in the + menu's row style; a line
    says how many of the person's flows did not fit."""
    from . import card_kit as kit, chat_add_menu, chat_view, flow_open, flow_window
    from .chat_add_menu import _shown
    width = min(ROW_W * s, region.width - 32 * s)
    x = (region.width - width) / 2
    y = region.height - flow_window.HEIGHT * s - 40 * s
    text_w = width - (chat_add_menu.TEXT_X + 20) * s

    def wrap(row):
        return [(l[0] if isinstance(l, tuple) else l).rstrip()
                for l in chat_view._flow_wrap(_shown(row, "subtitle"), text_w, 11.5 * s)]
    from . import flow_menu   # The Flows popup's rows and section labels; its header is the bar's own title here.
    laid, left = start_rows([r for r in flow_menu.flow_rows() if r["kind"] != "header"], wrap, y - 16 * s, s)
    hits = []
    for row, lines, height in laid:
        y -= height
        if row.get("kind") == "section":
            label = chat_add_menu._label(row["label"]).upper()
            chat_view.text(chat_view.elide(label, width - 24 * s, 11 * s), x + 12 * s, y + 9 * s, 11 * s, kit.MUTED)
            continue
        rect = (x, y, width, height - 6 * s)
        hovered = state["hover"] == ("row", row["value"])
        chat_view.shape(*rect, kit.PANEL_HOVER if hovered else kit.PANEL, 10 * s)
        tx = x + chat_add_menu.TEXT_X * s   # The title column, tile or not (studio_entry_points.rst, section 2).
        if row.get("glyph"):   # New Flow's plus; a template or one of your flows has none (its section says).
            import gpu
            from . import canvas_rtl
            with canvas_rtl.upright(x + 8 * s, 32 * s) as gx, gpu.matrix.push_pop():   # A glyph never mirrors.
                gpu.matrix.translate((gx, y + (rect[3] - 32 * s) / 2))
                gpu.matrix.scale((s, s))
                chat_add_menu.draw_glyph(chat_view, row["glyph"], 0, 0)
        title_y = y + rect[3] - 20 * s if lines else y + (rect[3] - 13 * s * 0.72) / 2   # Centred with no why.
        chat_view.text(chat_view.elide(_shown(row, "label"), x + width - 20 * s - tx, 13 * s), tx, title_y, 13 * s,
                       kit.TEXT)
        for n, line in enumerate(lines):   # Every line of the why: wrapped, never cut.
            chat_view.text(line, tx, y + rect[3] - 36 * s - n * LINE_H * s, 11.5 * s, kit.MUTED)
        hits.append((rect, "row", row["value"]))
        state.setdefault("rows", {})[row["value"]] = row
    if left:   # The rest of the person's flows are in Window > Generation Flows and the + menu.
        note = plural_(left, "{count} more flow in Window ▸ Generation Flows", "{count} more flows in Window ▸ "
                       "Generation Flows")
        chat_view.text(chat_view.elide(note, width - 24 * s, 11.5 * s), x + 12 * s, y - 20 * s, 11.5 * s, kit.MUTED)
    state["start_left"] = left
    return hits
