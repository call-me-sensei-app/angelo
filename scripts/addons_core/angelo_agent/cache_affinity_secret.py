# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The per-user secret that keys the account in a cache-affinity key (``cache_affinity.inputs``).

Every other input of a key is public (Angelo's instructions, tools and schema are open source) or a small
enumeration (the model), and the account's identity is a digest of its type and email: without a secret,
anyone who guesses an email could recompute a person's key. The account is therefore digested with
HMAC-SHA256 under 32 random bytes that only this user's Angelo knows.

* Created once, on the first keyed thread, as 64 hexadecimal digits in ``cache-affinity-secret`` inside
  Angelo's per-user data folder (``storage.data_directory``: ``~/Library/Application Support/Angelo``,
  ``%LOCALAPPDATA%\\Angelo``), owner-only (0600 in a 0700 folder on macOS and Linux). Not the state folder
  (``ANGELO_STATE_DIR``) or the account store (``ANGELO_CONNECTIONS_DIR``), which a benchmark sets per trial:
  every process and trial of one user on one machine shares the secret, so they share the key.
* Written to a private temporary file and linked into place, so a concurrent launch either wins and is
  read, or finds the finished file; a file that is not a valid secret is replaced.
* ``ANGELO_CACHE_KEY_SECRET`` (64 hexadecimal digits, only that spelling) replaces it, for a measurement
  run that must reproduce its keys on another machine or user. Any other value is an error.
* When the folder cannot be written, a secret held only by this process is used and said once on standard
  error: keys still reveal nothing, but they change with every launch.

Read once per process. Standard library only; no ``bpy``.
"""

import os
import re
import secrets
import sys
import threading
import uuid
from pathlib import Path

from . import product_env, storage
from .protocol import RpcError

FILE = "cache-affinity-secret"
VARIABLE = "CACHE_KEY_SECRET"  # ANGELO_CACHE_KEY_SECRET (product_env.current: no deprecated spelling)
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_lock = threading.Lock()
_loaded = {}


def secret(environ=None, folder=None):
    """32 bytes: ``ANGELO_CACHE_KEY_SECRET``, else the stored per-user secret (created once)."""
    value = product_env.current(VARIABLE, environ)
    if value is not None:
        if not _HEX.match(value.strip()):
            raise RpcError("ANGELO_CACHE_KEY_SECRET must be 64 hexadecimal digits (32 bytes), or unset")
        return bytes.fromhex(value.strip())
    try:
        folder = Path(folder) if folder is not None else storage.data_directory(environ)
    except ValueError as error:  # A relative XDG_STATE_HOME: no per-user folder to keep it in.
        folder, unusable = None, error
    with _lock:
        if folder not in _loaded:
            _loaded[folder] = _stored(folder) if folder is not None else _process_only("no data folder", unusable)
        return _loaded[folder]


def _read(path):
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return bytes.fromhex(text) if _HEX.match(text) else None


def _stored(folder):
    path = folder / FILE
    found = _read(path)
    if found is not None:
        return found
    value = secrets.token_bytes(32)
    temporary = folder / (".%s.%s.tmp" % (FILE, uuid.uuid4().hex))
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(value.hex() + "\n")
        try:
            os.link(temporary, path)  # Fails if another launch put its secret there first: read that one.
        except FileExistsError:
            found = _read(path)
            if found is not None:
                return found
            os.replace(temporary, path)  # Not a secret (a damaged file): this one replaces it.
        except OSError:
            os.replace(temporary, path)  # A file system without hard links.
        return value
    except OSError as error:
        return _process_only(folder, error.strerror or error, value)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _process_only(folder, reason, value=None):
    print("Angelo: the cache-affinity secret could not be stored in %s (%s); this launch uses its own, so its "
          "prompt-cache keys are not shared with later launches" % (folder, reason), file=sys.stderr, flush=True)
    return value if value is not None else secrets.token_bytes(32)


def forget():
    """Read the secret again on its next use (tests; a profile switch)."""
    with _lock:
        _loaded.clear()
