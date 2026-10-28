# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's events (``Controller.emit``) reach its main thread in order, and none is ever dropped or blocks its sender.

The main thread drains a chat's event queue in ``pump_events``. While it is away (a lead's long script or render, or
any long call), a chat keeps streaming: before, a full queue blocked the provider's thread 2 s per event and then
dropped the event ("Event queue full"), and a dropped ``finished`` left a worker busy for ever (break-it 19C M-3).

Now, past ``MARK`` of the queue (or while anything is held), events wait in the chat's own ordered ``_held_events``;
consecutive streamed fragments of one item (``delta``, ``thinking``: the chat appends each to its row) join into one,
so a long stream holds a handful of events, never thousands. ``flush`` (the main thread, as it starts draining) moves
what is held back into the queue, oldest first, up to ``MARK``. Every sender takes the chat's own lock, so a chat's
events keep one order. Standard library only and no ``bpy``.
"""

import threading

JOINED = frozenset({"delta", "thinking"})   # Appended to their item's row when drawn: consecutive ones join.
MARK = 2   # Hold once the queue is 1/MARK full: the rest of it is room for whatever the main thread sends meanwhile.


def _lock(chat):
    found = chat.__dict__.get("_events_lock")
    return found if found is not None else chat.__dict__.setdefault("_events_lock", threading.Lock())


def _full(events):
    return bool(events.maxsize) and events.qsize() * MARK >= events.maxsize


def put(chat, kind, data):
    """``chat``'s event, never blocking and never dropped: into its queue, else held in order (module docstring)."""
    events = chat.events
    with _lock(chat):
        held = chat.__dict__.setdefault("_held_events", [])
        if not held and not _full(events):
            events.put_nowait((kind, data))
            return
        last = held[-1] if held else None
        if (last is not None and kind in JOINED and last[0] == kind and "replace" not in data
                and "replace" not in last[1] and _same(last[1], data)):
            last[1]["text"] = str(last[1].get("text", "")) + str(data.get("text", ""))
        else:
            held.append((kind, dict(data)))


def _same(first, second):
    """The same item of the same request: every field but its text (and a first fragment's time) is equal."""
    return ({k: v for k, v in first.items() if k not in ("text", "time")}
            == {k: v for k, v in second.items() if k not in ("text", "time")})


def flush(chat):
    """Main thread, before it drains ``chat``'s queue: held events join it, oldest first, up to ``MARK``."""
    held = chat.__dict__.get("_held_events")
    if not held:
        return
    with _lock(chat):
        while held and not _full(chat.events):
            chat.events.put_nowait(held.pop(0))
