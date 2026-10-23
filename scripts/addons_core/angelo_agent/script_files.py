# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connected app's script files, and the person's "Always allow this file while it's unchanged".

Contract: doc/angelo/external_mcp.rst, "Script files". Maintainer decision (2026-10-23): an opt-in standing
approval for an external app's script file, keyed by the app, the project, the file's path and its content hash;
any change asks again.

``read_script`` is how Angelo reads a file an app names (``angelo_run_script_file``): an absolute path, resolved
to its real path, opened without following a final symbolic link, a regular file of at most ``MAX_CHARACTERS``
characters of UTF-8, read once. The text Angelo reviews, hashes and runs is that one read: a file edited after it,
or swapped for a link, never changes what runs.

The approvals live in Angelo's state folder (``external-script-files.json``), per user and install, never in a
.blend or a chat. The file is the source of truth: every check reads it as it is now, and every change re-reads it
under a lock, so another Angelo instance never brings back an approval the person removed. Each is ``(app, project, path, sha256)``: the connected app's client name, the open saved .blend's
real path, the script's real path and the SHA-256 of its UTF-8 bytes. ``allow`` writes the file atomically before
the approval takes effect, ``forget`` before it ends, so a crash never brings back one the person removed. A
missing or unreadable file holds none. Whether an approval may apply at all (an External conversation in Approve
for me, ``AllowAutoApproval``, a saved project) is ``TrustedScripts``'s.

Standard library only; no bpy. ``attach``, ``allow`` and ``forget`` run on the main thread; ``read_script`` on an
app call's worker thread.
"""

import hashlib
import json
import os
import stat
import time
import uuid
from dataclasses import dataclass

FILE = "external-script-files.json"
# angelo_execute_python's code limit (tool_specs): a longer file could never run as one script.
MAX_CHARACTERS = 32768
_entries = []    # [{"app", "project", "path", "sha256", "allowed_at"}], as saved.
_path = None     # Where they are saved (ui.register: the state folder); None: in memory only (tests).


class ScriptFileError(ValueError):
    """Why a named file can't be run as a script; the app gets this text."""


@dataclass(frozen=True)
class ScriptFile:
    """One read of a script file: its real path, the SHA-256 of its bytes and its text."""
    path: str
    sha256: str
    text: str

    @property
    def name(self):
        return os.path.basename(self.path)


def read_script(path):
    """``ScriptFile`` for the file at ``path``, read once, or ``ScriptFileError`` saying why not."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ScriptFileError("Give the script file's absolute path")
    if not os.path.isabs(path):
        raise ScriptFileError("The script file's path must be absolute: %s" % path)
    real = os.path.realpath(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(real, flags)
    except OSError as error:
        raise ScriptFileError("Angelo can't open the script file %s: %s" % (path, error.strerror or error)) from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ScriptFileError("%s is not a regular file" % path)
        # One UTF-8 character is at most four bytes; anything larger can't fit the limit.
        if info.st_size > MAX_CHARACTERS * 4:
            raise ScriptFileError("%s is larger than one script can be (%d characters)" % (path, MAX_CHARACTERS))
        chunks, size = [], 0
        while True:
            block = os.read(descriptor, 65536)
            if not block:
                break
            size += len(block)
            if size > MAX_CHARACTERS * 4:
                raise ScriptFileError("%s is larger than one script can be (%d characters)" % (path, MAX_CHARACTERS))
            chunks.append(block)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ScriptFileError("%s is not UTF-8 text" % path) from None
    if not text.strip():
        raise ScriptFileError("%s is empty" % path)
    if len(text) > MAX_CHARACTERS:
        raise ScriptFileError("%s is larger than one script can be (%d characters)" % (path, MAX_CHARACTERS))
    return ScriptFile(real, hashlib.sha256(data).hexdigest(), text)


def text_digest(text):
    """The SHA-256 ``read_script`` gives a file whose text is ``text`` (UTF-8 round-trips exactly)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- the approvals -------------------------------------------------------------------------------------------
# The file is the one source of truth: every check reads its current contents (cached by its identity and time),
# and every change re-reads it under an exclusive lock and writes it through a temporary file of its own. Two
# Angelo instances, or a running instance and Disconnect in another, never undo each other's change: a removal
# made anywhere applies everywhere at the next check.

def _valid(entry):
    return (isinstance(entry, dict) and all(isinstance(entry.get(k), str) and entry.get(k)
                                            for k in ("app", "project", "path", "sha256"))
            and len(entry["sha256"]) == 64)


def _parse(data):
    try:
        saved = json.loads(data)
    except ValueError:
        return []
    entries = saved.get("files") if isinstance(saved, dict) and saved.get("version") == 1 else None
    return [dict(entry) for entry in entries if _valid(entry)] if isinstance(entries, list) else []


