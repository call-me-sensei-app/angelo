# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a build keeps of a chat it cannot fully read: ``<id>.json.<UTC time>[-n].unreadable`` beside the chat.

``SessionStore.load`` keeps the file (``preserve``) before anything rewrites a
chat that loaded unverified or with a top-level field this build does not know,
so a later build may still read it (``session_journal``, "Other builds"). A key
bound later is redacted in these copies too (``redact_kept``,
``activity_rows.bind_key``). Nothing removes a kept copy; a load removes only an
empty kept name a kill left mid-copy (``session_journal.discard_stale``).

Only a top-level field or a record part this build does not know, or a version
it does not read, keeps the file: a later build's field nested inside one this
build cleans (an option, a task-memory or runtime-session field) is dropped by
the first save here, silently and without a copy. A later build must therefore
add data at the top level of the document or of a record, or raise the version.
"""

import errno
import json
import os
import re
import stat
import time

from . import session_journal

# Names one UTC second can give the copies of one chat (two instances keeping it at once take the next).
KEPT_NAMES = 100
_SPACE = re.compile(r"[ \t\n\r]*")


def kept_copies(path):
    """The copies kept of the chat file ``path``, oldest name first."""
    prefix = path.name + "."
    with os.scandir(path.parent) as entries:
        return sorted(path.parent / entry.name for entry in entries
                      if entry.name.startswith(prefix) and entry.name.endswith(".unreadable"))


def preserve(path):
    """Keep the file at ``path`` beside it as ``<name>.<UTC time>.unreadable`` before anything rewrites it.

    A hard link where the file system has them (atomic, no copy; the rewrite then replaces only the chat's own
    name), else a synced copy, then the folder is synced. The kept name is created exclusively: a second
    instance keeping the same chat in the same second never replaces another's copy, it takes the next name
    (``-2``, ``-3``, ...), or reuses one that already holds this very file (a hard link of it). A copy that
    fails leaves neither its temporary file nor the name. Returns the name kept, the name of an earlier copy
    of the same file, or "" when there is no regular file to keep; raises ``OSError`` when it cannot keep it.
    """
    try:
        status = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return ""
    if not stat.S_ISREG(status.st_mode):
        return ""  # A link or anything else is replaced as itself; what it points to is never written.
    data = None
    for earlier in kept_copies(path):
        try:
            kept = os.stat(earlier, follow_symlinks=False)
            if (kept.st_dev, kept.st_ino) == (status.st_dev, status.st_ino):
                return earlier.name
            if kept.st_size == status.st_size:
                data = path.read_bytes() if data is None else data
                if earlier.read_bytes() == data:
                    return earlier.name
        except FileNotFoundError:
            continue  # Another instance's copy that failed and was removed meanwhile.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    links = True
    for number in range(1, KEPT_NAMES + 1):
        name = "%s.%s%s.unreadable" % (path.name, stamp, "-%d" % number if number > 1 else "")
        target = path.parent / name
        try:
            if links:
                try:
                    os.link(path, target)  # Checked above: a regular file, not a link. Never replaces a name.
                except FileExistsError:
                    raise
                except OSError:  # No hard links here (FAT, exFAT, some network shares): a synced copy.
                    links = False
            if not links:
                _copy_new(path, target)
        except FileExistsError:  # Another instance's copy holds this name.
            try:
                held = os.stat(target, follow_symlinks=False)
            except OSError:
                continue
            if (held.st_dev, held.st_ino) == (status.st_dev, status.st_ino):
                return name  # It kept this very file.
            continue
        _sync_folder(path.parent)  # The kept name is durable before any rewrite of the chat.
        return name
    raise FileExistsError(errno.EEXIST, "No free name to keep the conversation journal under", str(path))


def _copy_new(path, target):
    """A synced copy of ``path`` at the new name ``target``; ``FileExistsError`` when the name is taken.

    The name is created exclusively and empty first, then the copy (``session_journal.rewrite``: its own
    temporary file, synced) replaces only that placeholder. On failure neither the temporary file nor the
    name is left.
    """
    os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | session_journal.BINARY, 0o600))
    try:
        with open(path, "rb") as source:
            data = source.read()
        session_journal.rewrite(target, data)
    except BaseException:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise


def redact_kept(path, secret):
    """Redact ``secret`` in the copies kept of the chat file ``path``; returns the names that may still hold it.

    A copy is a format this build may not read, so only the key's bytes change: its JSON-encoded forms (raw
    UTF-8 and ``\\u`` escapes) become ``[REDACTED]``, inside the JSON string that held the key. A copy is
    written back only when it then holds as many JSON values as before, every line counted, also past a damaged
    one (``_json_values``): a key whose bytes also occur outside a string is left, and returned. It is written
    atomically and synced: ``session_journal.rewrite`` (a temporary file, ``fsync``, ``os.replace``), then the
    folder. A copy is never changed in place, so one that is still a hard link of the chat file leaves the chat
    file as it is (its next save rewrites it).
    """
    if not secret:
        return []
    forms = {json.dumps(secret, ensure_ascii=escaped)[1:-1].encode("utf-8") for escaped in (False, True)}
    try:
        copies = kept_copies(path)
    except OSError:
        return []  # No folder to list: no copy either.
    held, written = [], False
    for kept in copies:
        try:
            if not stat.S_ISREG(os.stat(kept, follow_symlinks=False).st_mode):
                continue
            data = kept.read_bytes()
            clean = data
            for form in forms:
                clean = clean.replace(form, b"[REDACTED]")
            if clean == data:
                continue
            if _json_values(clean) < _json_values(data):
                held.append(kept.name)
                continue
            session_journal.rewrite(kept, clean)
            written = True
        except OSError:
            held.append(kept.name)
    if written:
        _sync_folder(path.parent)
    return held


def _json_values(data):
    """How many JSON values ``data`` holds one after another (a base and its records). Past a value that does not
    parse (damage, a torn record) counting resumes on the next line, so every record line is checked."""
    text, decoder, count, index = data.decode("utf-8", "replace"), json.JSONDecoder(), 0, 0
    while True:
        index = _SPACE.match(text, index).end()
        if index == len(text):
            return count
        try:
            index = decoder.raw_decode(text, index)[1]
            count += 1
        except ValueError:
            end = text.find("\n", index)
            if end < 0:
                return count
            index = end + 1


def _sync_folder(folder):
    if os.name != "nt":
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
