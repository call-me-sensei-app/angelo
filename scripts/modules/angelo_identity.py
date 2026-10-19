# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

"""Angelo's application identity for Python: product name, vendor, URLs.

The values live in ``angelo_identity.json`` beside this module (the single
source also read by ``build_files/cmake/angelo_identity.cmake``; see
``doc/angelo/app_identity.rst``). Nothing here is hard-coded, so a pending
identity decision is a one-line change in that file.

Standard library only: usable from Blender's UI scripts, the external CLI
client and host tests alike. Import-time work is one small JSON read.
"""

__all__ = (
    "PRODUCT_NAME",
    "PRODUCT_SLUG",
    "PRODUCT_TAGLINE",
    "BASED_ON",
    "VENDOR_NAME",
    "BUNDLE_ID",
    "URL_SCHEME",
    "APP_VERSION",
    "APP_VERSION_DISPLAY",
    "data",
    "url",
    "url_table",
    "pending_urls",
)

import json
import os

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "angelo_identity.json")


def _load():
    with open(_FILE, encoding="utf-8") as handle:
        return json.load(handle)


_DATA = _load()


def data():
    """The complete identity table (treat as read-only)."""
    return _DATA


PRODUCT_NAME = _DATA["product"]["name"]
PRODUCT_SLUG = _DATA["product"]["slug"]
PRODUCT_TAGLINE = _DATA["product"]["tagline"]
BASED_ON = _DATA["product"]["based_on"]
VENDOR_NAME = _DATA["vendor"]["name"]
BUNDLE_ID = _DATA["bundle"]["macos_id"]
URL_SCHEME = _DATA["windows"]["url_scheme"]

# Angelo's own product version (``1.0.0``), independent of ``bpy.app.version``: the Blender version
# stays what file compatibility and add-on checks see, and is shown as the base ("based on Blender").
APP_VERSION = _DATA["version"]["label"]
_major, _minor, _patch = APP_VERSION.split(".")
# What people read: ``1.0`` for 1.0.0, ``1.0.1`` for a patch release.
APP_VERSION_DISPLAY = APP_VERSION if _patch != "0" else _major + "." + _minor


def url_table():
    """``{key: template}`` of every link; keys starting with ``_`` are notes, not links."""
    return {key: value for key, value in _DATA["urls"].items() if not key.startswith("_")}


def pending_urls():
    """Keys of the links that are placeholders until a release confirms them."""
    return tuple(_DATA["pending_urls"])


def url(key, **fields):
    """The link ``key`` with ``{language}``/``{major}``/``{minor}`` (or any given field) filled in.

    Raises ``KeyError`` for an unknown key, so a typo is found by the first test
    that draws the menu rather than by a user clicking a dead entry.
    """
    template = url_table()[key]
    if "{" not in template:
        return template
    if "major" not in fields or "minor" not in fields:
        import bpy
        fields.setdefault("major", bpy.app.version[0])
        fields.setdefault("minor", bpy.app.version[1])
    if "language" not in fields:
        import bpy
        fields["language"] = bpy.utils.manual_language_code()
    return template.format(**fields)
