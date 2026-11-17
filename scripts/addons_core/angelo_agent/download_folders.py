# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where ``angelo_download`` puts a file (doc/angelo/downloads.rst, "Where the file goes"). Main thread for ``root``
(it may write the unsaved document's id); no bpy at import.

* **A saved document**: beside its file (``<its folder>/assets/downloads``).
* **An unsaved document**: its own folder, ``<Blender's temporary folder>/angelo-downloads/<id>``, where Blender also
  keeps an unsaved session's autosave. The id is a random one kept as the ``_angelo_downloads_id`` custom property of
  every local scene, so the document keeps it through a reload of the unsaved session (Recover Last Session, Recover
  Auto Save). Never the chat's own folder: it sits under the agent state folder, which the script confinement keeps
  private, so a later agent script could not read the file there (v201-local-models' spec, 2026-11-15).
* Saving later moves nothing: earlier downloads stay where they are, so their paths in the chat stay valid; later
  ones go beside the saved file.

``unsaved_base`` is the folder the script confinement lists as a tool's output (``script_confinement_blender.
tool_output_folders``, on the confinement branch that merges before this one): an agent script may read a download
there but not change it. The temporary folder is shared on Linux,
so ``angelo-downloads`` and each document's folder are made owner-only, and one that is a link, another user's, or
writable by others is refused (``Unavailable``) instead of used.
"""

import os
import re
import tempfile
import uuid
from pathlib import Path

FOLDER = "angelo-downloads"
PROPERTY = "_angelo_downloads_id"
_ID = re.compile(r"^[0-9a-f]{32}$")


class Unavailable(OSError):
    """The unsaved document's folder can't be used safely (a link, another user's, open to others)."""


def where(bpy):
    """"project" for a saved document, "unsaved" otherwise."""
    return "project" if bpy.data.filepath else "unsaved"


def unsaved_base(bpy):
    """``<Blender's temporary folder>/angelo-downloads``: the parent of Blender's own session folder
    (``bpy.app.tempdir`` is ``<base>/blender_<n>/``, removed when Blender quits), else the system's."""
    session = (getattr(bpy.app, "tempdir", "") or "").rstrip("/\\")
    base = os.path.dirname(session) if session else tempfile.gettempdir()
    return Path(base) / "angelo-downloads"   # This module's FOLDER, spelled out (see root)


def _local(scene):
    return getattr(scene, "library", None) is None and getattr(scene, "override_library", None) is None


def _key(bpy):
    """The unsaved document's id: the one its local scenes agree on, else a new one written to each of them."""
    scenes = [scene for scene in bpy.data.scenes if _local(scene)]
    found = {scene.get(PROPERTY) for scene in scenes} - {None}
    value = found.pop() if len(found) == 1 else None
    if isinstance(value, str) and _ID.fullmatch(value):
        return value
    value = uuid.uuid4().hex
    for scene in scenes:
        try:
            scene[PROPERTY] = value
        except (TypeError, AttributeError, RuntimeError):
            pass   # A scene that refuses it: the others keep the id.
    return value


def _owned(folder):
    """Make ``folder`` owner-only if missing; refuse one that is a link, another user's or open to others."""
    try:
        os.mkdir(folder, 0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise Unavailable("The downloads folder %s could not be made: %s" % (folder, error.strerror)) from None
    info = os.lstat(folder)
    import stat
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Unavailable("%s is a link or not a folder" % folder)
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise Unavailable("%s belongs to another user or others can change it" % folder)


def root(bpy):
    """The folder ``assets/downloads`` is made in, for the open document. Main thread. Its downloads folder is
    Angelo's by identity (``owned_paths.make_dir_beside``: made by C now, or adopted when an earlier session made it):
    named by Blender's own document path or temporary folder, never by Python state, so a download lands there
    whatever object later names it, and nothing else in the document's folder becomes writable. The names under it
    are literals here, never ``download_spec.FOLDER`` or this module's ``FOLDER``: a script that changed a module's
    value in place would otherwise name a folder of the person's (``("..", "..")`` names the document's
    grandparent). download_fetch places a file only where C says yes, so a changed FOLDER there is refused."""
    if bpy.data.filepath:
        found = Path(bpy.data.filepath).parent
    else:
        base = unsaved_base(bpy)
        os.makedirs(base.parent, exist_ok=True)
        _owned(base)
        found = base / _key(bpy)
        _owned(found)
        found = found.resolve()
    from . import owned_paths
    try:   # download_spec.FOLDER, spelled out; Angelo's by identity (made now, or by an earlier session).
        owned_paths.make_dir_beside(found, os.path.join("assets", "downloads"))
    except PermissionError:
        raise Unavailable("The downloads folder in %s can't be used: a link, or a script made it" % found) from None
    return found
