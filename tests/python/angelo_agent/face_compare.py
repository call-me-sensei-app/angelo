# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A side's face and hair readings against the ground truth's (``face_measure``): errors in mm and reference pixels.

NumPy only. Every error is a distance in the view it was read in, in
millimetres and in pixels of the reference picture of that view (1,280
pixels across), so "within 2 mm" and "within 5 pixels" can be read off the
same number. A reading the side does not have (no eye object, no jump from
chin to neck, no mouth line) is ``found: false`` with an infinite error,
never a guess.

* ``landmarks``: profile landmarks the truth has well defined (inside its
  window and standing out by ``MIN_PROMINENCE``).
* ``eyes``: each eye centre, in 3D and in the front and side pictures.
* ``jaw``: the chin bottom and the jaw line, column by column.
* ``mouth``: both corners and the height of its middle.
* ``outline``: the distances between the two silhouettes' edge points
  (``face_measure.contour``), each to the other's nearest, as mean, 95th
  percentile and maximum, and the silhouettes' intersection over union.
* ``relief``: per region of the face, how far each head's visible surface
  lies from the other's surface, both ways (``face_truth.read``).
* ``hair``: per view, the hair masks' intersection over union and the
  outline of head and hair together; the hairline over the forehead, column
  by column; the length at the back and at the sides.
"""

import math

import numpy

import face_measure

MIN_PROMINENCE = face_measure.MIN_PROMINENCE
INF = float("inf")


def _mm(value):
    return round(value * 1000.0, 3) if math.isfinite(value) else INF


def _px(value, pixel):
    return round(value / pixel, 2) if math.isfinite(value) and pixel else INF


def defined_landmarks(truth_landmarks):
    """Names of the truth's landmarks that are well defined (inside their window, standing out)."""
    return sorted(name for name, found in truth_landmarks.items()
                  if found and not found["at_edge"] and found["prominence"] >= MIN_PROMINENCE)


def landmarks(side, truth, names, pixel):
    found = {}
    for name in names:
        mine, theirs = (side or {}).get(name), truth[name]
        if not mine:
            found[name] = {"found": False, "error_mm": INF, "error_px": INF}
            continue
        du, dv = mine["u"] - theirs["u"], mine["v"] - theirs["v"]
        error = math.hypot(du, dv)
        found[name] = {"found": True, "error_mm": _mm(error), "error_px": _px(error, pixel), "forward_mm": _mm(du),
                       "up_mm": _mm(dv), "at_edge": mine["at_edge"]}
    return found


def eyes(side, truth, front_pixel, side_pixel):
    found = {}
    for key, centre in truth.items():
        mine = side.get(key)
        if mine is None:
            found[key] = {"found": False, "error_mm": INF, "front_px": INF, "side_px": INF}
            continue
        delta = numpy.asarray(mine, dtype=numpy.float64) - numpy.asarray(centre, dtype=numpy.float64)
        found[key] = {"found": True, "error_mm": _mm(float(numpy.linalg.norm(delta))),
                      "front_px": _px(math.hypot(delta[0], delta[2]), front_pixel),
                      "side_px": _px(math.hypot(delta[1], delta[2]), side_pixel),
                      "offset_mm": [_mm(float(value)) for value in delta]}
    return found


def jaw(side, truth, pixel):
    menton = INF if side.get("menton") is None or truth.get("menton") is None else \
        abs(side["menton"] - truth["menton"])
    theirs = numpy.asarray(truth["jaw"]["v"], dtype=numpy.float64)
    mine = numpy.asarray(side["jaw"]["v"], dtype=numpy.float64) if side.get("jaw") else numpy.full_like(theirs, numpy.nan)
    defined = numpy.isfinite(theirs)
    both = defined & numpy.isfinite(mine)
    errors = numpy.abs(mine[both] - theirs[both])
    return {"menton": {"found": math.isfinite(menton), "error_mm": _mm(menton), "error_px": _px(menton, pixel)},
            "line": {"columns": int(defined.sum()), "found": int(both.sum()),
                     "mean_mm": _mm(float(errors.mean())) if len(errors) else INF,
                     "max_mm": _mm(float(errors.max())) if len(errors) else INF}}


def mouth(side, truth, pixel):
    if truth is None:
        return None
    found = {}
    for key in ("left", "right", "middle"):
        if side is None:
            found[key] = {"found": False, "error_mm": INF, "error_px": INF}
            continue
        error = math.dist(side[key], truth[key])
        found[key] = {"found": True, "error_mm": _mm(error), "error_px": _px(error, pixel),
                      "outward_mm": _mm(abs(side[key][0]) - abs(truth[key][0]))}
    return found


