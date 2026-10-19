# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The conversation journal's file: a compacted base line, then appended change records.

A chat's file (``sessions/<id>.json``, ``SessionStore``) is::

    {...the whole conversation: the version 1 document...}\\n
    {"seq": 1, ...what changed since the base...}\\n
    {"seq": 2, ...what changed since record 1...}\\n

The base is the document every earlier build wrote (one JSON object with
``"version": 1``), with ``messages`` as its last key, so a compacted file opens
in any build. A file with records does not: an earlier build's ``json.loads``
meets "Extra data" and shows an unverified, empty history with its mutation
IDs closed, so it never repeats an admitted mutation; but its next save
writes that empty history over the file, and the chat is lost for every
build. The release candidates before 1.0 behave so. From 1.0 on, a build that
cannot fully read a chat keeps the file beside it before it writes
(``preserve``, ``SessionStore.load``), so a later format never costs a chat.

A record holds only what changed, each part absolute (never "add 1"):

* ``set`` / ``unset``: top-level fields whose cleaned value changed or went;
* ``rows``: ``{"n": length, "at": [[index, row], ...]}``, rows replaced in
  place or appended in order, then the list cut to ``n``;
* ``results``: ``{"del": [key, ...], "set": [[key, record], ...]}`` or
  ``{"all": {...}}``;
* ``ledger``: ``{"closed": bool, "add": [[id, entry], ...]}`` or
  ``{"all": {...}}``.

Durability is that of the whole-file save it replaces: when a save returns,
the state it was given is on disk and synced.

* An append writes its record and newline with one ``write`` on a descriptor
  opened ``O_APPEND``, then ``fsync``s the file before returning. Each record
  is synced before the next is written, so a crash can tear only the last one.
* A rewrite (compaction) writes the base to a temporary file of its own
  (``<id>.json.<random>.tmp``: two writers never share one), ``fsync``s it and
  ``os.replace``s the file, as the whole-file save did; there is no directory
  sync, as before. A temporary file a crash left is removed by the next load.
* ``fsync``, not macOS's ``F_FULLFSYNC``: as before the journal, a save waits
  for the file system, not for the drive's own cache (a power cut can lose
  the last saves the drive acknowledged; they then load as a torn tail or the
  previous base). ``F_FULLFSYNC`` takes milliseconds per call, on the main
  thread four times per edit and under the controller lock at admission.
* ``read`` ignores a torn last record: one without its newline, or one that is
  not valid UTF-8 JSON. Any other malformed line, a sequence gap or an
  operation that does not fit the document is corruption (``ValueError``),
  which the session loader treats as unverified history (fail closed).
* A writer appends only to the file it last wrote (the same device, inode,
  size, modification and change times, one link), opened without following a
  link, and checks and writes under an exclusive ``flock`` on POSIX. Anything else (another writer, an
  earlier build's rewrite, a restored or replaced file, a write that failed
  part way) makes the save rewrite the whole file from memory, which is what
  every save did before.

