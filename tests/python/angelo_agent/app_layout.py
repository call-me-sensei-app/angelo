# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a built app keeps its per-version resources, read from the app itself (standard library only).

Blender lays out ``<major>.<minor>/`` (its ``python``, ``scripts`` and ``datafiles``) in ``Contents/Resources`` on macOS
and beside the executable on Windows and Linux. Host drivers that start an app's bundled Python or import its add-on
find that folder here, never from a literal: a ``Resources/5.2`` left in four drivers failed when the base moved to 5.3.
"""

import re
from pathlib import Path

SERIES = re.compile(r"\d+\.\d+")


def resources(binary):
    """The app's one ``<major>.<minor>`` folder, for ``binary`` (``Contents/MacOS/Angelo`` or ``Angelo.exe``)."""
    binary = Path(binary).resolve()
    root = binary.parents[1] / "Resources" if binary.parent.name == "MacOS" else binary.parent
    found = sorted(path for path in root.iterdir() if path.is_dir() and SERIES.fullmatch(path.name))
    if len(found) != 1:
        raise FileNotFoundError("Expected one <major>.<minor> folder in %s, found %s" % (root, [p.name for p in found]))
    return found[0]


def python(binary):
    """The app's bundled Python interpreter."""
    folder = resources(binary) / "python" / "bin"
    windows = folder / "python.exe"
    if windows.is_file():
        return windows
    found = sorted(path for path in folder.glob("python3.*") if re.fullmatch(r"python3\.\d+", path.name))
    if len(found) != 1:
        raise FileNotFoundError("Expected one python3.<minor> in %s, found %s" % (folder, [p.name for p in found]))
    return found[0]
