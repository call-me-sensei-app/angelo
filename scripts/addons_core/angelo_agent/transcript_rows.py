# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The controller's transcript rows and their journal: where rows go, which changed, and when they are saved.

``Controller`` keeps the rows (``state["messages"]``) and indexes into them
(assistant items, activities, thinking rows). The journal (``SessionStore``,
``session_writer``) writes the rows new since its last write and those the
controller marks changed (``_touched``); a pump's owed save writes nothing
else (but its bounded scrub), other saves also compare the current request's
rows. Every change in place to an existing row, the current request's
included, is marked: by index with ``_touched`` (or made by ``_change_row``),
or, by code that holds the row itself (a restore's notice), inside
``changing(controller, row)``; and so is every row an insert moves
(``_insert_request_message``). A change to many rows at once (a restore's
badges, a key's redaction) calls ``store.invalidate()`` instead. The mark
follows the change, both under ``lock``, which every save holds: a save that
ran between them would find the row unmarked and changed, never marked and
unchanged, so no save clears a mark before its change is made. Host tests
check both on every save (``session_writer.CHECK_TOUCHES``), and
``test_native_row_marks.py`` finds every change to a row in the add-on's
source that is neither marked nor made before the row joins the chat.
"""

import time
from contextlib import contextmanager

from . import session_writer


def _locked(controller, index):
    if session_writer.CHECK_TOUCHES and not controller.lock._is_owned():
        raise AssertionError("Conversation row %d was marked changed without the chat's lock" % index)


@contextmanager
def changing(controller, row):
    """Change ``row``, a row object of ``controller``'s chat, in place: under its lock, then marked (by identity,
    from the newest row), the block's partial changes too. A row no longer in the chat is not marked."""
    with controller.lock:
        try:
            yield row
        finally:
            rows = controller.state["messages"]
            index = next((index for index in range(len(rows) - 1, -1, -1) if rows[index] is row), None)
            if index is not None:
                controller.store.touch(index)
                _locked(controller, index)


class TranscriptRows:
    __slots__ = ()
    _save_owed = False   # A durable change the pump's save throttle has not written yet (tool_finalization.save_if_owed).

    def save(self, *, compact=False, compact_if_due=False, leave=False, changes_only=False):
        """Make the conversation durable: append what changed (``SessionStore.save``).

        ``changes_only``: compare only the rows new or touched since the last write (the pump's owed save,
        ``tool_finalization.save_if_owed``).

        ``compact`` rewrites it whole: ``Controller.close`` (chat close, add-on unregister and quit) passes
        it. ``leave`` leaves it one base line, writing nothing when it already is one holding the chat
        (``SessionStore.leave``): ``ui.show_session``, a switch to another chat. ``compact_if_due`` rewrites
        it once its records outgrow the base: the end of a turn (``Controller._pump_events``).
        """
        with self.lock:
            self.state["updated"] = time.time()
            if compact:
                self.store.compact(self.state)
            elif leave:
                self.store.leave(self.state)
            elif compact_if_due:
                self.store.compact_if_due(self.state)
            elif changes_only:
                self.store.save(self.state, changes_only=True)
            else:
                self.store.save(self.state)
            self._last_save = time.monotonic()
            self._save_owed = False   # Everything noted is written: the pump owes no save.

    def _redacted_text(self, previous, text):
        """``text`` redacted for a row whose stored text was ``previous`` (``Controller.redact``).

        A key streamed in pieces is recognized only once its last piece arrives, and an earlier save may
        already have written the first ones: when the redaction reaches into ``previous``, the journal is
        rewritten whole at the next save, so no earlier record keeps that prefix.
        """
        clean = self.redact(text)
        if clean != text and previous and not clean.startswith(previous):
            self.store.invalidate()
        return clean

    def _append_row(self, row):
        """Append a row under the lock every save holds: a save reads the row list's length once (session_writer)."""
        with self.lock:
            self.state["messages"].append(row)

    def _touched(self, index):
        """Row ``index`` was just changed in place (or moved), under ``lock``: the next save writes it."""
        self.store.touch(index)
        _locked(self, index)

    def _change_row(self, index, **fields):
        """Set ``fields`` on row ``index`` in place, then mark it (``_touched``); the caller holds ``lock``."""
        self.state["messages"][index].update(fields)
        self._touched(index)

    def _insert_request_message(self, index, message):
        """Put successful local UI intent before only its own queued activity.

        Provider/other-call rows keep their relative order; subsequent deltas,
        activities, thinking updates and checkpoint annotations keep exact rows.
        """
        messages = self.state["messages"]
        messages.insert(index, message)
        for moved in range(index, len(messages) - 1):   # A row moved is a row changed: an owed save compares no window.
            self._touched(moved)
        for positions in (self._items, self._activities, self._thinking_messages):
            for identity, position in list(positions.items()):
                if position >= index:
                    positions[identity] = position + 1
        if type(self._thinking_message) is int and self._thinking_message >= index:
            self._thinking_message += 1
        if type(self._request_message_index) is int and self._request_message_index >= index:
            self._request_message_index += 1
        if type(self._native_unsynced_from) is int and self._native_unsynced_from >= index:
            self._native_unsynced_from += 1
