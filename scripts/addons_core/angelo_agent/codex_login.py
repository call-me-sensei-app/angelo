# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Codex login file a shared app-server started with (``codex_runtime``): where it is, its stamp, and the
refusal when it is there but cannot be read.

A running app-server keeps the login it started with, so a sign-out or account switch made outside Angelo (the Codex
CLI or app sharing the profile) changes only this file; every connect compares its stamp with the one taken before
the process started (``codex_runtime.Runtime.check_login``).
"""

import hashlib
import os
from pathlib import Path

from .protocol import RpcError
from .ui_text import n_, notice

# Read by the person (translated) and by the model (English, as the chat's error row): what happened, what to do.
LOGIN_UNREADABLE = n_("Angelo can't read Codex's sign-in file (auth.json in Codex's folder). Check that the file "
                      "can be read, or sign in to Codex again, then send the message again.")
UNREADABLE = "unreadable"   # ``login_stamp`` of a login file that is there but cannot be read.


class LoginUnreadable(RpcError):
    """The login file is there but cannot be read: the connect refuses at once (``codex_runtime.transport``),
    naming no path."""

    message_not_sent = True   # ``Controller._run``: a connect sends nothing; the message goes back to the box,
    show_reason = True        # and the chat says why.

    def __init__(self):
        message = notice(LOGIN_UNREADABLE)
        super().__init__(message["text"])
        self.i18n = message.get("i18n")


def login_file(env):
    """The ChatGPT login file of the app-server's profile (``CODEX_HOME``, else ``~/.codex``, as ``codex_command``)."""
    return Path(env.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"


def login_stamp(path):
    """The login file's presence, device, inode, modification time, size and SHA-256 of its bytes.

    Codex rewrites the file in place (truncate, then write), so its bytes decide: a same-size rewrite keeps every stat
    field in a coarse clock's tick (NTFS, FAT) or with its time put back. A token refresh rewrites it too (rarely: a
    profile used for five days of runs kept the same file) and retires the process as well: one process start. A
    file that is there but cannot be read is ``UNREADABLE``: never compared, a connect refuses on it
    (``LoginUnreadable``). A missing file is ``None``, a sign-out.
    """
    try:
        with open(path, "rb") as handle:   # A few kilobytes, read once per connect.
            info, digest = os.fstat(handle.fileno()), hashlib.sha256(handle.read()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return UNREADABLE
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size, digest
