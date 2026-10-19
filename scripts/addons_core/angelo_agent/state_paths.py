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

Standard library only: the external CLI client resolves the same folder
without Blender.
"""

from pathlib import Path

from . import product_env

STATE_FOLDER = "angelo-agent"
LEGACY_STATE_FOLDER = "toonlab-agent"


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
