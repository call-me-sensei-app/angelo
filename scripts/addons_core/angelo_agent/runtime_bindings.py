# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a model runtime child Angelo runs for a worker chat is bound to, and the ends of that binding.

* ``binding(chat)``: a child serves only chats of its own connection, mode and key, ``(mode, connection_id,
  fingerprint)``. The fingerprint is an HMAC of the key under a secret of this process: it is computed and compared
  here and never leaves Angelo. A closed chat's child (the worker's Codex pool keeps it, ``codex_runtime.pool_key``,
  which never crosses connections) may serve the next chat of the same binding only (``RuntimeService``).
* ``end_connection`` / ``end_modes``: a connection removed, re-keyed or signed in again, or a Team sign-out, ends
  every child of that connection in every worker at once, a closed chat's included; ``end_mode_key`` does so for a
  mode's own key, and ``end_personal`` every personal connection's once the managed policy refuses them.
  ``retire_connection`` retires the window's shared app-servers too. Every connection site calls these itself
  (``ui``: remove, re-key, the reload's removals, sign-in and sign-out; ``team_account_ui``: Team sign-out and,
  through ``connection_access.end_refused``, every account change that may tighten the policy), so no site depends
  on which chats are open; ``Controller.invalidate_runtime`` calls it as well.
* ``Churn``: a chat starts at most ``LAUNCH_BURST`` children within ``LAUNCH_WINDOW`` seconds (launches and
  prewarms); beyond it a launch is refused with a clear error.
