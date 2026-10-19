# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The controller's transcript rows and their journal: where rows go, which changed, and when they are saved.

``Controller`` keeps the rows (``state["messages"]``) and indexes into them
(assistant items, activities, thinking rows). The journal (``SessionStore``,
``session_writer``) writes a save's current request rows and, of older rows,
only those the controller marks changed (``_touched``). Every change in place
to an existing row goes through ``_touched`` or happens to a row of the
current request; a change to many old rows at once (a restore's badges, a
key's redaction) calls ``store.invalidate()`` instead.
"""

import time


class TranscriptRows:
    __slots__ = ()

    def save(self, *, compact=False, compact_if_due=False, leave=False):
        """Make the conversation durable: append what changed (``SessionStore.save``).

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
            else:
                self.store.save(self.state)
            self._last_save = time.monotonic()

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
        """Row ``index``, which the caller is about to change in place: the next save writes it."""
        self.store.touch(index)
        return self.state["messages"][index]

    def _insert_request_message(self, index, message):
        """Put successful local UI intent before only its own queued activity.

        Provider/other-call rows keep their relative order; subsequent deltas,
        activities, thinking updates and checkpoint annotations keep exact rows.
        """
        self.state["messages"].insert(index, message)
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
