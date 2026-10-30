# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One UI pump of a worker chat's mirror (``MirrorController.pump_events``): its forwarded frames applied in order.

A ``journal`` frame's read of the chat's file runs on the mirror's reader thread (``worker_mirror_reader``): the pump
asks for it and the frames drained after it wait (``behind``) until a later pump puts its state in, then go on in
order. While a read runs, the pump drains nothing more; the queue holds what arrives (``event_queue``, never dropped).
Standard library only and no ``bpy``.
"""

import queue

from . import event_queue, worker_mirror_events as events, worker_mirror_opens as opens, worker_mirror_ops as ops
from . import worker_mirror_reader as reader

BATCH = 256   # Frames drained per pump.


def pump(mirror, lead_kinds):
    """``pump_events``: True when anything shown changed (``lead_kinds``: the frames the lead itself answers)."""
    changed = opens.settle(mirror)   # Every pump, read running or not: an unsettled Approve (worker_mirror_settle).
    drained = []
    while True:
        if reader.waiting(mirror):
            done = reader.take(mirror)
            if done is None:
                break   # Its read still runs: the frames behind it wait for a later pump.
            state, error, drained = done
            changed = ops.applied(mirror, state, error) or changed
        else:
            event_queue.flush(mirror)   # What the reader held while the queue was full joins it first, in order.
            for _ in range(BATCH - len(drained)):
                try:
                    drained.append(mirror.events.get_nowait())
                except queue.Empty:
                    break
            if not any(kind == events.JOURNAL for kind, _data in drained) and events.retry_due(mirror):
                reader.ask(mirror, None, drained)   # A read that failed is tried again.
                drained = []
                continue
        changed, asked = _apply(mirror, drained, lead_kinds, changed)
        drained = []
        if not asked:
            break
    if changed:
        mirror.version += 1
    return changed


def _apply(mirror, drained, lead_kinds, changed):
    """The frames in order up to the last journal frame, whose read is then asked for with the rest behind it.
    ``(changed, asked)``."""
    last_journal = max((i for i, (kind, _data) in enumerate(drained) if kind == events.JOURNAL), default=-1)
    for index, (kind, data) in enumerate(drained):
        if kind in events.RECORDS:
            if callable(mirror.records):
                mirror.records(kind, data)   # Usage of any open is real usage: always written (C8).
        elif kind in lead_kinds:
            changed = lead_kinds[kind](mirror, data) or changed
        elif not opens.admits(mirror, data):
            continue   # A frame of an earlier (or unannounced) open of this chat (C9).
        elif kind == events.JOURNAL:
            seq = data.get("seq")
            if type(seq) is int:
                mirror.journal_seq = max(mirror.journal_seq, seq)
            if index == last_journal:   # The file holds every earlier write: one read per pump.
                records = data.get("records")   # The lead's own rows (its errors) stay where they were.
                reader.ask(mirror, records if type(records) is int else None, drained[index + 1:])
                return changed, True
        elif kind == events.PENDING:
            if mirror.busy and mirror.cancelled:
                continue   # Stopping: the cards drawn before the Stop are gone (C5), and none comes back.
            changed = events.apply_pending(mirror, data) or changed   # Never an older account (audit2 #5).
        elif kind == events.NEWS:
            events.news(mirror, data)
        elif kind == events.SIGNS:
            events.signs(mirror, data)
        elif kind == events.RUNNING:
            events.running(mirror, data)
        elif kind == "turn_facts":   # Rounds and the turn's clock for worker_facts (worker_chat_events).
            from .worker_chat_events import apply_turn_facts
            apply_turn_facts(mirror, data)
        elif events.admit(mirror, kind, data):
            if kind == "message" and data.get("role") == "user":
                ops.took(mirror)   # The worker wrote the Send's own row: it took it, whatever its answer says.
            changed = events.apply(mirror, kind, data) or changed
    return changed, False
