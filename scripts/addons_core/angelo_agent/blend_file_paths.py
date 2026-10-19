# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How ``angelo_blend_file`` names a referenced file, and whether it is there. No Blender dependency.

A .blend stores the paths of its images, sounds, fonts, caches and libraries
as the artist's machine wrote them, and an absolute path carries folder names
(a user, a client, a project) that must not reach a model provider. So a file
is named to the model in one of two ways only:

* a path relative to the selected file workspace (the form the file tools
  take) or, failing that, to the .blend's own folder, when the file lies
  inside one of them;
* otherwise its base name alone, marked ``outside``.

``..`` never appears: a path that climbs out of both folders is outside. The
decision is lexical (``os.path.normpath``); nothing is opened or resolved to
make it. ``state`` then asks the filesystem one question, whether the file
exists, for the status the model reads.
"""

import ntpath
import os
import posixpath
import re

PACKED, PRESENT, MISSING, UNKNOWN = "packed", "present", "missing", "unknown"
BLEND_RELATIVE = "//"
# Blender's built-in font and other placeholders are not files.
NOT_FILES = frozenset({"", "<builtin>"})
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")
_TOKEN = re.compile(r"<UDIM>|<UVTILE>")
MAX_NAME = 255


def base_name(raw):
    """The last component of a stored path, whichever platform wrote it."""
    return re.split(r"[\\/]", raw.rstrip("\\/"))[-1][:MAX_NAME]


def absolute(raw, blend_folder):
    """The absolute path a stored path means, or None when it cannot be known.

    ``//`` is relative to the .blend's folder, unknown while the file is
    unsaved. A path written on Windows cannot be located from another platform.
    """
    if raw.startswith(BLEND_RELATIVE):
        if not blend_folder:
            return None
        return os.path.normpath(os.path.join(blend_folder, raw[2:].replace("\\", os.sep)))
    if _DRIVE.match(raw) and os.name != "nt":
        return None
    if not os.path.isabs(raw):
        return None  # Blender stores absolute or //-relative paths; anything else has no known base.
    return os.path.normpath(raw)


def _inside(path, folder):
    """``path`` relative to ``folder`` when it lies inside it, else None (lexical; case-insensitive on Windows)."""
    if not folder:
        return None
    module = ntpath if os.name == "nt" else posixpath
    folder = module.normpath(folder)
    try:
        relative = module.relpath(path, folder)
    except ValueError:  # Another drive.
        return None
    if relative == module.curdir or relative == module.pardir or relative.startswith(module.pardir + module.sep):
        return None
    return relative.replace("\\", "/")


def public_name(raw, blend_folder, workspace_root=None):
    """``{"file": name}`` for the model, with ``outside`` true when only the base name can be given."""
    located = absolute(raw, blend_folder)
    if located is not None:
        for folder in (workspace_root, blend_folder):
            relative = _inside(located, folder)
            if relative:
                return {"file": relative[:1024]}
    return {"file": base_name(raw), "outside": True}


def state(raw, blend_folder, *, packed=False, exists=None):
    """``packed``, ``present``, ``missing`` or ``unknown`` (unsaved file, foreign platform path).

    ``exists``: the one filesystem question asked, ``os.path.exists`` unless a test supplies its own.
    """
    exists = exists or os.path.exists
    if packed:
        return PACKED
    located = absolute(raw, blend_folder)
    if located is None:
        return UNKNOWN
    if _TOKEN.search(located):
        # A tiled image names its tiles with a token; the first tile stands for the set.
        located = located.replace("<UDIM>", "1001").replace("<UVTILE>", "u1_v1")
    try:
        return PRESENT if exists(located) else MISSING
    except OSError:
        return UNKNOWN
