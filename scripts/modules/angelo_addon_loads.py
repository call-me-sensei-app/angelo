# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own record of which of the person's add-ons loaded, at which add-on version, on this Blender version.

Written by ``angelo_safe_start`` each time a start (or a preference read's enabling) completes, into this version's
``<config>/angelo-start/addon-loads.json``; read by the update card's add-on check (``angelo_agent.addon_compat``)
from the folder of the version an update is built on, so an add-on the person already ran there counts as known to
work (doc/angelo/updates_and_reports.rst, "Your add-ons and the update"). Only module names and version strings are
kept: never a path. Written only when it changes, replaced in one rename. No bpy: test_native_addon_compat.py.
"""

import json
import os
import re
from pathlib import Path

FOLDER = "angelo-start"
FILE = "addon-loads.json"
FORMAT = 1
MAX_ADDONS = 500
_SERIES_FOLDER = re.compile(r"^\d+\.\d+$")


def version_text(version):
    """An add-on's version as the record keeps it: three numbers (``(1, 2)`` -> ``"1.2.0"``, as Blender's
    extension code pads them), so it matches the update check's reading; "" for anything else."""
    parts = list(version) if isinstance(version, (tuple, list)) else []
    if not parts or len(parts) > 3 or not all(isinstance(part, int) and part >= 0 for part in parts):
        return ""
    return ".".join(str(part) for part in (parts + [0, 0])[:3])


def _document(blender, addons):
    return {"format": FORMAT, "blender": ".".join(str(part) for part in blender),
            "addons": dict(sorted(addons.items())[:MAX_ADDONS])}


def write(folder, blender, addons):
    """Record ``addons`` (``{module: version text}``) as loaded on Blender ``blender`` (a version tuple). Returns
    whether the file changed; raises ``OSError`` for a folder that cannot be written (the caller prints it)."""
    folder = Path(folder)
    path = folder / FILE
    text = json.dumps(_document(blender, addons), indent=1, sort_keys=True)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeError):
        pass
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (".%s.%d" % (FILE, os.getpid()))
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return True


def read(path):
    """``(series, {(module, version)})`` from a record, ``(None, set())`` for one missing or not well formed."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None, set()
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        return None, set()
    blender, addons = document.get("blender"), document.get("addons")
    parts = blender.split(".") if isinstance(blender, str) else []
    if len(parts) != 3 or not all(part.isdigit() for part in parts) or not isinstance(addons, dict):
        return None, set()
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in addons.items()):
        return None, set()
    return (int(parts[0]), int(parts[1])), set(addons.items())


def record_for_series(config, target_series):
    """The record of another Blender series beside this version's ``config`` folder (``<root>/5.3/config`` ->
    ``<root>/5.4/config/angelo-start/addon-loads.json``), or None where there are no per-version folders
    (``BLENDER_USER_CONFIG`` and the like)."""
    config = Path(config)
    version_folder = config.parent
    if config.name != "config" or not _SERIES_FOLDER.match(version_folder.name):
        return None
    return version_folder.parent / ("%d.%d" % tuple(target_series[:2])) / "config" / FOLDER / FILE
