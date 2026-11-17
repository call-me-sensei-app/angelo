# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the agent state (chats, snapshots, task memory, telemetry) lives.

The default is the ``angelo-agent`` folder in Blender's user config folder.
``ANGELO_STATE_DIR`` (or a deprecated spelling, see ``product_env``) selects an
isolated state folder instead; nothing is then migrated or aliased.

Before the 2026-10-07 rename the default folder was ``toonlab-agent``.
``state_migration`` moves it once; ``legacy_sibling`` names it for the readers
that must recognize identities recorded there (task-memory profile scope,
retained native CLI profiles, file-journal roots).

Blender keeps one user folder per version (``<product>/5.2/config/angelo-agent``);
``version_carry_over`` moves the state into a newer version's folder on its first
launch, and ``earlier_state_folders`` names every earlier folder the state may
have lived in, for the same readers.

Standard library only: the external CLI client resolves the same folder
without Blender.
"""

import json
from pathlib import Path

from . import product_env

STATE_FOLDER = "angelo-agent"
LEGACY_STATE_FOLDER = "toonlab-agent"
NATIVE_PROFILES = ("claude-profile", "codex-profile")
# In a version's ``config`` folder, beside the state folder: where its state was carried over from
# (``version_carry_over``). Never inside the state folder, so it does not travel with the state.
CARRY_OVER_RECORD = "angelo-carry-over.json"


# This process's own state folder when it is not this version's default: a launch that found the earlier version's
# state in use (``version_carry_over``) works on that folder in place. In-process only, so it never reaches a child
# or an updated app, and the folder keeps the default folder's behaviour (it is no isolated one).
session_redirect = None


def state_override(environ=None):
    """``(path, variable)`` for an explicit state folder, or ``(None, None)``."""
    value, variable = product_env.lookup("STATE_DIR", environ)
    return (Path(value), variable) if value else (None, None)


def legacy_sibling(state_directory, environ=None):
    """The pre-rename folder beside a default-named state folder, or None.

    Pure path arithmetic on the resolved folder; the result may not exist. An
    explicit ``ANGELO_STATE_DIR`` (any spelling) has no pre-rename sibling.
    """
    if state_override(environ)[0] is not None:
        return None
    directory = Path(state_directory).absolute().resolve()
    if directory.name != STATE_FOLDER:
        return None
    return directory.parent / LEGACY_STATE_FOLDER


def version_key(name):
    """``(major, minor)`` of a per-version folder name (``"5.3"``), or None."""
    parts = name.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def per_version(state_directory):
    """``(product folder, (major, minor))`` when ``state_directory`` is ``<product>/<major.minor>/config/<STATE>``."""
    path = Path(state_directory).absolute()
    key = version_key(path.parent.parent.name)
    if path.name != STATE_FOLDER or path.parent.name != "config" or key is None:
        return None, None
    return path.parent.parent.parent, key


def earlier_version_folders(state_directory, strict=False):
    """The same state folder in every earlier version's folder that exists, newest first (one listing). An
    unreadable product folder gives none, or raises ``OSError`` with ``strict``."""
    product, current = per_version(state_directory)
    if product is None:
        return []
    try:
        # With ``strict`` a link to something not there is listed too, so the caller finds it in order.
        names = [(version_key(entry.name), entry.name) for entry in product.iterdir()
                 if entry.is_dir() or (strict and entry.is_symlink())]
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        return []
    earlier = sorted((item for item in names if item[0] is not None and item[0] < current), reverse=True)
    return [product / name / "config" / STATE_FOLDER for _key, name in earlier]


def same_place(first, second):
    """Whether two absolute paths name one folder, through any link in either (string-equal paths always do)."""
    if str(first) == str(second):
        return True
    try:
        return Path(first).resolve() == Path(second).resolve()
    except (OSError, RuntimeError):
        return False


def carry_over_record(state_directory):
    """The finished carry-over record beside ``state_directory`` (``version_carry_over``) that moved state into it,
    or None (none, unreadable, still moving, or another folder's)."""
    folder = Path(state_directory).absolute()
    try:
        value = json.loads((folder.parent / CARRY_OVER_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if (not isinstance(value, dict) or value.get("status") != "done" or not isinstance(value.get("into"), str)
            or not isinstance(value.get("from"), str) or not Path(value["from"]).is_absolute()
            or not same_place(value["into"], folder)):
        return None
    return value


def carried_chats(state_directory):
    """``{chat id: when}`` of the chats carried into ``state_directory`` from an earlier version. A native context
    such a chat saved before then started in the old working folder, to which a native CLI keys its sessions, and is
    never resumed (``sessions``); chats that were already here keep theirs."""
    record = carry_over_record(state_directory)
    at, chats = (record.get("at"), record.get("chats")) if record else (None, None)
    if not isinstance(at, (int, float)) or not isinstance(chats, list):
        return {}
    found = {chat: float(at) for chat in chats if isinstance(chat, str)}
    returned = record.get("returned")   # Chats brought over again, after a return to the earlier version.
    if isinstance(returned, dict):
        found.update((chat, float(when)) for chat, when in returned.items()
                     if isinstance(chat, str) and isinstance(when, (int, float)))
    return found


def carried_from(state_directory):
    """Every folder the state was carried over from, newest first: the record beside a folder names its source,
    and that source's own record the one before."""
    found, folder = [], Path(state_directory).absolute()
    while len(found) < 32:
        record = carry_over_record(folder)
        if record is None or Path(record["from"]) in found:
            break
        found.append(Path(record["from"]))
        folder = Path(record["from"])
    return found


def earlier_state_folders(state_directory, environ=None):
    """Every folder this profile's state may have lived in before, newest first: the folders it was carried over
    from, the same folder in every earlier version's folder, and the pre-rename sibling of each and of this one.
    Path arithmetic, one listing and small record reads; the folders may not exist. An explicit
    ``ANGELO_STATE_DIR`` has none."""
    if state_override(environ)[0] is not None:
        return []
    found = []
    for folder in [Path(state_directory).absolute()] + carried_from(state_directory) + earlier_version_folders(
            state_directory):
        for candidate in (folder, folder.parent / LEGACY_STATE_FOLDER):
            if candidate not in found and candidate != Path(state_directory).absolute():
                found.append(candidate)
    return found


def relocate(path, legacy, current):
    """``path`` moved from inside ``legacy`` to the same place inside ``current``; else unchanged.

    String-based on purpose: recorded paths may no longer exist.
    """
    text, old = str(path), str(legacy)
    if text == old:
        return str(current)
    if text.startswith(old + "/") or text.startswith(old + "\\"):
        return str(current) + text[len(old):]
    return text
