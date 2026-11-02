# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Files Angelo copies into a person's folders get that person's normal permissions, never the source's.

A source installed read-only (Angelo's own bundle in an install the user can't write, a vendor's engine, another
Blender's add-ons) carries read-only modes. A copy that kept them can't be moved into its place (macOS and Linux need
write permission on a folder to give it a new parent), updated or removed by the person, and a cleanup that ignores
errors leaves it behind. ``own`` adds owner read and write (and search, on folders) to everything a copy made and
keeps every other bit, so an executable stays executable.

Used by every copy into a person's folders: engine live-link plugins (``engine_link_assets.install_plugin``), engine
add-ons Angelo cannot link (``engine_setup``) and imported Blender settings (``settings_import``).
"""

import os
import shutil
import stat


def own(path):
    """Make ``path`` and, for a folder, everything under it owner-readable and -writable. Links are left alone."""
    _own_one(path)
    if os.path.isdir(path) and not os.path.islink(path):
        for root, folders, files in os.walk(path):   # Top-down: a folder is owned before it is listed.
            for name in folders + files:
                _own_one(os.path.join(root, name))


def _own_one(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        return
    wanted = stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if stat.S_ISDIR(info.st_mode) else 0)
    if info.st_mode & wanted != wanted:
        os.chmod(path, stat.S_IMODE(info.st_mode) | wanted)


def copy_tree(source, target, **options):
    """``shutil.copytree`` whose copy the person owns (``own``). Returns ``target``."""
    shutil.copytree(source, target, **options)
    own(target)
    return target


def copy_file(source, target):
    """``shutil.copy2`` whose copy the person owns (``own``). Returns ``target``."""
    shutil.copy2(source, target)
    own(target)
    return target


def remove_tree(path):
    """Remove a folder a copy made, read-only parts included; best effort, never raises."""
    def retry(function, failed, error):
        try:
            own(os.path.dirname(failed))
            if os.path.lexists(failed):
                _own_one(failed)
            function(failed)
        except OSError:
            pass
    try:
        shutil.rmtree(path, onexc=retry)
    except OSError:
        pass