Windows (not exercised on this host): ``O_BINARY`` keeps the separator one
byte; there is no ``flock`` (another app on the same profile is unsupported
there, as on POSIX); ``st_ino`` is the NTFS file index; ``st_ctime`` is the
creation time on Python 3.13 (Python's documented deprecation), so the writer's
check rests on device, index, size and modification time there; ``os.replace``
is retried briefly while another process (an indexer, an antivirus) holds the
file open without delete sharing.
"""

import json
import os
import stat
import time
import uuid

try:
    import fcntl
except ImportError:  # Windows: another app on the same profile is unsupported there as well.
    fcntl = None

ENCODER = json.JSONEncoder(ensure_ascii=False, allow_nan=False)
STRUCTURED = frozenset({"messages", "results", "invocations"})
BINARY = getattr(os, "O_BINARY", 0)  # Windows: never translate the record separator.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def encode(value):
    """The journal's canonical bytes for one JSON value (``json.dumps`` defaults, UTF-8, finite numbers)."""
    return ENCODER.encode(value).encode("utf-8")


def obj(items):
    """A JSON object from ``(key, encoded value)`` pairs, without encoding the values again."""
    return b"{" + b", ".join(encode(key) + b": " + value for key, value in items) + b"}"


class JournalInfo:
    """What ``read`` found: the base and record sizes, the record count and a torn last record."""

    __slots__ = ("base_bytes", "tail_bytes", "records", "torn")

    def __init__(self, base_bytes, tail_bytes, records, torn):
        self.base_bytes, self.tail_bytes, self.records, self.torn = base_bytes, tail_bytes, records, torn


def read(path):
    """The conversation as one raw document (base plus records), and its ``JournalInfo``.

    Raises ``FileNotFoundError`` for a missing file and ``ValueError`` for a
    corrupt one. A file without records (every earlier build's, or a
    compacted one) is parsed exactly as before, whatever its formatting.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    torn = False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        # Only the last line may be torn: every earlier record was synced before the next one was written.
        start = data.rfind(b"\n", 0, error.start) + 1
        if start == 0 or data.find(b"\n", error.start) not in (-1, len(data) - 1):
            raise ValueError("The conversation journal is not valid UTF-8") from error
        text, torn = data[:start].decode("utf-8"), True
    decoder = json.JSONDecoder()
    raw, end = decoder.raw_decode(text, len(text) - len(text.lstrip()))
    if type(raw) is not dict:
        raise ValueError("The conversation journal's base is not a JSON object")
    rest = text[end:]
    if not rest.strip():
        return raw, JournalInfo(len(data), 0, 0, torn)
    if not rest.startswith("\n"):
        raise ValueError("The conversation journal's base is not followed by a record line")
    lines = rest[1:].split("\n")
    last = len(lines) - 1  # "" when the file ends with its newline.
    records = 0
    for index, line in enumerate(lines):
        if index == last:
            torn = torn or bool(line)  # An unterminated last record never finished its write.
            break
        try:
            record = json.loads(line)
        except ValueError:
            if index == last - 1 and not lines[last]:
                torn = True  # The file's last record, garbage up to its newline: a torn write too.
                break
            raise
        records += 1
        apply(raw, record, records)
    base = data.find(b"\n") + 1  # A file with records was compacted by this writer: its base is one line.
    return raw, JournalInfo(base, len(data) - base, records, torn)


def apply(raw, record, seq):
    """Apply one record to the raw document; ``ValueError`` when it does not fit."""
    if type(record) is not dict or record.get("seq") != seq or type(record["seq"]) is not int:
        raise ValueError("Conversation journal record %d is out of sequence" % seq)
    unknown = set(record) - {"seq", "set", "unset", "rows", "results", "ledger"}
    if unknown:
        raise ValueError("Conversation journal record %d has unknown parts" % seq)
    fields = record.get("set", {})
    removed = record.get("unset", [])
    if type(fields) is not dict or type(removed) is not list or STRUCTURED & (set(fields) | set(removed)):
        raise ValueError("Conversation journal record %d sets a field it may not" % seq)
    raw.update(fields)
    for name in removed:
        if type(name) is not str:
            raise ValueError("Conversation journal record %d unsets a field it may not" % seq)
        raw.pop(name, None)
    if "rows" in record:
        _apply_rows(raw, record["rows"], seq)
    if "results" in record:
        raw["results"] = _apply_keyed(raw.get("results"), record["results"], seq, deletes=True)
    if "ledger" in record:
        _apply_ledger(raw, record["ledger"], seq)


def _apply_rows(raw, change, seq):
    rows = raw.get("messages")
    if type(rows) is not list or type(change) is not dict or set(change) != {"n", "at"}:
        raise ValueError("Conversation journal record %d has malformed rows" % seq)
    length, updates = change["n"], change["at"]
    if type(length) is not int or type(updates) is not list:
        raise ValueError("Conversation journal record %d has malformed rows" % seq)
    for update in updates:
        if type(update) is not list or len(update) != 2 or type(update[0]) is not int or update[0] < 0:
            raise ValueError("Conversation journal record %d has a malformed row" % seq)
        index, row = update
        if index < len(rows):
            rows[index] = row
        elif index == len(rows):
            rows.append(row)
        else:
            raise ValueError("Conversation journal record %d skips a row" % seq)
    if not 0 <= length <= len(rows):
        raise ValueError("Conversation journal record %d has a row count that does not fit" % seq)
    del rows[length:]


def _apply_keyed(current, change, seq, *, deletes):
    """``{"all": {...}}`` or ``{"del": [...], "set": [[key, value], ...]}`` on a dict; returns the dict."""
    if type(change) is not dict:
        raise ValueError("Conversation journal record %d has a malformed table" % seq)
    if set(change) == {"all"}:
        if type(change["all"]) is not dict:
            raise ValueError("Conversation journal record %d has a malformed table" % seq)
        return change["all"]
    if type(current) is not dict or not set(change) <= {"del", "set"} or not deletes and "del" in change:
        raise ValueError("Conversation journal record %d changes a table that does not fit" % seq)
    removed, values = change.get("del", []), change.get("set", [])
    if type(removed) is not list or type(values) is not list:
        raise ValueError("Conversation journal record %d has a malformed table" % seq)
    for key in removed:
        if type(key) is not str or key not in current:
            raise ValueError("Conversation journal record %d removes an unknown entry" % seq)
        del current[key]
    for pair in values:
        if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str:
            raise ValueError("Conversation journal record %d has a malformed entry" % seq)
        current[pair[0]] = pair[1]
    return current


def _apply_ledger(raw, change, seq):
    if type(change) is dict and set(change) == {"all"}:
        raw["invocations"] = change["all"]
        return
    ledger = raw.get("invocations")
    if (type(change) is not dict or not set(change) <= {"closed", "add"} or type(ledger) is not dict
            or type(ledger.get("entries")) is not dict):
        raise ValueError("Conversation journal record %d changes a ledger that does not fit" % seq)
    if "closed" in change:
        ledger["closed"] = change["closed"]
    added = change.get("add", [])
    if type(added) is not list:
        raise ValueError("Conversation journal record %d has a malformed ledger" % seq)
    for pair in added:
        # The ledger only grows: an entry written twice is not a record this writer makes.
        if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str or pair[0] in ledger["entries"]:
            raise ValueError("Conversation journal record %d has a malformed ledger entry" % seq)
        ledger["entries"][pair[0]] = pair[1]


def rewrite(path, data):
    """Replace the file with ``data`` (a base line): temporary file, ``fsync``, ``os.replace``.

    Returns the new file's identity (``_identity``), or None when the file at
    ``path`` is no longer the one written (the next save rewrites again). On
    failure the temporary file is removed and the old file is unchanged.
    """
    temporary = path.parent / ("%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | BINARY, 0o600)
    try:
        try:
            _write_all(fd, data)
            os.fsync(fd)
            written = os.fstat(fd)
        finally:
            os.close(fd)
        _replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    # A rename may update the file's change time: read it from the path, and keep it only for the same file.
    try:
        status = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    same = (status.st_dev, status.st_ino, status.st_size) == (written.st_dev, written.st_ino, written.st_size)
    return _identity(status) if same else None


def _replace(source, target, attempts=5):
    """``os.replace``; on Windows retried a few times while another process holds ``target`` without
    delete sharing (an indexer or antivirus scan), the documented cause of a transient access error."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def discard_stale(path, older_than=60.0):
    """Remove what a crash left beside ``path`` over ``older_than`` seconds ago (never the chat file itself).

    Temporary files of its rewrites and of its kept copies (``session_kept``), the legacy ``<id>.tmp``, and an
    empty kept name: a copy creates its name empty and fills it in milliseconds (``session_kept._copy_new``),
    so an empty one that old was left by a kill mid-copy. One scan of the folder.
    """
    now, prefix, legacy = time.time(), path.name + ".", path.with_suffix(".tmp").name
    try:
        entries = os.scandir(path.parent)
    except OSError:
        return  # No folder: nothing was left in it.
    with entries:
        for entry in entries:
            name = entry.name
            temporary = name == legacy or name.startswith(prefix) and name.endswith(".tmp")
            if not temporary and not (name.startswith(prefix) and name.endswith(".unreadable")):
                continue
            try:
                status = entry.stat(follow_symlinks=False)
                if (stat.S_ISREG(status.st_mode) and now - status.st_mtime > older_than
                        and (temporary or status.st_size == 0)):
                    os.unlink(entry.path)
            except OSError:
                continue  # Gone, or another writer's: it is never read.


def _identity(status):
    """What a writer checks before it appends: any other writer's change alters at least one of these."""
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns


def append(path, identity, data):
    """Append one record line to the file last written as ``identity``; returns the new identity.

    ``FileChanged`` when the file is not that one (nothing is written).
    Otherwise a failed write is cut back to the old size where possible and
    the error raised; the caller rewrites the whole file next time.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | BINARY | _NOFOLLOW)
    except OSError as error:  # Gone, a link, or not writable: a rewrite replaces it or reports why it cannot.
        raise FileChanged("The conversation journal cannot be appended to: %s" % error) from error
    try:
        if fcntl is not None:
            # Another app on this profile appends between our check and our write only after this one is done,
            # so it sees our record and rewrites instead; and a failed write is never cut back over its record.
            fcntl.flock(fd, fcntl.LOCK_EX)
        status = os.fstat(fd)
        if _identity(status) != identity or status.st_nlink != 1:
            raise FileChanged("The conversation journal was changed by another writer")
        try:
            _write_all(fd, data)
            os.fsync(fd)
            status = os.fstat(fd)
        except BaseException:
            try:
                os.ftruncate(fd, identity[2])
            except OSError:
                pass
            raise
        return _identity(status)
    finally:
        os.close(fd)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("The conversation journal write made no progress")
        view = view[written:]


class FileChanged(OSError):
    """The file on disk is not the one this writer last wrote: rewrite, never append."""
