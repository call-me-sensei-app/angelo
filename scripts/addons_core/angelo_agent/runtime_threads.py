# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The native threads a worker chat may resume or fork, as Angelo itself saw them opened (``runtime_service``).

A worker chat's model runtime child runs in Angelo (design A′), so a ``thread/resume`` or ``thread/fork`` the
worker sends opens whatever thread it names on the person's account. Only threads Angelo saw that chat's own
children open are named as themselves (``runtime_filter.Filter``): the ids in the child's answers to the chat's
``thread/start``, ``thread/resume`` and ``thread/fork`` (Codex) and the sessions its stream names (Claude). Nothing
the worker writes counts: not the chat's journal (``native_id``) nor a remembered base in a folder it writes.

* Per chat (its session id): the threads its children opened, the newest ``MAX_THREADS``.
* Per connection (mode and connection id): the warm-up bases (``codex_bases``) any of its chats' children started:
  a thread whose turn was exactly the neutral ``WARM_UP`` exchange, which every chat of the connection forks.
* Kept in ``FILE`` in Angelo's account store (``auth_directory``, ``ANGELO_CONNECTIONS_DIR``), which a worker never
  gets (its own account store is in its private folder), so a chat resumed after Angelo restarts still resumes its
  thread. The file is read once per process; a write merges with what another Angelo wrote meanwhile and replaces it
  atomically. A file that cannot be read counts as empty, and one that cannot be written leaves the record in this
  process only: an unknown thread is never resumed, so the chat continues in a new context.

Standard library only and no ``bpy``.
"""

import json
import os
import threading
import uuid
from pathlib import Path

FILE = "worker-threads.json"
MAX_THREADS = 64     # A chat's threads kept (its own, resumed and forked: a few per context).
MAX_CHATS = 512      # Chats kept; the least recently updated is dropped first.
MAX_ID = 128

_lock = threading.Lock()
_records = {}   # folder -> ThreadRecord


def _valid(thread):
    return isinstance(thread, str) and 0 < len(thread) <= MAX_ID and thread.isprintable()


class ThreadRecord:
    def __init__(self, folder=None):
        """``folder``: Angelo's account store, or None to keep the record in this process only."""
        self.path = Path(folder) / FILE if folder else None
        self._lock = threading.Lock()
        self._data = None

    def _load(self):
        if self._data is None:
            self._data = _read(self.path)
        return self._data

    def known(self, session, scope=None):
        """The threads ``session``'s chat may resume or fork: its own, and its connection's bases."""
        with self._lock:
            data = self._load()
            return frozenset(data["chats"].get(session, ())) | frozenset(data["bases"].get(_scope(scope), ()))

    def bases(self, scope):
        """The warm-up bases of connection ``scope`` (mode and connection id)."""
        with self._lock:
            return frozenset(self._load()["bases"].get(_scope(scope), ()))

    def opened(self, session, thread):
        self._add("chats", session, thread)

    def based(self, scope, thread):
        self._add("bases", _scope(scope), thread)

    def _add(self, part, name, thread):
        if not isinstance(name, str) or not name or not _valid(thread):
            return
        with self._lock:
            data = self._load()
            if thread in data[part].get(name, ()):
                return
            _put(data, part, name, thread)
            if self.path is None:
                return
            merged = _read(self.path)
            for kind in ("chats", "bases"):
                for key, threads in data[kind].items():
                    for value in threads:
                        if value not in merged[kind].get(key, ()):
                            _put(merged, kind, key, value)
            if _write(self.path, merged):
                self._data = merged


def _scope(scope):
    return json.dumps(list(scope)) if isinstance(scope, (list, tuple)) else str(scope or "")


def _put(data, part, name, thread):
    threads = [value for value in data[part].pop(name, []) if value != thread] + [thread]
    data[part][name] = threads[-MAX_THREADS:]   # Re-inserted last: the most recently updated.
    while len(data[part]) > MAX_CHATS:
        data[part].pop(next(iter(data[part])))


def _read(path):
    empty = {"chats": {}, "bases": {}}
    if path is None:
        return empty
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return empty
    if not isinstance(value, dict):
        return empty
    for part in empty:
        entries = value.get(part)
        if isinstance(entries, dict):
            empty[part] = {name: [thread for thread in threads if _valid(thread)][-MAX_THREADS:]
                           for name, threads in list(entries.items())[-MAX_CHATS:]
                           if isinstance(name, str) and isinstance(threads, list)}
    return empty


def _write(path, data):
    temporary = path.with_name(".%s.%s.tmp" % (FILE, uuid.uuid4().hex))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False


def record(folder):
    """The process's one record kept in ``folder`` (None: in this process only)."""
    key = str(folder) if folder else None
    with _lock:
        if key not in _records:
            _records[key] = ThreadRecord(key)
        return _records[key]
