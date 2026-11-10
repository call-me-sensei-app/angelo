# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The host's measurement of a held goal (doc/angelo/goal_mode.rst, "Checks"). Main thread, read-only.

``REFERENCE``: the outline comparison ``angelo_view`` makes, on a lighter path. The free camera is
``view_free_camera.camera`` at the reference's aspect, the reference's pixels ``view_compare.tile`` (decoded once per
goal, camera size and file: ``_tiles``), the targets' silhouette ``view_silhouette.geometry`` and ``draw``, and the
numbers ``view_compare.measure``. No PNG is written, no viewport is drawn, nothing enters the file or the Undo
history. Targets are a rule resolved now (``VISIBLE``, ``COLLECTION:<name>``), never handles: a build grows and
handles die with Undo. ``VISIBLE`` keeps only objects whose world bounds reach into the camera's view, so off-camera
geometry never counts toward the silhouette's triangle limit; each object drawn is named with the share of the
picture its projected bounds cover, and one that covers most of it (a floor, a backdrop, a reference on a plane) is
noted: it would inflate the overlap.

``SCENE``: ``angelo_scene_verify``'s closed checks, each object a check names bound to its current revision (an edit
changes revisions; the goal's checks name the objects, not a moment). An object gone is an unmeasured round.

Anything that cannot be measured returns ``{"measured": False, "reason": ...}``; ``goal_loop`` counts it as an
unmeasured round. No bpy at import.
"""

from . import goal_state as G

RESOLUTION = 512          # Pixels on the longer side: the view tool's default looks, enough for a 0.5-point step.
MAX_OBJECTS = 2000        # Objects scanned for VISIBLE; more is stated.
MAX_NAMED = 32            # Objects named in a result; the rest are counted.
FILLS_FRAME = 0.8         # A drawn object whose projected bounds cover this share of the picture is noted.
_tiles = {}               # (goal id, reference id, width, height, file bytes) -> (rows, alpha): decoded once.


def measure(controller, goal, window):
    if goal["check"] == G.SCENE:
        return _scene(goal, window)
    return _reference(controller, goal, window)


# REFERENCE ---------------------------------------------------------------------------------------------------------

def _projected_share(obj, camera):
    """The share of the picture the object's projected world bounds cover (0-1), or None when behind the camera."""
    import numpy
    corners = numpy.array([list(obj.matrix_world @ _vector(corner)) + [1.0] for corner in obj.bound_box])
    clip = corners @ (numpy.array(camera["projection_matrix"]) @ numpy.array(camera["view_matrix"])).T
    front = clip[:, 3] > 1e-9
    if not front.any():
        return None
    ndc = clip[front, :2] / clip[front, 3:4]
    if not front.all():   # Straddling the camera: its projection is unbounded; count it as reaching the frame.
        return 1.0
    low, high = numpy.clip(ndc.min(axis=0), -1, 1), numpy.clip(ndc.max(axis=0), -1, 1)
    if (high <= low).any():
        return 0.0
    return float((high[0] - low[0]) * (high[1] - low[1]) / 4.0)


def _vector(values):
    from mathutils import Vector
    return Vector(values)


def _targets(window, rule, camera):
    """``(objects, named, notes)`` for the targets rule at this camera."""
    from .view_silhouette import MESHLIKE
    layer = window.view_layer
    notes = []
    if rule.startswith("COLLECTION:"):
        import bpy
        name = rule[len("COLLECTION:"):]
        collection = bpy.data.collections.get(name)
        if collection is None:
            raise LookupError("the collection %s is not in this file" % name)
        candidates = list(collection.all_objects)
    else:
        candidates = list(window.scene.objects)
    if len(candidates) > MAX_OBJECTS:
        notes.append({"code": "objects_truncated", "detail": "Only the first %d objects were considered" % MAX_OBJECTS})
        candidates = candidates[:MAX_OBJECTS]
    objects, named = [], []
    for obj in candidates:
        if obj.type not in MESHLIKE and obj.type != "CURVES" or not obj.visible_get(view_layer=layer):
            continue
        share = _projected_share(obj, camera)
        if share is None or share <= 0.0:
            continue   # Out of the camera's view: never drawn, never counted toward the silhouette's limits.
        objects.append(obj)
        if len(named) < MAX_NAMED:
            named.append({"name": obj.name[:63], "share": round(share, 3)})
        if share >= FILLS_FRAME:
            notes.append({"code": "fills_frame", "detail": "%s covers most of the picture and inflates the overlap; "
                          "COLLECTION:<name> measures chosen objects" % obj.name[:63]})
    return objects, named, notes


