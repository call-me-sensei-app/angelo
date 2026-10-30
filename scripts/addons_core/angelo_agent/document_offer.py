# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a restored chat waiting for its own file says, and its offer to continue on the file that carries its id.

A chat waits for its file (``document_identity.restore``): nothing binds it to another document. When the open
document carries the chat's id but is not its document (another path, or another file at its path: a copy, the
file moved, or rewritten by another tool; no handler can tell these apart), the chat asks the person whether to
continue it there (``OFFER``), and only the person's Continue moves it (``continue_with_open_file``; the maintainer,
break-it round 2: "a chat never binds to a different path on its own").

* The offer **stands** while the chat waits and the open document is still the one it named (its id, path and file:
  a Save As, a Restore that moved it, or another load withdraw it). Only a standing offer draws Continue, on every
  card that names the open file, wherever that card is in the transcript (``chat_notices``), and a request the
  waiting chat refuses says the offer again (``awaiting``: its error row is the same card), so the person is never
  left with only "Open stool.blend" for a file that cannot be opened.
* A card is posted at every load of that file (a reopen offers again); a click on a Continue that no longer stands
  says so in a plain row (``STALE``) and changes nothing.

Main thread. No bpy at import.
"""

import hashlib
import os
import unicodedata

from . import document_identity as identity
from .ui_text import n_

OFFER = n_("This chat worked on {old}. Continue it with {new}?")   # A card with Continue (chat_notices).
# Its own path came back in a file that cannot confirm it (``document_identity._await``): one file, named once.
CHANGED = n_("{file} changed outside Angelo since this chat last worked on it. Continue this chat with it?")
STALE = n_("That offer no longer applies: the file it named is not the one open now.")
# The person's row and the model's fact once Continue moved the chat (``continue_with_open_file``).
CONTINUED = n_("This chat now works on {file}.")
CONTINUED_FACT = ("The person continued this chat with '%s', the same document moved or changed outside Angelo; "  # i18n: model-facing fact, never shown
                  "re-read the scene before relying on earlier results.")
LABEL = 60   # Characters a file's label keeps at most.
ELLIPSIS = "\u2026"


def person_only(message):
    """Whether a stored row is one of these cards or refusals (an offer, a withdrawn one): the person's UI, never a
    model's context (``routing.handoff``, ``external_apps``), whatever made the row (a card, a refused request)."""
    recipe = message.get("i18n") if isinstance(message, dict) else None
    return isinstance(recipe, (list, tuple)) and bool(recipe) and recipe[0] in (OFFER, CHANGED, STALE)


def standing(chat):
    """Whether ``chat``'s offer stands: it waits, and the open document is still the one its offer named."""
    record, offered = getattr(chat, "awaited_file", None), getattr(chat, "offered_file", None)
    return (record is not None and offered is not None and list(offered) == list(identity._open[1:])
            and identity.elsewhere(record, *identity._open[1:]))


offered = standing   # chat_notices' name for it.


def _clean(text):
    """Control characters (never a name's own text) as U+FFFD."""
    return "".join("\ufffd" if unicodedata.category(char) == "Cc" else char for char in text)


