# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Move the pre-rename ``toonlab-agent`` state folder to ``angelo-agent`` once, without losing anything.

The agent state folder, inside Blender's user config folder, holds chats
(``sessions``), snapshots (``checkpoints``, the deduplicated ``snapshot-store``
and ``checkpoint-timeline.json``), attachments (``references``), task memory
(inside the chats), telemetry, the default file workspace (``workspace``) and
isolated native CLI profiles (``codex-profile``, ``claude-profile``).

Besides the shared move rules of ``folder_moves``:

* An explicit ``ANGELO_STATE_DIR`` (or a deprecated spelling) selects an
  isolated state folder, so nothing is moved.
* Native CLI profiles stay at their exact original paths, because a native CLI
  may key a stored sign-in to the profile path; ``storage.native_profile_directory``
  keeps resolving them and a note in the old folder says it is still in use.
* Absolute paths recorded inside the folder:

  - Ordered snapshot records (those with a ``boundary``) record each file
    journal's absolute ``root``. Roots, and every record's ``original_path``,
    that point into the old folder are rewritten to the same place in the new
    folder, under the profile transaction, so restoring writes into the moved
    default workspace. Pre-ordered (legacy) journals keep their recorded roots:
    their write timing never allowed a combined restore, and their exact
    content is the timeline's admission evidence.
  - Attachment records are found by file name (``references.py``) and chat task
    memory is re-scoped on load (``sessions.py``); neither is rewritten.

* The rewrite is resumable: ``MARKER`` in the new folder names the old folder
  from just before the first entry moves until every record is rewritten.

