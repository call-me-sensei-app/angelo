# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's own scroll: which eight of the draft's rows it shows, its wheel and trackpad, and its scrollbar.

API (``chat_composer`` and ``chat_view`` call these):
    shown_first(state, total, caret_row, draft_key) -> int   the first row drawn, kept per chat (``state``)
    place(state, rect, start_y, line_height, total)           records where the rows were drawn, for hits
    row_at(state, y) -> int                                   the draft row under a pointer ``y`` (scrolled)
    draw_bar(view, state)                                     the thin, auto-hiding scrollbar
    handle(event, state, area, x, y, scale) -> True           the composer's wheel, trackpad and scrollbar
    drag_edge(state, y), step(state, rows)                    a drag-select past an edge scrolls (``composer_input``)

The offset is the first row shown, an index into the draft's wrapped rows, kept in the chat's view state, so each
chat (area) keeps its own. It moves only when asked: a wheel turn, a trackpad swipe or the scrollbar scroll it, and
it follows the caret only when the caret or the draft changed since the last draw (typing, a key, a click, a paste,
an Undo), and then only as far as keeps the caret in view. Reading earlier rows is never undone by a redraw.

Wheel and trackpad over the composer scroll its draft whenever the draft has more rows than it shows: through
``scroll_input.view_up``, the system's scroll direction, as the transcript does; at either end the event stops
there and never reaches the transcript behind it. A draft that fits leaves them to the transcript, as before.

The text is a left-to-right text field even in a right-to-left interface (``chat_composer``), so its bar is too:
drawn and hit-tested unmirrored, on the text's right. 1.0.9 (maintainer report): the composer always showed the
eight rows ending at the caret, so a long draft's earlier rows could only be reached by moving the caret.
"""

import math
import time

from . import scroll_input, ui_kit_rows

ROWS = 8   # The rows the composer shows (``chat_layout.composer_layout`` grows it to eight).
WHEEL_ROWS = 3   # One wheel notch, as most systems scroll text.
LINGER = 1.2   # The bar stays this long after a scroll, as the transcript's does (``chat_scrollbar``).
_HIT = 3   # Hit width left of the drawn bar: its hover growth (``ui_kit_rows.BAR_GROW``, 2) and a unit.
# How far the text keeps from its viewport's right edge (``place``): the bar's own inset there, the wider of its
# hover growth and its hit margin, and a unit, so a click on a full row's last character is the text's and the bar
# never covers a glyph (break-it R3-3: the bar's hit zone took the last column). From the kit's own geometry.
BAR_CLEARANCE = ui_kit_rows.BAR_INSET + max(ui_kit_rows.BAR_GROW, _HIT) + 1


def clamp(first, total):
    return max(0, min(int(first), max(0, total - ROWS)))


def follow(first, caret_row):
    """The least move that brings ``caret_row`` into view (none when it is in view)."""
    if caret_row < first:
        return caret_row
    if caret_row >= first + ROWS:
        return caret_row - ROWS + 1
    return first


def wheel(first, carry, travel, line_height, total):
    """``(first, carry)`` after the view moved ``travel`` layout units toward earlier rows (``view_up``). Whole rows
    move; the rest carries to the next event, so a slow trackpad swipe still scrolls. An end keeps no carry, so
    turning back moves at once."""
    carry += travel
    rows = int(carry / line_height)   # Toward zero.
    carry -= rows * line_height
    moved = clamp(first - rows, total)
    if moved != first - rows:
        carry = 0.0
    return moved, carry


def shown_first(state, rows, caret_row, draft_key, layout_key=None):
    """The first row to draw of ``rows`` (the draft's wrapped rows, (text, source offset) each). ``draft_key`` is
    what the caret follows: (draft, caret) as last drawn. ``layout_key`` is what the wrapping depends on (width,
    size, scale): when it changes, the rows are new, so the view keeps the same text at its top (the source offset
    of the first row shown), and a caret that was in view stays in view."""
    total = len(rows)
    first = state.get("composer_first", 0)
    if state.get("composer_layout", layout_key) != layout_key and state.get("composer_top") is not None and rows:
        first = max((i for i, (_, offset) in enumerate(rows) if offset <= state["composer_top"]), default=0)
        if state.get("composer_caret_shown"):
            first = follow(first, caret_row)
    if state.get("composer_seen") != draft_key:
        first = follow(first, caret_row)
    first = clamp(first, total)
    state.update(composer_first=first, composer_seen=draft_key, composer_layout=layout_key,
                 composer_top=rows[first][1] if rows else None,
                 composer_caret_shown=first <= caret_row < first + ROWS)
    return first


def place(state, rect, start_y, line_height, total):
    """Where the draft's rows were drawn: ``rect`` the text's viewport (layout units, unmirrored), ``start_y`` the
    first shown row's baseline."""
    state["composer_view"] = {"rect": rect, "start_y": start_y, "line_height": line_height, "total": total}


def row_at(state, y):
    """The draft row under ``y``: each row owns its selection band (``y - 3`` up one line), clamped to the shown
    rows, so a pointer above or below them lands on the first or the last shown row."""
    view = state.get("composer_view")
    if not view or not view["total"]:
        return 0
    first = state.get("composer_first", 0)
    index = math.floor((view["start_y"] - 3 + view["line_height"] - y) / view["line_height"])
    return first + max(0, min(index, ROWS - 1, view["total"] - first - 1))


def drag_edge(state, y):
    """-1 when a drag is above the shown rows (earlier rows wait), 1 below them, 0 inside."""
    view = state.get("composer_view")
    if not view:
        return 0
    _, bottom, _, height = view["rect"]
    return -1 if y > bottom + height else 1 if y < bottom else 0


def scrolls(state):
    """Whether the draft has more rows than the composer shows."""
    view = state.get("composer_view")
    return bool(view) and view["total"] > ROWS


def step(state, rows):
    """Scroll ``rows`` rows (positive: later rows). True when the view moved."""
    view = state.get("composer_view")
    if not view:
        return False
    first = state.get("composer_first", 0)
    moved = clamp(first + rows, view["total"])
    state["composer_first"] = moved
    if moved != first:
        shown(state)
    return moved != first


def scroll_to(state, where):
    """A scroll-only key (macOS Home, End, Page Up, Page Down): the view moves, the caret stays."""
    view = state.get("composer_view")
    if not view:
        return
    first = state.get("composer_first", 0)
    target = {"top": 0, "bottom": view["total"], "page_up": first - (ROWS - 1),
              "page_down": first + (ROWS - 1)}[where]
    state["composer_first"] = clamp(target, view["total"])
    shown(state)


def shown(state):
    """The bar shows now and fades by itself after ``LINGER``."""
    state["composer_bar_time"] = time.monotonic()
    from . import chat_scrollbar
    chat_scrollbar._fade_later()   # One redraw after the linger time hides the bar (shared with the transcript's).


def _bar_geometry(state):
    view = state.get("composer_view")
    if not view or view["total"] <= ROWS:
        return None
    line_height = view["line_height"]
    content, visible = view["total"] * line_height, ROWS * line_height
    offset = state.get("composer_first", 0) * line_height
    return view["rect"], content, visible, offset


def frame_key(state, now):
    """What the scroll adds to a kept frame's key (``marble_frames``): the offset and whether the bar shows."""
    return (state.get("composer_first", 0), bool(state.get("composer_bar_drag") or state.get("composer_hover")
                                                 or now - state.get("composer_bar_time", 0) < LINGER))


def draw_bar(view, state):
    geometry = _bar_geometry(state)
    state["composer_bar"] = None
    if not geometry:
        return
    dragging = bool(state.get("composer_bar_drag"))
    hover = state.get("composer_hover")
    if not (dragging or hover or time.monotonic() - state.get("composer_bar_time", 0) < LINGER):
        return
    rect, content, visible, offset = geometry
    ui_kit_rows.draw_scrollbar(view, rect, 1.0, content, visible, offset, hover=hover == "bar", dragging=dragging)
    state["composer_bar"] = ui_kit_rows.scrollbar_rects(rect, content, visible, offset, 1.0)


def _inside(rect, x, y):
    return rect is not None and rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]


