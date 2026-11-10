# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The signed-in organization's app policy, and the last one received, kept for a launch without the network.

The console sends the organization's app policy (``orgs.app_policy``: the
switches an owner or admin sets) with every token answer (``team_view.org_policy``);
``managed_policy.current`` combines it with the machine policy, the stricter
value winning. It is not a secret, so the last one received is kept in the
connections folder (``team-org-policy.json``) and applies while a stored
sign-in restores or Angelo is offline: going offline never lifts it. Signing
out forgets it (also a copy left by another console). A console that sends no
policy leaves the machine policy alone.

That file is the window's own offline copy. Processes that run without the
window (the MCP launcher and its headless child) read what the window applies
from ``<state>/mcp/organization.json`` instead (``external_clients.read_organization``):
an explicit record where "no organization" is written too, so a missing file
there counts as the strictest policy, never as none.

A save Windows refused (another program holds the file) stays owed: tried again
``shared_replace.RETRY_SECONDS`` later, once more when the account closes, and
written meanwhile to ``team-org-policy.json.pending``, which the next launch
reads first. A policy tightened, or a sign-out, never comes back looser offline.

Standard library only; no bpy. ``remember`` runs on the main thread.
"""

import json
import math
import os
import stat
import time

from . import shared_replace

from .team_view import OFFLINE, RESTORING, SIGNED_OUT, org_policy

FILE = "team-org-policy.json"
PENDING = FILE + ".pending"   # The last policy whose save was refused: newer than FILE while it is there.
MAX_FILE = 64 * 1024


# What an unreadable or missing policy counts as outside the window: every switch off, edits ask (fails closed).
STRICTEST = {"allowPersonalConnections": False, "allowLocalCliAccess": False, "allowVoice": False,
             "allowCollaborators": False, "allowAutoApproval": False, "requireAskForEdits": True}


def read_regular(path):
    """A small saved file's text: only a regular file, opened without waiting on a FIFO or following a link at its
    name, and at most ``MAX_FILE`` bytes, so ``/dev/zero`` or a huge file never exhausts memory (break-it review N1).
    ``FileNotFoundError`` when there is none; any other problem raises ``OSError`` (``UnicodeDecodeError`` for
    bytes that are not UTF-8, a ``ValueError``)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = shared_replace.open_shared(path, flags)   # Windows: sharing delete, so the window's save lands.
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not a regular file")
        data = os.read(descriptor, MAX_FILE + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_FILE:
        raise OSError("larger than any saved policy")
    return data.decode("utf-8")


def read_saved(directory):
    """The policy in the window's offline copy (any console): None when there is no file; an unreadable or malformed
    file counts as the strictest policy. Not what a process without the window applies: a file that failed to write
    or sits in another connections folder would read as none there (break-it review S1), so the MCP launcher reads
    ``external_clients.read_organization``."""
    path = os.path.join(os.fspath(directory), FILE)
    try:
        saved = json.loads(read_regular(path))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return org_policy(STRICTEST)
    policy = org_policy(saved.get("policy")) if isinstance(saved, dict) else None
    return policy if policy is not None else org_policy(STRICTEST)


class OrganizationPolicy:
    def __init__(self, account, directory):
        self.account = account
        self.path = os.path.join(directory, FILE)
        self.known = None
        self.leftover = False   # A file there this account would not apply (another console's, or unreadable).
        self.unsaved = None     # When a save of ``known`` was refused (monotonic): ``remember`` tries it again later.
        self.pending = os.path.join(directory, PENDING)
        try:
            saved = json.loads(read_regular(self.path))
        except FileNotFoundError:
            saved = None
        except (OSError, ValueError, RecursionError):
            saved, self.leftover = None, True
        owed = self._read(self.pending)
        self.stamped = max(_stamp(saved), _stamp(owed))   # Each save is stamped later than any read or written.
        if self._ours(owed) and (not self._ours(saved) or _stamp(owed) > _stamp(saved)):
            self.known, self.leftover = org_policy(owed.get("policy")), False   # None: a sign-out's forgetting.
            self.unsaved = time.monotonic() - shared_replace.RETRY_SECONDS   # Saved at the first ``retry_due``.
        elif self._ours(saved):
            self.known = org_policy(saved.get("policy"))
            self.leftover = self.known is None
        elif saved is not None:
            self.leftover = True

    def _ours(self, record):
        return isinstance(record, dict) and record.get("console") == self.account.console_url

    @staticmethod
    def _read(path):
        try:
            return json.loads(read_regular(path))
        except (OSError, ValueError, RecursionError):   # A damaged file (nested too deep for the parser too).
            return None

    def current(self):
        """The policy to apply now (``managed_policy.set_organization_source``)."""
        view = self.account.view()
        if view.signed_in:
            return view.org_policy
        return self.known if view.status in {RESTORING, OFFLINE} else None

    def remember(self, view):
        """Keep what the console sent last; forget it once signed out, also a file another console left (break-it
        review M1: after a console switch, the earlier console's file outlived the sign-out)."""
        policy = view.org_policy if view.signed_in else None if view.status == SIGNED_OUT else self.known
        if policy == self.known and not (view.status == SIGNED_OUT and self.leftover):
            self.retry_due()   # A refused save is tried again after a pause, never at every tick.
            return
        self.known, self.leftover = policy, False
        self.save_owed(force=True)

    def retry_due(self):
        """From the pump: a refused save is tried again ``RETRY_SECONDS`` after it, never at every tick."""
        if self.unsaved is not None and time.monotonic() - self.unsaved >= shared_replace.RETRY_SECONDS:
            self.save_owed()

    def save_owed(self, force=False):
        """Save ``known`` (``force``) or a save still owed (the account closing: its last try). A refusal keeps it
        owed and in ``PENDING``, stamped, so the newer of the two applies at the next launch; never logged (this
        module prints nothing)."""
        if not force and self.unsaved is None:
            return
        # Later than any stamp on disk too (another Angelo's save, a clock stepped back): the newest save wins.
        on_disk = max(_stamp(self._read(self.path)), _stamp(self._read(self.pending)))
        self.stamped = max(time.time(), self.stamped + 0.001, on_disk + 0.001)
        record = {"version": 1, "console": self.account.console_url, "policy": self.known, "saved": self.stamped}
        try:
            if self.known is None:
                try:
                    os.remove(self.path)
                except FileNotFoundError:
                    pass
            else:
                self._write(self.path, record)   # Refused while held: tried again after the pause.
        except OSError:
            self.unsaved = time.monotonic()
            try:
                self._write(self.pending, record)   # Newer than the file: it applies at the next launch.
            except OSError:
                pass
            return
        self.unsaved = None
        try:
            os.remove(self.pending)
        except FileNotFoundError:
            pass
        except OSError:   # Never older than the file: a PENDING left says what the file says.
            try:
                self._write(self.pending, record)
            except OSError:
                self.unsaved = time.monotonic()

    @staticmethod
    def _write(path, record):
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            shared_replace.replace(temporary, path)
        except OSError:
            shared_replace.discard(temporary)
            raise


def _stamp(record):
    saved = record.get("saved") if isinstance(record, dict) else None
    if isinstance(saved, bool) or not isinstance(saved, (int, float)):
        return 0.0
    try:
        saved = float(saved)   # An integer too large for a float (a damaged file) is no stamp.
    except OverflowError:
        return 0.0
    return saved if math.isfinite(saved) else 0.0
