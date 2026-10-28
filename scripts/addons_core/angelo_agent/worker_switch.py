# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A running worker Blender takes a new part file (``worker_host``'s ``switch_part`` request).

Angelo sends it when a warm worker is given another part (``worker_pool``: a done worker's process taking a waiting
part, or a worker launched ahead taking its first): one process then builds several parts one after another, each
in its own file and folder, each with a chat of its own in Angelo.

Contract, for any part: the earlier part's file stays exactly as its last save left it (every edit saved it, and
nothing is saved here), so its merge reads what its chat built; and nothing of it reaches the new part. The
worker's per-chat state goes (its chats' shadow controllers, their observed handles and prepared turns, the count of
saves, the view cache), and Blender's data is the factory start-up again, made empty and saved as the new part
exactly as a new worker's first part (``worker_main.open_part``), whose load starts that part's own Undo history.
Preferences stay (the probe on Mac 2, 2026-10-25: ``read_homefile`` with factory start-up keeps them, and the new
part's data blocks equal a fresh worker's). What the machine taught the worker, whether it draws offscreen, stays
too: that is what keeps it warm. A switch is refused, the worker still on its earlier part, while a program stages
or a call waits there, or (design A') while a chat it hosts is still open: Angelo closes each first
(``worker_chat_lifecycle.before_switch``), so a chat's journal is sealed on the part it was written for.
"""

import os

from . import worker_host


def switch_part(host, part):
    """``host`` (a ``WorkerHost``) leaves its part for ``part``, a full ``.blend`` path; the answer's layout is the
    new, empty part's (``live_layout``)."""
    from . import native_programs, view_cache
    from .worker_main import open_part
    if not isinstance(part, str) or not part.endswith(".blend") or not os.path.isabs(part):
        raise worker_host._refused("A worker switches only to a .blend part file given by its full path")
    staging = getattr(native_programs, "runtime_is_staging", native_programs.has_active_programs)
    if staging() or any(shadow.pending for shadow in host.shadows.values()):
        raise worker_host._refused("A program stages or a call waits in this worker; it keeps its part")
    if host.hosted_sessions():   # A': Angelo closes every chat this worker hosts first (C10).
        raise worker_host._refused("A chat is still open in this worker; it keeps its part until Angelo closes it")
    for session in host.shadows:
        host.ui._controllers.pop(session, None)
    host.shadows.clear()
    host.tables.clear()
    host.turns.clear()
    view_cache.clear()
    result = host.bpy.ops.wm.read_homefile(use_factory_startup=True, load_ui=False)
    if "FINISHED" not in result:
        raise RuntimeError("The worker could not clear its earlier part")
    host.part, host.revision = part, 0
    open_part(host.bpy, part)
    return {"value": part, "layout": host.layout()}
