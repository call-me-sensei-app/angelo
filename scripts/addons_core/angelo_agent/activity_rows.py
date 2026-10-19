# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A tool call's row in the chat (``role: activity``): stored, saved, handed off and drawn without the key.

A call's input can repeat the connection's API key (a script that sets it, a
plan whose operations carry it), and so can a result the runtime wrote. The
call runs with what it was given; its row is the transcript's copy, which the
chat saves, hands off to a later context (``routing.handoff``) and draws when
the row is opened (``chat_view``). That copy passes through the chat's own
redaction (``Controller.redact``, as replies, thinking and errors do), so the
key becomes ``[REDACTED]`` in every string of the row, mapping keys included.

Only the row is redacted: the pending call and what the model received in the
turn keep what they had (``Controller.finish_tool`` already redacts the result
it returns). Without a bound key a row stores what the event carried, as
before; with one, a value that holds no key is stored as it came. Binding a key
(``bind_key``) redacts it from every row, every saved result record and the
chat's kept copies, and the next save rewrites the journal.
"""

import json
from collections.abc import Mapping

# The fields an ``activity`` event may set on its row (Controller._pump_events).
FIELDS = ("status", "result", "tool", "arguments", "parent_call", "collaborator")


def _holds(value, key):
    """Whether ``key`` occurs in a string anywhere in ``value`` (True when that cannot be told cheaply)."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        return True
    # JSON escapes each character on its own, so the key's encoded form occurs exactly where the key does.
    return json.dumps(key, ensure_ascii=False)[1:-1] in text


def _redacted(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {(redact(name) if isinstance(name, str) else name): _redacted(item, redact) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_redacted(item, redact) for item in value]
        return items if isinstance(value, list) else tuple(items)
    return value


def redacted(controller, value):
    """``value`` as a row may hold it: a redacted copy when it holds the bound key, else ``value`` itself."""
    key = controller.api_key
    return _redacted(value, controller.redact) if key and _holds(value, key) else value


def store(controller, data):
    """Record one ``activity`` event on its call's row, adding the row on the call's first event."""
    fields = redacted(controller, {name: data[name] for name in FIELDS if name in data})
    identity = data["id"]
    with controller.lock:
        messages = controller.state["messages"]
        if identity not in controller._activities:
            controller._activities[identity] = len(messages)
            messages.append({"role": "activity", "call_id": identity, "text": "", "tool": fields.get("tool", ""),
                             "arguments": fields.get("arguments", {}), "status": "queued"})
        messages[controller._activities[identity]].update(fields)
        controller.store.touch(controller._activities[identity])  # A call can end after its request (session_writer).


def bind_key(controller, api_key):
    """Bind the connection's key (``Controller.api_key``); a new one is also redacted from what the chat holds.

    A chat saved before the key was bound can hold it in any row (a reply, a thinking summary, a call's input
    or result) and in the result records replayed to the model. Every string of them is redacted, mapping keys
    included. The next save then rewrites the whole journal when memory held the key, and also when it did not
    but the file is not exactly memory's state: an earlier record can hold what memory no longer does (the head
    of a thinking summary cut to its last 16,000 characters, an earlier version of a reply, a result record
    evicted from the 256 kept). A file that is one base line holding memory's state (``SessionStore.current``,
    as a chat just opened and saved is) holds no piece of a key memory does not, so it is not rewritten again.
    The chat's kept unreadable copies are redacted too (``session_kept.redact_kept``). Binding the same key
    again changes nothing.
    """
    changed = bool(api_key) and api_key != controller._api_key
    controller._api_key = api_key
    if changed:
        with controller.lock:
            held = False
            for message in controller.state["messages"]:
                clean = redacted(controller, message)
                if clean is not message:
                    message.clear()  # The same row object: the controller's indexes and the journal's know it.
                    message.update(clean)
                    held = True
            results = controller.state["results"]
            for identity, record in list(results.items()):
                clean = redacted(controller, record)
                if clean is not record:
                    results[identity] = clean  # A new record, never a change in place (session_writer).
                    held = True
            if held or not controller.store.current(controller.state):
                controller.store.invalidate()  # Earlier records may hold the key: the next save rewrites the file.
        controller.store.redact_kept(api_key)  # File work, outside the chat's lock.
