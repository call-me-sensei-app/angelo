# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cached hidden approval labels and their display-only launcher route; no approvals or provider work."""
_items = ()  # (session id, call id, label), refreshed by the existing launcher watch.
_rects = {}  # Window pointer -> the last drawn pill in window pixels.
_size = None


def refresh():
    """Main-thread pump/watch: scan once here, never from drawing or pointer events."""
    global _items
    from . import approval_wait, chat_toggle, ui
    active = ui._sessions.active if ui._sessions else None
    controllers = approval_wait.unseen(tuple(ui._controllers.values()), active, chat_toggle.chat_shown())
    items = tuple((c.state["id"], approval_wait.awaiting(c).id, label(c, active)) for c in controllers)
    changed = items != _items
    _items = items
    if changed:
        _rects.clear()
    return changed


def label(controller, active_id):
    """Another conversation's own displayed title prefixes the card title, in reading order."""
    from . import approval_wait, chat_titles
    from .ui_text import join
    text = approval_wait.label(controller)
    if controller.state.get("id") != active_id and controller.state.get("title"):
        return join((chat_titles.session_title(controller.state), text))
    return text


def first():
    """The cached first unseen chat; reads no scene data and changes no call."""
    return _items[0] if _items else None


def clear_hidden_hits():
    """A hidden canvas has no live hits or keyboard focus when its area is reused."""
    import sys
    view = sys.modules.get(__package__ + ".chat_view")
    for state in getattr(view, "_views", {}).values():
        state.update(hits=[], blur_request=True, focused=False)
        for key in ("focus", "pressed", "pressed_approval_review", "pressed_generation"):
            state.pop(key, None)


def select(session_id=None):
    """Reread the real card at click time, select its chat and request a shielded reveal; never approve it."""
    from . import approval_wait, ui
    found = first()
    identity = session_id or (found[0] if found else None)
    controller = ui._controllers.get(identity)
    if controller is None:
        return False
    waiting = approval_wait.awaiting(controller)
    if waiting is None:
        return False
    try:
        ui.show_session(identity)
    except (RuntimeError, ValueError):
        return False  # A conversation removed since the drawn notification has no authority.
    approval_wait.request_reveal(identity, waiting, shield=True, controller=controller)
    return True


def hit(window, x, y):
    """Whether a real cached wait's last drawn pill owns this window-pixel point."""
    rect = _rects.get(window.as_pointer()) if _items else None
    return bool(rect and rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3])


def draw(window, region, cx, cy, radius, scale):
    """Beside the raven toward the view centre; width is measured only when its label/scale changes."""
    global _size
    found = first()
    pointer = window.as_pointer()
    _rects.pop(pointer, None)
    if found is None:
        return
    from .card_host import _View
    from .card_kit import AMBER, PANEL, TEXT
    from .chat_view import elide, measure
    view = _View()
    text = found[2]
    gap, pad, height = 12 * scale, 12 * scale, 32 * scale
    left = cx < region.width / 2
    room = region.width - cx - radius - gap - pad if left else cx - radius - gap - pad
    if room < 60 * scale:
        return
    key = (text, scale, room)
    if _size is None or _size[0] != key:
        shown = elide(text, max(0, room - 2 * pad), 12 * scale)
        _size = (key, shown, min(room, measure(shown, 12 * scale) + 2 * pad))
    _key, shown, width = _size
    x = cx + radius + gap if left else cx - radius - gap - width
    y = cy - height / 2
    view.shape(x, y, width, height, PANEL, 12 * scale)
    view.text(shown, x + pad, y + 10 * scale, 12 * scale, TEXT)
    dot = 8 * scale
    view.shape(cx + radius / 2, cy + radius / 2, dot, dot, AMBER, dot / 2)
    _rects[pointer] = (region.x + x, region.y + y, width, height)
