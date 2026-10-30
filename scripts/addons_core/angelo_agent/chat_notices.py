# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turn-level notices in the chat transcript: an error row is a notice card, never something that looks like a reply.

API (``chat_view.draw`` calls these for every ``error`` row):
    block(message, key, latest, ui, controller, width, size, line_height, expanded) -> the transcript block
    draw(state, ui, controller, message, key, top, bottom, width, size, line_height)

Each card is the kit's inline notice (``ui_kit_status.notice``). Its kind comes from ``notice_kinds``: sign-in
expired, not enough credits, usage limit, rate limit, model unavailable, conversation too long (a warning mark),
provider busy, can't reach the provider (an error mark); "Something went wrong" only for an unknown error. It has
a plain-language title, one sentence, the action pills that exist for it (only on the latest row: the sign-in
or connect row for an expired sign-in, Switch connection and Connect/Use another subscription otherwise, from
``chat_welcome``) and the provider's own text in a collapsed Details disclosure (the chat's ``expand`` action
with ``("notice", key)``). An error row whose text
is one of Angelo's own translated sentences (it carries an ``i18n`` recipe) is already plain language: it is the
card's title and has no Details. The stored row is never changed; everything here is display only.
"""

import re

from . import chat_welcome, document_offer, first_run, notice_kinds, ui_kit_status
from .ui_text import iface_, n_

MARGIN = 16       # The card's inset from the transcript's edges.
GAP = 12          # Space above the card, between transcript rows.
_DETAILS = "notice_details"
# Exception names, stack lines, JSON, addresses, enum capitals, raw ids: such text goes under Details.
_TECHNICAL = re.compile(r"Traceback|\b[A-Z]\w*(?:Error|Exception)\b|[{}\[\]<>]|\bline \d+|0x[0-9a-fA-F]+|\b[A-Z0-9_]{6,}\b"
                        r"|[0-9a-f]{8}-[0-9a-f]{4}-")


def technical(text):
    """True when a provider's message is engineering detail rather than a sentence a person can act on."""
    text = text.strip()
    return not text or len(text) > 220 or "\n" in text or bool(_TECHNICAL.search(text))


def _view():
    from . import chat_view
    return chat_view


# kind -> (mark, title, sentence); every string translated where drawn. "usage" and "unknown" are built below.
_KNOWN = {
    "signin": ("warn", n_("Sign-in expired"), n_("Sign in again to keep going.")),
    "credits": ("warn", n_("Not enough credits"), n_("Add credits with your provider, or switch connection.")),
    "rate": ("warn", n_("Too many requests"), n_("The provider is limiting requests. Wait a moment, then try again.")),
    "model": ("warn", n_("Model unavailable"), n_("This connection can't use the selected model. Choose another.")),
    "context": ("warn", n_("Conversation too long"), n_("Start a new chat, or remove large attachments.")),
    "overloaded": ("error", n_("Provider is busy"), n_("The provider is overloaded right now. Try again in a moment.")),
    "offline": ("error", n_("Can't reach the provider"), n_("Check your internet connection, then try again.")),
}
_SWITCH = {"usage", "credits", "rate", "model", "overloaded", "offline"}  # Kinds Switch connection helps.


def _content(message):
    """(notice kind, mark, title, sentence, details) for an error row, every string translated for display."""
    from . import console
    kind = notice_kinds.kind(message)
    raw = str(message.get("text") or "")
    own = message.get("i18n")  # Angelo's own sentence: plain language, translated at display time, no Details.
    if kind == "usage":
        reset = first_run.reset_time(raw)
        sentence = (iface_("Resets {time}. Switch connection to keep going.", time=reset) if reset
                    else iface_("Switch connection to keep going, or wait for the limit to reset."))
        return kind, "warn", iface_("Usage limit reached"), console.display_text(message) if own else sentence, None if own else raw
    if kind in _KNOWN:
        mark, title, sentence = _KNOWN[kind]
        return kind, mark, iface_(title), console.display_text(message) if own else iface_(sentence), None if own else raw
    if own:   # An offer (document_identity.OFFER) is a question, not an error.
        return kind, "warn" if _offer(message) else "error", console.display_text(message), "", None
    if not technical(raw):  # The provider's own plain sentence (never translated: it is not Angelo's text).
        return kind, "error", iface_("Something went wrong"), raw.strip(), None
    return kind, "error", iface_("Something went wrong"), iface_("This turn stopped before it finished. Try again."), raw


def _offer(message):
    """A waiting chat's question whether to continue on the open file that carries its file's id (``document_offer``)."""
    recipe = message.get("i18n")
    return isinstance(recipe, (list, tuple)) and bool(recipe) and recipe[0] in (document_offer.OFFER,
                                                                             document_offer.CHANGED)


def _offer_stands(message, controller):
    """Its Continue is drawn wherever the card is, while the offer it names stands (never a stale one)."""
    fields = document_offer.names(controller)
    recipe = message.get("i18n")
    return (fields is not None and len(recipe) > 1 and isinstance(recipe[1], dict)
            and recipe[1].get("offer") == fields["offer"])   # By the two full paths, never by the names shown.


def _card(message, key, latest, ui, controller, expanded):
    kind, mark, title, body, details = _content(message)
    items = ()
    if latest and kind == "signin":
        items = chat_welcome.sign_in_items(ui, controller)
    elif latest and kind in _SWITCH:
        items = chat_welcome.notice_items(ui, controller)
    elif _offer(message) and _offer_stands(message, controller):
        items = [(iface_("Continue"), "welcome_continue_file", None)]   # Only the person's click rebinds the chat.
    actions = tuple((action, value, label, "secondary") for label, action, value in items)
    return dict(kind=mark, title=title, body=body, actions=actions, details=details or None,
                expanded=("notice", key) in expanded, details_action=_DETAILS if details else None)


def block(message, key, latest, ui, controller, width, size, line_height, expanded):
    card = _card(message, key, latest, ui, controller, expanded)
    height, _ = ui_kit_status.notice(_view(), MARGIN, 0, width - 2 * MARGIN, 1.0, None, False, **card)
    return "notice_card", dict(message, _notice_card=card), (), height + GAP, key, 0


def draw(state, ui, controller, message, key, top, bottom, width, size, line_height):
    card = message["_notice_card"]
    pointer = dict(state)  # The Details hit is the chat's expand action; its hover state follows it.
    for name in ("hover", "pressed", "focus"):
        if pointer.get(name) == ("expand", ("notice", key)):
            pointer[name] = (_DETAILS, None)
    _, hits = ui_kit_status.notice(_view(), MARGIN, top - GAP, width - 2 * MARGIN, 1.0, pointer, True, **card)
    for rect, action, value in hits:
        state["hits"].append((rect, "expand", ("notice", key)) if action == _DETAILS else (rect, action, value))
