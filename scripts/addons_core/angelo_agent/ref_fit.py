# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How well a model fits its reference board, view by view, as facts the agent reads instead of eyeballing renders.
NumPy only, never ``bpy``, no module state: safe to call many times from a worker thread (hair fitting does).

``score_views(board, silhouettes)`` takes, per view, the model's silhouette mask and its depth (or None) drawn at the
board's camera and size (``view_silhouette.draw``), and returns ``view_outline.compare``'s measures (IoU, outline
distance mean / 95th percentile / max, centre offset, size ratio) in pixels and millimetres, the places they are
farthest apart (``view_outline.gaps``, only with depth: each with its 3D point and in-plane offset) and a verdict.

Millimetres: at the outline's own depth when depth is given, else from an orthographic camera's scale; a
perspective view without depth has none (``no_scale``). A verdict needs a threshold (``iou_min``,
``p95_mm_max``); without one ``passed`` is None, never a pass. A board passes only when every view passes.
doc/angelo/reference_fit.rst is the contract.
"""

import numpy

from . import view_outline


def _mm_per_px(board, view_id, target, depth):
    camera = board.camera(view_id)
    if depth is not None:
        world = view_outline.outline_pixel_world(target, depth, camera)
        return None if world is None else world * board.unit_mm
    projection = numpy.asarray(camera["projection_matrix"], dtype=numpy.float64)
    if projection[3, 3] == 1.0:   # Orthographic: one pixel is the same size everywhere.
        return view_outline.pixel_size(camera, board.size(view_id)[0], 1.0) * board.unit_mm
    return None


def score_view(board, view_id, silhouette):
    """One view's facts (see ``score_views``)."""
    result = {"view_id": view_id, "compared": False, "iou": None, "mean_px": None, "p95_px": None, "max_px": None,
              "mean_mm": None, "p95_mm": None, "max_mm": None, "mm_per_px": None, "centre_offset_px": None,
              "centre_offset_mm": None, "size_ratio": None, "gaps": [], "passed": None, "reasons": []}
    reference = board.reference_outline(view_id)   # Normalized once per view, however often a fit scores.
    if silhouette is None:
        result["reasons"].append("not_compared:no_silhouette")
        return result
    if reference is None:
        result["reasons"].append("not_compared:no_picture")
        return result
    target, depth = silhouette
    target = numpy.asarray(target, dtype=bool)
    width, height = board.size(view_id)
    if target.shape != (height, width) or (depth is not None and numpy.shape(depth) != (height, width)):
        result["reasons"].append("not_compared:size_mismatch")
        return result
    outline = view_outline.normalized(target)
    if not reference.any():
        result["reasons"].append("not_compared:no_subject")
        return result
    if not outline.any():
        result["reasons"].append("not_compared:not_in_view")
        return result
    measured = view_outline.compare(reference, outline)
    result.update({key: measured[key] for key in ("iou", "mean_px", "p95_px", "max_px", "centre_offset_px",
                                                  "size_ratio")}, compared=True)
    if "measured_every_px" in measured:
        result["measured_every_px"] = measured["measured_every_px"]
    scale = _mm_per_px(board, view_id, target, None if depth is None else numpy.asarray(depth, dtype=numpy.float32))
    if scale is not None:
        result["mm_per_px"] = round(scale, 4)
        for key in ("mean", "p95", "max"):
            result[key + "_mm"] = round(measured[key + "_px"] * scale, 3)
        result["centre_offset_mm"] = [round(value * scale, 3) for value in measured["centre_offset_px"]]
    if depth is not None:
        result["gaps"] = view_outline.gaps(reference, outline, numpy.asarray(depth, dtype=numpy.float32),
                                           board.camera(view_id))
    limits = board.thresholds(view_id)
    judged = []
    if limits["iou_min"] is not None:
        judged.append(result["iou"] >= limits["iou_min"])
        if not judged[-1]:
            result["reasons"].append("iou_below")
    if limits["p95_mm_max"] is not None:
        if result["p95_mm"] is None:
            result["reasons"].append("no_scale")
        else:
            judged.append(result["p95_mm"] <= limits["p95_mm_max"])
            if not judged[-1]:
                result["reasons"].append("p95_over")
    if limits["iou_min"] is None and limits["p95_mm_max"] is None:
        result["reasons"].append("no_threshold")
    if False in judged:
        result["passed"] = False
    elif judged and "no_scale" not in result["reasons"]:
        result["passed"] = True
    return result


def score_views(board, silhouettes):
    """{"views": {view_id: facts}, "passed", "failed"} for every view of ``board``; ``silhouettes`` is
    {view_id: (mask, depth or None)}. A view with no silhouette is reported (``not_compared:no_silhouette``)."""
    views = {view_id: score_view(board, view_id, silhouettes.get(view_id)) for view_id in board.view_ids()}
    failed = [view_id for view_id, view in views.items() if view["passed"] is False]
    if failed:
        passed = False
    elif views and all(view["passed"] is True for view in views.values()):
        passed = True
    else:
        passed = None
    return {"views": views, "passed": passed, "failed": failed}
