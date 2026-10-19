# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""App-wide data locations. Account settings are never tied to a scratch scene or chat store.

The per-user data folder is ``Angelo``; ``Sensei`` is its pre-rename name.
``data_migration`` moves an existing ``Sensei`` folder once, but leaves each
isolated native CLI profile at its exact original path, because a native CLI may
key its stored sign-in to that path. ``state_migration`` does the same for the
agent state folder. ``native_profile_directory`` keeps resolving those retained
profiles.
"""

import os
import sys
from pathlib import Path

from . import product_env, state_paths

DATA_FOLDER = "Angelo"
LEGACY_DATA_FOLDER = "Sensei"
NATIVE_PROFILE_FAMILIES = ("claude-profile", "codex-profile")


def data_directory(environ=None, platform=None, home=None, folder=DATA_FOLDER):
    """The per-user application data folder (Blender's own config folder is separate)."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if platform == "darwin":
        return home / "Library/Application Support" / folder
    if platform == "win32":
        base = Path(environ.get("LOCALAPPDATA") or home / "AppData/Local")
    else:
        base = Path(environ.get("XDG_STATE_HOME") or home / ".local/state")
    if not base.is_absolute():
        raise ValueError("The user data directory must be an absolute path")
    return base / folder


def connection_override(environ=None):
    """``(path, variable)`` for an explicit account-store override, or ``(None, None)``."""
    value, variable = product_env.lookup("CONNECTIONS_DIR", environ)
    if not value:
        return None, None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{variable} must be an absolute path")
    return path, variable


def connection_directory(environ=None, platform=None, home=None):
    environ = os.environ if environ is None else environ
    override, _variable = connection_override(environ)
    if override is not None:
        return override
    return data_directory(environ, platform, home) / "connections"


def native_profile_directory(state_dir, family, name, environ=None, platform=None, home=None):
    """Isolated native CLI profile ``<state_dir>/<family>/<name>``, or its retained pre-rename path.

    Only the default account store and the default agent state folder have a
    pre-rename location. A profile that already exists under the current folder
    always wins; a new profile is always created there.
    """
    current = Path(state_dir) / family / name
    if family not in NATIVE_PROFILE_FAMILIES or current.exists():
        return current
    legacy_state = state_paths.legacy_sibling(state_dir, environ)
    if legacy_state is not None:
        retained = legacy_state / family / name
        return retained if retained.is_dir() and not retained.is_symlink() else current
    try:
        default = data_directory(environ, platform, home) / "connections"
        legacy = data_directory(environ, platform, home, LEGACY_DATA_FOLDER) / "connections" / family / name
    except ValueError:
        return current
    if Path(state_dir) != default:
        return current
    return legacy if legacy.is_dir() and not legacy.is_symlink() else current


def valid_profile_directory(value, identity, mode):
    """Keep the native CLI's exact namespace; never inspect/copy its credentials."""
    if not value:
        return None
    path = Path(value)
    family = "claude" if mode.startswith("claude_") else "codex"
    if not path.is_absolute() or path.name != identity or path.parent.name != family + "-profile":
        raise ValueError("Invalid saved native runtime profile directory")
    return str(path)
