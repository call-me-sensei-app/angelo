# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a library overlay's add and drop do: fetch, then import into the view's scene (``library_overlay``'s actions).

``add`` resolves the target at once (the surface or object under the drop point, or the view's centre, and the
selected objects for a texture set), starts the download (``library_host.fetch``) and shows its progress on the
pill a few times a second; when the files are checked, ``library_import.import_item`` runs in the same window and
view, selects what it added and the pill says so, or shows the importer's sentence when it failed.
"""

import time

import bpy

from . import live_windows
from .ui_text import iface_


def _overlay():
    from . import library_overlay
    return library_overlay


def _notice(state, text):
    state["notice"] = (text, time.monotonic())
    state.pop("progress", None)


def view_point(context, region, rv3d, x, y):
    """``(location, object)`` under region pixel (x, y): the surface hit, else the ground plane, else the cursor."""
    from bpy_extras import view3d_utils
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    hit, location, _normal, _index, obj, _matrix = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin,
                                                                            direction)
    if hit:
        return location, getattr(obj, "original", obj)
    if abs(direction.z) > 1e-6 and -origin.z / direction.z > 0:
        return origin + direction * (-origin.z / direction.z), None
    return context.scene.cursor.location.copy(), None


def add(window, area, region, item, at=None):
    """Fetch then import ``item`` into ``area``'s scene: at region pixel ``at`` (a drop) or the view's centre."""
    from . import library_host
    state = _overlay().state_for(area)
    resolution = state["resolution"].get(item["id"])
    space, rv3d = area.spaces.active, region.data
    x, y = at if at is not None else (region.width / 2, region.height / 2)
    with bpy.context.temp_override(window=window, area=area, region=region):
        point, target = view_point(bpy.context, region, rv3d, x, y)
        selected = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if item["kind"] == "texture":
        targets = [target] if at is not None and target is not None and target.type == 'MESH' else selected
        if not targets:
            _notice(state, iface_("Drop a material on an object, or select objects first"))
            area.tag_redraw()
            return False
    else:
        targets = []
    names = [o.name for o in targets]
    window_pointer, area_pointer = window.as_pointer(), area.as_pointer()
    state["progress"] = (0.02, iface_("Getting “{title}”", title=item["title"]))
    progress = {"fraction": 0.02}

    def moved(done, total):
        progress["fraction"] = done / total if total else 1.0

    def finished(facts, error):
        live = _live(window_pointer, area_pointer)
        target_state = _overlay()._views.get(area_pointer, state)
        target_state.pop("progress", None)
        if error is not None:
            message, _code = library_host.describe(error)
            _record("failed", item["id"], message)
            _notice(target_state, message)
            _overlay().redraw_all()
            return
        if live is None:
            return
        target_window, target_area, target_region = live
        objects = [bpy.data.objects.get(n) for n in names]
        try:
            with bpy.context.temp_override(window=target_window, area=target_area, region=target_region):
                from . import library_import
                result = library_import.import_item(bpy.context, item, facts, point=point,
                                                    targets=[o for o in objects if o is not None],
                                                    set_view=target_area.spaces.active)
                _select(bpy.context, result)
        except Exception as failure:   # The importer's own sentence: what failed and whether anything was kept.
            _record("failed", item["id"], str(failure))
            _notice(target_state, str(failure)[:160])
            _overlay().redraw_all()
            return
        _record("added", item["id"], result)
        _notice(target_state, iface_("Added “{title}”", title=item["title"]))
        _overlay().redraw_all()

    library_host.library().fetch(item, resolution, finished, moved)
    _watch_progress(area_pointer, progress)
    area.tag_redraw()
    return True


def _record(kind, identity, detail):
    _overlay().events.append((kind, identity, detail))
    del _overlay().events[:-20]


def _select(context, result):
    names = [row["name"] for row in result.get("objects") or ()]
    objects = [bpy.data.objects.get(n) for n in names]
    objects = [o for o in objects if o is not None]
    if not objects:
        return
    for obj in context.view_layer.objects:
        if obj.select_get():
            obj.select_set(False)
    for obj in objects:
        try:
            obj.select_set(True)
        except RuntimeError:
            pass
    context.view_layer.objects.active = objects[0]


def _live(window_pointer, area_pointer):
    for window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.as_pointer() == area_pointer and window.as_pointer() == window_pointer:
            region = _overlay().main_region(area)
            return (window, area, region) if region is not None else None
    return None


def _watch_progress(area_pointer, progress):
    """Redraw the pill's progress a few times a second while its download runs (never every frame)."""
    def tick():
        state = _overlay()._views.get(area_pointer)
        if state is None or "progress" not in state:
            return None
        state["progress"] = (progress["fraction"], state["progress"][1])
        for _window, area in live_windows.window_areas(bpy.context.window_manager):
            if area.as_pointer() == area_pointer:
                area.tag_redraw()
        return 0.15
    bpy.app.timers.register(tick, first_interval=0.15)
