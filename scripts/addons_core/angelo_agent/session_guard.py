# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's store writes only over the file as it last read or wrote it: ``GuardedWriter``.

Every ``SessionStore`` (a lead chat's, a worker's ``OwnedSessionStore``) writes through this writer. Its
``expected`` is the file as the store last left it: read (``adopt``, at the end of ``SessionStore.load``) or
written. It holds the file's ``session_journal.full_identity`` and the CRC-32 of its bytes. A store never loaded
(``UNCLAIMED``) holds no view of the file: its first save writes the whole state its caller gives it (a new
chat, a migration), and from then on it is guarded like any other.

An append already checks the file under its ``flock`` (``session_journal.append``). A rewrite (the first save
after a load, a compaction, a save after ``FileChanged``) checks it here, holding the old file's ``flock`` (on
Windows, which has none, the check's handle is closed before the replace, since ``MoveFileExW`` never replaces an
open name, and the file's identity is checked again by ``stat`` just before it):

* the identity expected: written over;
* the same file (device, index, size and type) whose times or links changed, holding the same bytes (CRC-32):
  a metadata-only change (a backup tool's attribute, ``touch``, a kept copy's hard link); written over;
* anything else is another writer's content (its append or rewrite, a replaced or removed file, an in-place
  edit): ``JournalTaken``, and nothing written, until the store loads the file again. Before, a store rewrote
  the whole file from memory here and erased what the other writer recorded.

The store's own failures never read as another writer. A failed append is cut back under the lock, and the
file as it was left becomes ``expected`` (``journal_left``). A rewrite that failed with the file still as it was
(the replace refused: on Windows another process's open handle, see ``session_journal``; or no space for the
temporary file) leaves the writer describing that file, and the save appends what changed instead: the
compaction is retried when it is next due, never forced on the next save. A rewrite whose own stat after the
replace failed takes the file's identity from a stat now, when the file holds exactly the bytes it wrote; if that
fails too, the writer expects those bytes (``WRITTEN``): its next save checks them under the ``flock`` (CRC-32) and
writes on, while another writer's content is still refused. One failed stat never locks a chat's saves out.

Standard library only; no bpy.
"""

import os
import stat
import zlib

from . import session_journal as journal
from .session_journal import JournalTaken
from .session_writer import JournalWriter

try:
    import fcntl
except ImportError:  # Windows: the identity check alone (no flock there, as in session_journal).
    fcntl = None


UNCLAIMED = ("unclaimed",)   # A store that never loaded: no view of the file to protect yet.
WRITTEN = ("written",)       # The file as this writer rewrote it, its identity not known: its bytes are (``crc``).


_UNKNOWN = object()


def unknown():
    """An identity no file has, nor any other unknown one: what cannot be checked never passes a check."""
    return (_UNKNOWN, object())


def is_unknown(identity):
    return type(identity) is tuple and identity[:1] == (_UNKNOWN,)


def identity_of(path):
    """The file's ``session_journal.full_identity``; None for no file, ``unknown()`` when it cannot be read."""
    try:
        status = journal.stat_of(path)   # As a descriptor reports it: compared with descriptors' identities.
    except FileNotFoundError:
        return None
    except OSError:
        return unknown()
    return journal.full_identity(status)


def _same_file(one, other):
    """Two full identities of one file with the same size: device, index, size and type."""
    return (type(one) is tuple and type(other) is tuple and len(one) == len(other) == 7
            and one[:3] == other[:3] and one[6] == other[6])


