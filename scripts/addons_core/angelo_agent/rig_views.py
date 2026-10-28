# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pictures for the RIG tools: a hand's skin in poses the tools skinned themselves, drawn off-screen on the GPU like
``angelo_view``'s WEIGHT shading (``view_weights._draw``), so a candidate pose is seen without changing the scene.

Cameras come from the hand's own frame, not world axes: BACK and PALM look along the palm normal, THUMB_SIDE and
LITTLE_SIDE across the knuckle row, PALM_THUMB at the palm turned toward the thumb (the side one of the rigs' fist
photographs shows; a pose is compared with a photo only at the photo's own angle). The fingers point up. Every
pose picture has its rest pose beside it, on the right: an edge already in the rest mesh is the mesh's, not the
weights' (rig B). Skin deeper than contact in another part is red; joint markers are yellow. Main thread for the
draw; PNG encoding as view_weights does it.
"""

import os

import numpy as np

from . import hand_contact as C
from . import hand_rig as H
from .view_framing import orthographic_projection, view_matrix
from .view_png import encode_png

SKIN = np.array((0.80, 0.74, 0.68))
SINKING = np.array((0.92, 0.16, 0.10))
MARKER = np.array((1.0, 0.85, 0.10))
AMBIENT = 0.35
GAP = 0.15                 # of one copy's width, between copies
POSE_PANEL = 384           # pixels per side of each copy in a pose picture
RANGE_PANEL = 256
COLUMNS = ("rest", "back reach", "half forward", "forward reach")


def _directions(rig, solver, posed, view):
    """(forward, up) in world for a view, from the hand's frame as posed (the hand bone's own turn included)."""
    hand, skel = solver.hand, solver.skel
    turn = (posed.posed[hand.bone] @ np.linalg.inv(skel.rest[hand.bone]))[:3, :3]
    n = rig.direction_to_world(turn @ hand.palm_normal)
    axis = rig.direction_to_world(turn @ hand.axis())
    knuckles = [skel.heads[f.joints[0]] for f in hand.fingers]
    front = rig.direction_to_world(turn @ H.unit(H.perpendicular(knuckles[0] - knuckles[-1], hand.axis())))
    forward = {"BACK": -n, "PALM": n, "THUMB_SIDE": front, "LITTLE_SIDE": -front,
               "PALM_THUMB": H.unit(n + front)}[view]
    up = H.perpendicular(axis, forward)
    if np.linalg.norm(up) < 1e-6:
        up = H.perpendicular(n, forward)
    return forward, H.unit(up)


def _normals(positions, triangles):
    a, b, c = (positions[triangles[:, i]] for i in range(3))
    face = np.cross(b - a, c - a)
    out = np.zeros_like(positions)
    for i in range(3):
        np.add.at(out, triangles[:, i], face)
    length = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(length, 1e-12)


def _sinking(solver, posed):
    """Vertices of the hand sunk deeper than skin contact (SKIN_DEPTH) in another part, judged as the tools judge
    contact (webs touching at rest left out)."""
    contact, out = solver.contact, np.zeros(len(solver.skin.rest), dtype=bool)
    for a in contact.parts:
        for b in contact.parts:
            if a == b:
                continue
            idx = contact.eligible(solver.rest.positions, solver.rest.posed, a, b)
            # Skin already inside at rest is the mesh's fold, not the pose's: the rest picture beside shows it.
            idx = np.setdiff1d(idx, contact.rest_inside(solver.rest.positions, solver.rest.posed, a, b))
            if not len(idx):
                continue
            d = contact.signed(posed.positions, posed.posed, a, b, idx) / solver.r
            out[idx[np.isfinite(d) & (d < -C.SKIN_DEPTH) & (d > -C.WRONG_SIDE)]] = True
    return out


def _octahedron(centre, radius):
    offsets = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=np.float64)
    faces = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    return centre + offsets * radius, faces


class _Copy:
    """One drawn copy of the hand: world positions, triangles, per-vertex colour and its offset in the picture."""

    def __init__(self, positions, triangles, colours):
        self.positions, self.triangles, self.colours = positions, triangles, colours


def _hand_copy(rig, solver, posed, markers=False, sinking=True):
    region = solver.hs.display
    local = -np.ones(len(solver.skin.rest), dtype=np.int64)
    local[region] = np.arange(len(region))
    tri = solver.skin.triangles[np.all(local[solver.skin.triangles] >= 0, axis=1)]
    tri = local[tri]
    positions = posed.positions.copy()
    extra = np.setdiff1d(region, solver.region)   # The forearm stretch: posed with the arm as it is.
    positions[extra] = solver.skin.deform(solver.skel, posed.posed, extra)
    world = rig.to_world(positions[region])
    colours = np.tile(SKIN, (len(region), 1))
    if sinking:
        colours[_sinking(solver, posed)[region]] = SINKING
    if markers:
        joints = [solver.hand.bone] + solver.joints
        extra_p, extra_t, extra_c = [], [], []
        base = len(world)
        for j in joints:
            p, f = _octahedron(rig.to_world(posed.posed[j][:3, 3][None])[0], 0.3 * solver.r)
            extra_t.append(f + base)
            extra_p.append(p)
            extra_c.append(np.tile(MARKER, (len(p), 1)))
            base += len(p)
        world = np.concatenate([world] + extra_p)
        tri = np.concatenate([tri] + extra_t)
        colours = np.concatenate([colours] + extra_c)
    return _Copy(world, tri, colours)


def _layout(copies, forward, up, columns):
    """Place copies in rows of ``columns`` across the picture plane; returns the camera and pixel size."""
    right = np.cross(up, forward)
    widths = [float(np.ptp(c.positions @ right)) for c in copies]
    heights = [float(np.ptp(c.positions @ up)) for c in copies]
    cell_w, cell_h = max(widths) * (1 + GAP), max(heights) * (1 + GAP)
    for i, c in enumerate(copies):
        row, col = divmod(i, columns)
        centre = c.positions.mean(axis=0)
        mid = (c.positions @ right).min() + widths[i] / 2, (c.positions @ up).min() + heights[i] / 2
        shift = right * (col * cell_w - mid[0]) + up * (-row * cell_h - mid[1])
        shift -= forward * float(centre @ forward)
        c.positions = c.positions + shift
    rows = (len(copies) + columns - 1) // columns
    all_points = np.concatenate([c.positions for c in copies])
    xs, ys, zs = all_points @ right, all_points @ up, all_points @ forward
    half_w = (xs.max() - xs.min()) / 2 * 1.08
    half_h = (ys.max() - ys.min()) / 2 * 1.08
    depth_span = float(zs.max() - zs.min())
    centre = right * (xs.max() + xs.min()) / 2 + up * (ys.max() + ys.min()) / 2
    distance = depth_span + max(half_w, half_h) * 2
    position = centre + forward * distance
    world = [[right[i], up[i], forward[i], position[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
    depth = distance * 2 + depth_span
    return {"matrix_world": world, "view_matrix": view_matrix(world),
            "projection_matrix": orthographic_projection(half_w, half_h, depth)}, (columns, rows), (half_w, half_h)


def _draw(copies, camera, forward, width, height):
    """RGBA8 rows (bottom first) of the copies, through ``view_weights._draw`` one copy at a time into one buffer:
    each geometry carries its own colours."""
    from .view_weights import _draw as draw
    geometries = []
    for c in copies:
        geometries.append({"positions": c.positions.astype(np.float32),
                           "normals": _normals(c.positions, c.triangles).astype(np.float32),
                           "indices": c.triangles.astype(np.int32), "colours": c.colours})

    def colours(geometry, camera):
        facing = np.abs(geometry["normals"].astype(np.float64) @ forward)
        rgb = geometry["colours"] * (AMBIENT + (1.0 - AMBIENT) * facing)[:, None]
        return np.concatenate([rgb, np.ones((len(rgb), 1))], axis=1).astype(np.float32)
    return draw(geometries, camera, width, height, colours)


def _write(references, label, png, width, height, extra):
    reference = references.new_observation(label)
    try:
        with open(reference["path"], "wb") as handle:
            handle.write(png)
        os.chmod(reference["path"], 0o600)
    except Exception:
        references.discard_observation(reference)
        raise
    reference["bytes"] = len(png)
    return dict(reference, view_id=reference["id"], view=label, framing="HAND", shading="HAND",
                width=width, height=height, **extra)


def _picture(references, copies, forward, up, columns, panel, label, extra):
    camera, (cols, rows), (half_w, half_h) = _layout(copies, forward, up, columns)
    width = panel * cols
    height = max(1, int(round(width * half_h / half_w)))
    raw = _draw(copies, camera, forward, width, height)
    return _write(references, label, encode_png(raw, width, height), width, height, extra)


def pose_views(rig, solver, posed, views, references, markers=False, label="pose"):
    """One picture per view: the pose on the left, the rest pose on the right."""
    written = []
    try:
        for view in views:
            forward, up = _directions(rig, solver, posed, view)
            pose_copy = _hand_copy(rig, solver, posed, markers)
            rest_copy = _hand_copy(rig, solver, solver.rest, markers, sinking=False)
            written.append(_picture(references, [pose_copy, rest_copy], forward, up, 2, POSE_PANEL,
                                    "%s %s · %s" % (solver.hand.name[:40], view.lower(), label),
                                    {"layout": "%s (left), rest (right)" % label, "hand_view": view}))
    except Exception:
        for reference in written:
            references.discard_observation(reference)
        raise
    return written


def range_sheets(rig, solver, rows, view, references):
    """One sheet per digit: a row per joint (from rows, ``rig_host.joint_range``), columns rest, back reach, half
    forward and forward reach; at most six sheets."""
    from .hand_pose import Posed
    skel, hand = solver.skel, solver.hand
    by_digit = {}
    for row in rows:
        j = skel.index[row["bone"]]
        k = next(i for i, d in enumerate(hand.digits) if j in d.joints)
        by_digit.setdefault(k, []).append((j, row))
    forward, up = _directions(rig, solver, solver.rest, view)
    written = []
    try:
        for k, entries in list(by_digit.items())[:6]:
            copies = []
            for j, row in entries:
                back, ahead = row["reach"]
                for value in (0.0, back, ahead / 2, ahead):
                    angles = {joint: (0.0, 0.0, 0.0) for joint in solver.joints}
                    angles[j] = (value, 0.0, 0.0)
                    copies.append(_hand_copy(rig, solver, Posed(solver, angles)))
            written.append(_picture(references, copies, forward, up, 4, RANGE_PANEL,
                                    "%s %s range · %s" % (hand.name[:32], hand.digits[k].role, view.lower()),
                                    {"layout": "rows: %s; columns: %s" % (", ".join(r["bone"] for _, r in entries),
                                                                         ", ".join(COLUMNS)), "hand_view": view}))
    except Exception:
        for reference in written:
            references.discard_observation(reference)
        raise
    return written