def _tile(goal, pair, size):
    key = (goal["id"], pair["id"], size[0], size[1], pair["bytes"])
    if key not in _tiles:
        from .view_compare import tile
        rows, _share, alpha = tile(pair, size)
        if len(_tiles) >= 8:   # Bounded: a few goals' references at one size each.
            _tiles.pop(next(iter(_tiles)))
        _tiles[key] = (rows, alpha)
    return _tiles[key]


def _reference(controller, goal, window):
    from . import scene_queries, view_compare, view_free_camera, view_silhouette
    from .view_framing import output_size
    spec = goal["measure"]
    try:
        pair = view_compare.reference(controller.references, spec["reference"], "measure.reference")
        camera, facts = view_free_camera.camera(window, spec["camera"], view_free_camera.scene_bounds(window),
                                                scene_queries.document_epoch(), RESOLUTION, pair["aspect"])
        width, height = output_size(RESOLUTION, pair["aspect"])
        objects, named, notes = _targets(window, spec["targets"], camera)
        if not objects:
            return {"measured": False, "reason": "nothing the targets rule names is in the camera's view"}
        rows, alpha = _tile(goal, pair, (width, height))
        items, drawn_facts = view_silhouette.geometry(window, objects)
        drawn = len(objects) - int(drawn_facts.get("not_drawn") or 0)   # In view, yet nothing of it drawn: not counted.
        if drawn <= 0:
            return {"measured": False,
                    "reason": "nothing the targets rule names draws any geometry in the camera's view"}
        mask, depth, outline = view_silhouette.draw(items, camera, width, height)
        view_compare.measure(rows, alpha, mask, outline, depth, camera)
    except (LookupError, RuntimeError, ValueError) as error:   # A refusal or bound names what it could not do.
        return {"measured": False, "reason": str(error)[:200]}
    if not outline.get("compared"):
        return {"measured": False, "reason": outline.get("reason") or "the outlines could not be compared"}
    if facts.get("nothing_in_front"):
        notes.append({"code": "nothing_in_front", "detail": "Nothing visible is in front of the camera"})
    keep = ("iou", "mean_px", "p95_px", "max_px", "mean_world", "p95_world", "max_world", "centre_offset_px",
            "size_ratio", "measured_every_px", "reference", "gaps")
    result = {key: outline[key] for key in keep if key in outline}
    result.update(measured=True, drawn=drawn, objects=named, notes=notes,
                  size=[width, height])
    if len(objects) > len(named):
        result["objects_not_named"] = len(objects) - len(named)
    return result


# SCENE -------------------------------------------------------------------------------------------------------------

def _scene(goal, window):
    from . import scene_validation
    from .refusal_codes import RefusalError
    from .scene_queries import SceneQueryError, object_revision, resolve_object
    from .scene_settings import scene_handle, settings_revision
    from .validation_spec import check_references
    checks = goal["measure"]["checks"]
    read_set = []
    try:
        for handle in sorted({ref for check in checks for ref in check_references(check)}):
            if handle == scene_handle(window.scene):
                read_set.append({"handle": handle, "revision": settings_revision(window.scene)})
            else:
                read_set.append({"handle": handle, "revision": object_revision(window, resolve_object(window, handle))})
        result = scene_validation.execute(window, "angelo_scene_verify", {"read_set": read_set, "checks": checks})
    except (RefusalError, SceneQueryError, ValueError) as error:
        return {"measured": False, "reason": ("an object a check names: %s" % error)[:200]}
    statuses = result.get("checks") or []
    if statuses and all(status.get("status") == "inconclusive" for status in statuses):
        return {"measured": False, "reason": (statuses[0].get("reason") or "every check was inconclusive")[:200]}
    failing = [{key: status[key] for key in ("index", "kind", "target", "status", "reason", "measured", "expected")
                if key in status} for status in statuses if not status.get("passed")][:8]
    return {"measured": True, "passed_count": sum(1 for status in statuses if status.get("passed")),
            "total": len(statuses), "all_passed": bool(result.get("passed")), "failing": failing}
