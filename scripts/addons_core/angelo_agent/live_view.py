# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker's part, live in Angelo, never in Angelo's own data.

Each worker chat (``worker_routing``) has one **live part**: its saved part file read into a private Main of its own
(``_bpy._angelo_live.open``, as a file open reads it, never set up as the application's data), with its own
depsgraph evaluated without any add-on handler. Angelo draws it (the session rail's pictures, the live view:
``rail_pictures``, ``live_view_draw``), always in Solid, and asks it for bounds, a ray cast and a summary; no Python
object ever points into it. After each worker save the newest file is read again into a new private Main, and the
old one is freed.

Why not link the part into Angelo's data (the first phase 1 design, break-it P1-1): every ``bpy.data`` lookup,
iteration and script of the artist's own chat then saw the worker's objects, materials and collections (a script
assigning "Wood" took a worker's linked material; name prediction counted worker names as taken; "clear the scene"
deleted them), each reload was a whole-Main mutation that could race a bake or render thread, and keeping the
library out of saved files needed save-time localisation. A private Main has none of those: the artist's file, Undo,
saves, snapshots and lookups never contain a part.

Cost: one read of the part file and one evaluation of the part alone per worker save, whatever the size of the
artist's document (measured in ``bench_live_view.py``). Reads happen on the main thread. A worker writes its file
only while it answers an edit (it saves before it answers), so a part is not read while its chat's lane waits for an
answer that may write it (``parallel_sessions._answering``) and a read never meets a half-written save. With the
lanes that tell an edit from a read (e2e's ``worker_dispatch.writing``) only an edit holds the part back (break-it P1-8
P2); before them, any answer does.
"""

import os
import time

# Subdivision levels a live part shows at most (Blender's Simplify on the private copy). Every read is a new Main, and
# Blender sets each subdivided mesh up again at its first draw: 24 subdivided spheres cost 0.30 s at level 1, 2 or 3
# alike (the setup, not the face count) and 1.6 ms at level 0 (Mac 2, bench_live_view.py --heavy 24). So a live part
# shows its subdivision cage, a simplified picture; the worker's file and the merged result keep their levels.
SUBDIVISION = 0
GPU_SUBDIVISION = False   # Evaluated on the CPU when read (the GPU path cost the same per read, measured).
_parts = {}    # worker chat session id -> {"path": absolute part path, "part": capsule or None, "revision": int}
reloads = []   # Fixtures: (session id, seconds) of each read + evaluation, newest last; bounded.


def _native():
    import _bpy
    return _bpy._angelo_live


def available():
    """Whether this build has private live parts (a build without the native module shows no live parts)."""
    try:
        _native()
    except (ImportError, AttributeError):
        return False
    return True


def sessions():
    return list(_parts)


def part(session_id):
    """The open, evaluated live part of a worker chat, or None (none read yet, or its file is missing)."""
    entry = _parts.get(session_id)
    return entry["part"] if entry is not None else None


def revision(session_id):
    """How many times this worker's part was read: what a drawn picture of it depends on."""
    entry = _parts.get(session_id)
    return entry["revision"] if entry is not None else None


def attach(session_id, path):
    """Show the worker chat ``session_id``'s part (``path``: its .blend), read now when the file exists. Whatever reads
    a part has its exit cleanup registered (``register``, idempotent), so no part outlives Blender's data."""
    register()
    _parts[session_id] = {"path": str(path), "part": None, "revision": 0}
    return refresh(session_id)


def refresh(session_id):
    """Read the newest saved part into a new private Main, evaluate it, free the old one. Seconds spent."""
    entry = _parts.get(session_id)
    if entry is None or not available():
        return 0.0
    started = time.perf_counter()
    fresh = None
    if os.path.isfile(entry["path"]):
        native = _native()
        fresh = native.open(entry["path"], SUBDIVISION)
        try:
            native.evaluate(fresh, GPU_SUBDIVISION)
        except BaseException:
            native.close(fresh)
            raise
    old, entry["part"] = entry["part"], fresh
    entry["revision"] += 1
    if old is not None:
        _native().close(old)
    spent = time.perf_counter() - started
    reloads.append((session_id, spent))
    del reloads[:-64]
    return spent


def detach(session_id):
    """Forget a worker chat's live part and free it."""
    entry = _parts.pop(session_id, None)
    if entry is not None and entry["part"] is not None and available():
        _native().close(entry["part"])


def detach_all():
    for session_id in list(_parts):
        detach(session_id)


def bounds(session_id):
    """``((min), (max))`` of the part's visible objects, or None."""
    found = part(session_id)
    return _native().bounds(found) if found is not None else None


def summary(session_id):
    """``{"objects", "meshes", "materials", "size"}`` of the part, or None."""
    found = part(session_id)
    return _native().summary(found) if found is not None else None


def ray_cast(session_id, origin, direction):
    """``(object name, object type value)`` first hit along the ray, or None."""
    found = part(session_id)
    return _native().ray_cast(found, tuple(origin), tuple(direction)) if found is not None else None


def native_draw(target, offscreen, space, view_matrix, projection_matrix, *, view_layer=None, grid=False,
                background=True):
    """Draw a live part (or a scene of the document with its ``view_layer``) into ``offscreen`` with a view of its
    own, always in Solid with ``space``'s Solid settings; ``space`` is only read (``_bpy._angelo_live.draw``).
    ``background`` False: what shows no geometry is left transparent (its alpha says where geometry shows)."""
    if background:
        _native().draw(target, offscreen, space, view_matrix, projection_matrix, view_layer=view_layer, grid=grid)
    else:
        _native().draw(target, offscreen, space, view_matrix, projection_matrix, view_layer=view_layer, grid=grid,
                       background=False)


def hold(window, area, seconds):
    """No event reaches ``area``'s own handlers for ``seconds``, and over it the window's key maps run only the
    allowed commands (``allow``); ``area`` None releases the window's hold (``wm_angelo_input_hold.cc``)."""
    _native().hold(window, area, float(seconds))


def held(window, area):
    """Whether ``area`` holds its input now; False in a build without the hold, so no live view shows there."""
    native = _native()
    return hasattr(native, "held") and native.held(window, area)


def allow(idnames):
    _native().allow(tuple(idnames))


def release_holds():
    native = _native()
    if hasattr(native, "release_holds"):
        native.release_holds()


def window_pixels(window):
    """``(width, height, bytes)``: ``window`` drawn again now into an offscreen and read (RGBA, bottom row first),
    never the front buffer, which shows the last presented frame on macOS and Windows (break-it P1-3 M1)."""
    return _native().window_pixels(window)


def _exit_pre(*_args):
    """Free every part while Blender's data and GPU still exist (Python's own last collection is too late)."""
    try:
        detach_all()
    except Exception as error:   # noqa: BLE001 - quitting goes on whatever this says.
        print("Angelo: live parts at exit:", error)


_exit_pre._bpy_persistent = True   # As @persistent: a file load never drops it, so no part outlives Blender's data.


def register():
    import bpy
    if _exit_pre not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(_exit_pre)


def unregister():
    import bpy
    if _exit_pre in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_exit_pre)
    detach_all()
    try:
        release_holds()
    except (ImportError, AttributeError):
        pass
