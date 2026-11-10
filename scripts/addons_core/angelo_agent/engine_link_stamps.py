# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linked-asset stamp records: the digest of bytes actually imported or written, never a later read of the asset.

``engine_link_assets`` keeps its public asset-based interface and supplies the stamp path and identities here.
Reads share replacement with a concurrent writer; writes land through a new private file. A child's record names
its writer, save and link, so a later process or a newer save never lends its digest to an older save's preflight.
No bpy or engine adapters.
"""

import json
import os
import secrets

from . import shared_replace


def read(path):
    """The digest recorded for a working file, or None for a missing or unreadable record."""
    try:
        value = json.loads(shared_replace.read_text(path)).get("sha256")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def write(path, asset, sha256):
    """Record the supplied digest of imported/written bytes, never digest the asset after that read or write."""
    temporary = "%s.%s.tmp" % (path, secrets.token_hex(4))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"sha256": sha256, "asset": asset}, handle)
        shared_replace.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise
    return sha256


def written(path, asset, save, link, writer):
    """The digest recorded by this writer's child for exactly this save and link; another record is unknown."""
    try:
        value = json.loads(shared_replace.read_text(path))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("asset") != asset or value.get("save") != save
            or value.get("link") != link or value.get("writer") != writer
            or not isinstance(value.get("sha256"), str)):
        return None
    return value["sha256"]
