# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tell the person once that a newer Angelo moved their chats away, and how to get them back.

Blender keeps one user folder per version (``<product>/5.2``, ``<product>/5.3``). Angelo 1.1 (Blender 5.3) moves the
agent state (chats, snapshots, workspace) out of 1.0's folder into its own at its first launch (1.1's
``version_carry_over``, angelo-studio #137). A person who then opens 1.0.x again finds no chats. This module, read
only, recognises that from what the newer version leaves (its format, not its code):

* **the note in this version's state folder** (``README-Angelo.txt``), which the newer Angelo writes there after
  every move, and
* **the newer version's carry-over record** (``<product>/<newer>/config/angelo-carry-over.json``), whose ``from``
  names this version's state folder, whatever its record version or status (a move that stopped part-way moved
  chats too). It covers a move that could not leave the note.

It never writes, moves or deletes anything of the newer version, and never touches the note. Once the notice has
shown, it leaves the newer version's own marker beside the note (``.angelo-moved-notice-shown``): the newer Angelo
reads it the same way and removes it after its next move, so a later move is told again. An explicit
``ANGELO_STATE_DIR``, ``BLENDER_USER_CONFIG`` or ``BLENDER_USER_RESOURCES`` has no per-version folders and is never
moved, so there is nothing to tell.

Contract: doc/angelo/app_identity.rst, "Going back to an earlier version". Standard library only, but the card; shown by ``app_notices``.
"""

import json
import os
from pathlib import Path

from . import state_paths
from .ui_text import iface_, n_

# The file names the newer Angelo uses (#137 NOTE_NAME, NOTE_SEEN, state_paths.CARRY_OVER_RECORD): its note in the
# earlier state folder, the marker beside it once told (the newer one clears it after a move), and its record in
# its own ``config``.
NOTE_NAME = "README-Angelo.txt"  # i18n: a file name
SHOWN_MARKER = ".angelo-moved-notice-shown"  # i18n: a file name
RECORD = "angelo-carry-over.json"  # i18n: a file name
MAX_RECORD_BYTES = 1 << 20                  # A record names folders and chat ids; anything larger is not one.

TITLE = n_("Your chats are in your newer Angelo")
BODY = n_("When you updated, your newer Angelo moved your chats and snapshots to its own folder, and nothing was "
          "deleted. Open the newer Angelo to continue them, or install it again if you removed it.")


def _version(name):
    parts = name.split(".")
    return (int(parts[0]), int(parts[1])) if len(parts) == 2 and all(part.isdigit() for part in parts) else None


def _same_folder(first, second):
    try:
        return Path(first).resolve() == Path(second).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _named_by_a_newer_record(state, product, current):
    """The newer version's folder name whose carry-over record moved ``state`` away, or None. Read only."""
    try:
        names = [entry.name for entry in os.scandir(product) if entry.is_dir()]
    except OSError:
        return None
    for name in sorted(names, key=lambda item: _version(item) or (0, 0), reverse=True):
        version = _version(name)
        if version is None or version <= current:
            continue
        path = product / name / "config" / RECORD
        try:
            if path.stat().st_size > MAX_RECORD_BYTES:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        source = record.get("from") if isinstance(record, dict) else None
        if isinstance(source, str) and source and _same_folder(source, state):
            return name
    return None


def moved_to(config_directory, environ=None):
    """The newer version (``"5.3"``, or ``"newer"`` when only the note says so) that moved this version's chats
    away and has not been told yet, or None. Never raises: an unreadable folder tells nothing."""
    if state_paths.state_override(environ)[0] is not None:
        return None
    environ = os.environ if environ is None else environ
    if environ.get("BLENDER_USER_CONFIG") or environ.get("BLENDER_USER_RESOURCES"):
        return None
    config = Path(config_directory).absolute()
    current = _version(config.parent.name)
    if config.name != "config" or current is None:
        return None
    state = config / state_paths.STATE_FOLDER
    try:
        if (state / SHOWN_MARKER).exists():
            return None
        newer = _named_by_a_newer_record(state, config.parent.parent, current)
        return newer or ("newer" if (state / NOTE_NAME).is_file() else None)
    except OSError:
        return None


def mark_told(config_directory):
    """Leave the marker that this version has told the person (in its own state folder; never the newer one's)."""
    state = Path(config_directory) / state_paths.STATE_FOLDER
    try:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        (state / SHOWN_MARKER).write_text("", encoding="utf-8")
    except OSError:
        pass   # Told again at the next launch.


def card(config_directory):
    """The corner notice, or None when there is nothing to tell. It stays until closed and marks the person told
    when it shows (a layout change that hides it shows it again)."""
    if moved_to(config_directory) is None:
        return None
    from .update_card import NoticeCard

    class MovedChatsCard(NoticeCard):
        kind = "moved_chats"   # Its own place in the queue: another notice never drops it.
        priority = 60          # After a crash dialog (100), before What's New (50): the chats matter more.
        retry_when_lost = True

        def opened(self, host):
            super().opened(host)
            mark_told(config_directory)

    return MovedChatsCard("alert", iface_(TITLE), iface_(BODY))