* ``under(path, roots)``: a local file the worker names for the child to read (a turn's ``localImage``) must
  resolve, after symbolic links, inside one of the chat's reference folders. ``read_image`` then reads it here, never
  following a link at any part of that path (so a file swapped for a link after the check is refused), and only a
  regular PNG, JPEG or WebP file of at most ``IMAGE_BYTES``: the child is sent its bytes, never a path to open.

Standard library only and no ``bpy``.
"""

import base64
import collections
import hashlib
import hmac
import os
import secrets
import stat
import sys
import threading
import time

LAUNCH_BURST = 30       # A chat's launches within the window: an honest chat starts a few per message at most.
LAUNCH_WINDOW = 60.0
IMAGE_BYTES = 5 * 1024 * 1024   # A reference image's own limit (``references.MAX_BYTES``).
IMAGE_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))   # Plus WebP (``_mime``).

_SALT = secrets.token_bytes(32)   # This process's only: a fingerprint means nothing anywhere else.


def fingerprint(key):
    return hmac.new(_SALT, (key or "").encode("utf-8"), hashlib.sha256).hexdigest()


def binding(chat):
    """``(mode, connection_id, fingerprint)`` of a ``runtime_service.LaunchChat``."""
    return (str(chat.mode or ""), str(getattr(chat, "connection_id", "") or ""), fingerprint(chat.api_key))


def matches(chat, launched):
    """Whether a child launched with binding ``launched`` may serve ``chat`` now: the same mode and connection, and a
    key that is not another one. A key Angelo does not hold at this moment (none, or the window's keys briefly gone
    while it reloads) is unknown, never a mismatch: a key removed, replaced or withdrawn ends its children itself
    (``end_connection``, ``end_modes``, ``end_mode_key``), so only a different key present now refuses."""
    current = binding(chat)
    return current[:2] == tuple(launched[:2]) and (not chat.api_key or current[2] == launched[2])


def _services():
    module = sys.modules.get(__package__ + ".runtime_service")   # None loaded: no worker runs a child.
    return list(getattr(module, "_SERVICES", ()))


def end_connection(connection_id):
    """Every worker's children of ``connection_id`` end now; how many."""
    if not connection_id:
        return 0
    return sum(service.end(lambda bound: bound[1] == str(connection_id)) for service in _services())


def end_modes(modes):
    """Every worker's children of a connection in ``modes`` (a Team sign-out: its team modes) end now."""
    modes = frozenset(modes)
    return sum(service.end(lambda bound: bound[0] in modes) for service in _services())


def end_personal():
    """The managed policy forbids personal connections (``connection_access.end_refused``): every worker's personal
    children end now, a closed chat's included: a stored connection's and a mode's own key's (no connection) alike.
    A mode not known as a team one counts as personal; only a team connection's children are not this rule's."""
    from .provider_registry import TEAM_MODES
    return sum(service.end(lambda bound: bound[0] not in TEAM_MODES) for service in _services())


def end_mode_key(mode):
    """A mode's own key (a chat with no connection record runs on it) was replaced: that key's children end now."""
    return sum(service.end(lambda bound: bound[0] == str(mode) and not bound[1]) for service in _services())


def retire_connection(connection_id):
    """``Controller.invalidate_runtime``: the connection's window app-servers retire, its worker children end."""
    from . import codex_runtime
    codex_runtime.retire_connection(connection_id)
    end_connection(connection_id)


class Churn:
    """Launches per chat within ``LAUNCH_WINDOW``: ``admit(session)`` is False beyond ``LAUNCH_BURST``."""

    def __init__(self, burst=LAUNCH_BURST, seconds=LAUNCH_WINDOW, clock=time.monotonic):
        self.burst, self.seconds, self.clock = burst, seconds, clock
        self._lock = threading.Lock()
        self._times = {}

    def admit(self, session):
        now = self.clock()
        with self._lock:
            times = self._times.setdefault(session, collections.deque())
            while times and now - times[0] >= self.seconds:
                times.popleft()
            if len(times) >= self.burst:
                return False
            times.append(now)
            for name in [name for name, seen in self._times.items() if not seen]:
                del self._times[name]
            return True


def under(path, roots):
    """Whether ``path`` is an absolute file path that resolves inside one of ``roots`` (each resolved too)."""
    if not isinstance(path, str) or not path or "\x00" in path or not os.path.isabs(path):
        return False
    real = os.path.realpath(path)
    for root in roots:
        if not root:
            continue
        base = os.path.realpath(str(root))
        try:
            if os.path.commonpath([real, base]) == base and real != base:
                return True
        except ValueError:   # Another drive on Windows.
            continue
    return False


def _mime(head):
    """The image type of a file starting ``head``, as Codex names it, or None (a type a turn never attaches)."""
    for magic, mime in IMAGE_TYPES:
        if head.startswith(magic):
            return mime
    return "image/webp" if head[:4] == b"RIFF" and head[8:12] == b"WEBP" else None


_resolve = os.path.realpath


def _open_unlinked(real):
    """A descriptor of the file at ``real`` (a resolved path), opened one part at a time without following a link."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if os.name == "nt" or os.open not in os.supports_dir_fd:   # No per-part opening: the file itself, checked after.
        descriptor = os.open(real, flags | getattr(os, "O_BINARY", 0))
        if os.path.realpath(real) != real or not os.path.samestat(os.fstat(descriptor), os.lstat(real)):
            os.close(descriptor)
            raise OSError("The image moved while it was opened")
        return descriptor
    parts = real.split(os.sep)
    directory = os.open(os.sep, flags | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            following = os.open(part, flags | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        return os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)


def read_image(path, roots, limit=IMAGE_BYTES):
    """``data:`` URL of the image at ``path`` inside one of ``roots`` (``under``), read here without following any
    link, or None: outside, a link, not a regular file, larger than ``limit``, or not a PNG, JPEG or WebP image."""
    if not isinstance(path, str) or not path or "\x00" in path or not os.path.isabs(path):
        return None
    real = _resolve(path)   # Resolved once: what is checked is what is opened, a link at no part of it.
    if not under(real, roots):
        return None
    try:
        descriptor = _open_unlinked(real)
    except (OSError, ValueError):
        return None
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size > limit:
            return None
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    mime = _mime(data[:12])
    if len(data) > limit or mime is None:
        return None
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
