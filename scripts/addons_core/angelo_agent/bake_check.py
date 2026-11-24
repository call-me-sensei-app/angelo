# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How close a baked material looks to the material it was baked from: the views and the comparison. Pure (NumPy).

Contract: doc/angelo/long_jobs.rst, "Bake check". The target is drawn with its own material and with the baked one,
from ``views`` (three unlike directions framing its bounding sphere) under the same light, and ``compare`` reads the
two sets of pictures (linear RGBA floats, rows from the top, alpha the object's coverage). The facts say how far apart
they look, never whether that is good enough: the agent and the person judge that against the asset.

* ``delta_e_mean`` / ``delta_e_p95``: CIE76 colour difference in CIELAB (D65 white) over the pixels both pictures
  cover. About 2 is the smallest difference a person tells apart side by side.
* ``lightness_share``: how much of the difference is lightness (shading: roughness, normal, ambient occlusion) rather
  than the colour plane (base colour, emission).
* ``only_one_share``: of the pixels either picture covers, those only one covers (alpha baked wrong, a part lost).
* ``worst``: the tile with the largest mean difference, by view and as a box of fractions of the picture (x right,
  y up from the bottom left, as ``angelo_view`` regions are given).
"""

import math

import numpy as np

from .view_framing import SENSOR_WIDTH_MM

# Linear sRGB (Rec. 709 primaries) to CIE XYZ, D65 white (IEC 61966-2-1).
_RGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]])
_WHITE = _RGB_TO_XYZ.sum(axis=1)   # The white point the matrix maps (1, 1, 1) to.
_EPSILON, _KAPPA = 216.0 / 24389.0, 24389.0 / 27.0
COVERED = 0.5     # Alpha above which a pixel shows the object.
TILE = 16         # Pixels on a side of the tiles the worst region is chosen among.
DISTANCE = 3.0    # Camera distance in bounding-sphere radii.
# Front, three-quarter from above, and nearly straight down: unlike enough that one view's blind side is another's.
DIRECTIONS = ((0.0, -1.0, 0.25), (0.6, -0.6, 0.5), (0.0, -0.2, 1.0))


def lab(rgb):
    """CIELAB (L*, a*, b*) of linear sRGB colours ``rgb`` (..., 3)."""
    xyz = np.asarray(rgb, dtype=np.float64) @ _RGB_TO_XYZ.T / _WHITE
    f = np.where(xyz > _EPSILON, np.cbrt(np.maximum(xyz, 0.0)), (_KAPPA * xyz + 16.0) / 116.0)
    return np.stack([116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])],
                    axis=-1)


def views(center, radius):
    """``angelo_view`` free cameras (``location``, ``look_at``, ``lens``) framing the sphere at ``center``."""
    # The sphere's half angle seen from DISTANCE radii fills half the free cameras' sensor (view_framing).
    lens = (SENSOR_WIDTH_MM * 0.5) / math.tan(math.asin(1.0 / DISTANCE))
    out = []
    for direction in DIRECTIONS:
        unit = np.asarray(direction, dtype=np.float64)
        unit /= np.linalg.norm(unit)
        location = np.asarray(center, dtype=np.float64) - unit * DISTANCE * radius
        out.append({"location": [float(v) for v in location], "look_at": [float(v) for v in center],
                    "lens": round(lens, 3)})
    return out


def _round(value, digits=4):
    return round(float(value), digits)


def compare(originals, bakeds, tile=TILE):
    """The facts above for pictures of the original material and of the baked one, view by view."""
    if len(originals) != len(bakeds) or not originals:
        raise ValueError("compare needs one baked picture per original view")
    lightness = colour = total = 0.0
    values, only_one, either, worst = [], 0, 0, None
    for index, (original, baked) in enumerate(zip(originals, bakeds)):
        original, baked = np.asarray(original, dtype=np.float64), np.asarray(baked, dtype=np.float64)
        if original.shape != baked.shape:
            raise ValueError("view %d: the pictures are %s and %s" % (index, original.shape, baked.shape))
        a_in, b_in = original[..., 3] > COVERED, baked[..., 3] > COVERED
        both = a_in & b_in
        only_one += int(np.count_nonzero(a_in ^ b_in))
        either += int(np.count_nonzero(a_in | b_in))
        difference = lab(original[..., :3]) - lab(baked[..., :3])
        delta = np.where(both, np.linalg.norm(difference, axis=-1), 0.0)
        values.append(delta[both])
        lightness += float(np.abs(difference[..., 0])[both].sum())
        colour += float(np.hypot(difference[..., 1], difference[..., 2])[both].sum())
        height, width = delta.shape
        for row in range(0, height, tile):
            for column in range(0, width, tile):
                mask = both[row:row + tile, column:column + tile]
                if not mask.any():
                    continue
                mean = float(delta[row:row + tile, column:column + tile][mask].mean())
                if worst is None or mean > worst[0]:
                    bottom = height - min(row + tile, height)
                    worst = (mean, index, [column / width, bottom / height, min(column + tile, width) / width,
                                           (height - row) / height])
    covered = np.concatenate(values)
    if not either:
        return {"covered_pixels": 0}
    facts = {"covered_pixels": int(len(covered)), "only_one_share": _round(only_one / either, 6)}
    if len(covered):
        total = lightness + colour
        facts.update(delta_e_mean=_round(covered.mean()), delta_e_p95=_round(np.percentile(covered, 95)),
                     lightness_share=_round(lightness / total if total > 0.0 else 0.0),
                     worst={"view": worst[1], "box": [_round(v, 6) for v in worst[2]], "delta_e": _round(worst[0])})
    return facts
