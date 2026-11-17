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
    """Isolated native CLI profile ``<state_dir>/<family>/<name>``, or its retained earlier path.

    Only the default account store and the default agent state folder have
    earlier locations: the pre-rename folders, and for the agent state folder
    every earlier Blender version's (``state_paths.earlier_state_folders``),
    newest first. A profile that already exists under the current folder always
    wins; a new profile is always created there.
    """
    current = Path(state_dir) / family / name
    if family not in NATIVE_PROFILE_FAMILIES or current.exists():
        return current
    if state_paths.legacy_sibling(state_dir, environ) is not None:
        for earlier in state_paths.earlier_state_folders(state_dir, environ):
            retained = earlier / family / name
            if retained.is_dir() and not retained.is_symlink():
                return retained
        return current
    try:
        default = data_directory(environ, platform, home) / "connections"
        legacy = data_directory(environ, platform, home, LEGACY_DATA_FOLDER) / "connections" / family / name
    except ValueError:
        return current
    if Path(state_dir) != default:
        return current
    return legacy if legacy.is_dir() and not legacy.is_symlink() else current


def valid_profile_directory(value, identity, mode):
    """Keep the native CLI's exact namespace; never inspect/copy its credentials. Its form only: a launch also asks
    C where it is (``private_profile``); a record's own folders make Angelo's private set (``private_roots``)."""
    if not value:
        return None
    path = Path(value)
    from .provider_registry import spec_for   # A custom endpoint's mode carries no runtime prefix.
    family = "claude" if spec_for(mode)["runtime"] == "claude" else "codex"
    if not path.is_absolute() or path.name != identity or path.parent.name != family + "-profile":
        raise ValueError("Invalid saved native runtime profile directory")
    return str(path)


class ProfileOutside(ValueError):
    """A runtime profile folder outside Angelo's private folders: no runtime is started with it."""
    refusal_code = "READ_ONLY"


def private_profile(path):
    """``path``, a runtime's profile folder (``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR``), when C keeps it inside Angelo's
    private folders, a set no script can change. Its name comes from Python state a script could change in place (a
    provider's settings, a connection record), and the runtime reads its configuration from there unconfined (the
    programs it may start, its sign-in): a folder anywhere else is refused before anything is made in it. Without
    the native guard every path passes (``owned_replace._private``)."""
    from .owned_replace import _private
    if not _private(path):
        raise ProfileOutside("A model runtime starts only with its profile in Angelo's own folders: %s"
                             % Path(path).name)
    return path