class GuardedWriter(JournalWriter):
    """A ``JournalWriter`` that never writes over another writer's content (module docstring)."""

    def __init__(self, path):
        self.expected = UNCLAIMED
        self.crc = None        # CRC-32 of the expected file's bytes; None when not known.
        super().__init__(path)

    def loading(self):
        """Start of ``SessionStore.load``: forget the file, and have this thread's next ``session_journal.read``
        of it report what it read (``session_journal.capture``) for ``adopt``."""
        self.reset()
        journal.capture.path, journal.capture.info = self.path, None

    def _refuse(self):
        """Raise when this writer may not write at all (``worker_journal_owner.OwnedWriter``)."""

    def adopt(self, partial):
        """End of ``SessionStore.load``: expect the file as it was read (``session_journal.capture``).

        A file changed since it was read is another writer's, unless only its metadata changed. A file that
        could not be read is expected as it is now only when the load kept it (``partial``: ``session_kept``),
        so what is written over is kept; one that did not exist then must not exist now.
        """
        info = journal.capture.info if getattr(journal.capture, "path", None) == self.path else None
        journal.capture.path = journal.capture.info = None
        current = identity_of(self.path)
        seen = info.identity if info is not None else None
        if _same_file(current, seen) and (partial or current == seen or self._holds(info.crc)):
            self.expected, self.crc = current, info.crc
        elif partial:   # Kept (or never written over: _unkept): expect it as it is, bytes and all.
            self.expected, self.crc = current, self._crc_now() if _same_file(current, current) else None
        elif info is None and current is None:
            self.expected, self.crc = None, None
        else:
            self.expected, self.crc = unknown(), None

    def _crc_now(self):
        try:
            fd = journal.open_shared(self.path, follow=False)
        except OSError:
            return None
        try:
            return journal.crc_of(fd)
        except OSError:
            return None
        finally:
            os.close(fd)

    def _holds(self, crc):
        """True when the file at the path holds bytes whose CRC-32 is ``crc``."""
        return crc is not None and self._crc_now() == crc

    def _matches(self, current, fd):
        if current == self.expected or self.expected is UNCLAIMED:
            return True
        if fd is None or self.crc is None or not (self.expected is WRITTEN or _same_file(current, self.expected)):
            return False
        try:
            return journal.crc_of(fd) == self.crc   # Read under the flock: no append of ours changes it meanwhile.
        except OSError:
            return False

    def append(self, fields, rows, results, ledger, sealed=0):
        self._refuse()
        before = self._identity
        try:
            appended = super().append(fields, rows, results, ledger, sealed)
        except BaseException as error:
            left = getattr(error, "journal_left", None)
            if left is not None:   # The file as this write left it, under the lock: still this writer's.
                self.expected, cut = left
                self.crc = self.crc if cut else None
            raise
        written = self._identity
        if written is not before and isinstance(written, journal.Appended):
            self.expected = written.full
            self.crc = zlib.crc32(written.record, self.crc) if self.crc is not None else None
        return appended

    def _commit_rewrite(self, data, field_bytes, rows, row_bytes, results, ledger, sealed, fields):
        self._refuse()
        fd = failed = None
        try:
            try:
                fd = journal.open_shared(self.path, follow=False)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise JournalTaken("This chat's file is not the one its store last wrote: %s" % error) from error
            if fd is not None and fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)   # An append waiting on this file finds it unlinked afterwards.
            current = identity_of(self.path)
            if fd is not None and current is not None:
                held = os.fstat(fd)
                if (held.st_dev, held.st_ino) != current[:2]:
                    current = unknown()   # Replaced between the open and the lock.
            if not self._matches(current, fd):
                raise JournalTaken("Another writer changed this chat's file; it is not written over")
            if fd is not None and fcntl is None:
                # Windows: no lock to hold, and MoveFileExW never replaces a name any handle has open, this
                # check's own included: closed now, and the file checked again just before the replace.
                os.close(fd)
                fd = None
            kept = dict(vars(self))
            self._before_replace = lambda: self._still(current)
            try:
                super()._commit_rewrite(data, field_bytes, rows, row_bytes, results, ledger, sealed, fields)
            except Exception as error:
                if (kept["_identity"] is None or tuple(kept["_identity"]) != current[:5]
                        or identity_of(self.path) != current):
                    raise
                vars(self).update(kept)   # The file is as this writer last wrote it: it still describes it.
                failed = error
            else:
                written, self.crc = self._identity, zlib.crc32(data)
                self.expected = written + (1, stat.S_IFREG) if written is not None else self._rewritten()
        finally:
            vars(self).pop("_before_replace", None)
            if fd is not None:
                os.close(fd)
        if failed is not None:
            # Keep appending (after the flock is released: an append takes it on its own descriptor).
            self._follow(ledger)
            if not self.append(fields, rows, results, ledger, sealed):
                raise failed

    def _still(self, checked):
        """Just before a rewrite's replace: the file is still the one checked (by ``stat``: no handle of ours is
        open on it then). Where the check holds the ``flock``, no append of another store lands meanwhile anyway."""
        if not is_unknown(checked) and identity_of(self.path) != checked:
            raise JournalTaken("Another writer changed this chat's file; it is not written over")

    def _rewritten(self):
        """The file this writer just rewrote, whose stat after the replace failed (module docstring)."""
        current = identity_of(self.path)
        if type(current) is tuple and not is_unknown(current) and self._holds(self.crc):
            return current
        return WRITTEN

    def _follow(self, ledger):
        """A rewrite's caller may hand a cleaned copy of the ledger (``SessionStore._rewrite``). When it equals
        the one last tracked, deltas continue from it; otherwise the append declines and the error stands."""
        if (ledger is not self._ledger and type(ledger) is dict and type(ledger.get("entries")) is dict
                and ledger == self._ledger):
            self._ledger, self._entries = ledger, ledger["entries"]
