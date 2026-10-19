# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo environment variables, with the pre-rename spellings as deprecated fallbacks.

Every product variable is read here, by its suffix (``STATE_DIR`` for
``ANGELO_STATE_DIR``): the ``ANGELO_`` name when it is set to a non-empty value,
else the first deprecated spelling that is set, else the default. An empty value
counts as unset, matching the shell's ``${NAME:-default}`` used by
``scripts/angelo/build_native.sh``. The deprecated prefixes are the two
pre-rename product names (see ``LEGACY_PREFIXES``); new code and documentation
use only ``ANGELO_``.

Standard library only: the external CLI client, host tests and the native suite
runner import it without Blender.
"""

import os

PREFIX = "ANGELO_"
# Deprecated spellings, in lookup order. Each variable existed under at most one
# of them before the rename; both are accepted so nothing that was set is lost.
LEGACY_PREFIXES = ("SENSEI_", "TOONLAB_")


def names(name):
    """``(current, *deprecated)`` variable names for a suffix such as ``STATE_DIR``."""
    return (PREFIX + name,) + tuple(prefix + name for prefix in LEGACY_PREFIXES)


def lookup(name, environ=None):
    """``(value, variable)`` for the variable that supplies ``name``; ``(None, None)`` when unset.

    The variable is returned so that error messages name what the user actually set.
    """
    environ = os.environ if environ is None else environ
    for variable in names(name):
        value = environ.get(variable)
        if value:
            return value, variable
    return None, None


def get(name, environ=None, default=None):
    value, _variable = lookup(name, environ)
    return default if value is None else value


def current(name, environ=None):
    """Only the ``ANGELO_`` spelling, exactly as set: ``""`` when set but empty, None when unset.

    For variables introduced after the rename, which have no deprecated spelling and where an empty value is
    a mistake to report rather than "unset" (``ANGELO_AGENT_RUNTIME``, ``ANGELO_CACHE_KEY_SECRET``).
    """
    environ = os.environ if environ is None else environ
    return environ.get(PREFIX + name)


def flag(name, environ=None):
    """True only for the documented opt-in value ``1``; ``ANGELO_NAME=0`` overrides a deprecated ``=1``."""
    return get(name, environ) == "1"


def clear(name, environ=None):
    """Remove every spelling, e.g. before a test re-enables a default that an outer runner disabled."""
    environ = os.environ if environ is None else environ
    for variable in names(name):
        environ.pop(variable, None)


def deprecated_in_use(environ=None):
    """Sorted deprecated names that are set, for a one-time migration note."""
    environ = os.environ if environ is None else environ
    return sorted(key for key, value in environ.items() if key.startswith(LEGACY_PREFIXES) and value)
