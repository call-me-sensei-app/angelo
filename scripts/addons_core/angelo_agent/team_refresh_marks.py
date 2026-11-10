# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team sign-in record every Angelo process here shares: a generation and the spent refresh tokens.

Contract: doc/angelo/ai_providers.rst, "A rotated token the store refuses".

Refresh tokens rotate and a used one presented again ends the grant for every
Angelo process (reuse detection). Saving a rotated token in the OS store can
fail after the console rotated it, while other processes still hold the used
one. So, before presenting a token, a process marks it here (its
``team_access_share.fingerprint``, never the token), and no process presents a
marked token. The **generation** changes with every sign-in and sign-out, so a
process holding an unsaved token can tell that the person signed in or out in
another window meanwhile.

The file ``team-refresh.json`` in the connections folder is read and written
only under ``team_lock`` (the caller holds it), written durably (temporary
file, ``fsync``, ``shared_replace.replace``; a Windows sharing violation is retried
briefly). A file that can't be read or written raises ``RecordError``
(``kind`` "io": tried again later); one whose content this build can't use
(damaged, or a format version it doesn't know) raises it with ``kind``
"invalid". Either way the caller presents nothing (fail closed); a sign-in or
a sign-out starts a new record, the only way out of an unusable one.
Value: ``{"v": 1, "accounts": {<console url>: {"gen": "<hex>", "spent":
{<fingerprint>: <epoch seconds>}}}}``; marks older than ``KEEP`` are pruned.
``v`` changes only when older builds must stop using the record; additions
keep it, and readers keep keys and other consoles' entries they don't read.

``holder`` is the OS file lock ``team-unsaved.lock`` (``team_lock.Holder``),
held by a process while it holds a token the others can't use, so that they
can tell a live holder from a closed or crashed one.

Without a folder (a memory-only account) the record lives in this process.

Standard library only; no bpy.
"""

import json
import os
import time
import uuid
from pathlib import Path

from . import shared_replace
from .team_access_share import fingerprint
from .team_lock import Holder

NAME = "team-refresh.json"
HOLDER = "team-unsaved.lock"
KEEP = 60 * 24 * 3600   # seconds: a refresh token unused this long has expired on the console anyway
MAX_MARKS = 20000       # per console; about 140 days of one rotation per 10 minutes
_FORMAT = 1
_REPLACE_TRIES = 5


class RecordError(Exception):
    """The record can't be used: nothing may be presented. ``kind``: "io" (can't read or write it now) or
    "invalid" (its content can't be used by this build)."""

    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind


def _valid(value, key):
    """Whether ``value`` is a record this build can use: its format, and the entry of console ``key`` if any."""
    if not isinstance(value, dict) or value.get("v") != _FORMAT or not isinstance(value.get("accounts"), dict):
        return False
    entry = value["accounts"].get(key)
    if entry is None:
        return True
    if not isinstance(entry, dict) or not isinstance(entry.get("gen"), str) or not isinstance(entry.get("spent"), dict):
        return False
    return all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
               for k, v in entry["spent"].items())


