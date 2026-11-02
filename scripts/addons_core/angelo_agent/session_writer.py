# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What changed in a conversation since its journal was last written, and when to rewrite it whole.

``SessionStore`` owns what is saved (the cleaned fields, the ledger's rules);
this writer owns how little of it each save writes (``session_journal``):

* **Fields** (everything but rows, results and the ledger) are cleaned on
  every save, as before, and written when their bytes differ. A value that is
  the very object last written is not encoded again when nothing can have
  changed it: an immutable value, or one the store owns (``owned_fields``,
  the cleaned task memory ``SessionStore`` keeps).
* **Rows** are compared by their encoded bytes, kept for every row. Every
  change in place to a row the file holds is marked (``touch``), the current
  request's rows included; a row moved by an insert or a removal is a row
  changed. A save encodes the rows new since the last write and the touched
  ones. Most saves also encode the rows of the current request (from the
  caller's ``sealed`` index, at most the last ``WINDOW_ROWS``) again; a pump's
  owed save (``changes_only``: ``tool_finalization.save_if_owed``, every 0.5 s
  while a request is open) does not: it re-encodes a window row only when its
  own fields are no longer the objects it last encoded (``_same``), so a field
  set, replaced or removed reaches the file marked or not, and a change inside
  a field's value (a nested list or mapping) needs its mark. Every save also re-encodes
  ``SCRUB_ROWS`` rows in turn, a scrub in cycles: each cycle covers the rows
  the file held when it began, the window too, and the next begins at row 0
  once it ends, however fast rows are added meanwhile. A change no caller
  marked thus reaches the file within two cycles: at most
  ceil(n1 / SCRUB_ROWS) + ceil(n2 / SCRUB_ROWS) saves, n1 and n2 the rows held
  when they began. Host tests check the rule on every save
  (``CHECK_TOUCHES``, ``journal_checks.rows_touched``).
  Inserting or removing an older row (the row just before the window is no
  longer the same object), or replacing the list, rewrites the file.
* **Results** (at most 256) are compared by identity: every phase stores a
  new record object, and the writer holds the ones it wrote, so an address is
  never reused for another record. A different table, or an order a delta
  cannot reproduce, is written whole. The comparison runs in C, never record
  by record in Python, so it costs the same with 256 records as with 16.
* **The mutation-ID ledger** only grows. New entries (found from its end) are
  validated as they are written; an entry removed, a different table or an
  invalid entry rewrites the file, which validates the whole ledger once as
  every save did before.

A save costs the request's rows plus what changed, not the conversation; an owed save, what changed.
A rewrite (compaction) costs the conversation, and happens after a load (the
first save), after ``invalidate``, after a failed write, when another writer
changed the file, at chat close, at a switch to another chat unless the file
is already one base line holding it (``current``), and once the records
outgrow the base (``compaction_due``, checked when a turn ends; ``force_due``
by the save itself, as a backstop).
"""

from itertools import chain, islice, takewhile
import json
import threading

from . import session_journal as journal
from .invocations import valid_entry, well_formed
from .journal_checks import enabled, results_touched, rows_touched

# Host tests and native suites (journal_checks.enabled): every save then encodes every row the file holds and fails
# when one changed without a ``touch`` (``journal_checks.rows_touched``). Off in the product.
CHECK_TOUCHES = enabled("ANGELO_JOURNAL_TOUCHES")

WINDOW_ROWS = 64
SCRUB_ROWS = 8
RESULT_SCRUB = 8   # Result records a save compares that no mark named, in turn (_result_changes).
# Records may grow to the base's size (and at least 1 MiB) before a turn's end compacts; a save compacts by itself
# only past four times the base (and at least 64 MiB).
COMPACT_MIN_BYTES = 1 << 20
FORCE_MIN_BYTES = 64 << 20
_MISSING = object()
_IMMUTABLE = frozenset({str, int, float, bool, type(None)})


def _same(pairs, row):
    """Whether ``row`` holds exactly the key and value objects ``pairs`` recorded, in order (``_row_changes``)."""
    if pairs is None or type(row) is not dict or len(pairs) != len(row):
        return False
    return all(key is other_key and value is other for (key, value), (other_key, other) in zip(pairs, row.items()))


class JournalWriter:
    _before_replace = None  # Run just before a rewrite's replace (``session_journal.rewrite``; ``session_guard``).

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.last_write = None  # "append" or "rewrite": what the last successful save wrote (None: nothing).
        self.owned_fields = frozenset()  # Field values only the caller holds and never changes in place.
        self.reset()

    def reset(self):
        """Forget the file: the next save rewrites it whole."""
        self._identity = None
        self._seq = 0
        self._base_bytes = self._tail_bytes = 0
        self._fields = {}
        self._field_values = {}
        self._rows = None
        self._row_bytes = []
        self._sealed = 0
        self._sentinel = None
        self._touched = set()
        self._scrub = self._scrub_end = 0   # The scrub's next row, and the end of its cycle (_row_changes).
        self._shallow = {}   # Window row -> its (key, value) objects when last encoded (_row_changes, _same).
        self._results = None
        self._result_values = {}
        self._result_keys = []   # The held records' keys in order: the table as last written (_result_changes).
        self._touched_results = {}   # Result keys replaced in place since the last write (touch_result), in order.
        self._result_scrub = 0
        self._ledger = self._entries = self._last_entry = None
        self._entry_count = 0
        self._closed = None

    @property
    def ready(self):
        return self._identity is not None

    def compaction_due(self):
        return self.ready and self._tail_bytes > max(self._base_bytes, COMPACT_MIN_BYTES)

    def force_due(self):
        return self.ready and self._tail_bytes > max(4 * self._base_bytes, FORCE_MIN_BYTES)

    def written_fields(self):
        """The fields as the file holds them, decoded (journal verification)."""
        return [(name, json.loads(data)) for name, data in self._fields.items()]

    def touch(self, index):
        """Row ``index`` changed in place (or moved): the next save writes it, an owed save's included."""
        if type(index) is int and index >= 0:
            self._touched.add(index)

    def touch_result(self, key):
        """The record under ``key`` was replaced (a new phase, its completion) or changed: the next save writes it."""
        self._touched_results[key] = None

    # -- whole file --------------------------------------------------------------
    def rewrite(self, fields, rows, results, ledger, sealed=0):
        """Write the whole conversation as a new base; the caller cleaned ``fields`` and ``ledger``."""
        field_bytes = [(name, journal.encode(value)) for name, value in fields]
        # Every row is encoded on its own and other threads run between them: the base describes exactly the
        # rows counted here, and a row another thread appends meanwhile goes to the next record.
        row_bytes = [journal.encode(row) for row in list(rows)]
        data = journal.obj(field_bytes + [("results", journal.encode(results)), ("invocations", journal.encode(ledger)),
                                          ("messages", b"[" + b", ".join(row_bytes) + b"]")]) + b"\n"
        self._commit_rewrite(data, field_bytes, rows, row_bytes, results, ledger, sealed, fields)

    def compact(self, rows, results, ledger, sealed=0):
        """Rewrite from what the file already holds: the bytes of every field and row as last written.

        The caller saves first, so these are the current ones; nothing is encoded again but the
        results and the ledger. A row changed without a ``touch`` keeps its last written bytes here and
        is found by the scrub later, as it would be without the compaction.
        """
        field_bytes = list(self._fields.items())
        data = journal.obj(field_bytes + [("results", journal.encode(results)), ("invocations", journal.encode(ledger)),
                                          ("messages", b"[" + b", ".join(self._row_bytes) + b"]")]) + b"\n"
        self._commit_rewrite(data, field_bytes, rows, list(self._row_bytes), results, ledger, sealed,
                             list(self._field_values.items()))

    def _commit_rewrite(self, data, field_bytes, rows, row_bytes, results, ledger, sealed, fields):
        try:
            identity = journal.rewrite(self.path, data, self._before_replace)
        except BaseException:
            self.reset()
            raise
        self.reset()
        self._identity, self._base_bytes = identity, len(data)
        self._fields = dict(field_bytes)
        self._field_values = dict(fields) if fields is not None else {}
        self._rows, self._row_bytes = rows, row_bytes
        self._seal(rows, max(sealed, len(row_bytes) - WINDOW_ROWS), len(row_bytes))
        self._results, self._result_values = results, dict(results)
        self._result_keys = list(self._result_values)
        self._track_ledger(ledger)
        self.last_write = "rewrite"

    # -- one record ----------------------------------------------------------------
    def append(self, fields, rows, results, ledger, sealed=0, *, changes_only=False):
        """Append what changed since the last write. False when only a rewrite can describe it.

        ``changes_only`` (a pump's owed save) encodes only the rows new or touched since, and the scrub's;
        otherwise the current request's rows are compared too. Raises ``journal.FileChanged`` (nothing
        written) when the file is not the one last written, and the write's own error otherwise; either
        way the next save rewrites the whole file.
        """
        change = self._changes(fields, rows, results, ledger, sealed, changes_only)
        if change is None:
            return False
        changed, removed, (updates, length, new_sealed, carry), (results_part, result_values), ledger_change = change
        parts = []
        if changed:
            parts.append(("set", journal.obj(changed)))
        if removed:
            parts.append(("unset", journal.encode(removed)))
        if updates or length != len(self._row_bytes):
            parts.append(("rows", b'{"n": %d, "at": [' % length
                          + b", ".join(b"[%d, " % index + data + b"]" for index, data in updates) + b"]}"))
        if results_part:
            parts.append(("results", results_part))
        if ledger_change:
            parts.append(("ledger", journal.obj(ledger_change)))
        if parts:
            record = journal.obj([("seq", b"%d" % (self._seq + 1))] + parts) + b"\n"
            try:
                self._identity = journal.append(self.path, self._identity, record)
            except BaseException:
                self.reset()
                raise
            self._seq += 1
            self._tail_bytes += len(record)
        for name, data in changed:
            self._fields[name] = data
        for name in removed:
            del self._fields[name]
        self._field_values = dict(fields)
        for index, data in updates:
            if index < len(self._row_bytes):
                self._row_bytes[index] = data
            else:
                self._row_bytes.append(data)
        del self._row_bytes[length:]
        self._touched.clear()
        self._scrub, self._scrub_end, self._shallow = carry
        self._seal(rows, new_sealed, length)
        self._hold_results(results, result_values)
        self._track_ledger(ledger)
        self.last_write = "append"
        return True

    def _hold_results(self, results, update):
        """Hold what this write wrote: a whole table's copy, or ``(removed, changed, added, next scrub)`` applied in
        place (``removed``: the held table's first keys; ``added``: keys new at its end)."""
        self._results = results
        self._touched_results = {}
        if type(update) is dict:
            self._result_values, self._result_keys, self._result_scrub = update, list(update), 0
            return
        removed, changed, added, scrub = update
        seen = self._result_values
        for key in removed:
            del seen[key]
        seen.update(changed)   # Replaced keys keep their place; new ones follow, as in the table.
        del self._result_keys[:len(removed)]
        self._result_keys.extend(added)
        self._result_scrub = max(0, scrub - len(removed))   # The scrub's next record, where the front removal left it.

    @property
    def records(self):
        """The records appended since the file was last rewritten (0: the file is one base line)."""
        return self._seq

    def current(self, fields, rows, results, ledger, sealed=0, *, but=()):
        """True when the file is one base line holding this state, but for the fields named in ``but``.

        What an append would write is worked out and nothing is written (``SessionStore.leave``): it costs
        what an append's comparison costs, the current request's rows and the changed fields.
        """
        if self._seq:
            return False
        change = self._changes(fields, rows, results, ledger, sealed)
        if change is None:
            return False
        changed, removed, (updates, length, _, _), (results_part, _), ledger_change = change
        return (not updates and length == len(self._row_bytes) and not results_part and not ledger_change
                and all(name in but for name in removed) and all(name in but for name, _ in changed))

    def _changes(self, fields, rows, results, ledger, sealed, changes_only=False):
        """``(changed fields, removed fields, row changes, result changes, ledger changes)`` since the last write,
        each encoded; None when only a rewrite can describe them (or the file was never written)."""
        if not self.ready:
            return None
        rows_change = self._row_changes(rows, sealed, changes_only)
        ledger_change = self._ledger_changes(ledger)
        if rows_change is None or ledger_change is None:
            return None
        changed = [(name, data) for name, data in ((name, journal.encode(value)) for name, value in fields
                                                   if not self._unchanged(name, value))
                   if self._fields.get(name) != data]
        names = {name for name, _ in fields}
        removed = [name for name in self._fields if name not in names]
        return changed, removed, rows_change, self._result_changes(results), ledger_change

    def _unchanged(self, name, value):
        """The very object last written, and one nobody can have changed since: an immutable value, or one the
        store owns (``owned_fields``: the cleaned task memory it keeps). Anything else is encoded and compared."""
        return (self._field_values.get(name, _MISSING) is value
                and (type(value) in _IMMUTABLE or name in self.owned_fields))

    def _seal(self, rows, sealed, length):
        """Rows before ``sealed`` are encoded again only when touched or scrubbed; ``length``: the rows written."""
        self._sealed = max(0, min(sealed, length))
        self._sentinel = rows[self._sealed - 1] if self._sealed else None

    def _row_changes(self, rows, sealed, changes_only=False):
        """``(updates, length, new sealed, (next scrub row, scrub cycle end, window snapshots))``, or None when the
        list changed shape below the window.

        ``changes_only``: the rows new since the last write, the touched ones, and each window row whose own fields
        are no longer the objects last encoded (``_same``: a field set, replaced or removed, marked or not; a change
        inside a field's value needs its mark); otherwise every window row (from ``sealed``) and the touched older
        ones. Both add the scrub's next ``SCRUB_ROWS`` rows of its cycle.
        """
        old = self._sealed
        if rows is not self._rows or type(rows) is not list:
            return None
        # Read once: other threads run between the rows encoded below, and a row appended meanwhile without the
        # chat's lock must go to the next record whole, never be counted here without its bytes.
        length = len(rows)
        if length < old or old and rows[old - 1] is not self._sentinel:
            return None  # A row was inserted or removed below the window.
        cached = self._row_bytes
        held = min(len(cached), length)   # The rows the file holds that are still in the list.
        if CHECK_TOUCHES:
            rows_touched(rows, cached, self._touched, length)
        indices = set(range(held if changes_only else old, length)) | {index for index in self._touched if index < length}
        scrub, end = self._scrub, self._scrub_end
        if scrub >= end or end > held:   # A cycle ended (or rows it counted went): the next covers the rows held now.
            scrub, end = 0, held
        stop = min(end, scrub + SCRUB_ROWS)
        indices.update(range(scrub, stop))
        shallow = self._shallow
        if changes_only:   # Identity checks only: a fraction of encoding the window (ruling B1).
            indices.update(index for index in range(old, held) if not _same(shallow.get(index), rows[index]))
        updates, encoded = {}, {}
        for index in indices:
            row = rows[index]
            if index >= old and type(row) is dict:
                encoded[index] = tuple(row.items())   # Taken before the bytes, under the lock every change holds.
            data = journal.encode(row)
            if index >= len(cached) or cached[index] != data:
                updates[index] = data
        new_sealed = max(old, min(sealed, length), length - WINDOW_ROWS)
        kept = {index: pairs for index, pairs in shallow.items() if new_sealed <= index < length and index not in indices}
        kept.update((index, pairs) for index, pairs in encoded.items() if index >= new_sealed)
        return sorted(updates.items()), length, new_sealed, (stop, end, kept)

    def _result_changes(self, results):
        """``(encoded part or b"", how to update the values held)``; a table that cannot be described is written whole.

        Records arrive at the end of the table and leave from its front (the cap drops the oldest), so both are found
        from the ends, and a record replaced in place by its mark (``touch_result``) or by the scrub: a save costs
        what changed, never the table (``RESULT_SCRUB`` records a save; the host tests' check, every record).
        """
        if type(results) is not dict:
            raise TypeError("Conversation results must be a JSON object")
        seen = self._result_values
        if results is not self._results:
            return self._whole_results(results)
        removed = list(takewhile(lambda key: key not in results, seen))
        added, last = [], None
        for key in reversed(results):
            if key in seen:
                last = key
                break
            added.append(key)
        added.reverse()
        kept, keys = len(seen) - len(removed), self._result_keys
        # Every other held key is still there, in its place, and only the new ones follow: one comparison in C of the
        # keys (a record removed from the middle, or one that left and came back, is compared one by one instead).
        if kept + len(added) != len(results) or list(islice(results, kept)) != (keys[len(removed):] if removed else keys):
            return self._scanned_changes(results)
        start = self._result_scrub if self._result_scrub < len(keys) else 0
        scrub = keys[start:start + RESULT_SCRUB]
        following = start + len(scrub) if start + len(scrub) < len(keys) else 0   # Past the end: the next cycle.
        if CHECK_TOUCHES:
            results_touched(results, seen, self._touched_results)
        touched = self._touched_results   # A marked record is written whatever its identity (changed in place too).
        replaced = [key for key in touched if key in seen and key in results]
        replaced += [key for key in scrub if key not in touched and results.get(key, seen[key]) is not seen[key]]
        changed = [(key, results[key]) for key in chain(replaced, added)]
        if not removed and not changed:
            return b"", ((), (), (), following)
        part, _ = self._delta(removed, changed, None)
        return part, (removed, changed, added, following)

    def _scanned_changes(self, results):
        """Every record compared, for a table whose shape the ends cannot describe; marked records written too."""
        seen, touched = self._result_values, self._touched_results
        if CHECK_TOUCHES:
            results_touched(results, seen, touched)
        removed = [key for key in seen if key not in results]
        changed = [(key, value) for key, value in results.items()
                   if seen.get(key, _MISSING) is not value or key in touched]
        # A delta keeps every remaining key in place and appends new ones; a key that left and came back moved
        # to the end in memory (even as the same record), and only the whole table states that order.
        if list(results) != [key for key in seen if key in results] + [key for key, _ in changed if key not in seen]:
            return self._whole_results(results)
        if not removed and not changed:
            return b"", dict(seen)
        return self._delta(removed, changed, results)

    @staticmethod
    def _delta(removed, changed, results):
        if any(type(key) is not str for key, _ in changed):
            raise TypeError("Conversation result keys must be strings")
        parts = [("del", journal.encode(removed))] if removed else []
        if changed:
            parts.append(("set", b"[" + b", ".join(b"[" + journal.encode(key) + b", " + journal.encode(value) + b"]"
                                                   for key, value in changed) + b"]"))
        return journal.obj(parts), (dict(results) if results is not None else None)

    @staticmethod
    def _whole_results(results):
        return journal.obj([("all", journal.encode(results))]), dict(results)

    def _ledger_changes(self, ledger):
        """Encoded ``(name, bytes)`` parts ([] when unchanged), or None when only a rewrite can describe it."""
        if ledger is not self._ledger or not well_formed(ledger) or ledger["entries"] is not self._entries:
            return None
        entries = ledger["entries"]
        count = len(entries) - self._entry_count
        if count < 0:
            return None
        newest = reversed(entries.items())
        added = [next(newest) for _ in range(count)][::-1]
        if self._entry_count and next(newest)[0] != self._last_entry:
            return None  # An entry was removed: the ledger never shrinks, so say it whole.
        if not all(valid_entry(key, value) for key, value in added):
            return None
        parts = []
        if ledger["closed"] is not self._closed:
            parts.append(("closed", journal.encode(ledger["closed"])))
        if added:
            parts.append(("add", b"[" + b", ".join(b"[" + journal.encode(key) + b", " + journal.encode(value) + b"]"
                                                   for key, value in added) + b"]"))
        return parts

    def _track_ledger(self, ledger):
        self._ledger = ledger
        self._entries = ledger.get("entries") if type(ledger) is dict else None
        self._entry_count = len(self._entries) if type(self._entries) is dict else 0
        self._last_entry = next(reversed(self._entries), None) if self._entry_count else None
        self._closed = ledger.get("closed") if type(ledger) is dict else None