def _nearest(a, b, chunk=2048):
    found = numpy.empty(len(a))
    for start in range(0, len(a), chunk):
        part = a[start:start + chunk]
        found[start:start + chunk] = numpy.sqrt(((part[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
    return found


def _sampled(points, limit=6000):
    return points[::max(1, len(points) // limit)]


def _chamfer(mine, theirs):
    return numpy.concatenate((_nearest(_sampled(mine), theirs), _nearest(_sampled(theirs), mine)))


def outline(side, truth, pixel):
    """Distances between the two silhouettes' edge points (each to the other's nearest), and their IoU."""
    if side is None or not len(side["contour"]):
        return {"found": False, "mean_mm": INF, "p95_mm": INF, "max_mm": INF, "iou": 0.0}
    distances = _chamfer(side["contour"], truth["contour"])
    p95 = float(numpy.percentile(distances, 95))
    return {"found": True, "mean_mm": _mm(float(distances.mean())), "p95_mm": _mm(p95), "p95_px": _px(p95, pixel),
            "max_mm": _mm(float(distances.max())), "iou": round(iou(side, truth, "depth"), 4)}


def _canvas(datasets, key):
    """Masks of several lattice rasters (the same pitch) placed on one common lattice."""
    step = float(datasets[0]["us"][1] - datasets[0]["us"][0])
    ks = [(int(round(data["us"][0] / step)), int(round(data["us"][-1] / step))) for data in datasets]
    ls = [(int(round(data["vs"][-1] / step)), int(round(data["vs"][0] / step))) for data in datasets]
    k0, k1 = min(k[0] for k in ks), max(k[1] for k in ks)
    l0, l1 = min(l[0] for l in ls), max(l[1] for l in ls)
    masks = []
    for data, (a, _), (_, top) in zip(datasets, ks, ls):
        mask = numpy.zeros((l1 - l0 + 1, k1 - k0 + 1), dtype=bool)
        values = data[key]
        values = numpy.isfinite(values) if values.dtype != bool else values
        row, column = l1 - top, a - k0
        mask[row:row + values.shape[0], column:column + values.shape[1]] = values
        masks.append(mask)
    return masks


def iou(first, second, key):
    a, b = _canvas([first, second], key)
    union = numpy.count_nonzero(a | b)
    return 1.0 if union == 0 else numpy.count_nonzero(a & b) / union


def relief(maps, regions):
    """Per region ``{name: [u0, u1, v0, v1]}`` of the front view: how far each head's visible surface lies from
    the other's (both ways: the side's points from the truth's surface, the truth's from the side's), as mean,
    90th percentile and maximum in mm."""
    found = {}
    for name, (u0, u1, v0, v1) in regions.items():
        parts = []
        for key in ("side_to_truth", "truth_to_side"):
            data = maps.get(key) if maps else None
            if data is None:
                continue
            inside = (data["u"] >= u0) & (data["u"] <= u1) & (data["v"] >= v0) & (data["v"] <= v1)
            parts.append(numpy.asarray(data["d"])[inside])
        distances = numpy.concatenate(parts) if parts else numpy.zeros(0)
        if not len(distances):
            found[name] = {"p90_mm": INF, "mean_mm": INF, "max_mm": INF, "cells": 0}
            continue
        found[name] = {"p90_mm": _mm(float(numpy.percentile(distances, 90))),
                       "mean_mm": _mm(float(distances.mean())), "max_mm": _mm(float(distances.max())),
                       "cells": int(len(distances))}
    return found


def hair(side, truth, pixels):
    """Hair coverage, outline, hairline and length of a side against the truth (``face_truth.hair_truth``)."""
    found = {"views": {}}
    for view, theirs in truth["views"].items():
        mine = side["views"].get(view)
        if mine is None:
            found["views"][view] = {"hair_iou": 0.0, "outline_p95_mm": INF}
            continue
        mine_edges, their_edges = face_measure.hair_contour(mine), face_measure.hair_contour(theirs)
        if not len(mine_edges):
            found["views"][view] = {"hair_iou": 0.0, "outline_p95_mm": INF}
            continue
        distances = _chamfer(mine_edges, their_edges)
        p95 = float(numpy.percentile(distances, 95))
        found["views"][view] = {"hair_iou": round(iou(mine, theirs, "hair"), 4),
                                "outline_p95_mm": _mm(p95), "outline_p95_px": _px(p95, pixels.get(view)),
                                "outline_mean_mm": _mm(float(distances.mean()))}
    theirs, mine = numpy.asarray(truth["hairline"], dtype=float), numpy.asarray(side["hairline"], dtype=float)
    defined = numpy.isfinite(theirs)
    errors = numpy.where(numpy.isfinite(mine), numpy.abs(mine - theirs), numpy.inf)[defined]
    found["hairline"] = {"columns": int(defined.sum()), "found": int(numpy.isfinite(errors).sum()),
                         "mean_mm": _mm(float(numpy.mean(errors))) if len(errors) else INF,
                         "max_mm": _mm(float(numpy.max(errors))) if len(errors) else INF}
    found["length"] = {key: {"error_mm": _mm(abs(side["length"][key] - value))
                             if math.isfinite(side["length"].get(key, math.nan)) else INF}
                       for key, value in truth["length"].items() if math.isfinite(value)}
    return found
