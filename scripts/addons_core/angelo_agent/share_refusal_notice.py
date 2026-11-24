# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The network share an agent script was refused, named to the person and never to a model.

The confinement refuses a script's access to a network share the person hasn't connected
(doc/angelo/script_confinement.rst). The agent reads only the plain network refusal (``script_confinement.NETWORK``),
never the share's name, because a name it read could lead it to ask the person to connect a share the script chose.
The person is told which share it was here:

* ``_angelo_guard.network_shares_refused()`` (the app's C module; only Windows refuses by a share) keeps the last 16
  refusals as ``(serial, share)`` pairs, latest last; ``serial`` counts every refusal in the process, repeats included.
  A later C module adds the refused code's call key as a third element (iso-core, planned for 2.1.x), read as a pair
  until attribution uses it.
  It is refused while a script runs (confined), and this module never asks then.
* ``script_confinement.run_confined`` (every agent script's run) reads the refusals around the run
  (``attributed``): those made while it ran are owed to the chat of its call key (``<chat id>/<call id>``,
  ``script_admission.prepare``), however many chats' scripts run before any chat reads its events. A refusal made
  outside a run (work a script left for later: a timer, a handler) is owed to no chat yet. Attribution is by time,
  so work another chat's script left that runs during this run (a handler this script's edits trigger) is named as
  this script's (review of 04c159c S2; the person is the same, only the tab differs). The C module keeps no call
  key per refusal in 2.1.0; its third element (2.1.x) will give the refused code's own chat.
* After each call's activity changes (``activity_rows.store``) and at each turn's end (``turn_end.end_turn_calls``),
  a chat names what it is owed, and what no chat is owed (``note``): a card per share (``chat_notices._OWN``), the
  latest ``SHOWN`` of them; the C module's console line names every one. Each refusal is named once in the process;
  one refused again later is named again.
* The row is ``person_only``: no handoff, external app or model summary reads it (``document_offer.person_only``).
  The share's name is the script's choice, so it is shown bounded (``NAME``) with control characters replaced.

No ``bpy``: host-testable.
"""

import contextlib
import threading
import unicodedata

from .ui_text import n_, notice

ONE = n_("An agent script was refused the network share “{share}”. Scripts can't use a network share you haven't "
         "connected. If it's yours, map it to a drive letter or open your project from it.")
TEMPLATES = (ONE,)
SHOWN = 3       # Cards a check adds at most: the latest shares, one card each (the console lists every one).
NAME = 80       # Characters a share's name keeps at most.
ELLIPSIS = "…"

_lock = threading.Lock()
_named = [0]     # The highest serial read: each refusal is collected once in the process.
_owed = {}       # chat id -> shares its scripts were refused, latest last, not yet named.
_unowned = []    # Shares refused outside any run, not yet named.


def _guard():
    from . import script_confinement
    return script_confinement._guard


def refusals():
    """``[(serial, share)]`` the C module reports, or ``[]``: no C module (host tests), a build without the call, a
    script running on this thread (it would be refused), or a reply that isn't the pairs it promises."""
    guard = _guard()
    read = getattr(guard, "network_shares_refused", None)
    if read is None:
        return []
    try:
        if guard.confined():
            return []
        return [(item[0], item[1]) for item in read() if isinstance(item, tuple) and len(item) in (2, 3)
                and type(item[0]) is int and isinstance(item[1], str) and item[1]]   # A third: a later call key.
    except Exception:   # noqa: BLE001 - a refused or failed read, or one that isn't a list, names nothing.
        return []


def shown_name(share):
    """``share`` as a card shows it: control, format and line or paragraph separator characters as U+FFFD, at most
    ``NAME`` characters. The sentence quotes it, as the folder card quotes its name."""
    text = "".join("�" if unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") else char for char in share)
    return text if len(text) <= NAME else text[:NAME - 1] + ELLIPSIS


def _add(shares, more):
    for share in more:
        if share in shares:
            shares.remove(share)
        shares.append(share)


def _collect(shares):
    """Add to ``shares`` the refusals not read yet (latest last, each share once)."""
    pairs = refusals()
    with _lock:
        fresh = [share for serial, share in pairs if serial > _named[0]]
        if fresh:
            _named[0] = max(serial for serial, _share in pairs)
            _add(shares, fresh)


@contextlib.contextmanager
def attributed(call_key):
    """Around one agent script's run: what it was refused is owed to its call key's chat (the key before ``/``); a
    run without one (no chat) owes it to none."""
    _collect(_unowned)   # Refused before this run: not this script's.
    try:
        yield
    finally:
        chat = call_key.split("/", 1)[0] if isinstance(call_key, str) else ""
        with _lock:
            target = _owed.setdefault(chat, []) if chat else _unowned
        _collect(target)


def unnamed(chat_id):
    """The shares owed to ``chat_id`` and to no chat, latest last; taken (each is named once)."""
    _collect(_unowned)
    with _lock:
        shares = list(_owed.pop(chat_id, ()))
        _add(shares, _unowned)
        _unowned.clear()
    return shares


def row(share):
    """The chat's card for one refused ``share``: a person-only notice, its name quoted by each language's sentence
    (one card per share, so no list of names needs quoting)."""
    return {"role": "system", "notice": True, "person_only": True, **notice(ONE, share=shown_name(share))}


def note(controller):
    """Name in ``controller``'s chat the shares owed to it and to no chat (a card); True when it added one.

    A chat hosted in a worker Blender (``hosted_in_worker``, its lead's mirror) names nothing here: its scripts run in
    the worker's process, which keeps their record, and its state is replaced by the worker's journal at each read, so
    a card here would take another chat's refusal and then vanish (review of 04c159c S1)."""
    if getattr(controller, "hosted_in_worker", False):
        return False
    shares = unnamed(controller.state["id"])
    if not shares:
        return False
    with controller.lock:
        messages = controller.state["messages"]
        for share in shares[-SHOWN:]:
            messages.append(row(share))
            controller._touched(len(messages) - 1)
    return True