Standard library only; runs on Blender's main thread when the add-on registers,
before anything opens the state folder.
"""

import json
from pathlib import Path

from .folder_moves import MigrationResult, conflicts, describe, move_all, remove_if_empty, write_note
from .state_paths import LEGACY_STATE_FOLDER, STATE_FOLDER, relocate, state_override

NATIVE_PROFILES = ("claude-profile", "codex-profile")
NOTE_NAME = "README-Angelo.txt"
NOTE_TEXT = ("Angelo moved this folder's chats, snapshots and workspace to {current}.\n\n"
             "The native sign-in profiles still in this folder ({profiles}) are used by Angelo at their\n"
             "original paths, so saved Claude and Codex accounts stay signed in. Do not delete or move\n"
             "them while those accounts are in use.\n")
MARKER = ".moved-from-pre-rename-folder.json"


def _planned_moves(legacy, current):
    """``(moves, retained)``: top-level entries to rename into ``current`` and profiles kept in place."""
    moves, retained = [], []
    for entry in sorted(legacy.iterdir()):
        if entry.name == NOTE_NAME:
            continue
        if entry.name in NATIVE_PROFILES and entry.is_dir() and not entry.is_symlink():
            retained.append(entry)
        else:
            moves.append((entry, current / entry.name))
    return moves, retained


def _relocated_record(record, legacy, current):
    """A copy of a snapshot record with paths moved into ``current``, or None when nothing changes."""
    if not isinstance(record, dict):
        return None
    updated, changed = dict(record), False
    if isinstance(record.get("original_path"), str):
        updated["original_path"] = relocate(record["original_path"], legacy, current)
        changed = updated["original_path"] != record["original_path"]
    if record.get("boundary") and isinstance(record.get("files"), list):
        files = []
        for entry in record["files"]:
            if isinstance(entry, dict) and isinstance(entry.get("root"), str):
                root = relocate(entry["root"], legacy, current)
                if root != entry["root"]:
                    entry, changed = dict(entry, root=root), True
            files.append(entry)
        updated["files"] = files
    return updated if changed else None


def relocate_snapshot_records(current, legacy):
    """Rewrite snapshot records in ``current`` that point into ``legacy``; returns how many changed."""
    from .checkpoint_filesystem import _private_directory, _read_json, _write_json
    from .checkpoint_transactions import _transaction
    base, count = current / "checkpoints", 0
    if not base.is_dir():
        return 0
    with _transaction(current):
        for session in sorted(base.iterdir()):
            if session.is_symlink() or not session.is_dir():
                continue
            for folder in sorted(session.iterdir()):
                if folder.name == "recoveries" or folder.is_symlink() or not folder.is_dir():
                    continue
                with _private_directory(folder) as descriptor:
                    try:
                        record = _read_json(descriptor, "checkpoint.json")
                    except FileNotFoundError:
                        continue
                    updated = _relocated_record(record, legacy, current)
                    if updated is not None:
                        _write_json(descriptor, "checkpoint.json", updated)
                        count += 1
    return count


def _pending_relocation(current):
    """The old folder named by an unfinished move's marker, or None."""
    try:
        value = json.loads((current / MARKER).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ValueError(f"The move marker {current / MARKER} is unreadable: {error}") from None
    if not isinstance(value, dict) or not isinstance(value.get("from"), str) or not Path(value["from"]).is_absolute():
        raise ValueError(f"The move marker {current / MARKER} is invalid")
    return Path(value["from"])


def _finish_relocation(current, legacy):
    count = relocate_snapshot_records(current, legacy)
    (current / MARKER).unlink()
    return count


def _relocation_failed(current, error):
    return MigrationResult("failed", f"Every item moved to {current}, but updating its snapshot records stopped: "
                           f"{error}. Angelo retries on its next launch; until then restoring a snapshot's file "
                           "changes may report its old location.")


def migrate_state_folder(config_directory, environ=None):
    """Perform (or resume) the move and return a ``MigrationResult``. Never raises for I/O errors."""
    override, variable = state_override(environ)
    if override is not None:
        return MigrationResult("isolated", f"{variable} selects an isolated state folder; "
                               f"the {LEGACY_STATE_FOLDER} state folder was not checked")
    try:
        config = Path(config_directory).absolute().resolve()
        legacy, current = config / LEGACY_STATE_FOLDER, config / STATE_FOLDER
        pending = _pending_relocation(current)
        if legacy.is_symlink() or not legacy.is_dir():
            moves, retained = [], []
        else:
            moves, retained = _planned_moves(legacy, current)
    except (OSError, ValueError) as error:
        return MigrationResult("failed", f"Couldn't inspect the {LEGACY_STATE_FOLDER} state folder: {error}")
    if not moves:
        if pending is None:
            return MigrationResult("nothing", f"No {LEGACY_STATE_FOLDER} state to move", retained=tuple(map(str, retained)))
        try:
            count = _finish_relocation(current, pending)
        except (OSError, ValueError, RuntimeError) as error:
            return _relocation_failed(current, error)
        return MigrationResult("migrated", f"Finished moving {pending} to {current} ({count} snapshot records updated)",
                               retained=tuple(map(str, retained)))
    found = conflicts(moves, current)
    if found:
        return MigrationResult("conflict", (
            f"Both {legacy} and {current} contain agent state ({describe(found)}). Angelo uses {current} "
            f"and moved nothing. Quit every Angelo and pre-rename build, then merge the remaining items "
            f"from {legacy} into {current} yourself (or rename {legacy}) and restart Angelo."))
    try:
        current.mkdir(parents=True, exist_ok=True, mode=0o700)
        if pending is None:
            write_note(current, MARKER, json.dumps({"from": str(legacy)}))
            if _pending_relocation(current) != legacy:
                raise OSError(f"Couldn't record the move in {current / MARKER}")
        moved = move_all(moves, current)
    except (OSError, ValueError) as error:
        moved = getattr(error, "moved", ())
        return MigrationResult("failed", (
            f"Moving {legacy} to {current} stopped ({len(moved)} of {len(moves)} items moved): {error}. "
            "Nothing was deleted; Angelo resumes the move on its next launch."), moved)
    try:
        count = _finish_relocation(current, pending or legacy)
    except (OSError, ValueError, RuntimeError) as error:
        return _relocation_failed(current, error)
    if retained:
        names = ", ".join(sorted(path.name for path in retained))
        write_note(legacy, NOTE_NAME, NOTE_TEXT.format(current=current, profiles=names))
    else:
        remove_if_empty(legacy)
    return MigrationResult("migrated", f"Moved {len(moved)} items from {legacy} to {current} "
                           f"({count} snapshot records updated)"
                           + (f"; {len(retained)} native sign-in profile folders stay in {legacy}" if retained else ""),
                           moved, tuple(map(str, retained)))


last_result = None


def migrate_on_startup(config_directory):
    """Run once per add-on registration; reports anything noteworthy to the console.

    An unexpected defect here must not stop the add-on from loading: it is
    reported like any other failure and nothing further is attempted.
    """
    global last_result
    try:
        last_result = migrate_state_folder(config_directory)
    except Exception as error:  # Defensive: I/O errors are already handled inside.
        last_result = MigrationResult("failed", f"The {LEGACY_STATE_FOLDER} state folder check stopped "
                                      f"unexpectedly ({type(error).__name__}: {error}); nothing more was moved")
    if last_result.status in {"migrated", "conflict", "failed"}:
        print(f"Angelo state folder: {last_result.message}")
    return last_result


def attention_message():
    """The message to show in the AI Providers preferences, or None."""
    return last_result.message if last_result is not None and last_result.needs_attention else None
