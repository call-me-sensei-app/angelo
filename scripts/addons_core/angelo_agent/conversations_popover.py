# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Conversations popover: the chat header's title opens it on the chat canvas (popover kind ``"conversations"``;
``chat_view`` owns opening, outside clicks and its generic Esc, as for the + menu).

Search (typing always goes to the field; title and requests, ``conversation_search``), New chat, every chat with its
title and when it was last used, its state while it works or waits, and on the row under the pointer or the keyboard
its Rename and Delete buttons. Rename edits the title in place (Return saves, Esc cancels, an empty name brings the
automatic title back). Delete asks first, in the row, naming the chat; a deleted chat shows Undo at the top and is
listed under Recently Deleted (the footer link) with Restore. A refusal (a chat still working...) is said in the same
banner. Keys: Up/Down/Home/End move, Return opens (or answers the question), F2 renames, Delete (forward delete, with an
empty search) asks to delete, Esc steps back (the question, the rename, the search, the page) and then closes.
Geometry: ``conversations_layout``; actions: ``conversations_ui``.
"""

import contextlib

from . import canvas_rtl, conversations_layout as layout, ui_tokens
from .ui_kit_field import FieldState, field_key
from .ui_text import iface_, join, n_, plural_

SEARCH = n_("Search chats")
NEW = n_("New chat")
UNDO = n_("Undo")
DELETED = n_("Deleted “{title}”.")
DELETED_PAGE = n_("Recently Deleted")
DELETED_LINK = n_("Recently Deleted ({count})")
DELETED_ON = n_("Deleted {date}")
RESTORE = n_("Restore")
QUESTION = n_("Delete “{title}”?")
CANCEL = n_("Cancel")
DELETE = n_("Delete")
RENAME_HINT = n_("Return saves. Leave it empty to use the automatic title.")
WORKING = n_("Working")
WAITING = n_("Approval needed")
BACK = n_("Back")
PREFIX = "popup_conversations_"


def open_popover(context):
    """Open the popover in the chat ``context`` shows (or the window's first chat). False when there is none."""
    import bpy
    from . import chat_view, console, live_windows
    area = context.area if context.area and console.is_chat(context.area) else next(
        (a for a in live_windows.areas(context.window) if console.is_chat(a)), None) if context.window else None
    if area is None:
        return False
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    with context.temp_override(window=context.window, area=area, region=region):
        if not chat_view.show_popover(bpy.context, "conversations"):
            return False
    _start(chat_view._views[area.as_pointer()]["popover"])
    return True


def _start(popup):
    from . import conversations, conversations_delete as rules, ui
    rules.settle(ui)
    rules.search_index(ui).forget(None)   # Checked again (one stat per chat) at the first search.
    popup.update(conversations=True, search=FieldState(editing=True), rename=None, confirm=None, banner=None,
                 page="list", cursor=None, keyboard=False, scroll=0, hover=None,
                 deleted=conversations.entries(ui.state_directory()))


def _hit(view, state, rect, action, value, width, height, clip=None):
    from .chat_hit_testing import clip_rect
    visible = clip_rect(rect, clip) if clip else rect
    visible = clip_rect(visible, (0, 0, width, height)) if visible else None
    if visible:
        state["popup_hits"].append((visible, PREFIX + action, value))


def _lit(popup, action, value):
    return popup.get("hover") == (PREFIX + action, value)


def draw(view, state, controller, width, height):
    import gpu
    gpu.state.scissor_test_set(True)
    try:
        _draw(view, state, controller, width, height)
    finally:
        gpu.state.scissor_test_set(False)


def _matches(ui, popup, records):
    from . import conversations_delete as rules
    query = popup["search"].text
    if not query.strip() or popup["page"] == "deleted":
        return None
    index = rules.search_index(ui)
    if not index.refresh([record["id"] for record in records]):
        _keep_drawing()   # Older chats are still being read: matched by title until then.
    return index.search(records, query, layout.shown_title)


def _draw(view, state, controller, width, height):
    import gpu
    from . import ui
    popup = state["popover"]
    if not popup.get("conversations"):
        _start(popup)
    records = ui._sessions.ordered()
    found = layout.rows(records, _matches(ui, popup, records), popup, popup["deleted"])
    if popup.get("rows_for") != [row["id"] for row in found]:
        popup["rows_for"] = [row["id"] for row in found]
        stops = layout.stops(found)
        popup["cursor"] = stops[0] if stops and popup["search"].text.strip() else popup.get("cursor")
    popup["rows"] = found
    page = popup["page"]
    geometry = layout.place(found, width, height, banner=bool(popup.get("banner")), header=page == "deleted",
                            footer=page != "deleted" and bool(popup["deleted"]), scroll=popup.get("scroll", 0))
    popup["scroll"], popup["layout"] = geometry["scroll"], geometry
    popup["esc_back"] = bool(popup["search"].text or page == "deleted" or popup.get("rename") or popup.get("confirm"))
    x, y, w, h = state["popup_rect"] = geometry["rect"]
    gpu.state.scissor_set(*view._scissor(x, y, w, h))
    view.shape(x, y, w, h, ui_tokens.HIGHLIGHT, 15)
    view.shape(x + 1, y + 1, w - 2, h - 2, ui_tokens.MENU, 14)
    _field(view, geometry["search"], popup["search"], iface_(SEARCH), not popup.get("rename"))
    _hit(view, state, geometry["search"], "search", None, width, height)
    if geometry.get("banner"):
        _banner(view, state, popup, geometry["banner"], width, height)
    if geometry.get("header"):
        _header(view, state, popup, geometry["header"], width, height)
    lx, ly, lw, lh = clip = geometry["list_rect"]
    gpu.state.scissor_set(*view._scissor(lx, ly, lw, lh))
    for index, (row, rect) in enumerate(zip(found, geometry["rows"])):
        if rect[1] + rect[3] > ly and rect[1] < ly + lh:
            _row(view, state, popup, controller, index, row, rect, clip, width, height)
    gpu.state.scissor_set(*view._scissor(x, y, w, h))
    if geometry["max_scroll"]:
        content = lh + geometry["max_scroll"]
        thumb = max(22, lh * lh / content)
        view.shape(lx + lw + 1, ly + lh - thumb - (lh - thumb) * geometry["scroll"] / geometry["max_scroll"], 2,
                   thumb, ui_tokens.MUTED, 1)
    if geometry.get("footer"):
        fx, fy, fw, fh = geometry["footer"]
        view.shape(x + 1, fy + fh + 2, w - 2, 1, ui_tokens.HIGHLIGHT)
        if _lit(popup, "deleted_page", None):
            view.shape(fx, fy, fw, fh, ui_tokens.HIGHLIGHT, 8)
        label = iface_(DELETED_LINK, count=len(popup["deleted"]))
        view.text(view.elide(label, fw - 24, 12), fx + 10, fy + 9, 12, ui_tokens.MUTED)
        _hit(view, state, geometry["footer"], "deleted_page", None, width, height)


def _field(view, rect, fs, placeholder, focused):
    """A one-line field as the + menu's search draws it: typed text reads left to right in every language."""
    sx, sy, sw, sh = rect
    view.shape(sx, sy, sw, sh, ui_tokens.TEAL if focused and fs.text else ui_tokens.HIGHLIGHT, 9)
    view.shape(sx + 1, sy + 1, sw - 2, sh - 2, ui_tokens.CANVAS, 8)
    if not fs.text:
        view.text(view.elide(placeholder, sw - 24, 12.5), sx + 12, sy + sh / 2 - 4.5, 12.5, ui_tokens.MUTED)
    # Clipped in canvas units first (mirrored with the canvas), then typed text drawn left to right in plain pixels.
    with _clip(view, (sx + 2, sy, sw - 4, sh)), canvas_rtl.upright(sx + 12, sw - 24) as tx, canvas_rtl.unmirrored():
        caret = view.measure(fs.text[:fs.caret()], 12.5, shaped=False) if fs.text else 0
        offset = max(0, caret - (sw - 28))   # A long name scrolls so its caret stays in view.
        if fs.text:
            view.text(fs.text, tx - offset, sy + sh / 2 - 4.5, 12.5, ui_tokens.TEXT, shaped=False)
        if focused:
            view.shape(tx - offset + caret + 1, sy + 7, 1.5, sh - 14, ui_tokens.TEAL)


@contextlib.contextmanager
def _clip(view, rect):
    """Scissor to ``rect`` (canvas layout units) inside the scissor already set, then put that one back."""
    import gpu
    previous = tuple(gpu.state.scissor_get())
    x, y, w, h = view._scissor(*rect)
    px, py, pw, ph = previous
    if pw > 0 and ph > 0:
        x0, y0 = max(x, px), max(y, py)
        w, h, x, y = min(x + w, px + pw) - x0, min(y + h, py + ph) - y0, x0, y0
    gpu.state.scissor_set(int(x), int(y), max(1, int(w)), max(1, int(h)))
    try:
        yield
    finally:
        gpu.state.scissor_set(*previous)


def _button(view, state, popup, rect, action, value, label, width, height, clip=None, fill=None, ink=None):
    lit = _lit(popup, action, value) or _focused(popup, action, value)
    view.shape(*rect, (ui_tokens.CORAL_HOVER if lit else ui_tokens.CORAL) if fill is ui_tokens.CORAL
               else (ui_tokens.HIGHLIGHT if lit else fill or ui_tokens.RAISED), 7)
    size = 12
    text = view.elide(label, rect[2] - 12, size)
    view.text(text, rect[0] + (rect[2] - view.measure(text, size)) / 2, rect[1] + rect[3] / 2 - 4, size,
              ink or ui_tokens.TEXT)
    _hit(view, state, rect, action, value, width, height, clip)


def _focused(popup, action, value):
    rows, cursor = popup.get("rows") or [], popup.get("cursor")
    if not popup.get("keyboard") or cursor is None or cursor >= len(rows):
        return False
    row = rows[cursor]
    return (action, value) == ("confirm", row["id"]) if row["kind"] == "confirm" else False


def _banner(view, state, popup, rect, width, height):
    bx, by, bw, bh = rect
    kind, text, value = popup["banner"]
    view.shape(bx, by, bw, bh, ui_tokens.CORAL_SOFT if kind == "error" else ui_tokens.TEAL_SOFT, 8)
    room = bw - 24 - (70 if kind == "undo" else 0)
    from .display_text import shown
    view.text(view.elide(shown(text), room, 12), bx + 10, by + bh / 2 - 4, 12, ui_tokens.TEXT)   # Translated as drawn.
    if kind == "undo":
        _button(view, state, popup, (bx + bw - 66, by + 5, 60, bh - 10), "undo", value, iface_(UNDO), width, height,
                ink=ui_tokens.TEAL)


def _header(view, state, popup, rect, width, height):
    """The Recently Deleted page's Back button, its name and how long its chats stay."""
    hx, hy, hw, hh = rect
    back = (hx + 2, hy + hh / 2 - 13, 26, 26)
    cx, cy = back[0] + 13, back[1] + 13
    if _lit(popup, "back", None):
        view.shape(*back, ui_tokens.HIGHLIGHT, 7)
    view.stroke(cx + 2, cy + 5, cx - 3, cy, ui_tokens.TEXT, 1.6)
    view.stroke(cx - 3, cy, cx + 2, cy - 5, ui_tokens.TEXT, 1.6)
    _hit(view, state, back, "back", None, width, height)
    view.text(view.elide(iface_(DELETED_PAGE), hw - 44, 13), hx + 36, hy + hh - 19, 13)
    days = _keep_days()
    view.text(view.elide(plural_(days, "Chats here are removed after {count} day.",
                                 "Chats here are removed after {count} days.", count=days), hw - 44, 11),
              hx + 36, hy + 6, 11, ui_tokens.MUTED)


def _keep_days():
    from .conversations import KEEP_DAYS
    return KEEP_DAYS


def _row(view, state, popup, controller, index, row, rect, clip, width, height):
    rx, ry, rw, rh = rect
    kind = row["kind"]
    if kind == "note":
        view.text(view.elide(iface_(row["label"]), rw - 20, 12), rx + 10, ry + rh / 2 - 4, 12, ui_tokens.MUTED)
        return
    keyboard = popup.get("keyboard") and popup.get("cursor") == index
    action = layout.ACTIONS.get(kind, "").replace(PREFIX, "")
    pointed = keyboard or (action and _lit(popup, action, row["id"])) or any(
        _lit(popup, name, row["id"]) for name in ("rename", "delete"))
    if pointed and kind not in {"rename", "confirm"}:
        view.shape(rx, ry + 1, rw, rh - 2, ui_tokens.HIGHLIGHT, 9)
    if keyboard:
        from . import ui_kit
        ui_kit.focus_ring(view, (rx, ry + 1, rw, rh - 2), 9, 1.0)
    if kind == "new":
        view.icon("plus", rx + 17, ry + rh / 2, ui_tokens.TEAL)
        view.text(iface_(NEW), rx + 34, ry + rh / 2 - 4.5, 13)
        _hit(view, state, rect, action, None, width, height, clip)
    elif kind == "rename":
        field = (rx + 6, ry + rh - 6 - 30, rw - 12, 30)
        _field(view, field, popup["rename"][1], layout.shown_title(row["record"]), True)
        view.text(view.elide(iface_(RENAME_HINT), rw - 24, 11), rx + 12, ry + 7, 11, ui_tokens.MUTED)
    elif kind == "confirm":
        view.shape(rx, ry + 1, rw, rh - 2, ui_tokens.CORAL_SOFT, 9)
        title = layout.shown_title(row["record"])
        view.text(view.elide(iface_(QUESTION, title=title), rw - 24, 13), rx + 12, ry + rh - 22, 13)
        from .conversations_ui import keep_sentence
        cancel, delete = layout.confirm_buttons(rect)
        view.text(view.elide(keep_sentence(), rw - 24, 11), rx + 12, ry + rh - 40, 11, ui_tokens.SUBTITLE)   # On coral.
        _button(view, state, popup, cancel, "cancel", row["id"], iface_(CANCEL), width, height, clip)
        _button(view, state, popup, delete, "confirm", row["id"], iface_(DELETE), width, height, clip,
                fill=ui_tokens.CORAL, ink=ui_tokens.INK)
    elif kind == "deleted":
        from .ui_formats import format_clock
        restore = (rx + rw - 8 - 76, ry + (rh - 26) / 2, 76, 26)
        view.text(view.elide(layout.shown_title(row["record"]), rw - 110, 13), rx + 12, ry + rh - 20, 13)
        view.text(view.elide(iface_(DELETED_ON, date=format_clock(row["deleted"])), rw - 110, 11.5), rx + 12, ry + 9,
                  11.5, ui_tokens.MUTED)
        _button(view, state, popup, restore, "restore", row["id"], iface_(RESTORE), width, height, clip)
    else:
        _chat(view, state, popup, controller, row, rect, pointed, clip, width, height)


def _chat(view, state, popup, controller, row, rect, pointed, clip, width, height):
    from . import ui
    from .ui_formats import format_clock
    rx, ry, rw, rh = rect
    record, identity = row["record"], row["id"]
    if identity == controller.state["id"]:   # The chat on screen: a teal mark at the row's start.
        view.shape(rx + 3, ry + 12, 3, rh - 24, ui_tokens.TEAL, 1.5)
    room = rw - 26 - (64 if pointed else 0)
    view.text(view.elide(layout.shown_title(record), room, 13), rx + 14, ry + rh - 20, 13)
    other = ui._controllers.get(identity)
    status, color = None, ui_tokens.MUTED
    if other is not None and any(getattr(call, "approval", False) for call in other.pending.values()):
        status, color = iface_(WAITING), ui_tokens.AMBER
    elif other is not None and other.busy:
        status, color = iface_(WORKING), ui_tokens.TEAL
    used = format_clock(record["updated"]) if record.get("updated") else ""   # 0: a chat listed again, time unknown.
    line = join([part for part in (status, used, row.get("snippet")) if part])
    view.text(view.elide(line, room, 11.5), rx + 14, ry + 9, 11.5, color if status and not row.get("snippet")
              else ui_tokens.MUTED)
    _hit(view, state, rect, "open", identity, width, height, clip)
    if pointed:
        rename, delete = layout.buttons(rect)
        for button, name in ((rename, "rename"), (delete, "delete")):
            if _lit(popup, name, identity):
                view.shape(*button, ui_tokens.HIGHLIGHT if name == "rename" else ui_tokens.CORAL_SOFT, 7)
            ink = ui_tokens.CORAL if name == "delete" and _lit(popup, name, identity) else ui_tokens.TEXT
            cx, cy = button[0] + button[2] / 2, button[1] + button[3] / 2
            if name == "rename":
                view.icon("pencil", cx - 1, cy, ink)
            else:
                _trash(view, cx, cy, ink)
            _hit(view, state, button, name, identity, width, height, clip)


def _trash(view, x, y, color):
    """A bin: its lid with a handle, and its body with two ribs. Strokes only; a symbol, never mirrored."""
    view.stroke(x - 6, y + 5, x + 6, y + 5, color, 1.3)
    view.stroke(x - 2, y + 7, x + 2, y + 7, color, 1.3)
    for a, b in (((-4.5, 5), (-3.5, -6)), ((-3.5, -6), (3.5, -6)), ((3.5, -6), (4.5, 5)),
                 ((-1.3, 2.5), (-1.1, -3.5)), ((1.3, 2.5), (1.1, -3.5))):
        view.stroke(x + a[0], y + a[1], x + b[0], y + b[1], color, 1.2)


# Keys and clicks -----------------------------------------------------------------------------------------------------

def key(context, controller, state, event):
    """A key press in the open popover (``chat_view.popover_event``): Esc steps back while ``esc_back``."""
    popup = state.get("popover") or {}
    if not popup.get("conversations"):
        return
    rows = popup.get("rows") or []
    cursor = popup.get("cursor")
    if popup.get("rename"):
        identity, fs = popup["rename"]
        outcome = field_key(fs, event.type, getattr(event, "unicode", "") or "", bool(event.ctrl or event.oskey),
                            lambda: context.window_manager.clipboard)
        if outcome == "submit":
            dispatch(context, controller, state, PREFIX + "save", identity)
        elif outcome == "cancel":
            popup["rename"] = None
        return
    if popup.get("confirm"):
        if event.type in {'RET', 'NUMPAD_ENTER'}:
            dispatch(context, controller, state, PREFIX + "confirm", popup["confirm"])
        elif event.type == 'ESC':
            popup["confirm"] = None
        return
    if event.type == 'ESC':
        if popup["search"].text:
            popup["search"] = FieldState(editing=True)
            popup.update(scroll=0, cursor=None, keyboard=False)
        elif popup["page"] == "deleted":
            dispatch(context, controller, state, PREFIX + "back", None)
        return
    stops = layout.stops(rows)
    if event.type in {'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'} and stops:
        if event.type == 'HOME' or (cursor not in stops and event.type == 'DOWN_ARROW'):
            cursor = stops[0]
        elif event.type == 'END' or cursor not in stops:
            cursor = stops[-1]
        else:
            cursor = stops[max(0, min(len(stops) - 1, stops.index(cursor) + (-1 if event.type == 'UP_ARROW' else 1)))]
        popup.update(cursor=cursor, keyboard=True, hover=None)
        if popup.get("layout") and cursor < len(popup["layout"]["rows"]):
            popup["scroll"] = layout.reveal(popup["layout"], cursor)
        return
    row = rows[cursor] if cursor is not None and cursor < len(rows) else None
    if event.type in {'RET', 'NUMPAD_ENTER'}:
        if row is not None and row["kind"] in layout.ACTIONS:
            dispatch(context, controller, state, layout.ACTIONS[row["kind"]], row["id"])
        return
    if event.type == 'F2' and row is not None and row["kind"] == "chat":
        dispatch(context, controller, state, PREFIX + "rename", row["id"])
        return
    if event.type == 'DEL' and not popup["search"].text and row is not None and row["kind"] == "chat":
        dispatch(context, controller, state, PREFIX + "delete", row["id"])
        return
    if popup["page"] == "deleted":
        return
    before = popup["search"].text
    field_key(popup["search"], event.type, getattr(event, "unicode", "") or "", bool(event.ctrl or event.oskey),
              lambda: context.window_manager.clipboard)
    popup["search"].editing = True   # Return and Esc never end the search field: they act on the list.
    if popup["search"].text != before:
        popup.update(scroll=0, keyboard=bool(popup["search"].text.strip()), banner=None)


def dispatch(context, controller, state, action, value):
    """A click on (or Return over) one of the popover's controls; ``action`` starts with ``PREFIX``."""
    import bpy
    from . import conversations, conversations_ui as actions, ui
    from .display_text import Text
    popup = state.get("popover")
    if not popup:
        return
    name = action[len(PREFIX):]
    if name == "search":
        popup.update(rename=None, confirm=None)
    elif name == "new":
        state.pop("popover", None)
        bpy.ops.angelo.new_chat()
    elif name == "open":
        state.pop("popover", None)
        if value != controller.state["id"]:
            bpy.ops.angelo.switch_session(session_id=value)
    elif name == "rename":
        record = ui._sessions.records.get(value) or {}
        from .sessions import UNTITLED
        text = record.get("title", "") if record.get("title") != UNTITLED else ""
        popup.update(rename=(value, FieldState(text=text, editing=True)), confirm=None, banner=None)
        _focus(popup, value)
    elif name == "save":
        title, reason = actions.rename(value, popup["rename"][1].text)
        popup["rename"] = None
        popup["banner"] = ("error", reason, None) if reason else None
    elif name == "delete":
        popup.update(confirm=value, rename=None, banner=None)
        _focus(popup, value)
    elif name == "cancel":
        popup.update(confirm=None, rename=None)
    elif name == "confirm":
        title = actions.title_of(value)
        entry, reason = actions.delete(value)
        popup["confirm"] = None
        popup["banner"] = ("error", reason, None) if reason else ("undo", Text(DELETED, title=title), value)
        _follow(popup, ui)
    elif name in {"undo", "restore"}:
        record, reason = actions.restore(value)
        popup["banner"] = ("error", reason, None) if reason else None
        _follow(popup, ui)
    elif name == "deleted_page":
        popup.update(page="deleted", scroll=0, cursor=None, keyboard=False, rename=None, confirm=None)
    elif name == "back":
        popup.update(page="list", scroll=0, cursor=None, keyboard=False)
    popup["deleted"] = conversations.entries(ui.state_directory())
    if popup.get("page") == "deleted" and not popup["deleted"] and name != "deleted_page":
        popup["page"] = "list"


def _focus(popup, identity):
    rows = popup.get("rows") or []
    popup["cursor"] = next((i for i, row in enumerate(rows) if row["id"] == identity), popup.get("cursor"))


def _follow(popup, ui):
    """The popover stays open on the chat now shown (a delete of the shown chat switched to another)."""
    current = ui.get_controller()
    popup.update(session=current.state["id"], connection=current.selection_connection_id, cursor=None, keyboard=False)


_ticking = [False]


def _keep_drawing():
    """Redraw the open popover until the search index has read every chat (``conversation_search``)."""
    import bpy
    if _ticking[0]:
        return
    _ticking[0] = True
    bpy.app.timers.register(_tick, first_interval=0.02)


def _tick():
    import bpy
    from . import chat_view, conversations_delete as rules, console, live_windows, ui
    open_areas = []
    for window in live_windows.windows(bpy.context.window_manager):
        for area in live_windows.areas(window):
            popup = chat_view._views.get(area.as_pointer(), {}).get("popover")
            if console.is_chat(area) and popup and popup.get("conversations"):
                open_areas.append(area)
    if not open_areas or ui._sessions is None or rules.search_index(ui).complete:
        _ticking[0] = False
        for area in open_areas:
            area.tag_redraw()
        return None
    for area in open_areas:
        area.tag_redraw()
    return 0.02
