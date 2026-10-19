# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the update check remembers between launches (``updates.json`` in the agent state folder).

Non-secret and small. It holds the person's choices (automatic checks on or
off, "skip this version", "later"), the schedule (last attempt and success,
failure count), the highest feed sequence ever accepted (rollback guard), the
feed's ETag, failed installations of a downloaded update, and the **raw signed envelope** of the last good feed, so an offer
survives a restart while offline. The envelope is verified again every time it
is read (``update_check.cached_feed``): this file is never trusted as an
answer. Also records ``last_seen_version`` for "What's New" (``release_notes``).

A damaged file reads as defaults. Writes are atomic (``studio_state``).
Standard library only; no bpy.
"""

import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .studio_state import atomic_json, read_json

STATE_FILE = "updates.json"
MAX_ENVELOPE_TEXT = 64 * 1024 + 1024


@dataclass
class UpdateState:
    automatic: bool = True            # The person's "Check for updates automatically".
    last_attempt: float = 0.0         # Epoch seconds of the last try, successful or not.
    last_success: float = 0.0
    failures: int = 0                 # Consecutive failed attempts (resets on success).
    last_sequence: int = 0            # Highest feed sequence accepted: older feeds are refused.
    etag: str = ""
    envelope: str = ""                # Raw text of the last good signed feed.
    skipped_version: str = ""         # "Skip this version".
    snoozed_version: str = ""         # "Later" hides the notice for this version until...
    snoozed_until: float = 0.0
    last_seen_version: str = ""       # The version whose What's New the person has been shown.
    install_failed_version: str = ""  # The last version whose installation failed (update_restart) ...
    install_failures: int = 0         # ... and how often in a row: at INSTALL_ATTEMPTS it is offered as a download.

    def skipped(self, version, critical):
        """The person chose "Skip this version" (a critical release can't be skipped)."""
        return self.skipped_version == version and not critical

    def snoozed(self, version, now=None):
        """The person chose "Later" and the pause has not ended."""
        now = time.time() if now is None else now
        return self.snoozed_version == version and now < self.snoozed_until


def _typed(name, value, default):
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default
    if isinstance(default, float):
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else default
    if isinstance(default, str):
        return value if isinstance(value, str) and len(value) <= MAX_ENVELOPE_TEXT else default
    return default


def load(directory):
    """The saved state of a state folder; every damaged or mistyped field falls back to its default."""
    document = read_json(Path(directory) / STATE_FILE, {})
    document = document if isinstance(document, dict) else {}
    defaults = UpdateState()
    return UpdateState(**{f.name: _typed(f.name, document.get(f.name), getattr(defaults, f.name)) for f in fields(defaults)})


def save(directory, state):
    """Persist ``state``; returns False when the folder can't be written (the in-memory state still applies)."""
    try:
        atomic_json(Path(directory) / STATE_FILE, asdict(state))
    except OSError:
        return False
    return True