def _window(text, other, size):
    """At most ``size`` characters of ``text``, cut in the middle, keeping where it first differs from ``other``."""
    if len(text) <= size:
        return text
    at = next((index for index, (a, b) in enumerate(zip(text, other)) if a != b), min(len(text), len(other)))
    start = max(0, min(at - size // 2, len(text) - size))
    piece = text[start:start + size]
    if start:
        piece = ELLIPSIS + piece[1:]
    if start + size < len(text):
        piece = piece[:-1] + ELLIPSIS
    return piece


def _split(path):
    return [part for part in os.path.normpath(path).split(os.sep) if part]


def labels(old, new):
    """The two files' labels, told apart within ``LABEL`` characters: their names when those differ, else the
    trailing folders down from the first one that differs, with the folders between them as an ellipsis when it is
    too long. The differing folder (around where it differs) and the file's name are always kept."""
    first, second = _split(old), _split(new)
    if not first or not second:
        return _clean(_window(old or "?", new, LABEL)), _clean(_window(new or "?", old, LABEL))
    if first[-1] != second[-1]:
        return _clean(_window(first[-1], second[-1], LABEL)), _clean(_window(second[-1], first[-1], LABEL))
    depth = 2
    while depth < max(len(first), len(second)) and first[-depth:] == second[-depth:]:
        depth += 1

    def label(path, parts, other):
        whole = depth >= len(parts)
        lead = (os.path.normpath(path)[:len(os.path.normpath(path)) - len(os.sep.join(parts))] if whole
                else ELLIPSIS + os.sep)
        differing, between, name = parts[-min(depth, len(parts))], parts[-min(depth, len(parts)) + 1:-1], parts[-1]
        if depth == 1 or len(parts) == 1:
            return _window(lead + name, lead + name, LABEL)
        full = lead + os.sep.join([differing] + between + [name])
        if len(full) <= LABEL:
            return full
        middle = [ELLIPSIS] if between else []
        room = LABEL - len(lead) - len(os.sep) * (1 + len(middle)) - len(ELLIPSIS) * len(middle)
        name_room = min(len(name), max(12, room // 2))
        mark = other[-min(depth, len(other))] if other else ""
        return lead + os.sep.join([_window(differing, mark, max(4, room - name_room))] + middle
                                  + [_window(name, name, name_room)])
    return _clean(label(old, first, second)), _clean(label(new, second, first))


def _token(old, new):
    """Which offer a card names, by the two full paths (never shown, never a path): a card for another pair of files
    with the same labels never draws this offer's Continue."""
    return hashlib.sha256((old + "\0" + new).encode("utf-8", "surrogatepass")).hexdigest()[:24]


def card(chat):
    """(template, fields) of ``chat``'s standing offer, or None: its own path in a file that cannot confirm it
    (``CHANGED``, the file named once), else two files (``OFFER``); its ``offer`` token is in the fields."""
    if not standing(chat):
        return None
    old, new = chat.awaited_file["path"], identity._open[2]
    token = _token(old, new)
    if identity._canonical(old) == identity._canonical(new):
        return CHANGED, {"file": labels(old, new)[1], "offer": token}
    first, second = labels(old, new)
    return OFFER, {"old": first, "new": second, "offer": token}


def names(chat):
    """The card's fields for ``chat``'s standing offer (with its ``offer`` token), or None."""
    found = card(chat)
    return found[1] if found is not None else None


def post(chat, ui):
    """A load (or ``restore``) left ``chat`` waiting: offer the open document when it carries the chat's id, once per
    load; otherwise withdraw an earlier offer."""
    record, document = getattr(chat, "awaited_file", None), list(identity.open_document(ui))
    if record is None or not identity.elsewhere(record, *document):
        chat.offered_file = None
        chat.version += 1
        return
    chat.offered_file = document
    if getattr(chat, "_offer_posted", None) == ui._document_serial:
        return
    from .ui_text import notice
    chat._offer_posted = ui._document_serial
    template, fields = card(chat)
    chat.emit("message", {"role": "error", **notice(template, **fields)})


def continue_with_open_file(chat, ui):
    """The person's Continue: ``chat`` works on the open document from now on (its record moves there). A Continue
    that no longer stands changes nothing and says so (``STALE``)."""
    if not standing(chat) or list(identity.open_document(ui)) != list(chat.offered_file):
        from .ui_text import notice
        chat.emit("message", {"role": "system", **notice(STALE)})
        chat.version += 1
        return False
    fields = names(chat)
    label = fields.get("new") or fields.get("file") or "?"
    record, chat.offered_file = chat.awaited_file, None
    from .ui_text import model_notice
    chat.emit("message", {"role": "system", **model_notice(CONTINUED_FACT % label, CONTINUED, file=label)})
    from .worker_resume import cancel
    cancel(chat.state["id"])   # Never a batch from before the load (its workers ended there: after_load).
    identity._bind(chat, ui, [record["scene"]])
    return True


def awaiting(chat, translate):
    """``translate``'s sentence for a restored chat waiting for its file (``document_scene``'s refusals and the
    composer's hint): its standing offer, else which file to open (or that it was never saved); else None."""
    record = getattr(chat, "awaited_file", None)
    if record is None:
        return None
    found = card(chat)
    if found is not None:
        return translate(found[0], **found[1])
    if not record["path"]:
        return translate(identity.UNSAVED)
    return translate(identity.AWAITING, file=identity._display(record["path"]))


def refused(operator, chat):
    """A window action that would change the open document for ``chat`` (Restore): refused while it waits."""
    from .ui_text import rpt_
    why = awaiting(chat, rpt_) if chat is not None else None
    if why is not None:
        operator.report({'ERROR'}, why)
    return why is not None
