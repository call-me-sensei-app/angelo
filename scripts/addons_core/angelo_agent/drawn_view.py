# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a 3D view's matrices are the ones its settings give now. No bpy at import.

Blender computes a region's view, window and perspective matrices (``RegionView3D.view_matrix`` and the others) only
when the region draws. Its settings (rotation, location, distance, perspective, lens, clipping, the camera it looks
through, its size) change at once, so after a script or a tool sets them, the matrices still hold the previous frame's
view until the next draw. The Main tile's picture read them in that gap (1.0.8 T4 round f3038da40a4,
test_blender_rail_crowd: the view set to look at the sky was decided through the previous case's view of a ground
plane). So ``note`` runs in the PRE_VIEW of every 3D view, after that frame's matrices are set, and records the
settings and the perspective matrix the frame drew with; ``current`` says whether the matrices now are a frame's drawn
from exactly the settings now. A view never noted is not current: its next frame notes it. Not read: a spot light
or an empty as the view's camera (its cone), a locked bone's pose and the view's lock offset (``ofs_lock``, not in
RNA); a change to only those is drawn after the next change the settings do show.
"""

WAIT = 2.0    # A view's frame is awaited at most this long: a window that is not drawn (minimized) still gets pictures.
RESUMED = 1.0  # A wait not asked about for this long was left off (a hold, hidden panels): the next one starts afresh.
_drawn = {}   # RegionView3D pointer -> (settings, perspective matrix) of its last frame.
_waiting = {}  # Who waits (a session's picture) -> (when it began waiting for its view's frame, when it last asked).
gave_up = [0]  # Fixtures: waits that ran out (``WAIT``) this launch.


def _flat(values):
    return tuple(float(value) for row in values for value in (row if hasattr(row, "__len__") else (row,)))


def _pointer(value):
    return value.as_pointer() if value is not None else None


def settings(space, rv3d, width, height, scene):
    """Everything Blender computes the view's matrices from (``view3d_viewmatrix_set``, ``view3d_winmatrix_set``)."""
    found = (_flat(rv3d.view_rotation), _flat(rv3d.view_location), float(rv3d.view_distance), rv3d.view_perspective,
             float(rv3d.view_camera_zoom), _flat(rv3d.view_camera_offset), float(space.lens),
             float(space.clip_start), float(space.clip_end), int(width), int(height))
    if rv3d.view_perspective == 'CAMERA':   # Through a camera: its place and lens are scene data, read as drawn.
        camera = space.camera
        data = getattr(camera, "data", None)
        found += (_pointer(camera),) + ((_flat(camera.matrix_world),) if camera is not None else ())
        if data is not None and hasattr(data, "lens"):
            found += (data.type, float(data.lens), float(data.ortho_scale), float(data.sensor_width),
                      float(data.sensor_height), data.sensor_fit, float(data.shift_x), float(data.shift_y),
                      float(data.clip_start), float(data.clip_end))
    lock = space.lock_object
    if lock is not None:   # Locked to an object (or one of its bones) or the 3D cursor: its place is the view's.
        found += (_pointer(lock), space.lock_bone, _flat(lock.matrix_world))
    if space.lock_cursor:
        found += ("cursor", _flat(scene.cursor.location))
    return found


def note(space, rv3d, region, scene):
    """PRE_VIEW of a 3D view: the settings and matrices this frame draws with."""
    if rv3d is None or region is None or not hasattr(rv3d, "view_rotation"):
        return
    key = rv3d.as_pointer()
    _drawn.pop(key, None)   # Last noted last: only views not drawn for longest are dropped.
    _drawn[key] = (settings(space, rv3d, region.width, region.height, scene), _flat(rv3d.perspective_matrix))
    if len(_drawn) > 64:   # Views closed long ago (each window's handful at most is live).
        del _drawn[next(iter(_drawn))]


def current(space, rv3d, width, height, scene):
    """Whether ``rv3d``'s matrices now are those a frame drew from exactly its settings now."""
    kept = _drawn.get(rv3d.as_pointer())
    return kept is not None and kept == (settings(space, rv3d, width, height, scene), _flat(rv3d.perspective_matrix))


def ready(key, area, found, scene, now):
    """Whether ``key``'s picture through the view ``found`` (``(region_3d, width, height)`` of ``area``, or None: no
    view) may be decided now: no view, its matrices current, or its frame awaited ``WAIT`` already (asked about at
    least every ``RESUMED``; a wait left off longer starts again). Otherwise its regions are tagged, so the frame
    that brings its matrices up to date comes, and the caller tries again."""
    if found is None or current(area.spaces.active, found[0], found[1], found[2], scene):
        _waiting.pop(key, None)
        return True
    began, asked = _waiting.get(key, (now, now))
    began = now if now - asked > RESUMED else began   # A wait left off never lets a later change through at once.
    if now - began >= WAIT:
        _waiting.pop(key, None)
        gave_up[0] += 1
        return True
    _waiting[key] = (began, now)
    for region in area.regions:
        if region.type == 'WINDOW':
            region.tag_redraw()
    return False


def forget():
    _drawn.clear()
    _waiting.clear()