def _read(path):
    """``(entries, identity)`` of the file at ``path``: none, and None, when it is missing or unreadable."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:   # Never waits on a FIFO or follows a link put at the name: only a regular file is read (fails closed).
        descriptor = os.open(path, flags)
    except OSError:
        return [], None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return [], None
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            data = handle.read(MAX_STORE + 1)
    except OSError:
        return [], None
    finally:
        os.close(descriptor)
    if len(data) > MAX_STORE:
        return [], None
    return _parse(data.decode("utf-8", "replace")), (info.st_ino, info.st_size, info.st_mtime_ns)


MAX_STORE = 4 * 1024 * 1024
_cache = ([], None)    # (entries, identity) of the file as last read.
_revoked = set()       # Approvals removed here whose removal could not be saved: never honoured in this process.


_read_only = False    # The headless child reads the person's approvals and never changes them.


def attach(directory, read_only=False):
    """Use the approvals file in ``directory`` (register; ``read_only`` in a headless child). A missing or
    unreadable file holds none."""
    global _path, _entries, _cache, _read_only
    _path = os.path.join(os.fspath(directory), FILE)
    _read_only = bool(read_only)
    _cache = ([], None)
    _entries = []
    _revoked.clear()


def detach():
    """Stop using the file (unregister); in memory only, and empty, until attached again."""
    global _path, _entries, _cache, _read_only
    _path, _entries, _cache, _read_only = None, [], ([], None), False
    _revoked.clear()


def _current():
    """The approvals as they are now: the file's current contents, or the in-memory list when not attached."""
    global _cache
    if _path is None:
        return _entries
    try:
        info = os.stat(_path)
        identity = (info.st_ino, info.st_size, info.st_mtime_ns)
    except OSError:
        _cache = ([], None)
        return []
    if identity != _cache[1]:
        _cache = _read(_path)
    return [entry for entry in _cache[0] if _full(entry) not in _revoked]


def _full(entry):
    return _key(entry) + (entry["sha256"],)


def _key(entry):
    return entry["app"], entry["project"], entry["path"]


def allowed(app, project, path, sha256):
    """Whether the person allowed this exact file content for this app and project (the file as it is now)."""
    return bool(app and project) and any(_key(entry) == (app, project, path) and entry["sha256"] == sha256
                                         for entry in _current())


def previous(app, project, path):
    """The saved approval for this app, project and path (another content, after a change), or None."""
    return next((dict(entry) for entry in _current() if _key(entry) == (app, project, path)), None)


def entries(app=None):
    """The saved approvals, every app's or one app's (Preferences shows and removes them)."""
    return [dict(entry) for entry in _current() if app is None or entry["app"] == app]


def allow(app, project, script):
    """The person's "Always allow this file while it's unchanged": saved first, then in effect.

    One approval per app, project and path: a changed file's new approval replaces the old one. Raises ``OSError``
    when it can't be saved, and nothing changes then.
    """
    if not app or not project:
        raise ValueError("A script file is allowed only for a connected app and a saved project")
    entry = {"app": app, "project": project, "path": script.path, "sha256": script.sha256,
             "allowed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _change(lambda current: [e for e in current if _key(e) != _key(entry)] + [entry])
    _revoked.discard(_full(entry))   # The person allowed it again, here.


def shown_path(path):
    """A script's path as the person reads it on the card and in Preferences: the home folder as ``~``."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and home != "~" and path.startswith(home + os.sep) else path


def forget(app=None, path=None):
    """Remove approvals (every app's, one app's, or one file's of one app); saved first, then in effect.
    The number removed. ``OSError`` when the file can't be written (the removal then fails closed in memory
    for this process: see ``_change``)."""
    def keep(entry):
        return not ((app is None or entry["app"] == app) and (path is None or entry["path"] == path))
    return _change(lambda current: [e for e in current if keep(e)], removing=True)


def forget_at(directory, app):
    """``forget(app=...)`` on the approvals file in ``directory``, from code that never attached (Disconnect,
    a level change). The number removed."""
    global _path, _cache
    saved = _path, _cache
    _path, _cache = os.path.join(os.fspath(directory), FILE), ([], None)
    try:
        return forget(app=app)
    finally:
        _path, _cache = saved


def _change(update, removing=False):
    """Apply ``update`` (entries -> entries) to the approvals as they are on disk now, under an exclusive lock;
    the number of entries removed."""
    global _entries, _cache
    if _read_only:
        raise PermissionError("Script file approvals are changed only in Angelo's window")
    if _path is None:
        before = _entries
        _entries = update(list(before))
        return len(before) - len(_entries)
    with _locked(_path + ".lock"):
        current, _identity = _read(_path)
        updated = update(list(current))
        removed = len(current) - len(updated)
        if updated != current:
            try:
                _write(updated)
            except OSError:
                if removing:   # Never leave a removal undone in this process because the disk refused it.
                    kept = {_full(entry) for entry in updated}
                    _revoked.update(_full(entry) for entry in current if _full(entry) not in kept)
                raise
        _cache = (updated, None)   # Read again at the next check (the identity changed with the write).
    return removed


class _locked:
    """An exclusive advisory lock on ``path`` (created owner-only); a no-op where the platform has none."""

    def __init__(self, path):
        self.path, self.fd = path, None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            import fcntl
        except ImportError:   # Windows: the MCP transport is not available there yet.
            return self
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)   # Closing releases the lock.
        return False


def _write(updated):
    folder = os.path.dirname(_path)
    temporary = os.path.join(folder, ".%s.%s.tmp" % (FILE, uuid.uuid4().hex))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "files": updated}, handle, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    if os.name != "nt":   # The rename itself survives a crash once its folder is synced.
        directory = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
