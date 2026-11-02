# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the composer's + menu (the launcher) shows and how a person moves through it. Blender only in ``dispatch``.

The popover dict (``chat_view``'s ``state["popover"]``, kind ``"add"``) holds the view: the top level (the grid),
a group's page (``page``: a group opened in place of the grid, never inline) or search results (``query``, typed with
the menu open). The menu always opens at the top level and remembers nothing between openings (1.0.8: open groups
remembered across launches pushed features below the fold). ``esc_back`` tells ``chat_view`` that Esc belongs to the
menu (it clears the search or goes back) instead of closing it.

Keyboard: typing searches; the arrow keys move over the stops the last draw recorded (``stops``: action, value, row,
column), Up and Down by row to the nearest column, Left and Right along a row, Left from a page's first column goes
back; Tab and Shift+Tab walk every stop; Home and End jump; Return activates; Backspace edits the search or, with
none, goes back.
"""

from . import chat_add_layout, chat_destinations
from .ui_text import n_

SEARCH = n_("Search Angelo")
HINT = n_("Point at anything to see what it does. Type to search.")
NO_MATCH = n_("Nothing matches “{query}”")
QUERY_LIMIT = 80


def mode(popup):
    return "search" if popup.get("query") else "page" if popup.get("page") else "top"


def _reset(popup):
    popup.update(scroll=0, cursor=None, hover=None, pending=None, keyboard=False)
    popup["esc_back"] = bool(popup.get("query") or popup.get("page"))


def open_page(popup, group_id):
    popup.update(page=group_id, query="")
    _reset(popup)
    popup["reveal_selected"] = True   # A page opens with its chosen row (a picked base mesh) in view.


def back(popup):
    popup["page"] = None
    _reset(popup)


def set_query(popup, value):
    popup["query"] = value[:QUERY_LIMIT]
    _reset(popup)


def _folded(text):
    return " ".join(text.replace("​", "").casefold().split())


def search(query, chosen, fit_target, worker, shown):
    """Every feature and every row inside a group whose title (or whose group's title) holds ``query``, in menu order,
    as rows for the list (a group's own row opens its page). ``shown(row, key)`` is the text as drawn. A row inside a
    group carries its group's title as its subtitle, so the person sees where it lives."""
    wanted = _folded(query)
    if not wanted:
        return []
    groups = [entry["value"] for entry in chat_destinations.entries(worker) if entry["kind"] == "group"]
    found, group = [], None
    for row in chat_destinations.menu_rows(chosen, fit_target, set(groups), worker):
        if row["kind"] == "group":
            group = row
        elif not row.get("depth"):
            group = None
        if row["kind"] not in ("row", "group"):
            continue
        # As shown, and as written in the source: people type a feature's English name in any language ("render",
        # "uv") and product names (EEVEE, Cycles) are never translated.
        title = _folded(shown(row, "label")) + "\n" + _folded(_source(row))
        place = (_folded(shown(group, "label")) + "\n" + _folded(_source(group))) \
            if group is not None and row is not group else ""
        if wanted in title or (place and wanted in place):
            result = dict(row, depth=0, open=False)   # A group's result opens its page: a closed chevron.
            if row.get("depth") and group is not None:   # Where it lives, then what it does (the row's own lines).
                own, place = shown(row, "subtitle"), shown(group, "label")
                result.update(subtitle=chat_add_layout.joined(place, own) if own else place, shown_subtitle=True)
            found.append(result)
    return found


def _source(row):
    """A row's label as written in the source (an ``n_`` constant or a name), or ''."""
    label = row.get("label")
    return label if isinstance(label, str) else (label[0] if isinstance(label, tuple) and label else "")


def _move(stops, cursor, key, shift=False):
    """The stop the arrow, Tab, Home or End key moves to from ``cursor`` (an index into ``stops``: action, value, row,
    column). Left and Right step to the next column of the same row; Up and Down to the nearest column of the next
    row. ``key`` is already in reading direction (``key`` swaps Left and Right in a right-to-left language)."""
    if not stops:
        return None
    if cursor is None or cursor >= len(stops):
        return len(stops) - 1 if key in ("END", "UP_ARROW") or (key == "TAB" and shift) else 0
    if key == "HOME":
        return 0
    if key == "END":
        return len(stops) - 1
    if key == "TAB":
        return (cursor + (-1 if shift else 1)) % len(stops)
    _, _, row, column = stops[cursor][:4]
    if key in ("LEFT_ARROW", "RIGHT_ARROW"):
        ahead = [i for i, stop in enumerate(stops) if stop[2] == row
                 and (stop[3] > column if key == "RIGHT_ARROW" else stop[3] < column)]
        return min(ahead, key=lambda i: abs(stops[i][3] - column)) if ahead else cursor
    rows = sorted({stop[2] for stop in stops})
    position = rows.index(row) + (-1 if key == "UP_ARROW" else 1)
    if not 0 <= position < len(rows):
        return cursor
    candidates = [i for i, stop in enumerate(stops) if stop[2] == rows[position]]
    return min(candidates, key=lambda i: (abs(stops[i][3] - column), stops[i][3]))


def reveal(popup):
    """Scroll the list so the keyboard's stop is whole in view, before the next draw: the stops' rects are where the
    last draw put them (at ``layout["drawn_scroll"]``), so a second key before a redraw still lands right."""
    stops, cursor, geometry = popup.get("stops") or [], popup.get("cursor"), popup.get("layout") or {}
    if cursor is None or cursor >= len(stops) or len(stops[cursor]) < 5 or "area" not in geometry:
        return
    _, ry, _, rh = stops[cursor][4]
    ry += popup.get("scroll", 0) - geometry.get("drawn_scroll", 0)
    ly, lh = geometry["area"][1], geometry["area"][3]
    if ry + rh > ly + lh + 0.5 or ry < ly - 0.5:
        shift = (ly - ry) if ry < ly else -(ry + rh - ly - lh)
        popup["scroll"] = max(0, min(geometry.get("max_scroll", 0), popup.get("scroll", 0) + shift))


def key(popup, event, clipboard="", rtl=False):
    """One key press while the menu is open. Returns ``{"action", "value"}`` to dispatch (Return on the keyboard's
    stop), else None; the caller redraws. ``rtl``: the interface reads right to left, so the grid is mirrored and
    Left and Right swap (Right goes back from a page)."""
    kind = event.type
    if rtl and kind in ('LEFT_ARROW', 'RIGHT_ARROW'):
        kind = 'RIGHT_ARROW' if kind == 'LEFT_ARROW' else 'LEFT_ARROW'
    modifier = getattr(event, "ctrl", False) or getattr(event, "oskey", False)
    query = popup.get("query", "")
    if kind == 'ESC':
        if query:
            set_query(popup, "")
        elif popup.get("page"):
            back(popup)
        return None
    if modifier and kind == 'V':
        if clipboard:
            set_query(popup, query + " ".join(clipboard.split()))
        return None
    if kind in ('BACK_SPACE', 'DEL'):
        if query:
            set_query(popup, "" if modifier or kind == 'DEL' else query[:-1])
        elif popup.get("page"):
            back(popup)
        return None
    text = getattr(event, "unicode", "") or ""
    if text and text.isprintable() and not modifier and not (text == " " and not query):
        set_query(popup, query + text)
        return None
    stops = popup.get("stops") or []
    cursor = popup.get("cursor") if popup.get("keyboard") else None   # A pointer move hands focus to the pointer.
    if kind in ('UP_ARROW', 'DOWN_ARROW', 'LEFT_ARROW', 'RIGHT_ARROW', 'HOME', 'END', 'TAB'):
        if (kind == 'LEFT_ARROW' and mode(popup) == "page"
                and (cursor is None or cursor >= len(stops) or stops[cursor][3] == 0)):
            back(popup)
            return None
        rows = [i for i, stop in enumerate(stops) if stop[2] >= 0 and stop[0] != "popup_add_back"]
        if cursor is None and mode(popup) != "top" and rows and kind in ('DOWN_ARROW', 'TAB'):
            moved = rows[0]   # A page's (or the results') first row, not the bar above it.
        else:
            moved = _move(stops, cursor, kind, getattr(event, "shift", False))
        popup.update(cursor=moved, keyboard=True, hover=None)
        reveal(popup)
        return None
    if kind in ('RET', 'NUMPAD_ENTER') and cursor is not None and cursor < len(stops):
        return {"action": stops[cursor][0], "value": stops[cursor][1]}
    return None


def dispatch(context, controller, state, action, value):
    """A click (or Return) on one of the menu's tiles, rows, buttons or links."""
    import bpy
    from . import chosen_tools
    popup = state.get("popover") or {}
    if action == "popup_add_group":   # A group opens as a page in the same menu.
        open_page(popup, value)
    elif action == "popup_add_back":
        back(popup)
    elif action == "popup_add_search":
        pass   # Typing goes to the search field whatever is focused; a click only says where it is.
    elif action == "popup_add_attach":
        state.pop("popover", None)
        bpy.ops.angelo.attach_reference('INVOKE_DEFAULT')
    elif action == "popup_add_fit":
        popup["fit"] = not popup.get("fit")
        controller.chosen_tools = [dict(choice, fit_selected=popup["fit"]) if choice["tool"] == value else choice
                                   for choice in getattr(controller, "chosen_tools", []) or []]
    elif action == "popup_add_open":
        operator, properties = _operator(popup, value)
        state.pop("popover", None)
        if operator:
            group, name = operator.split(".", 1)
            # undo=True: called from Python, an operator pushes no undo step otherwise (WM_operator_call_py), so what a
            # row creates (New Flow, a template) would be lost to the next Undo. Operators without UNDO push nothing.
            getattr(getattr(bpy.ops, group), name)('INVOKE_DEFAULT', True, **dict(properties))
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


def _operator(popup, value):
    """The operator and properties of the shown row or tile ``value`` names (``popup["rows"]``, then the top level)."""
    for row in list(popup.get("rows") or ()) + list(popup.get("entries") or ()):
        if row.get("action") == "popup_add_open" and row.get("value") == value:
            return row.get("operator"), row.get("properties") or ()
    return "", ()
