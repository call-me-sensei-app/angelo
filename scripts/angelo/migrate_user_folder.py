#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Move Angelo's own agent state out of the folder a stock Blender also uses.

Builds from before the application identity change (``doc/angelo/app_identity.rst``)
kept Angelo's user folder in Blender's: ``<Blender user folder>/<version>/config/``
held Angelo's ``angelo-agent`` state folder (chats, snapshots, attachments, task
memory, telemetry, the default workspace) beside Blender's own files. Angelo now has
its own user folder, so that state is invisible to a new build until it is moved.

This is a one-time, explicit command. It never runs on its own, and:

* It looks at exactly one place in Blender's folder, the ``angelo-agent`` entry that
  Angelo itself created. It never opens, copies, moves or deletes anything else there
  (``userpref.blend``, ``startup.blend``, add-ons, extensions and bookmarks are Blender's).
* Nothing is copied or overwritten. Entries move with same-volume renames; if the new
  folder already holds state, nothing moves and the conflict is reported.
* Preferences are not migrated. Angelo starts with its own defaults; the two apps no
  longer share one ``userpref.blend``. A person who wants their Blender settings copies
  them (the setup card's "Use my Blender settings", or File > Defaults > Import Blender
  Settings…; ``angelo_agent/settings_import.py``), which leaves this state folder out.
* Native CLI sign-in profiles (``claude-profile``, ``codex-profile``) stay where they
  are, exactly like the earlier rename migration: a native CLI may key its stored
  sign-in to the profile path, and saved chats keep resolving their absolute paths.
* Recorded absolute paths follow the move: snapshot file journals are rewritten and a
  chat's task-memory scope is re-scoped to the new folder (otherwise the brief would be
  dropped as belonging to a different profile).

Dry run by default; pass ``--apply`` to move. Run it with Angelo's bundled Python::

    <Angelo.app>/Contents/Resources/5.2/python/bin/python3.13 -I \\
        scripts/angelo/migrate_user_folder.py [--apply]

Quit Angelo first. Standard library plus the agent's own storage helpers.
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "addons_core"))
sys.path.insert(0, str(HERE.parent / "modules"))

import angelo_identity  # noqa: E402
from angelo_agent import blender_folders, folder_moves, state_migration  # noqa: E402
from angelo_agent.state_paths import STATE_FOLDER  # noqa: E402

# Where a stock Blender keeps its files is named once, in angelo_agent.blender_folders (the settings import
# reads the same names); this command needs it to find the state Angelo once put there.
OVERRIDE_VARIABLES = blender_folders.SHARED_VARIABLES


def _roots(platform, home, environ, version):
    """``(old, new)`` per-version user folders: the one Blender uses, and Angelo's own."""
    paths = angelo_identity.data()["paths"]
    old = blender_folders.version_folder(platform, home, environ, version)
    if platform == "darwin":
        return old, old.parent.parent / paths["user_dir_name"] / version
    if platform == "win32":
        return old, old.parent.parent.parent / paths["user_dir_name"] / version
    return old, old.parent.parent / paths["user_dir_name_unix"] / version


def _rescope_chats(current, legacy):
    """Point each chat's task-memory scope at ``current``; returns how many chats changed."""
    from angelo_agent.task_memory import scope_token
    old_token = scope_token(str(Path(legacy).absolute().resolve()))
    new_token, changed = scope_token(str(Path(current).absolute().resolve())), 0
    for path in sorted((current / "sessions").glob("*.json")) if (current / "sessions").is_dir() else ():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scope = (state.get("task_memory") or {}).get("scope") if isinstance(state, dict) else None
        if isinstance(scope, dict) and scope.get("profile") == old_token:
            scope["profile"] = new_token
            path.write_text(json.dumps(state), encoding="utf-8")
            changed += 1
    return changed


