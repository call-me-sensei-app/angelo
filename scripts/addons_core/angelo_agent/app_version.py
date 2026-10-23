# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own product version and the product's public links.

The product version (``1.0.0``) is not Blender's (``bpy.app.version``): the
application identity (``scripts/modules/angelo_identity.py``, see
``doc/angelo/app_identity.rst``) is its single source, and the update check,
release notes and crash reports read it here. ``FALLBACK_VERSION`` and the link
table below stand in only where that module cannot be imported (a host test
that loads the add-on without ``scripts/modules`` on its path); a host test pins
that they equal the identity values, so the two can't drift apart unnoticed.

Standard library only; no bpy (the identity module imports ``bpy`` lazily).
"""

import re

FALLBACK_VERSION = "1.0.5"
# The identity table's destinations (angelo_identity.json "urls"), used until it is present.
FALLBACK_LINKS = {
    "site": "https://angelo.studio/",
    "support": "https://angelo.studio/support",
    "report_bug": "https://angelo.studio/support/report-a-bug",
    "release_notes": "https://angelo.studio/releases",
}
_VERSION = re.compile(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})")


def _identity():
    try:
        import angelo_identity
    except ImportError:
        return None
    return angelo_identity


def current():
    """The running product's version, ``MAJOR.MINOR.PATCH``."""
    identity = _identity()
    return identity.APP_VERSION if identity is not None else FALLBACK_VERSION


def parse(text):
    """``(major, minor, patch)`` of a ``MAJOR.MINOR.PATCH`` string, or None when it is anything else."""
    match = _VERSION.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def display(text):
    """What people read: ``1.0`` for 1.0.0, ``1.0.1`` for a patch release."""
    parsed = parse(text)
    if parsed is None:
        return str(text)
    return text if parsed[2] else "%d.%d" % parsed[:2]


def link(key):
    """A public link by identity key (``report_bug``, ``release_notes``, ``support``, ``site``)."""
    identity = _identity()
    if identity is not None:
        try:
            return identity.url(key)
        except KeyError:
            pass
    return FALLBACK_LINKS[key]
