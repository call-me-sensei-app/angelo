# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` free cameras on Blender's main thread: the scene's bounds, the camera, its plan and its notes.

A views item {location, look_at, lens | ortho_scale} (view_spec) is drawn like
an axis view, from matrices ``view_framing.free_camera`` computes, so no camera
object is made and nothing in the scene is written. What needs Blender is
here: the visible scene's bounding boxes, which set the clip planes
(``view_framing.free_clip``), and the render aspect. An item with
``reference`` is a reference pair: its view is drawn at its image's aspect
(``view_compare``). A REUSE of a free camera's view_id keeps its pose, lens
and aspect and clips the scene as it is now, so an object added nearer than
the old near plane is still drawn. ``view_capture`` plans the other views and
draws them all; ``view_motion`` holds a free camera over a strip's frames.
"""

from . import view_cache
from .view_framing import SENSOR_WIDTH_MM, box_depths, free_camera, look_at_basis, output_size, render_aspect
from .view_spec import CAMERA, framing_kind


def scene_bounds(window):
    """``(corners, complete)`` of every visible drawn bounding box, for a free camera's clip planes (view_framing).

    ``corners`` is None for a scene with nothing visible; ``complete`` is False when the scan was truncated.
    """
    from .view_capture import _framing_points
    try:
        points, truncated = _framing_points(window, [])
    except RuntimeError:
        return None, True
    return points, not truncated


def camera(window, item, bounds, epoch, resolution, aspect=None):
    """``(camera, facts)`` of a free camera item at ``aspect`` (default: the render aspect), clipped to ``bounds``.

    ``facts`` say what its notes need: nothing visible in front of it, clip planes from a truncated scan, or a
    visible box nearer than its clip_start.
    """
    width, height = output_size(resolution, aspect or render_aspect(window.scene.render))
    corners, complete = bounds
    right, up, forward = look_at_basis(item["location"], item["look_at"])
    perspective = "ortho_scale" not in item
    major = SENSOR_WIDTH_MM / 2.0 / item["lens"] if perspective else item["ortho_scale"] / 2.0
    aspect = width / height
    half = (major, major / aspect) if aspect >= 1 else (major * aspect, major)
    nearest_front, farthest, straddles = box_depths(corners or (), item["location"], forward,
                                                    (right, up, half[0], half[1], perspective))
    result = free_camera(item, width / height, nearest_front, farthest, straddles, complete)
    result.update(epoch=epoch, scene_uid=window.scene.session_uid, source=CAMERA)
    start = result["details"]["clip_start"]
    return result, {"nothing_in_front": bool(corners) and farthest is not None and farthest <= 0,
                    "clip_from_partial_bounds": corners is not None and not complete,
                    "nearer_than_clip_start": (round(nearest_front, 6) if nearest_front is not None
                                               and nearest_front < start else None)}


def plan(window, references, index, item, shared, epoch, resolution):
    """``(camera, facts)`` of views item ``index`` when it is a free camera or a REUSE of one, else ``(None, {})``.

    ``shared`` holds the scene's bounds for every free camera of one call, read once.
    """
    from . import view_compare
    kind = framing_kind(item)
    stored = None
    if kind == "REUSE":
        stored = view_cache.cameras.resolve(item, epoch=epoch, scene_uid=window.scene.session_uid)
        if "free" not in stored:
            return None, {}
    elif kind != CAMERA:
        return None, {}
    if "bounds" not in shared:
        shared["bounds"] = scene_bounds(window)
    if stored is not None:  # The same pose, lens and aspect; the clip planes of the scene now.
        result, facts = camera(window, stored["free"], shared["bounds"], epoch, resolution, stored["aspect"])
        result["reused_from"] = item
        return result, facts
    pair = (view_compare.reference(references, item["reference"], "views[%d].reference" % index)
            if "reference" in item else None)
    result, facts = camera(window, item, shared["bounds"], epoch, resolution, pair and pair["aspect"])
    return result, dict(facts, pair=pair)


def label(plan):
    """The picture's name for a free camera's plan (None for another view)."""
    if framing_kind(plan["item"]) != CAMERA:
        return None
    return "Reference pair" if plan.get("pair") else "Camera view"


def notes(plans):
    """Uncertainty about free cameras' pictures: nothing in front, near geometry cut, clip planes from a part."""
    found = []
    for index, plan in enumerate(plans):
        if plan.get("nothing_in_front"):
            found.append({"code": "camera_faces_no_geometry", "detail": (
                "views[%d]: every visible object's bounds are behind this camera, so its picture shows only the "
                "background" % index)})
        if plan.get("nearer_than_clip_start") is not None:
            found.append({"code": "camera_clip_cuts_near_geometry", "detail": (
                "views[%d]: a visible object's bounds come within %g of this camera, nearer than its clip_start "
                "(set so the farthest geometry stays distinct); what is nearer is not drawn"
                % (index, plan["nearer_than_clip_start"]))})
        if plan.get("clip_from_partial_bounds"):
            found.append({"code": "camera_clip_bounds_truncated", "detail": (
                "views[%d]: the scene's bounds were read only in part (instance or time budget), so the clip "
                "planes are estimates; geometry nearer than clip_start or past clip_end is not drawn" % index)})
    return found
