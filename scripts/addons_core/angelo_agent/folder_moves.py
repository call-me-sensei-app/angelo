# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared rules for moving a pre-rename folder's entries into its Angelo folder.

Used by ``data_migration`` (the per-user data folder) and ``state_migration``
(the agent state folder). The rules, in order:

* Every planned entry is moved within the same volume. Nothing is copied,
  overwritten or deleted.
* When any destination already exists, nothing moves and the caller reports a
  conflict; Angelo then uses its own folder and leaves the old one as it is.
* A move interrupted part-way resumes on the next launch: entries already moved
  are no longer in the old folder, so they are neither moved again nor conflicts.
* Only directories left empty by the move are removed.

Standard library only.
"""

import os
from dataclasses import dataclass

# Statuses that need the user's attention in the AI Providers preferences.
ATTENTION = frozenset({"conflict", "failed"})


@dataclass(frozen=True)
class MigrationResult:
    status: str  # isolated, nothing, migrated, conflict or failed
    message: str
    moved: tuple = ()
    retained: tuple = ()

    @property
    def needs_attention(self):
        return self.status in ATTENTION


def conflicts(moves, current):
    """Destinations of ``moves`` that already exist, the folder itself first if it is not a directory."""
    found = [target for _source, target in moves if os.path.lexists(target)]
    if os.path.lexists(current) and not current.is_dir():
        found.insert(0, current)
    return found


def describe(paths, limit=3):
    return ", ".join(str(path) for path in paths[:limit]) + (" …" if len(paths) > limit else "")


def move_entry(source, target):
    """Same-volume move that never replaces existing data, even if it appeared after the check.

    A directory cannot be renamed over a non-empty directory, and Windows refuses
    any existing target. A POSIX ``rename`` would replace a file, so files are
    hard-linked to the new name (failing if it exists) before the old name goes.
    """
    if os.name == "nt" or (source.is_dir() and not source.is_symlink()):
        os.rename(source, target)
        return
    os.link(source, target, follow_symlinks=False)
    os.unlink(source)


def move_all(moves, current):
    """Move every ``(source, target)``; returns the moved targets. Raises ``OSError`` with progress kept."""
    moved = []
    current.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        for source, target in moves:
            target.parent.mkdir(exist_ok=True, mode=0o700)
            move_entry(source, target)
            moved.append(str(target))
    except OSError as error:
        error.moved = tuple(moved)
        raise
    return tuple(moved)


def remove_if_empty(directory):
    try:
        directory.rmdir()  # Fails, harmlessly, unless the directory is empty.
    except OSError:
        pass


def write_note(directory, name, text):
    """Create an advisory note once; an existing or unwritable note is left alone."""
    try:
        fd = os.open(directory / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
