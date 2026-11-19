# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""CLI commands on the service thread: bounds, envelopes and projection.

Nothing here touches ``bpy``. Work that needs Blender or controller state is a
``Work`` item on a bounded queue that ``external_host`` drains from ``ui.pump``
on the main thread; the connection thread waits for it with a timeout.

No client runs a tool here: since 2.0.1 an external agent works through Angelo's agent
(``external_agent``), and ``call`` is refused for every client (``external_service``).
"""

import errno
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from . import kept_state, owned_paths, shared_replace

WORK_QUEUE_SIZE = 16
MAIN_THREAD_TIMEOUT = 10.0
RATE_PER_SECOND = 20
AUDIT_BYTES = 256 * 1024
PATH_KEYS = frozenset({"path", "filepath", "file_path", "directory", "root"})
_ABSOLUTE = re.compile(r"(?:/|~|[A-Za-z]:[\\/]|\\\\)")


def default_roots():
    """Private roots knowable without Blender; the host adds state/profile/project roots."""
    import tempfile
    return ((str(Path.home()), "<home>"), (tempfile.gettempdir(), "<temp>"))


def _root_pattern(roots):
    usable = []
    for path, token in roots:
        for variant in {str(path), os.path.realpath(str(path))}:
            variant = variant.rstrip("/\\")
            if len(variant) >= 4 and _ABSOLUTE.match(variant):
                usable.append((variant, token))
    usable.sort(key=lambda item: len(item[0]), reverse=True)
    if not usable:
        return None, {}
    tokens = dict(usable)
    pattern = re.compile("|".join(re.escape(path) for path, _ in usable) + r"(?=[/\\]|$|[^\w.\-])")
    return pattern, tokens


def project(value, roots):
    """Remove private absolute paths from a host result before it leaves Angelo.

    ``path``-like keys holding absolute paths become ``"<withheld>"``; a
    ``path`` also gains an opaque ``view_id`` digest for a future ``view``
    command. Private roots inside other strings become placeholder tokens.
    """
    pattern, tokens = _root_pattern(roots)

    def strip(text):
        return pattern.sub(lambda match: tokens[match.group(0)], text) if pattern else text

    def walk(item, depth):
        if depth > 96:
            return None
        if isinstance(item, str):
            return strip(item)
        if isinstance(item, list):
            return [walk(entry, depth + 1) for entry in item]
        if isinstance(item, dict):
            output = {}
            for key, entry in item.items():
                if key in PATH_KEYS and isinstance(entry, str) and _ABSOLUTE.match(entry):
                    output[key] = "<withheld>"
                    if key == "path" and "view_id" not in item:
                        output["view_id"] = hashlib.sha256(entry.encode("utf-8", "replace")).hexdigest()[:32]
                else:
                    output[strip(key) if isinstance(key, str) else key] = walk(entry, depth + 1)
            return output
        return item

    return walk(value, 0)


class Work:
    """One main-thread request. The host claims it once; a timed-out waiter abandons it."""

    def __init__(self, kind, client, payload):
        self.kind, self.client, self.payload = kind, client, payload
        self.enqueued = time.monotonic()
        self.claimed_at = self.completed_at = None
        self.admitted = False
        self.response = None
        self._event = threading.Event()
        self._claimed = threading.Event()
        self._lock = threading.Lock()
        self._state = "queued"

    def claim(self):
        with self._lock:
            if self._state != "queued":
                return False
            self._state, self.claimed_at = "claimed", time.monotonic()
            self._claimed.set()
            return True

    def wait_claimed(self, timeout):
        return self._claimed.wait(timeout)

    def abandon(self):
        """True when the host never claimed this work; it will now never run."""
        with self._lock:
            if self._state == "queued":
                self._state = "abandoned"
                return True
            return False

    def complete(self, response):
        with self._lock:
            if self._event.is_set():
                return
            self.response, self.completed_at = response, time.monotonic()
            self._event.set()

    def wait(self, timeout):
        return self._event.wait(timeout)


class WorkQueue:
    def __init__(self, size=WORK_QUEUE_SIZE):
        self._queue = queue.Queue(maxsize=size)

    def submit(self, work):
        try:
            self._queue.put_nowait(work)
            return True
        except queue.Full:
            return False

    def take(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def fail_all(self, response):
        while (work := self.take()) is not None:
            if work.claim():
                work.complete(response)

    def fail_label(self, label, response):
        """Answer every queued command of ``label`` with ``response`` (main thread, the only consumer); the others
        stay queued in their order."""
        kept = []
        while (work := self.take()) is not None:
            if work.client.label == label:
                if work.claim():
                    work.complete(response)
            else:
                kept.append(work)
        for work in kept:
            self.submit(work)

    def __len__(self):
        return self._queue.qsize()


class RateLimiter:
    """At most ``limit`` admitted calls per label in any one-second window."""

    def __init__(self, limit=RATE_PER_SECOND, clock=time.monotonic):
        self.limit, self.clock = limit, clock
        self._seen = {}
        self._lock = threading.Lock()

    def allow(self, label):
        now = self.clock()
        with self._lock:
            window = self._seen.setdefault(label, deque())
            while window and now - window[0] >= 1.0:
                window.popleft()
            if len(window) >= self.limit:
                return False
            window.append(now)
            if len(self._seen) > 256:
                self._seen.pop(next(iter(self._seen)))
            return True


class AuditLog:
    """Bounded owner-only ``cli/audit.jsonl``: metadata and argument digests, never arguments or results."""

    def __init__(self, path, limit=AUDIT_BYTES):
        self.path, self.limit = Path(path), limit
        self._lock = threading.Lock()

    def record(self, **fields):
        line = (json.dumps(dict(fields, time=round(time.time(), 3)), sort_keys=True) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with self._lock:
            try:
                descriptor = os.open(owned_paths.require_writable(self.path), flags, 0o600)
                try:
                    os.write(descriptor, line)
                    size = os.fstat(descriptor).st_size
                finally:
                    os.close(descriptor)
                if size > self.limit:
                    self._trim()
            except OSError:
                pass  # Audit is best-effort; transcripts and the invocation journal stay authoritative.

    def _trim(self):
        data = shared_replace.read_bytes(self.path)
        kept = data[-(self.limit // 2):]
        kept = kept[kept.find(b"\n") + 1:]
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        _replace_with(temporary, lambda handle: handle.write(kept), self.path, "wb")   # Another appender: retried.


def _digest(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None


class ClientRegistry:
    """label -> conversation ID in ``<state>/cli/clients.json`` (owner-only, atomic replace).

    A label is routing and display data, not a security principal.
    """

    def __init__(self, state_directory):
        self.path = Path(state_directory) / "cli" / "clients.json"

    def load(self):
        return self._clients(kept_state.read(self.path)[0])

    @staticmethod
    def _clients(raw):
        clients = raw.get("clients") if isinstance(raw, dict) else None
        output = {}
        for label, record in (clients or {}).items() if isinstance(clients, dict) else ():
            try:
                output[label] = str(uuid.UUID(record["session_id"]))
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
        return output

    def save(self, changes):
        """Save ``changes`` ({label: conversation}) over what the file holds now, never over a file it can't read: every
        other app's conversation is kept (the caller's own copy may come from a read that failed, or be stale). A
        damaged or wrong-shape file is kept beside it first."""
        fresh, state = kept_state.read(self.path)
        if state == kept_state.OK and not (isinstance(fresh, dict) and isinstance(fresh.get("clients"), dict)):
            state = kept_state.DAMAGED
        if state == kept_state.UNKNOWN or (state == kept_state.DAMAGED and not kept_state.keep_before_write(self.path)):
            raise OSError(errno.EIO, "The local apps' conversations can't be read now; nothing was changed")
        mapping = {**(self._clients(fresh) if state == kept_state.OK else {}), **changes}
        owned_paths.require_writable(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name("clients." + uuid.uuid4().hex + ".tmp")

        def write(handle):
            json.dump({"version": 1, "clients": {label: {"session_id": identity}
                                                 for label, identity in mapping.items()}}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with(temporary, write, self.path)


def _replace_with(temporary, write, target, mode="w"):
    """Write the new owner-only ``temporary`` (``write(handle)``) and replace ``target`` with it (``shared_replace``:
    a refusal outlasting its retries is raised); the temporary file never outlives a failure."""
    owned_paths.require_writable(target), owned_paths.require_writable(temporary)   # C first, for both names.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as handle:
            write(handle)
        shared_replace.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def external_label(state_directory, session_id):
    """The client label owning a conversation, or "" (used when ui builds a controller)."""
    for label, identity in ClientRegistry(state_directory).load().items():
        if identity == session_id:
            return label
    return ""
