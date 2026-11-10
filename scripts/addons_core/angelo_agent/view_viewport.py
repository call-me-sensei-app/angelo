# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The artist's 3D Viewport a native view draws through (``view_capture``, ``view_motion``, ``web_compare``).

Which viewport draws (one whose temporary shading change has no side effects), the state of it that is not
normalized (local view, object-type filters, camera view: part of a view's cache key and notes), and
``DisplayOverrides``, which normalizes its display for one draw and restores it in reverse order.
"""

from collections import namedtuple

from . import live_windows
from .refusal_codes import coded
from .view_looks import BEST_EFFORT, EEVEE_SHADINGS

_type_filters = None


class DisplayOverrides:
    """Normalize one viewport's display, then restore it in reverse order.

    Each old value is read immediately before its own assignment, and equal
    values are not written (no redundant RNA update, such as the geometry
    re-tag that entering MATERIAL causes for objects in Sculpt Mode).
    """

    def __init__(self):
        self._undo = []
        self.unavailable = []

    def apply(self, owner, settings):
        for key, value in settings:
            old = getattr(owner, key)
            if not isinstance(old, (str, bool, int, float)):
                old = tuple(old)
            if old == value:
                continue
            try:
                setattr(owner, key, value)
            except TypeError:
                if key not in BEST_EFFORT:
                    raise
                self.unavailable.append(key)
                continue
            self._undo.append((owner, key, old))

    def restore(self):
        errors = []
        while self._undo:
            owner, key, old = self._undo.pop()
            try:
                setattr(owner, key, old)
            except Exception as error:  # Keep restoring the rest; report every failure.
                errors.append(f"{key}: {error}")
        if errors:
            raise RuntimeError("Could not restore the artist's viewport display: " + "; ".join(errors))


NO_VIEWPORT = "Keep a visible 3D Viewport in the bound Blender window"


def _view3d_areas(window):
    areas = [area for area in live_windows.areas(window) if area.type == "VIEW_3D"]
    if not areas:
        raise coded(RuntimeError(NO_VIEWPORT), "UNSUPPORTED")
    return areas


def window_region(area):
    """A 3D View area's drawing region, or the refusal that says no viewport is open (one that is closing has none)."""
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None:
        raise coded(RuntimeError(NO_VIEWPORT), "UNSUPPORTED")
    return region


def _draw_area(areas):
    """Prefer a viewport whose temporary shading change has no side effects.

    Leaving RENDERED restarts that viewport's render engine, local view and
    local collections filter the drawn objects, and camera view lets EEVEE use
    the camera's depth of field and render border.
    """
    def cost(area):
        space = area.spaces.active
        return (space.shading.type == "RENDERED", space.local_view is not None,
                bool(space.use_local_collections), space.region_3d.view_perspective == "CAMERA")
    return min(areas, key=cost)


def _object_type_filters(space):
    global _type_filters
    if _type_filters is None:
        _type_filters = tuple(sorted(p.identifier for p in space.bl_rna.properties
                                     if p.identifier.startswith("show_object_viewport_")))
    return tuple(bool(getattr(space, name)) for name in _type_filters)


ViewportState = namedtuple("ViewportState", "local_view local_collections type_filters camera_view rendered")


def _viewport_state(space, shading):
    """Drawing-viewport state that is not normalized: part of the key and notes."""
    state = ViewportState(space.local_view is not None, bool(space.use_local_collections),
                          _object_type_filters(space), space.region_3d.view_perspective == "CAMERA",
                          space.shading.type == "RENDERED")
    notes = []
    if state.local_view or state.local_collections:
        notes.append({"code": "viewport_object_filter",
                      "detail": "The only drawable 3D Viewport uses local view or local collections; hidden objects are omitted"})
    if not all(state.type_filters):
        notes.append({"code": "viewport_type_filter",
                      "detail": "The drawing viewport hides some object types; those objects are omitted"})
    if state.camera_view and shading in EEVEE_SHADINGS:
        notes.append({"code": "viewport_in_camera_view",
                      "detail": "EEVEE may apply the camera's depth of field or render border to this view"})
    return state, notes
