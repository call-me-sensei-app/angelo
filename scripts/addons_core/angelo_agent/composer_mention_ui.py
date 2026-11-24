# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's @-mention list (``composer_mentions``): when it opens, its keys, the pick, and how it is drawn.

``refresh`` runs after every edit of the draft (``chat_view``): the list is open while the caret ends an ``@`` word
and some item matches; it is never open on the Rules page (``rules_sheet``) or in a worker's chat. While it is open,
``key`` takes Up, Down, Return, Tab and Escape (Escape closes the list and keeps editing); every other key is the
composer's. A pick (``pick``: Return, Tab or a click on a row) replaces the ``@`` word through the composer's own
insert, so one Undo takes it back, and remembers the item for Send. Main thread.
"""

from . import composer_mentions, rules_sheet
from .ui_text import iface_, n_

KIND_LABELS = {"OBJECT": n_("Object"), "COLLECTION": n_("Collection"), "MATERIAL": n_("Material")}
ROW = 26
ACTION = "mention_pick"


def _scene(controller):
    from . import document_scene, worker_routing
    if worker_routing.worker_snapshots(controller):
        return None
    return document_scene.scene_of(controller)


def refresh(state, line, controller, items=None):
    """Open, update or close the list for the draft as it is now."""
    found = None if rules_sheet.is_open(state) or line is None else composer_mentions.query_at(line.body, line.current_character)
    if found is None:
        state.pop("mention", None)
        return
    if items is None:
        try:   # The list never costs a keystroke: a chat whose scene can't be read just shows none.
            import bpy
            scene = _scene(controller)
            items = composer_mentions.scene_items(bpy, scene) if scene is not None else []
        except (AttributeError, ReferenceError, RuntimeError, ImportError):
            items = []
    rows = composer_mentions.candidates(items, found[1])
    if not rows:
        state.pop("mention", None)
        return
    previous = state.get("mention") or {}
    cursor = previous.get("cursor", 0) if previous.get("start") == found[0] else 0
    state["mention"] = {"start": found[0], "query": found[1], "rows": rows, "cursor": min(cursor, len(rows) - 1)}


def pick(state, line, controller, index, insert):
    """Put row ``index`` in the draft (``insert(state, line, text)``: the composer's own, one Undo step)."""
    mention = state.pop("mention", None)
    if mention is None or not 0 <= index < len(mention["rows"]):
        return False
    kind, name, uid = mention["rows"][index]
    state["selection"] = (mention["start"], line.current_character)   # The @ word is replaced, as a typed selection is.
    insert(state, line, "@" + name + " ")
    composer_mentions.remember(controller, kind, name, uid)
    return True


def key(event, state, line, controller, insert):
    """True when the open list took ``event``."""
    mention = state.get("mention")
    if mention is None or event.value != 'PRESS':
        return False
    if event.type in {'UP_ARROW', 'DOWN_ARROW'}:
        step = -1 if event.type == 'UP_ARROW' else 1
        mention["cursor"] = (mention["cursor"] + step) % len(mention["rows"])
        return True
    if event.type in {'RET', 'NUMPAD_ENTER', 'TAB'} and not event.shift:
        return pick(state, line, controller, mention["cursor"], insert)
    if event.type == 'ESC':
        state.pop("mention", None)
        return True
    return False


def draw(view, state, left, bottom, width):
    """The list above the composer's top-left (``bottom``), newest match nearest the draft. Hits: ``ACTION``."""
    from . import ui_tokens as tk
    mention = state.get("mention")
    if not mention:
        return
    rows = mention["rows"]
    w = min(320, width - left - 16)
    h = ROW * len(rows) + 8
    view.shape(left, bottom, w, h, tk.HIGHLIGHT, 9)
    view.shape(left + 1, bottom + 1, w - 2, h - 2, tk.MENU, 8)
    for index, (kind, name, _) in enumerate(rows):
        y = bottom + h - 4 - ROW * (index + 1)
        if index == mention["cursor"]:
            view.shape(left + 4, y + 1, w - 8, ROW - 2, tk.RAISED, 6)
        kind_label = iface_(KIND_LABELS[kind])
        kind_w = view.measure(kind_label, 11)
        view.text(view.elide(name, w - kind_w - 36, 12.5), left + 12, y + 8, 12.5, tk.TEXT, shaped=False)
        view.text(kind_label, left + w - 12 - kind_w, y + 8, 11, tk.MUTED)
        state["hits"].append(((left + 4, y, w - 8, ROW), ACTION, index))