def plan_and_run(platform=None, home=None, environ=None, version="5.2", apply=False):
    """Plan (and with ``apply`` perform) the move. Returns ``(status, message)``; status is one of
    ``isolated``, ``nothing``, ``planned``, ``conflict``, ``failed`` or ``migrated``."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    for variable in OVERRIDE_VARIABLES:
        if environ.get(variable):
            return "isolated", (f"{variable} is set, so Angelo and Blender already share one explicit folder "
                                "(or none): nothing to migrate.")
    old_root, new_root = _roots(platform, home, environ, version)
    legacy, current = old_root / "config" / STATE_FOLDER, new_root / "config" / STATE_FOLDER
    pending = _pending(current)
    moves, retained = [], []
    if not legacy.is_symlink() and legacy.is_dir():
        for entry in sorted(legacy.iterdir()):
            if entry.name == state_migration.NOTE_NAME:
                continue
            if entry.name in state_migration.NATIVE_PROFILES and entry.is_dir() and not entry.is_symlink():
                retained.append(entry)
            else:
                moves.append((entry, current / entry.name))
    if not moves and pending is None:
        return "nothing", (f"No {STATE_FOLDER} state to move at {legacy}" if not retained
                           else f"{legacy} holds only retained native sign-in profiles")
    if not moves:  # Every entry moved before an earlier run stopped: only the rewrite is left.
        if not apply:
            return "planned", f"Would finish the interrupted move from {pending} into {current}."
        return _finish(current, pending, 0, f"finished the interrupted move from {pending}")
    found = folder_moves.conflicts(moves, current)
    summary = (f"{len(moves)} entries from {legacy} to {current}"
               + (f"; {len(retained)} native sign-in profile folders stay in {legacy}" if retained else ""))
    if found:
        return "conflict", (f"Both folders hold state ({folder_moves.describe(found)}): nothing was moved. "
                            f"Merge {legacy} into {current} yourself, or rename {legacy}.")
    if not apply:
        return "planned", f"Would move {summary}. Re-run with --apply to do it."
    try:
        current.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder_moves.write_note(current, state_migration.MARKER, json.dumps({"from": str(legacy)}))
        moved = folder_moves.move_all(moves, current)
    except (OSError, ValueError) as error:
        return "failed", (f"Stopped: {error}. Nothing was deleted. Run this command again to resume the move "
                          f"(the marker in {current} names the old folder).")
    if retained:
        names = ", ".join(sorted(path.name for path in retained))
        folder_moves.write_note(legacy, state_migration.NOTE_NAME,
                                state_migration.NOTE_TEXT.format(current=current, profiles=names))
    else:
        folder_moves.remove_if_empty(legacy)
    return _finish(current, legacy, len(moved), summary)


def _pending(current):
    """The old folder named by an unfinished move's marker, or None."""
    try:
        record = json.loads((current / state_migration.MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Path(record["from"]) if isinstance(record, dict) and isinstance(record.get("from"), str) else None


def _finish(current, legacy, count, summary):
    """Rewrite what recorded a path inside the old folder, then drop the marker."""
    try:
        records = state_migration.relocate_snapshot_records(current, legacy)
        chats = _rescope_chats(current, legacy)
        (current / state_migration.MARKER).unlink()
    except (OSError, ValueError, RuntimeError) as error:
        return "failed", (f"The entries moved but updating their records stopped: {error}. Run this command "
                          f"again to finish (the marker in {current} is kept).")
    return "migrated", f"Moved {count} entries ({records} snapshot records, {chats} chats updated): {summary}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="perform the move (default: dry run)")
    parser.add_argument("--blender-version", default="5.2", help="per-version folder name (default 5.2)")
    parser.add_argument("--home", help="home folder to use instead of the current user's (tests)")
    parser.add_argument("--platform", choices=("darwin", "win32", "linux"), help="folder layout (tests)")
    options = parser.parse_args(argv)
    status, message = plan_and_run(options.platform, options.home, None, options.blender_version, options.apply)
    print(f"{status}: {message}")
    return 1 if status in {"conflict", "failed"} else 0


if __name__ == "__main__":
    sys.exit(main())
