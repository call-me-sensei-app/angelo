# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Move the pre-rename ``Sensei`` data folder to ``Angelo`` once, without losing anything.

The folder holds the account metadata (``connections/connections.json``), the
Media Studio library (``media-studio``) and isolated native CLI profiles
(``connections/claude-profile`` and ``connections/codex-profile``).

Besides the shared move rules of ``folder_moves``:

* An explicit ``ANGELO_CONNECTIONS_DIR`` (or a deprecated spelling) selects an
  isolated account store, so the default folders are not touched.
* Native CLI profiles stay at their exact original paths, because a native CLI
  may key a stored sign-in to the profile path. Saved accounts keep resolving
  them through ``storage.native_profile_directory``; new profiles are created
  under ``Angelo``. A short note in the old folder says it is still in use.

Standard library only; runs on Blender's main thread before the account store opens.
"""

from .folder_moves import ATTENTION, MigrationResult, conflicts, describe, move_all, remove_if_empty, write_note
from .storage import (DATA_FOLDER, LEGACY_DATA_FOLDER, NATIVE_PROFILE_FAMILIES, connection_override,
                      data_directory)

NOTE_NAME = "README-Angelo.txt"
NOTE_TEXT = ("Angelo moved this folder's data to {current}.\n\n"
             "The native sign-in profiles still in this folder ({profiles}) are used by Angelo at their\n"
             "original paths, so saved Claude and Codex accounts stay signed in. Do not delete or move\n"
             "them while those accounts are in use.\n")

__all__ = ("ATTENTION", "MigrationResult", "migrate_legacy_data", "migrate_on_startup", "attention_message")


def _planned_moves(legacy, current):
    """``(moves, retained)``: entries to rename into ``current`` and native profile folders kept in place."""
    moves, retained = [], []
    for entry in sorted(legacy.iterdir()):
        if entry.name == NOTE_NAME:
            continue
        if entry.name == "connections" and entry.is_dir() and not entry.is_symlink():
            for child in sorted(entry.iterdir()):
                if child.name in NATIVE_PROFILE_FAMILIES and child.is_dir() and not child.is_symlink():
                    retained.append(child)
                else:
                    moves.append((child, current / "connections" / child.name))
        else:
            moves.append((entry, current / entry.name))
    return moves, retained


def _write_note(legacy, current, retained):
    names = ", ".join(sorted({f"connections/{path.name}" for path in retained}))
    write_note(legacy, NOTE_NAME, NOTE_TEXT.format(current=current, profiles=names))


def migrate_legacy_data(environ=None, platform=None, home=None):
    """Perform (or resume) the move and return a ``MigrationResult``. Never raises for I/O errors."""
    try:
        override, variable = connection_override(environ)
    except ValueError as error:
        return MigrationResult("failed", str(error))
    if override is not None:
        return MigrationResult("isolated", f"{variable} selects an isolated account store; "
                               f"the {LEGACY_DATA_FOLDER} data folder was not checked")
    try:
        legacy = data_directory(environ, platform, home, LEGACY_DATA_FOLDER)
        current = data_directory(environ, platform, home, DATA_FOLDER)
        if legacy.is_symlink() or not legacy.is_dir():
            return MigrationResult("nothing", f"No {LEGACY_DATA_FOLDER} data folder to move")
        moves, retained = _planned_moves(legacy, current)
    except (OSError, ValueError) as error:
        return MigrationResult("failed", f"Couldn't inspect the {LEGACY_DATA_FOLDER} data folder: {error}")
    if not moves:
        return MigrationResult("nothing", f"The {LEGACY_DATA_FOLDER} data folder holds only retained "
                               "native sign-in profiles", retained=tuple(map(str, retained)))
    found = conflicts(moves, current)
    if found:
        return MigrationResult("conflict", (
            f"Both {legacy} and {current} contain data ({describe(found)}). Angelo uses {current} and moved "
            f"nothing. Quit any running Sensei, then merge the remaining items from {legacy} into "
            f"{current} yourself (or rename {legacy}) and restart Angelo."))
    try:
        moved = move_all(moves, current)
    except OSError as error:
        moved = getattr(error, "moved", ())
        return MigrationResult("failed", (
            f"Moving {legacy} to {current} stopped after {len(moved)} of {len(moves)} items: {error}. "
            "Nothing was deleted; Angelo resumes the move on its next launch."), moved)
    if retained:
        _write_note(legacy, current, retained)
    else:
        remove_if_empty(legacy / "connections")
        remove_if_empty(legacy)
    return MigrationResult("migrated", f"Moved {len(moved)} items from {legacy} to {current}"
                           + (f"; {len(retained)} native sign-in profile folders stay in {legacy}" if retained else ""),
                           moved, tuple(map(str, retained)))


last_result = None


def migrate_on_startup():
    """Run once per add-on registration; reports anything noteworthy to the console.

    An unexpected defect here must not stop the add-on from loading: it is
    reported like any other failure and nothing further is attempted.
    """
    global last_result
    try:
        last_result = migrate_legacy_data()
    except Exception as error:  # Defensive: I/O errors are already handled inside.
        last_result = MigrationResult("failed", f"The {LEGACY_DATA_FOLDER} data folder check stopped "
                                      f"unexpectedly ({type(error).__name__}: {error}); nothing more was moved")
    if last_result.status in {"migrated", "conflict", "failed"}:
        print(f"Angelo data folder: {last_result.message}")
    return last_result


def attention_message():
    """The message to show in the AI Providers preferences, or None."""
    return last_result.message if last_result is not None and last_result.needs_attention else None
