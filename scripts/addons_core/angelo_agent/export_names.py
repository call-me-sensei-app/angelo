# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where an export's save browser opens: the document's folder and one clean file name, never a path from scene data.

An object's or an action's name is data the assistant can set. Joined into a path, a name like ``../../Desktop/x`` or
``/Users/Shared/drop/x`` opened the browser in that folder, and the person's confirm then vouched a place the
assistant chose (independent review of the 2.1.0 exports, findings S-S1 and R-S1). ``default_export_path`` keeps only a
single file name made from the name (every path separator, drive colon, reserved or control character replaced, no
leading dots) in the saved document's folder, or the name alone for an unsaved document, where Blender's own default
folder applies. No ``bpy``: host tests check it directly (``test_native_export_names``).
"""

import os
import re

PATH = os.path   # The running system's path rules; test_native_export_names checks every system's (ntpath, posixpath).
UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')   # Separators on every system, drive and stream colons, reserved.
FALLBACK = "export"


def clean_name(name, fallback=FALLBACK):
    """``name`` as one file name with no folder in it."""
    cleaned = UNSAFE.sub("_", str(name or "")).strip().lstrip(".").rstrip(". ")
    return cleaned or fallback


def default_export_path(name, extension, document=""):
    """The browser's starting path for exporting something called ``name``: ``<document folder>/<clean name><ext>``,
    joined by the running system's rules (``PATH``), so on Windows a valid Windows path."""
    filename = clean_name(name) + extension
    folder = PATH.dirname(document) if document else ""
    return PATH.join(folder, filename) if folder else filename
