# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connected app's attachments to its message (``external_agent``, ``angelo_send``): images it sends as data, and
files of the open project's folder by a path inside it, which become the conversation's own references
(``ReferenceStore.import_images``, its types and limits), never the window's composer pictures. A path is refused
when it is a link, out of the project folder, under a hidden folder or name, or in Angelo's or Blender's own
folders, and every path is refused while the project folder is the home folder or holds one of those
(doc/angelo/external_mcp.rst, "Tools"). Main thread.
"""

import base64
import os
from pathlib import Path


def attach(env, controller, attachments):
    """The app's attachments as the conversation's own references (``ReferenceStore.import_images``, its checks and
    limits): images it sends as data, and files of the open project's folder by a path inside it (the folder the
    person opened; a link or a path out of it is refused). Never the window's composer pictures."""
    import bpy
    if not attachments:
        return []
    from . import private_roots
    project = Path(bpy.data.filepath).parent.resolve() if bpy.data.filepath else None
    paths = []
    with private_roots.TemporaryDirectory("angelo-app-attach-") as scratch:   # Private, and removed as it returns.
        for index, item in enumerate(attachments):
            if "data" in item:
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[item["mime"]]
                path = Path(scratch) / ("attachment-%d%s" % (index, suffix))
                path.write_bytes(base64.b64decode(item["data"], validate=True))
                paths.append(str(path))
                continue
            if project is None:
                raise ValueError("A file attachment must be inside the open project's folder; this file is unsaved")
            path = Path(item["path"])
            path = (project / path if not path.is_absolute() else path)
            if path.is_symlink() or _inside(path.resolve(), project) is None:
                raise ValueError("A file attachment must be inside the open project's folder")
            if not attachable(path.resolve(), project, private_folders(env), Path.home()):
                raise ValueError("A file attachment can't be in a hidden folder or in Angelo's own folders, nor "
                                 "come from a project saved in the home folder")
            paths.append(str(path.resolve()))
        return controller.references.import_images(paths)


def private_folders(env):
    """Angelo's and Blender's own folders (its state, with every chat, its connections, Blender's user config and
    scripts), never an app's attachment."""
    import bpy
    folders = [env.state_directory(), env.ui.connection_directory()]
    for kind in ("CONFIG", "SCRIPTS", "DATAFILES"):
        try:
            folders.append(bpy.utils.user_resource(kind))
        except (AttributeError, TypeError, ValueError):
            pass
    return [Path(folder) for folder in folders if folder]


def attachable(path, project, private, home=None):
    """Whether a file of the open project's folder may be an app's attachment: never under a hidden folder or name
    (``.git``, ``.env``, a tool's settings), never in one of ``private`` (Angelo's and Blender's own folders), and
    nothing at all while the project folder is the home folder or holds one of them (a .blend saved there would reach
    every chat and sign-in below it). Folders are compared by identity (device and inode), never by spelling, so a
    case-folded or linked path names the same folder."""
    relative = _inside(path, project)
    if relative is None or any(part.startswith(".") for part in relative):
        return False
    folders = [folder for folder in (Path(item) for item in private if item) if folder.exists()]
    if home is not None and _same(project, home):
        return False
    for folder in folders:
        if any(_same(project, above) for above in (folder, *folder.parents)):
            return False   # The project folder holds (or is) one of Angelo's own folders.
    return not any(_same(above, folder) for above in Path(path).parents for folder in folders)


def _inside(path, project):
    """The names below ``project`` that lead to ``path`` (by folder identity, so a case-folded spelling of the project
    is the project), or None when ``path`` is not inside it."""
    names = []
    for above in (Path(path), *Path(path).parents):
        if _same(above, project):
            return list(reversed(names))
        names.append(above.name)
    return None


def _same(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