class RefreshRecord:
    """One console's generation and spent marks. Every method but ``holder``'s runs under the team lock."""

    def __init__(self, directory, console_url, *, clock=time.time, replace=None, sleep=time.sleep):
        self.path = Path(directory) / NAME if directory else None
        self.holder = Holder(directory, HOLDER) if directory else None
        self.key, self._clock, self._sleep = console_url, clock, sleep
        # One try each (this loop retries): shared_replace's rename, which lands while a reader shares delete.
        self._replace = replace or (lambda source, target: shared_replace.replace(source, target, attempts=1,
                                                                                    told=False))
        self._memory = {"v": _FORMAT, "accounts": {}}

    # Reading and writing the whole file ----------------------------------

    def _load(self, salvage=False):
        """The whole record. ``salvage``: one whose content can't be used comes back without this console's entry
        (other consoles' entries kept when the format is known), instead of raising."""
        if self.path is None:
            return json.loads(json.dumps(self._memory))
        try:
            text = shared_replace.read_bytes(self.path).decode("utf-8")   # Sharing delete: never refuses a save.
        except FileNotFoundError:
            return {"v": _FORMAT, "accounts": {}}
        except UnicodeDecodeError:
            if salvage:
                return {"v": _FORMAT, "accounts": {}}
            raise RecordError("invalid", "not UTF-8") from None
        except OSError as error:
            raise RecordError("io", type(error).__name__) from None
        try:
            value = json.loads(text)
        except ValueError:
            value = None
        if _valid(value, self.key):
            return value
        if not salvage:
            raise RecordError("invalid", "unknown format")
        if isinstance(value, dict) and value.get("v") == _FORMAT and isinstance(value.get("accounts"), dict):
            value["accounts"].pop(self.key, None)
            return value
        return {"v": _FORMAT, "accounts": {}}

    def _save(self, value):
        if self.path is None:
            self._memory = value
            return
        data = json.dumps(value, separators=(",", ":"), sort_keys=True)
        temporary = self.path.with_name("%s.%d.tmp" % (NAME, os.getpid()))
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(_REPLACE_TRIES):
                try:
                    self._replace(temporary, self.path)
                    break
                except PermissionError as error:  # Windows: a scanner has the file open for a moment.
                    if attempt == _REPLACE_TRIES - 1:
                        if isinstance(error, shared_replace.Refused):   # Held (never a folder's own permissions).
                            shared_replace.tell(self.path)   # Its own tries are over: the person is told.
                        raise
                    self._sleep(0.05)
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise RecordError("io", type(error).__name__) from None
        if os.name != "nt":  # The rename survives a power loss only once its folder is synced: best effort, since the
            try:             # record is written (a failure here must never read as "not marked").
                folder = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(folder)
                finally:
                    os.close(folder)
            except OSError:
                pass

    def _entry(self, value):
        entry = value["accounts"].setdefault(self.key, {"gen": "", "spent": {}})
        cutoff = self._clock() - KEEP
        spent = {fp: at for fp, at in entry["spent"].items() if at >= cutoff}
        if len(spent) > MAX_MARKS:
            spent = dict(sorted(spent.items(), key=lambda item: item[1])[-MAX_MARKS:])
        entry["spent"] = spent
        return entry

    # Under the team lock ---------------------------------------------------

    def state(self):
        """``(generation, marked)``: the generation ("" before any 1.0.10 sign-in) and the set of marked
        fingerprints. Raises ``RecordError``."""
        entry = self._entry(self._load())
        return entry["gen"], frozenset(entry["spent"])

    def mark(self, token):
        """Mark ``token`` presented, before presenting it. Raises ``RecordError`` (then never present it)."""
        value = self._load()
        self._entry(value)["spent"][fingerprint(token)] = self._clock()
        self._save(value)

    def unmark(self, token):
        """The console certainly didn't use ``token``. Raises ``RecordError``."""
        value = self._load()
        if self._entry(value)["spent"].pop(fingerprint(token), None) is not None:
            self._save(value)

    def bump(self):
        """A sign-in or sign-out: a new generation, returned. A record whose content can't be used is replaced by
        a new one, the only way out of it (every token of the sign-in before is then given up or ended). Raises
        ``RecordError`` when the record can't be read or written."""
        value = self._load(salvage=True)
        entry = self._entry(value)
        entry["gen"] = uuid.uuid4().hex
        self._save(value)
        return entry["gen"]

    # Any thread ----------------------------------------------------------

    def holder_elsewhere(self):
        """True when another live process holds a token the others can't use (``HOLDER``)."""
        if self.holder is None:
            return False
        try:
            return self.holder.held_elsewhere()
        except OSError:
            return True  # Can't tell: waiting is the safe answer, never presenting a marked token.


def marked(token, fingerprints):
    return bool(token) and fingerprint(token) in fingerprints
