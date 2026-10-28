# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's images a worker chat's view names, shared into its worker Blender's own store. No bpy.

A view resolves its ``reference`` in the worker's own references (``worker_process.references_of``), which never held
the chat's images, so a worker's pair view was always refused (break-it E2E-16 M-2). ``share`` links (or copies) each
image the chat holds there under the same file name, one file on disk as the chat's own (E2E-16 L-4); one the chat
does not hold is left to the worker's own refusal.

The worker's private folder is the worker's: its start makes it with its state folder, and its stop removes it
(``worker_process``). A share runs only for a running worker and makes folders only below that state folder, one
level at a time, never the state or private folder itself; a share its end overtook removes what it made. So an ended
worker's folder is never made again (E2E-17 L-2; E2E-18 L-3: a stop between a check and ``mkdir(parents=True)`` had
it made again, with the person's image linked into it and nothing to remove it).
"""

import os
import shutil
from pathlib import Path


def share(worker, controller, session, arguments):
    """Each image ``controller`` holds that a view in ``arguments`` names, in ``worker``'s store for ``session``."""
    from .worker_process import STATE, references_of
    views = arguments.get("views") if isinstance(arguments, dict) else None
    private, alive = getattr(worker, "private", None), getattr(worker, "alive", None)
    store = getattr(controller, "references", None)
    if (not isinstance(views, list) or private is None or session is None or store is None or not callable(alive)
            or not alive()):
        return
    made = []
    try:
        for view in views:
            try:
                source = store.attached_image(view.get("reference")) if isinstance(view, dict) else None
            except ValueError:
                continue
            target = references_of(private, session) / source.name if source is not None else None
            if target is None or target.exists():
                continue
            made += folders(Path(private) / STATE, target.parent)
            try:
                os.link(source, target)
            except FileNotFoundError:
                raise
            except OSError:   # Another volume, or links refused: a copy.
                shutil.copy2(source, target)
            made.append(target)
    except FileNotFoundError:   # Its state folder went with its end.
        pass
    if made and not alive():   # Its end overtook this share: what it made goes, and the folders it emptied.
        for path in [*reversed(made), Path(private) / STATE, Path(private)]:
            try:
                path.rmdir() if path.is_dir() and not path.is_symlink() else path.unlink()
            except OSError:
                pass   # Never a folder that still holds anything (a dead worker's log stays for its stop).


def folders(state, folder):
    """The folders made for ``folder`` below the existing ``state`` folder, one level at a time, never ``state`` or
    above it (``FileNotFoundError`` once ``state`` is gone)."""
    made = []
    for level in reversed([folder, *folder.parents][:len(folder.relative_to(state).parts)]):
        try:
            level.mkdir(mode=0o700)
            made.append(level)
        except FileExistsError:
            pass
    return made
