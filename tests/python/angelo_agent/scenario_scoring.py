# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""End-of-session scores: a side's ray-cast capture against the ground truth's, per fixed view.

Host-only (NumPy). ``scenario_capture`` writes one depth map and one hit-object
map per view; this module compares two captures of the same views:

* ``scene_iou``: intersection over union of the pixels where any geometry was
  hit (the silhouette against the sky).
* ``subject_iou``: the same for the scenario's subject objects only (for the
  bridge: everything named Bridge*, Railing*, Lamp* or Car*), so a correct
  terrain cannot hide a wrong bridge. Names follow ``scenario_checks.matches``,
  and a part counts with its parent (a wheel parented to ``Car``).
* ``depth_rel_median`` / ``depth_rel_mean``: relative depth error
  ``|d_side - d_truth| / d_truth`` over pixels both captures hit.
* ``depth_agreement``: the share of pixels hit in either capture where both hit
  and the depths agree within 5 %; one number for shape and placement.

Means over views weight every view equally. ``silhouette_png`` draws the
overlap (both: white, truth only: orange, side only: blue) for the report.
"""

import json
import struct
import zlib

import numpy

from scenario_checks import matches

DEPTH_TOLERANCE = 0.05
METRICS = ("scene_iou", "subject_iou", "depth_agreement", "depth_rel_median", "depth_rel_mean")


def load(path):
    """``{"names", "parents", "resolution", "views": {view: {"depth", "ids"}}}`` from a capture archive."""
    with numpy.load(path) as archive:
        meta = json.loads(bytes(archive["meta"]).decode())
        views = {}
        for key in archive.files:
            if key.endswith(".depth"):
                view = key[:-len(".depth")]
                views[view] = {"depth": archive[key].astype(numpy.float64), "ids": archive[view + ".ids"]}
    return dict(meta, views=views)


def subject_ids(capture, subject):
    """Indices of hit-object names that belong to the subject (by name, or by their parent's name)."""
    parents = capture.get("parents") or {}
    chosen = []
    for index, name in enumerate(capture["names"]):
        candidates = [name] + ([parents[name]] if parents.get(name) else [])
        if any(matches(candidate, target) for candidate in candidates for target in subject):
            chosen.append(index)
    return numpy.asarray(chosen, dtype=numpy.int64)


def _iou(a, b):
    union = numpy.count_nonzero(a | b)
    return 1.0 if union == 0 else numpy.count_nonzero(a & b) / union


def score_view(truth, side, truth_subject, side_subject):
    truth_hit, side_hit = numpy.isfinite(truth["depth"]), numpy.isfinite(side["depth"])
    both = truth_hit & side_hit
    output = {"scene_iou": _iou(truth_hit, side_hit),
              "subject_iou": _iou(numpy.isin(truth["ids"], truth_subject), numpy.isin(side["ids"], side_subject)),
              "truth_coverage": float(numpy.count_nonzero(truth_hit)) / truth_hit.size,
              "side_coverage": float(numpy.count_nonzero(side_hit)) / side_hit.size}
    if numpy.count_nonzero(both):
        relative = numpy.abs(side["depth"][both] - truth["depth"][both]) / numpy.maximum(truth["depth"][both], 1e-6)
        output.update(depth_rel_median=float(numpy.median(relative)), depth_rel_mean=float(numpy.mean(relative)))
        agree = numpy.count_nonzero(relative < DEPTH_TOLERANCE)
        output["depth_agreement"] = agree / max(1, numpy.count_nonzero(truth_hit | side_hit))
    else:
        output.update(depth_rel_median=None, depth_rel_mean=None, depth_agreement=0.0)
    return {key: (round(value, 4) if isinstance(value, float) else value) for key, value in output.items()}


def score(truth, side, subject, views=None):
    """Per-view scores and their means; ``truth``/``side`` are loaded captures of the same views."""
    if truth["resolution"] != side["resolution"]:
        raise ValueError("Captures differ in resolution: %s vs %s" % (truth["resolution"], side["resolution"]))
    truth_subject, side_subject = subject_ids(truth, subject), subject_ids(side, subject)
    names = [view for view in (views or truth["views"]) if view in side["views"]]
    per_view = {view: score_view(truth["views"][view], side["views"][view], truth_subject, side_subject)
                for view in names}
    means = {}
    for metric in METRICS:
        values = [entry[metric] for entry in per_view.values() if entry.get(metric) is not None]
        means[metric] = round(sum(values) / len(values), 4) if values else None
    return {"views": per_view, "mean": means, "depth_tolerance": DEPTH_TOLERANCE}


def silhouette_png(path, truth_view, side_view):
    """Overlap image of two silhouettes (both white, truth only orange, side only blue, neither dark)."""
    truth_hit, side_hit = numpy.isfinite(truth_view["depth"]), numpy.isfinite(side_view["depth"])
    height, width = truth_hit.shape
    image = numpy.zeros((height, width, 3), dtype=numpy.uint8)
    image[:] = (24, 24, 28)
    image[truth_hit & side_hit] = (235, 235, 235)
    image[truth_hit & ~side_hit] = (240, 140, 40)
    image[~truth_hit & side_hit] = (60, 140, 240)
    write_png(path, image)


def write_png(path, image):
    """A minimal RGB PNG writer (no imaging dependency on the host)."""
    height, width, _ = image.shape
    raw = b"".join(b"\0" + image[row].tobytes() for row in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
