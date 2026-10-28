# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a worker chat's mirror reads the chat's file after each of its worker's writes (design A′, C3 ``journal``).

A mirror reloads on the lead's main thread once per pump that saw a journal frame, and a busy worker writes several
times per call. Reading and checking the whole file each time costs main-thread time that grows with the chat (the
UI review measured about 12 ms at 1 MiB and 31 ms at 2.5 MiB). ``MirrorStore`` is the chat's ``SessionStore`` with
four of its read's parts (``SessionStore._journal``, ``_rows``, ``_results`` and ``_ledger``) kept between reads:

* **The document.** The file is append-only between rewrites, so the store keeps the raw document it built, the
  byte offset it has read up to and the file's identity (device, inode), and applies only the records written after
  that offset (``session_journal.apply``, the reader's own). The whole file is read again, as a load reads it, when:
  the journal frame says ``records == 0`` (the worker rewrote it) or gives no count, its count is lower than what was
  read (a rewrite and new records since), the identity changed, the file is shorter than the offset, the bytes just
  before the offset differ (an inode reused by a later rewrite), the last record is torn, or any record does not
  apply. A failed apply drops what was kept: a half-applied document is never read from again.
* **The rows' checks.** ``session_validation.messages`` checks each row on its own, so a row object the document
  still holds unchanged keeps the checked copy made for it; only new or replaced rows are checked again.
* **The results' checks.** ``sessions._clean_result_payloads`` encodes every stored result to prove it finite UTF-8
  JSON: up to 256 records of up to about 80 KB each, so it cost the main thread time that grew with the chat (the
  1.0.6 diagnosis measured it as nearly all of a reload). A record is replaced, never changed in place
  (``tool_finalization``; the journal's ``apply`` only assigns), so a record object the document still holds keeps
  its check; only new records are encoded. The same loop, cap and order as the store's own (``_results``).
* **The ledger's checks.** The mutation-ID ledger only grows (up to 4,096 entries) and its entries are never replaced
  in place, so ``invocations.clean_invocations`` keeps each entry's check while the document holds it (``known``).

Everything else of the read (header, options, task memory) runs as in every load, so the mirror's state is the
file's by construction, whichever path read it. ``read_error`` keeps the I/O error of the last read (the reader
itself then answers as a load does); the mirror keeps what it shows and reads again (``worker_mirror_events``).

Standard library only and no ``bpy``.
"""

import json
import os

from . import invocations, session_journal, session_validation
from .sessions import SessionStore

RESULTS = 256   # Result records kept, as ``sessions._clean_result_payloads`` keeps them.

OVERLAP = 64   # Bytes before the offset compared on each read: a rewritten file never matches them by chance.


class _Tail:
    __slots__ = ("raw", "identity", "offset", "overlap", "records")

    def __init__(self, raw, identity, offset, overlap, records):
        self.raw, self.identity, self.offset, self.overlap, self.records = raw, identity, offset, overlap, records


class MirrorStore(SessionStore):
    """A mirror's ``SessionStore``: reads only what is new since its last read (module docstring)."""

    def __init__(self, directory, session_id=None):
        super().__init__(directory, session_id)
        self.hint = None          # The ``records`` count of the journal frame being read (None: read it whole).
        self.read_error = None    # The OSError of the last read, else None.
        self.whole_reads = self.tail_reads = 0
        self._tail = None
        self._checked = {}        # id(raw row) -> [raw row, its check or None, whether it verified, copy handed out]
        self._encoded = {}        # id(raw result record) -> (the record, whether it encodes as the journal's JSON)
        self._entries = {}        # mutation ID -> (raw entry, its cleaned entry or None, whether the ID is valid)

    def forget(self):
        """Read the whole file next time (a hand-off, a recovery)."""
        self._tail = None

    def _journal(self, path):
        hint, self.hint, self.read_error = self.hint, None, None
        try:
            tail = self._tail
            if tail is not None and type(hint) is int and hint > 0 and hint >= tail.records:
                raw = self._read_tail(path, tail)
                if raw is not None:
                    self.tail_reads += 1
                    return raw
            return self._read_whole(path)
        except OSError as error:
            self._tail, self.read_error = None, error
            raise

    def _read_whole(self, path):
        self._tail = None
        self.whole_reads += 1
        with open(session_journal.open_shared(path), "rb") as handle:   # Its identity and bytes kept (delete shared).
            before = _identity(os.fstat(handle.fileno()))
            raw, info = session_journal.read(path)
            after = _identity(session_journal.stat_of(path))   # As a descriptor reports it (Windows).
            offset = info.base_bytes + info.tail_bytes
            if before == after and not info.torn and offset > 0:
                handle.seek(max(0, offset - OVERLAP))
                overlap = handle.read(min(OVERLAP, offset))
                self._tail = _Tail(raw, before, offset, overlap, info.records)
        return raw

    def _read_tail(self, path, tail):
        """The kept document with the records written since, or None when the whole file must be read."""
        with open(session_journal.open_shared(path), "rb") as handle:   # Never blocks a writer's replace (Windows).
            status = os.fstat(handle.fileno())
            if _identity(status) != tail.identity or status.st_size < tail.offset:
                return None
            handle.seek(tail.offset - len(tail.overlap))
            data = handle.read()
        if not data.startswith(tail.overlap):
            return None
        new = data[len(tail.overlap):]
        if new and not new.endswith(b"\n"):
            return None   # A torn (or still being written) last record: the whole read decides what it is.
        self._tail = None   # Until every record applied: a document half applied is never read from again.
        records = tail.records
        try:
            for line in new.split(b"\n")[:-1]:
                records += 1
                session_journal.apply(tail.raw, json.loads(line.decode("utf-8")), records)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return None
        tail.offset, tail.records = tail.offset + len(new), records
        tail.overlap = data[-OVERLAP:] if len(data) >= OVERLAP else data
        self._tail = tail
        return tail.raw

    def _rows(self, rows):
        """``session_validation.messages``, row by row, keeping the check of each row object still held.

        Each row is handed out as a copy (the mirror's display events change rows in place). A row object still
        held hands out the copy it handed last time while that copy still equals its check, so the reload keeps
        the row shown by identity (``worker_mirror_events.reload``); one changed since gets a new copy.
        """
        if type(rows) is not list:
            return session_validation.messages(rows)
        checked, output, verified = {}, [], True
        known = self._checked
        if not any(id(row) in known for row in rows):   # A whole read: one check of the list, as every load.
            whole, valid = session_validation.messages(rows)
            if not valid:
                known = {}   # Rows were left out: checked one by one below, to know which.
            else:
                output = [dict(kept) for kept in whole]
                self._checked = {id(row): [row, kept, True, copy] for row, kept, copy in zip(rows, whole, output)}
                return output, True
        for row in rows:
            key = id(row)
            entry = known.get(key)
            if entry is None or entry[0] is not row:
                kept, valid = session_validation.messages([row])
                entry = [row, kept[0] if kept else None, valid, None]
            checked[key] = entry
            if entry[1] is not None:
                if entry[3] is None or entry[3] != entry[1]:
                    entry[3] = dict(entry[1])
                output.append(entry[3])
            verified = verified and entry[2]
        self._checked = checked
        return output, verified

    def _ledger(self, value):
        """``invocations.clean_invocations``, keeping the check of each entry object still held."""
        return invocations.clean_invocations(value, self._entries)

    def _results(self, value):
        """``sessions._clean_result_payloads``, keeping the encode check of each record object still held."""
        output, known, checked = {}, self._encoded, {}
        if type(value) is dict:
            for identity, record in value.items():
                if (type(identity) is not str or type(record) is not dict
                        or type(record.get("document")) is not str or type(record.get("result")) is not dict):
                    continue
                entry = known.get(id(record))
                if entry is None or entry[0] is not record:
                    entry = (record, _encodes(record))
                checked[id(record)] = entry
                if entry[1]:
                    output[identity] = record
                    if len(output) > RESULTS:
                        output.pop(next(iter(output)))
        self._encoded = checked   # Holds every record it names: an id is never reused while it is kept.
        return output


def _encodes(record):
    """Whether a result record saves (``sessions._clean_result_payloads``'s check)."""
    try:
        json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return False
    return True


def _identity(status):
    return status.st_dev, status.st_ino
