# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Conversations popover's rows and geometry (``conversations_popover`` draws them). No ``bpy`` or GPU.

The popover hangs from the top of the chat, under the header's title that opens it: the search field, the Undo
banner after a delete, then the list (New chat, the conversations, newest first, or the matches) or the Recently
Deleted page, and a footer link to that page while it holds any chat. Rectangles are the chat canvas's unscaled
region coordinates, y up; a right-to-left language mirrors them as it mirrors the whole canvas (``canvas_rtl``).

Rows (``rows``): ``new``, ``chat`` (``record``, ``snippet``), ``rename`` and ``confirm`` (a chat being renamed or
asked about), ``deleted`` (a Recently Deleted chat), ``note`` (an empty list's sentence). The keyboard stops on every
row but a note (``stops``).
"""

from .ui_text import iface_, n_

MARGIN = 8
WIDTH = 400
SEARCH = 30
BANNER = 34
HEADER = 44
FOOTER = 32
HEIGHTS = {"new": 36, "chat": 46, "rename": 58, "confirm": 76, "deleted": 46, "note": 34}
ACTIONS = {"new": "popup_conversations_new", "chat": "popup_conversations_open",
           "deleted": "popup_conversations_restore"}


def shown_title(record):
    """A chat's title as the popover draws it: its name or first request as written (the canvas shapes it), or
    "New chat" in the interface language."""
    from .sessions import UNTITLED
    return iface_(UNTITLED) if record.get("title") == UNTITLED else record.get("title") or ""


def rows(records, matches, popup, deleted=()):
    """The rows of the open page. ``records``: the chat list, newest first; ``matches``: ``[(record, snippet)]``
    for the current query (None without one); ``deleted``: Recently Deleted entries (``conversations.entries``)."""
    if popup.get("page") == "deleted":
        found = [{"kind": "deleted", "id": item["record"]["id"], "record": item["record"], "deleted": item["deleted"]}
                 for item in deleted]
        return found or [{"kind": "note", "id": None, "label": n_("Nothing was deleted recently.")}]
    output = [] if matches is not None else [{"kind": "new", "id": None}]
    listed = matches if matches is not None else [(record, None) for record in records]
    renaming, confirming = (popup.get("rename") or (None,))[0], popup.get("confirm")
    for record, snippet in listed:
        kind = "rename" if record["id"] == renaming else "confirm" if record["id"] == confirming else "chat"
        output.append({"kind": kind, "id": record["id"], "record": record, "snippet": snippet})
    if matches is not None and not listed:
        output.append({"kind": "note", "id": None, "label": n_("No chats match your search.")})
    return output


def stops(found):
    return [index for index, row in enumerate(found) if row["kind"] != "note"]


def place(found, width, height, *, banner=False, header=False, footer=False, scroll=0):
    """Where everything goes for a chat canvas of ``width`` x ``height``: ``rect`` (the popover), ``search``,
    ``banner``, ``header``, ``footer`` (or None), ``list_rect`` (the clipped list), ``rows`` (one rect per row, at
    the clamped ``scroll``), ``scroll`` and ``max_scroll``."""
    w = max(1, min(WIDTH, width - 2 * MARGIN))
    content = sum(HEIGHTS[row["kind"]] for row in found)
    chrome = 10 + SEARCH + 8 + (BANNER + 6 if banner else 0) + (HEADER if header else 0) + (FOOTER if footer else 0) + 8
    h = max(1, min(height - 2 * MARGIN, chrome + content))
    x, top = MARGIN, height - MARGIN
    y = top - h
    cursor = top - 10 - SEARCH
    geometry = {"rect": (x, y, w, h), "search": (x + 9, cursor, w - 18, SEARCH)}
    cursor -= 8
    if banner:
        geometry["banner"] = (x + 6, cursor - BANNER, w - 12, BANNER)
        cursor -= BANNER + 6
    if header:
        geometry["header"] = (x + 5, cursor - HEADER, w - 10, HEADER)
        cursor -= HEADER
    bottom = y + 8 + (FOOTER if footer else 0)
    geometry["footer"] = (x + 5, y + 6, w - 10, FOOTER - 4) if footer else None
    visible = max(0, cursor - bottom)
    maximum = max(0, content - visible)
    scroll = max(0, min(scroll, maximum))
    placed, top_y = [], cursor + scroll
    for row in found:
        rh = HEIGHTS[row["kind"]]
        placed.append((x + 5, top_y - rh, w - 10, rh))
        top_y -= rh
    geometry.update(list_rect=(x + 5, bottom, w - 10, visible), rows=placed, scroll=scroll, max_scroll=maximum)
    return geometry


def reveal(geometry, index):
    """The scroll that brings row ``index`` into view (the keyboard's cursor), else the current one."""
    _, ry, _, rh = geometry["rows"][index]
    _, ly, _, lh = geometry["list_rect"]
    scroll = geometry["scroll"]
    if ry < ly:
        scroll += ly - ry
    elif ry + rh > ly + lh:
        scroll -= ry + rh - ly - lh
    return max(0, min(scroll, geometry["max_scroll"]))


def buttons(rect):
    """A chat row's Rename and Delete buttons (26 px squares at its trailing end), in that order."""
    rx, ry, rw, rh = rect
    side, gap = 26, 4
    delete = (rx + rw - side - 8, ry + (rh - side) / 2, side, side)
    rename = (delete[0] - side - gap, delete[1], side, side)
    return rename, delete


def confirm_buttons(rect):
    """The Delete question's Cancel and Delete buttons, on its lower line at the trailing end."""
    rx, ry, rw, _ = rect
    delete = (rx + rw - 8 - 76, ry + 8, 76, 26)
    cancel = (delete[0] - 6 - 76, ry + 8, 76, 26)
    return cancel, delete
