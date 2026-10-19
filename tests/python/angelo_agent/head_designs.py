# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The heads of the face and hair one-shot tests, as ``head_sdf`` designs (NumPy only, no Blender).

* ``portrait``: an adult head of average proportions (adult anthropometric
  means: about 23 cm from crown to chin, 15 cm wide, 19.5 cm deep, eyes at
  half the head's height, 6.3 cm between the pupils);
* ``base``: the neutral start head of the portrait test, a smooth average
  head with every feature present but none of the person's shape;
* ``stylised``: a cartoon head (large cranium, large eyes, button nose,
  small jaw and round chin);
* ``anime``: an anime head (very large eyes set low, a small pointed nose,
  a narrow face tapering to a pointed chin).

Every design has the same frame: metres, Z up, the face towards -Y,
symmetric about x = 0, the eyes near z = 0 and the neck cut flat at
``NECK_CUT``. Each also lists its eyes (separate spheres or ellipsoids:
``head_kit`` makes them objects) and the box its mesh fits in.
"""

import head_sdf

NECK_CUT = -0.200


def _e(centre, radii, op="add", blend=0.0, mirror=False, rotation=None, part=None):
    step = {"kind": "ellipsoid", "centre": tuple(centre), "radii": tuple(radii), "op": op, "blend": blend,
            "mirror": mirror, "part": part}
    if rotation:
        step["rotation"] = tuple(rotation)
    return step


def _cone(a, b, ra, rb, op="add", blend=0.0, mirror=False, part=None):
    return {"kind": "round_cone", "a": tuple(a), "b": tuple(b), "ra": ra, "rb": rb, "op": op, "blend": blend,
            "mirror": mirror, "part": part}


def _s(centre, radius, op="add", blend=0.0, mirror=False, part=None):
    return {"kind": "sphere", "centre": tuple(centre), "radius": radius, "op": op, "blend": blend, "mirror": mirror,
            "part": part}


def _surface_y(steps, x, z, front=-0.13, back=0.0):
    """Where a ray along +Y at (x, z) first meets the surface the steps make (bisection)."""
    lo, hi = front, back
    for _ in range(40):
        mid = (lo + hi) * 0.5
        inside = head_sdf.distance({"steps": steps}, [(x, mid, z)])[0] < 0
        lo, hi = (lo, mid) if inside else (mid, hi)
    return (lo + hi) * 0.5


def with_mouth_line(steps, height, half_width, radius, corner_radius, lift=0.0012):
    """The line where the lips meet: a groove cut along the lips' own surface at ``height`` (rising ``lift`` at
    the corners), ``radius`` deep, from the middle to each corner and round at the corners (mirrored). Placed on
    the surface the other steps make, so it is a groove whatever the lips' shape, and it ends where it ends at
    any mesh resolution (a cut that tapered to nothing would not)."""
    xs = [0.0, 0.35 * half_width, 0.7 * half_width, half_width]
    points = []
    for x in xs:
        z = height + lift * (x / half_width) ** 2
        points.append((x, _surface_y(steps, x, z) + 0.0002, z))
    radii = [radius + (corner_radius - radius) * (x / half_width) for x in xs]
    groove = [_cone(a, b, ra, rb, op="cut", blend=0.0010, mirror=True, part="mouth")
              for a, b, ra, rb in zip(points[:-1], points[1:], radii[:-1], radii[1:])]
    return steps + groove


def _neck_cut(blend=0.004):
    return {"kind": "halfspace", "normal": (0.0, 0.0, 1.0), "offset": NECK_CUT, "op": "keep", "blend": blend}


def _eye_sockets(eye, radius, aperture, blend=0.003):
    """Lids around an eye at ``eye`` (the one at +x; mirrored), an opening onto it and a socket for it."""
    x, y, z = eye
    front = y - radius
    steps = [_e((x, front + 0.0050, z + aperture[2] * 0.70), (aperture[0] * 1.10, 0.0060, aperture[2] * 1.0),
                blend=0.005, mirror=True),
             _e((x, front + 0.0055, z - aperture[2] * 0.80), (aperture[0] * 1.0, 0.0052, aperture[2] * 0.9),
                blend=0.005, mirror=True)]
    steps += [_e((x, front - 0.003, z), aperture, op="cut", blend=blend, mirror=True),
              _s((x, y, z), radius + 0.0009, op="cut", blend=0.001, mirror=True)]
    return steps


def portrait():
    eye, eye_r = (0.0315, -0.060, 0.0), 0.0122
    steps = [
        _e((0.0, 0.012, 0.024), (0.0745, 0.0965, 0.091)),                                   # cranium
        _e((0.0, -0.028, -0.036), (0.058, 0.056, 0.060), blend=0.030),                      # midface
        _e((0.0, -0.014, -0.074), (0.0560, 0.062, 0.043), blend=0.026, part="mandible body"),
        _e((0.0500, 0.018, -0.052), (0.0120, 0.022, 0.038), blend=0.016, mirror=True,
           rotation=(-12.0, 0.0, 0.0), part="jaw"),                                         # ramus
        _e((0.0420, -0.004, -0.088), (0.0140, 0.026, 0.0110), blend=0.016, mirror=True,
           rotation=(0.0, 0.0, -24.0), part="jaw"),                                         # jaw line
        _e((0.0, -0.066, -0.097), (0.020, 0.016, 0.0165), blend=0.014),                     # chin
        _e((0.0470, -0.050, -0.007), (0.020, 0.016, 0.013), blend=0.012, mirror=True,
           rotation=(0.0, 0.0, 28.0)),                                                      # cheekbone
        _cone((0.0, -0.0820, 0.019), (0.041, -0.071, 0.017), 0.0100, 0.0075, blend=0.012, mirror=True),  # brow
        _cone((0.0, -0.0778, 0.004), (0.0, -0.0975, -0.031), 0.0058, 0.0085, blend=0.007),  # nose bridge
        _s((0.0, -0.0965, -0.0350), 0.0098, blend=0.006, part="nose tip"),
        _e((0.0155, -0.0840, -0.0410), (0.0080, 0.0085, 0.0068), blend=0.004, mirror=True),  # nose wing
        _e((0.0080, -0.0920, -0.0462), (0.0040, 0.0058, 0.0024), op="cut", blend=0.002, mirror=True),  # nostril
        _e((0.0, -0.0818, -0.0505), (0.0060, 0.0050, 0.0080), blend=0.006),                 # philtrum
        _e((0.0075, -0.0828, -0.0585), (0.0150, 0.0080, 0.0060), blend=0.006, mirror=True,
           rotation=(-14.0, 0.0, 8.0), part="mouth"),                                       # upper lip
        _e((0.0, -0.0798, -0.0735), (0.0190, 0.0092, 0.0070), blend=0.007, rotation=(14.0, 0.0, 0.0),
           part="mouth"),                                                                   # lower lip
        _e((0.0, -0.0775, -0.0870), (0.0140, 0.0045, 0.0035), op="cut", blend=0.006),       # under the lip
        *_eye_sockets(eye, eye_r, (0.0150, 0.0080, 0.0062)),
        _e((0.0745, 0.010, -0.010), (0.0080, 0.0200, 0.0310), blend=0.004, mirror=True,
           rotation=(-14.0, 0.0, 16.0)),                                                    # ear
        _e((0.0812, 0.008, -0.004), (0.0045, 0.0145, 0.0215), op="cut", blend=0.002, mirror=True,
           rotation=(-14.0, 0.0, 16.0)),                                                    # ear bowl
        _e((0.0790, 0.004, -0.014), (0.0060, 0.0080, 0.0105), op="cut", blend=0.002, mirror=True,
           rotation=(-14.0, 0.0, 16.0)),                                                    # concha
        _cone((0.0, 0.020, -0.070), (0.0, 0.030, -0.240), 0.0525, 0.0575, blend=0.022),     # neck
        _neck_cut(),
    ]
    steps = with_mouth_line(steps, -0.0662, 0.0215, 0.0014, 0.0011)
    return {"name": "portrait", "steps": steps, "eyes": [{"centre": eye, "radius": eye_r}],
            "low": (-0.105, -0.125, NECK_CUT - 0.012), "high": (0.105, 0.125, 0.130)}


def base():
    """The portrait test's start head: an average head, every feature soft, none of the person's shape."""
    eye, eye_r = (0.0320, -0.057, 0.004), 0.0122
    steps = [
        _e((0.0, 0.010, 0.026), (0.0730, 0.0940, 0.089)),
        _e((0.0, -0.028, -0.034), (0.058, 0.055, 0.058), blend=0.034),
        _e((0.0, -0.018, -0.070), (0.0560, 0.060, 0.044), blend=0.034),
        _e((0.0, -0.060, -0.093), (0.020, 0.016, 0.016), blend=0.020),
        _cone((0.0, -0.078, 0.020), (0.038, -0.070, 0.019), 0.0085, 0.0075, blend=0.016, mirror=True),
        _cone((0.0, -0.0760, 0.008), (0.0, -0.0890, -0.027), 0.0065, 0.0085, blend=0.010),
        _s((0.0, -0.0885, -0.0310), 0.0095, blend=0.008),
        _e((0.0140, -0.0790, -0.0365), (0.0080, 0.0080, 0.0065), blend=0.006, mirror=True),
        _e((0.0, -0.0790, -0.0545), (0.0230, 0.0095, 0.0075), blend=0.009),
        _e((0.0, -0.0765, -0.0690), (0.0210, 0.0095, 0.0075), blend=0.009),
        *_eye_sockets(eye, eye_r, (0.0140, 0.0080, 0.0055)),
        _e((0.0730, 0.012, -0.004), (0.0085, 0.0190, 0.0290), blend=0.006, mirror=True, rotation=(-10.0, 0.0, 12.0)),
        _e((0.0800, 0.008, -0.007), (0.0055, 0.0100, 0.0125), op="cut", blend=0.003, mirror=True,
           rotation=(-10.0, 0.0, 12.0)),
        _cone((0.0, 0.020, -0.066), (0.0, 0.028, -0.240), 0.0520, 0.0560, blend=0.024),
        _neck_cut(),
    ]
    steps = with_mouth_line(steps, -0.0620, 0.0210, 0.0013, 0.0010)
    return {"name": "base", "steps": steps, "eyes": [{"centre": eye, "radius": eye_r}],
            "low": (-0.105, -0.125, NECK_CUT - 0.012), "high": (0.105, 0.125, 0.130)}


def stylised():
    eye, eye_r = (0.0345, -0.060, -0.006), 0.0165
    steps = [
        _e((0.0, 0.004, 0.028), (0.0850, 0.0930, 0.0920)),                                  # big cranium
        _e((0.0, -0.032, -0.042), (0.056, 0.052, 0.052), blend=0.040),                      # small face
        _e((0.0, -0.040, -0.080), (0.040, 0.040, 0.030), blend=0.030),                      # small jaw
        _s((0.0, -0.062, -0.090), 0.018, blend=0.020),                                      # round chin
        _s((0.0, -0.0905, -0.0350), 0.0105, blend=0.010, part="nose tip"),                 # button nose
        _e((0.0, -0.0795, -0.0640), (0.0170, 0.0085, 0.0062), blend=0.008, part="mouth"),   # upper lip
        _e((0.0, -0.0770, -0.0750), (0.0150, 0.0085, 0.0062), blend=0.008, part="mouth"),   # lower lip
        _e((0.0420, -0.058, -0.045), (0.020, 0.016, 0.018), blend=0.020, mirror=True),      # round cheeks
        *_eye_sockets(eye, eye_r, (0.0180, 0.0100, 0.0105)),
        _e((0.0830, 0.006, -0.012), (0.0090, 0.0170, 0.0240), blend=0.006, mirror=True, rotation=(0.0, 0.0, 18.0)),
        _e((0.0905, 0.002, -0.014), (0.0055, 0.0090, 0.0140), op="cut", blend=0.003, mirror=True,
           rotation=(0.0, 0.0, 18.0)),
        _cone((0.0, 0.012, -0.080), (0.0, 0.016, -0.240), 0.0400, 0.0430, blend=0.020),     # thin neck
        _neck_cut(),
    ]
    steps = with_mouth_line(steps, -0.0698, 0.0165, 0.0013, 0.0010)
    return {"name": "stylised", "steps": steps, "eyes": [{"centre": eye, "radius": eye_r}],
            "low": (-0.115, -0.125, NECK_CUT - 0.012), "high": (0.115, 0.125, 0.135)}


def anime():
    eye, eye_r = (0.0300, -0.050, -0.014), 0.0185
    steps = [
        _e((0.0, 0.010, 0.030), (0.0790, 0.0890, 0.0890)),                                  # large cranium
        _e((0.0, -0.028, -0.030), (0.056, 0.052, 0.056), blend=0.032),                      # soft face
        _cone((0.0, -0.028, -0.048), (0.0, -0.058, -0.096), 0.042, 0.013, blend=0.030),     # tapering jaw
        _e((0.0360, -0.052, -0.050), (0.018, 0.016, 0.020), blend=0.020, mirror=True),      # full cheeks
        _cone((0.0, -0.0760, -0.022), (0.0, -0.0835, -0.036), 0.0030, 0.0042, blend=0.007),  # small nose
        _e((0.0, -0.0715, -0.0640), (0.0105, 0.0045, 0.0028), blend=0.004, part="mouth"),   # small mouth
        _e((0.0300, -0.0700, -0.014), (0.0220, 0.0100, 0.0200), op="cut", blend=0.006, mirror=True),  # eye bed
        _s(eye, eye_r + 0.0010, op="cut", blend=0.001, mirror=True),
        _e((0.0770, 0.008, -0.020), (0.0070, 0.0140, 0.0200), blend=0.006, mirror=True, rotation=(0.0, 0.0, 15.0)),
        _cone((0.0, 0.010, -0.075), (0.0, 0.014, -0.240), 0.0380, 0.0410, blend=0.020),     # slim neck
        _neck_cut(),
    ]
    steps = with_mouth_line(steps, -0.0640, 0.0095, 0.0009, 0.0008, lift=0.0005)
    return {"name": "anime", "steps": steps, "eyes": [{"centre": eye, "radius": eye_r, "scale": (1.0, 0.55, 1.18),
                                                       "iris": 0.55}],
            "low": (-0.110, -0.120, NECK_CUT - 0.012), "high": (0.110, 0.120, 0.135)}


DESIGNS = {"portrait": portrait, "base": base, "stylised": stylised, "anime": anime}
# Expected heights (z) of the profile landmarks: they only place the windows the ground truth's landmarks are
# looked for in (``face_measure.windows``); the truth's own readings then place a side's windows.
SEEDS = {
    "portrait": {"glabella": 0.019, "nasion": 0.006, "pronasale": -0.035, "subnasale": -0.047,
                 "labrale_superius": -0.058, "stomion": -0.066, "labrale_inferius": -0.074, "sublabiale": -0.087,
                 "pogonion": -0.098},
    "base": {"glabella": 0.020, "nasion": 0.008, "pronasale": -0.031, "subnasale": -0.041, "labrale_superius": -0.054,
             "stomion": -0.062, "labrale_inferius": -0.069, "sublabiale": -0.082, "pogonion": -0.093},
    "stylised": {"glabella": 0.030, "nasion": 0.005, "pronasale": -0.035, "subnasale": -0.050,
                 "labrale_superius": -0.064, "stomion": -0.070, "labrale_inferius": -0.075, "sublabiale": -0.083,
                 "pogonion": -0.093},
    "anime": {"nasion": -0.005, "pronasale": -0.037, "subnasale": -0.046, "stomion": -0.064, "pogonion": -0.095},
}


def design(name):
    return DESIGNS[name]()


def eye_centres(design_data):
    """World centres of both eyes, right (+x) first."""
    found = []
    for eye in design_data["eyes"]:
        x, y, z = eye["centre"]
        found += [(x, y, z), (-x, y, z)]
    return found