def _hover(state, x, y):
    geometry = _bar_geometry(state)
    if not geometry or not _inside(geometry[0], x, y):
        return None
    track = ui_kit_rows.scrollbar_rects(*geometry[:4], 1.0)[0]
    return "bar" if x >= track[0] - _HIT else "text"   # The drawn bar, widened under the pointer, and a little more.


def _set_offset(state, offset):
    view = state["composer_view"]
    state["composer_first"] = clamp(round(offset / view["line_height"]), view["total"])
    shown(state)


def handle(event, state, area, x, y, scale=1.0):
    """The composer's wheel, trackpad and scrollbar events; True when one was its own. ``x`` is unmirrored."""
    view = state.get("composer_view")
    if not view:
        return False
    if event.type in scroll_input.SCROLL_EVENTS:
        if view["total"] <= ROWS or not _inside(state.get("composer_card"), x, y):
            return False   # A draft that fits: the transcript's, as ever.
        first, carry = wheel(state.get("composer_first", 0), state.get("composer_carry", 0.0),
                             scroll_input.view_up(event, WHEEL_ROWS * view["line_height"], scale),
                             view["line_height"], view["total"])
        state["composer_first"], state["composer_carry"] = first, carry
        shown(state)
        area.tag_redraw()
        return True   # Even at an end: the composer's scroll stops there, never the transcript's.
    drag = state.get("composer_bar_drag")
    if event.type == 'MOUSEMOVE':
        if drag:
            geometry = _bar_geometry(state)
            if geometry:
                _set_offset(state, ui_kit_rows.drag_offset(drag[1], y - drag[0], *geometry[:3], 1.0))
                area.tag_redraw()
            return True
        hover = _hover(state, x, y)
        if hover != state.get("composer_hover"):
            state["composer_hover"] = hover
            area.tag_redraw()
        return hover == "bar"
    if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and _hover(state, x, y) == "bar":
        rect, content, visible, offset = _bar_geometry(state)
        track, thumb = ui_kit_rows.scrollbar_rects(rect, content, visible, offset, 1.0)
        if not thumb[1] <= y <= thumb[1] + thumb[3]:   # The track: jump so the thumb centres on the pointer.
            travel = max(1.0, track[3] - thumb[3])
            ratio = 1 - (y - thumb[3] / 2 - track[1]) / travel
            _set_offset(state, max(0.0, min(1.0, ratio)) * (content - visible))
        state["composer_bar_drag"] = (y, state.get("composer_first", 0) * view["line_height"])
        area.tag_redraw()
        return True
    return False
