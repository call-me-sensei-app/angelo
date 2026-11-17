# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a how-to card shows in the transcript (learn mode): under a typed step that happened, when it is open.

Learn is the person's choice, per chat: **with Teach me as you work off (the default) no card shows anywhere** and an
opened step shows exactly what it showed before learn existed. ``chat_blocks.build`` calls ``begin`` once per draw of
the transcript, which reads the option from memory and needs no Blender (``learn_option.teaching_now``); while it is
off, ``under`` returns the row untouched before anything else of learn runs (no ``has_card``, no resolver, no layout),
and ``draw_under`` finds no card to draw. Nothing here is a timer or a handler, and no model reads any of it.

With it on, ``chat_rows`` asks ``under`` while it builds a step's row and ``draw_under`` while it draws one. A step has
a card when it is a typed edit (``learn_cards.has_card``) that **happened** (its chip reads Done): a planned,
declined, failed or previewed step did nothing to learn from. The card is open when the step belongs to the chat's
latest request (those steps are kept out of a folded work group, so they are open by themselves) or when the person
opened the step.

Everything here is asked on every draw, so it only reads memory: the option (``learn_option``), the kept card
(``learn_resolver.card``) and the kept layout (``learn_card_draw.plan``). ``state["learn_drawn"]`` records what each
card last drew, for the GUI fixtures; the add-on never reads it.
"""

from . import learn_card_draw, learn_cards, learn_option

GAP = 10            # Under a card, before the step's details or the next row.
MAX_OPENED = 512    # The steps remembered in each of the two sets below (their ids only).
_opened = set()     # The steps whose card the person opened and saw drawn, this session.
_kept_open = set()  # The steps whose card the chat's option kept open and drew, this session.
RESERVED = 18       # Room for a step drawn inside an open work group (chat_rows.INDENT).
_latest = (None, 0, frozenset())   # (id of the chat's rows, how many, the ids of the latest request's rows).
_on = False   # Teach me as you work for the chat on screen, as ``begin`` read it for this draw.
_controller = None   # The chat ``begin`` was given for this draw.


def begin(controller):
    """Read Teach me as you work once for this draw of the transcript (``learn_option``: memory only, and no
    Blender, so the transcript's draw stays importable where Blender is not). Off: no card this draw."""
    global _on, _controller
    _controller = controller
    _on = controller is not None and learn_option.teaching_now(controller.state.get("id", ""))
    return _on


def _latest_request(messages):
    """The ids of the rows after the person's last message. Kept until a row is added."""
    global _latest
    if _latest[0] == id(messages) and _latest[1] == len(messages):
        return _latest[2]
    found = set()
    for message in reversed(messages):
        if message.get("role") == "user" and message.get("request_kind") not in ("angelo_event", "angelo_goal"):
            break
        found.add(id(message))
    _latest = (id(messages), len(messages), frozenset(found))
    return _latest[2]


def taught(message):
    """Whether ``message`` is a row of the latest request of a chat with Teach me as you work on."""
    if not _on or _controller is None:
        return False
    return id(message) in _latest_request(_controller.state["messages"])


def block(message, role, identity, expanded, width, size, line_height):
    """``(height, items, kept open)`` of the card under a step's row, or None when it shows none. ``kept open``: the
    card is open by the chat's option, not by the person's click. None whenever Teach me as you work is off."""
    if not _on or role != "activity" or not learn_cards.has_card(message):
        return None
    opened, kept_open = identity in expanded, False
    if not opened:
        kept_open = taught(message)
        if not kept_open:
            return None
    from . import activity_text, chat_guard, chat_view, learn_resolver
    from .chat_titles import happened
    if not happened(message) or activity_text.step_status(message) != "done":   # Not a preview, nor reverted.
        return None
    steps = chat_guard.call("A step's how-to card", learn_resolver.card, message, default=())   # Never stops the row.
    if not steps:
        return None

    def wrap(value, room, text_size):
        return [line.rstrip() for line, _ in chat_view.wrap(value, room, text_size, chat_view._font)]
    height, items = learn_card_draw.plan(steps, max(120, width - 40 - RESERVED), size, line_height,
                                         chat_view.measure, wrap)
    return height, items, kept_open


def under(message, role, identity, expanded, width, size, line_height, below):
    """``(message, below)`` for ``chat_rows.activity_block``: the row's message carrying its card's layout, and the
    height under the row with the card's added. A card the chat's option keeps open marks its row ``_attached``, so
    a work group never folds it away. Unchanged when the step shows no card, and at once while learn is off."""
    if not _on:
        return message, below
    found = block(message, role, identity, expanded, width, size, line_height)
    if found is None:
        return message, below
    height, items, kept_open = found
    marks = {"_attached": True} if kept_open else {"_learn_opened": True}
    return dict(message, _learn=items, _learn_height=height, **marks), below + height + GAP


def draw_under(view, message, x, top, width, state):
    """Draw the card ``under`` laid out for ``message`` with its top left corner at (``x``, ``top``). Returns the
    height it takes under the row with its gap (0 when the step shows none), for what is drawn below it."""
    if not message.get("_learn_height"):
        return 0
    height, items = message["_learn_height"], message["_learn"]
    seen = _opened if message.get("_learn_opened") else _kept_open
    if len(seen) < MAX_OPENED:
        seen.add(message.get("call_id"))
    wide = max(120, width - 40 - RESERVED)
    learn_card_draw.draw(view, items, x, top, wide, height)
    drawn = state.setdefault("learn_drawn", {})
    if len(drawn) > 128:
        drawn.clear()
    drawn[message.get("call_id")] = {"rect": (x, top - height, wide, height),
                                     "texts": [item[3] if item[0] == "text" else item[5] for item in items
                                               if item[0] in ("text", "cap", "pill")],
                                     "caps": [item[5] for item in items if item[0] == "cap"]}
    return height + GAP


def opened():
    """How many steps' cards the person has opened themselves and seen drawn this session (not those the chat's
    option keeps open). For a lesson's own check that a card was opened; nothing else reads it."""
    return len(_opened)


def kept_open():
    """How many steps' cards Teach me as you work has kept open and drawn this session. For a lesson's own check
    that the option showed a card; nothing else reads it."""
    return len(_kept_open)


def forget():
    global _latest, _on, _controller
    _latest, _on, _controller = (None, 0, frozenset()), False, None
    _opened.clear()
    _kept_open.clear()
