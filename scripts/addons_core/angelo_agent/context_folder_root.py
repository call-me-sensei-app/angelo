# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a chat's attached folder is opened at its root: judged by the chat's grant in C, never by C's ``may_write``.

``may_write`` refuses a person's project folder once any agent script has run in the process (C narrows after the
first script region), so a folder the person attached could no longer be read for the rest of such a session. The
shared walk (``checkpoint_filesystem._private_directory``) always asks ``may_write`` and stays that way; a context
folder's workspace opens its root here instead (``ReadOnlyFolder._open_root``, a class-level step of
``files.FileWorkspace._folder``), with the same open that follows no link or junction in any component, and the walk
goes on only when C holds a context-folder grant whose digest is the very folder that descriptor opened (its real path,
device and inode). The decision reads only C and the opened descriptor: no workspace attribute switches it on, and a
workspace of any other kind that reached this would still need C to have granted that exact folder. Standard library
only; no bpy.
"""

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

from . import anchored_windows, checkpoint_filesystem as walk, grant_authority

NOT_GRANTED = "The attached folder isn't the one the person attached any more; nothing was read or written there"


def digest(real, device, inode):
    """What C holds for a chat's folder: the SHA-256 of its real path with its device and inode."""
    return hashlib.sha256(("%s\0%d:%d" % (real, device, inode)).encode("utf-8", "surrogatepass")).hexdigest()


def _judged(root, key, folder):
    """``folder``, the opened root, when C holds ``key`` for exactly it; ValueError otherwise."""
    if not isinstance(key, str) or not (type(folder) is int or isinstance(folder, anchored_windows.WindowsDirectory)):
        raise ValueError(NOT_GRANTED)   # No grant named, or no descriptor to judge: never judged by its name alone.
    opened = walk.directory_info(folder)
    held = grant_authority.value(grant_authority.CONTEXT_FOLDERS, key)
    if held is None or held != digest(os.path.realpath(root), opened.st_dev, opened.st_ino):
        raise ValueError(NOT_GRANTED)
    return folder


@contextmanager
def granted_root(root, key):
    """The attached folder ``root``, opened without following a link anywhere in it (a descriptor, or a verified
    Windows handle), yielded only once C holds the grant ``key`` for that very folder. It never creates anything."""
    api = walk._windows_api()   # Looked up at each call, as the shared walk does.
    if api is not None:
        with anchored_windows.open_directory(root, False, api, root) as folder:
            yield _judged(root, key, folder)
        return
    if os.open not in os.supports_dir_fd:
        raise ValueError(NOT_GRANTED)
    path = Path(root).absolute()
    descriptor = None
    try:
        if walk._NOFOLLOW_ANY:
            descriptor = walk._open_existing_directory(path)
        else:
            descriptor = os.open(path.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            for part in path.parts[1:]:
                child = walk._open_directory_at(descriptor, part)
                os.close(descriptor)
                descriptor = child
        yield _judged(root, key, descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
