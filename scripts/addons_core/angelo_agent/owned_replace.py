# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saves and removals of a file Angelo keeps, whose name comes from Python state (doc/angelo/script_confinement.rst,
"Angelo's own I/O").

A store's file name is held in Python (a path attribute, a chat's id): an agent script that reached that state could
change it in place, and the next save or removal would replace or delete what the script named. So an existing
destination is replaced or removed only while it is on C's record of what Angelo made (``owned_paths``); a missing
one is created, then recorded. Angelo records its store's file when it first opens it (``adopt``), and records it
again after every replace (the replace makes it a new file). A refusal is ``NotAngelos`` (a ``PermissionError``):
the caller says it could not save, as for any refused save.

Without the native guard (tests that run outside Blender) every path counts as Angelo's, as before.
"""

import os

from . import owned_paths, shared_replace


class NotAngelos(PermissionError):
    """The destination exists and is not a file Angelo made: it is left as it is."""


def _private(path):
    """Whether C keeps ``path`` inside Angelo's private folders (a set no script can change): only a file there is
    recorded on Angelo's word that it is its own. Without the native guard: every path (nothing is recorded then)."""
    judge = getattr(owned_paths._GUARD, "judge_path", None)
    if judge is None:
        return True
    try:
        return judge(os.fspath(path)) == "private"
    except (TypeError, ValueError, OSError):
        return False


def adopt(path):
    """Record ``path``, the file a store keeps, as Angelo opens the store, when it lies in Angelo's private folders:
    a name from Python state never makes a file elsewhere Angelo's. A missing file is recorded when Angelo first
    writes it."""
    if os.path.lexists(path) and _private(path):
        owned_paths.own(path)


def mine(path):
    """Whether ``path`` may be replaced or removed: the file Angelo recorded, or no file yet where C lets Angelo
    write (``may_write``: never a new file in a folder a name in Python state was steered to)."""
    return owned_paths.owned(path) if os.path.lexists(path) else owned_paths.may_write(path)


def replace(temporary, path, *args, **kwargs):
    """``shared_replace.replace(temporary, path)`` onto a destination that is Angelo's (``mine``); the replaced
    file is recorded. ``temporary`` is the caller's own new file; on a refusal it is removed."""
    if not mine(path):
        shared_replace.discard(temporary)
        raise NotAngelos("%s is not a file Angelo keeps; it was left as it is" % os.fspath(path))
    owned_paths.disown(path)
    result = shared_replace.replace(temporary, path, *args, **kwargs)
    owned_paths.own(path)
    return result


def remove(path, *args, **kwargs):
    """``shared_replace.remove(path)`` when ``path`` is Angelo's; a file Angelo did not record is left (False)."""
    if not os.path.lexists(path):
        return False
    if not owned_paths.owned(path):
        return False
    owned_paths.disown(path)
    shared_replace.remove(path, *args, **kwargs)
    return True
