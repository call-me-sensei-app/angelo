# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The earlier version's side of a carry-over (``version_carry_over``): the note in its old state folder saying where the
state went, and copies of the person's choices it reads (``LEFT_BEHIND``), without a yes to the newer version.

Run once the move is ``done``, and again at later launches when it could not (``carry_over_finish``); a later try
never undoes what the earlier version did since (its seen mark, a yes it wrote). Standard library only.
"""

import json
import os
import shutil
from pathlib import Path

from . import carry_over_moves as moves, folder_moves, shared_replace
from . import version_carry_over as carry


def leave_behind(source, current, retained, again=False):
    """The old folder says where its state went and keeps the person's choices for the earlier version. False when it
    could not (``carry_over_finish`` tries again at later launches, ``again``: then the earlier version may have been
    used since, so its seen-notice mark stays and only a choice this step copies loses its yes)."""
    try:
        os.makedirs(moves.long_path(source), mode=0o700, exist_ok=True)
        text = carry.NOTE_TEXT.format(current=current)
        if retained:
            text += carry.NOTE_PROFILES.format(profiles=", ".join(path.name for path in retained))
        folder_moves.write_note(source, carry.NOTE_NAME, text)
        for name in (carry.NOTE_NAME, carry.NOTE_SEEN):   # A whole rename brought an earlier move's note along.
            if not again and moves.info(current / name) is not None:   # Later, they are this folder's own notes.
                os.unlink(moves.long_path(current / name))
        if not again and moves.info(source / carry.NOTE_SEEN) is not None:
            os.unlink(moves.long_path(source / carry.NOTE_SEEN))   # Told again after every move.
        copied = []
        for name in carry.LEFT_BEHIND:
            if moves.info(current / name) is not None and moves.info(source / name) is None:
                shutil.copy2(moves.long_path(current / name), moves.long_path(source / name))
                copied.append(name)
        if "updates.json" in copied or not again or _same_bytes(source / "updates.json", current / "updates.json"):
            _forget_yes(source / "updates.json")   # Ours (copied now or before); never one the earlier version wrote.
    except OSError as error:   # The move is done; only the courtesy for the earlier version is missing.
        print(f"Angelo version carry-over: could not leave a note in {source} ({error})")
        return False
    return True


def _same_bytes(first, second):
    try:
        return shared_replace.read_bytes(moves.long_path(first)) == shared_replace.read_bytes(moves.long_path(second))
    except OSError:
        return False


def _forget_yes(path):
    """A yes to the newer version stays with it: the earlier version asks again."""
    try:
        value = json.loads(shared_replace.read_text(moves.long_path(path)))
    except (OSError, ValueError, UnicodeError):
        return
    if isinstance(value, dict) and value.pop("accepted_line", None):
        from .carry_over_records import write_json
        write_json(Path(moves.long_path(path)), value)   # Replaced whole: a reader never sees half of it.
