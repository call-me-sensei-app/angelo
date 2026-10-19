# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Grader-only check that a surface a brief states as solid has no see-through gaps (``opaque``).

A brief that states a wall, a roof or a slab states a closed surface; a build of separate stones, tiles or planks
with open joints and nothing behind them is see-through there. The check casts a grid of parallel rays through
each stated area and counts the rays that cross it without meeting any part the brief allows to close it. It runs
after the turn, on every side alike, and is never shown to the model: it only grades.

Check fields:

* ``target``: what may close the surface (a scenario_checks target; ``any`` for several), e.g. any part of the
  cottage, so windows, doors and a separately named mortar core all close the wall.
* ``areas``: ``{"name", "corner", "u", "v", "direction", "depth"}`` each. Rays start at ``corner + s*u + t*v``
  for a grid of ``s, t`` in (0, 1) and travel ``depth`` metres along ``direction`` (from outside the brief's
  face to past its inner side). ``keep``, optional, is a polygon in ``(s, t)``: only the points inside it are
  cast (a gable's triangle).
  An area may give ``points`` (ray origins) instead of a grid, for a curved part (an arch ring).
* ``grid``: ``[columns, rows]`` per area (default 40 x 12).
* ``max_open``: the share of rays allowed to cross (measurement noise only); default ``NOISE``.
"""

import math

# A ray crosses a closed build only through a numerical miss at an exactly grazed edge; the grid is offset by
# irrational fractions so it never runs along modelled coordinates. Measured: 0 of about 110,000 rays crossed on
# 45 recorded cottage builds without open joints (both sides) and on the stand-in with every variant, so a single
# crossing ray is a gap (test_native_grader_opacity).
NOISE = 0.0
JITTER = (0.1373, 0.2917)   # Fractions of a grid cell, so no ray runs along a round coordinate.
MAX_RAYS = 20000


def inside(point, polygon):
    """Whether a 2-D point lies inside a polygon (even-odd rule)."""
    x, y = point
    found = False
    for index in range(len(polygon)):
        (x0, y0), (x1, y1) = polygon[index], polygon[index - 1]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            found = not found
    return found


def area_points(area, grid):
    """``[(s, t, origin)]`` of one area's rays."""
    if "points" in area:
        return [(None, None, list(point)) for point in area["points"]]
    columns, rows = grid
    corner, u, v = area["corner"], area["u"], area["v"]
    points = []
    for column in range(columns):
        s = (column + JITTER[0]) / columns
        for row in range(rows):
            t = (row + JITTER[1]) / rows
            if area.get("keep") and not inside((s, t), area["keep"]):
                continue
            points.append((s, t, [corner[axis] + s * u[axis] + t * v[axis] for axis in range(3)]))
    return points


def _check_opaque(bpy, scene, depsgraph, check):
    import castle_checks
    grid = check.get("grid", (40, 12))
    total, crossing, areas = 0, 0, []
    for area in check["areas"]:
        points = area_points(area, area.get("grid", grid))
        total += len(points)
        if total > MAX_RAYS:
            raise ValueError("opacity needs at most %d rays" % MAX_RAYS)
        open_points = []
        for s, t, origin in points:
            # castle_checks.first_hit passes other parts but not a closing face that shares their surface (a
            # lantern's back plate flush with the wall face).
            if castle_checks.first_hit(scene, depsgraph, origin, area["direction"], area["depth"],
                                       [check["target"]]) is None:
                open_points.append([round(value, 3) for value in origin])
        crossing += len(open_points)
        areas.append({"name": area["name"], "rays": len(points), "open": len(open_points),
                      "open_share": round(len(open_points) / len(points), 4) if points else 0.0,
                      "first_open": open_points[:6]})
    share = crossing / total if total else 1.0
    allowed = check.get("max_open", NOISE)
    return bool(total) and share <= allowed, {"rays": total, "open": crossing, "open_share": round(share, 4),
                                              "allowed": allowed, "areas": areas}


def wall_areas(half_x, half_y, eaves, ridge, low=0.1, edge=0.3, standoff=1.0, depth=2.5):
    """The four walls of a rectangular house (ridge along X, gable ends at -X and +X) as ``opaque`` areas.

    Each long wall is the band from ``low`` to just under the eaves, ``edge`` in from the corners; each gable end
    adds the triangle up to just under the roof line. Rays start ``standoff`` outside the brief's face and stop
    ``depth`` later, short of the opposite wall."""
    top = eaves - 0.05
    areas = []
    for name, y, sign in (("front wall", -half_y, 1.0), ("back wall", half_y, -1.0)):
        areas.append({"name": name, "corner": [-half_x + edge, y - sign * standoff, low],
                      "u": [2 * (half_x - edge), 0.0, 0.0], "v": [0.0, 0.0, top - low],
                      "direction": [0.0, sign, 0.0], "depth": depth})
    rise = ridge - eaves
    for name, x, sign in (("left gable end", -half_x, 1.0), ("right gable end", half_x, -1.0)):
        corner = [x - sign * standoff, -half_y + edge, low]
        areas.append({"name": name, "corner": corner, "u": [0.0, 2 * (half_y - edge), 0.0],
                      "v": [0.0, 0.0, top - low], "direction": [sign, 0.0, 0.0], "depth": depth})
        # The triangle above the eaves, kept 0.3 m under the roof line (the roof's own thickness is the model's).
        height = rise * (1 - edge / half_y) - 0.3
        if height > 0.2:
            areas.append({"name": name + " (gable)", "corner": [corner[0], -half_y + edge, eaves + 0.05],
                          "u": [0.0, 2 * (half_y - edge), 0.0], "v": [0.0, 0.0, height],
                          "direction": [sign, 0.0, 0.0], "depth": depth,
                          "keep": [[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]})
    return areas


def roof_areas(half_x, half_y, eaves, edge=0.6, height=12.0, below=0.2):
    """The roof over a rectangular footprint as one ``opaque`` area of downward rays that must meet the roof (or
    another part closing it, a chimney) before they are ``below`` under the eaves. ``edge`` keeps the rays off
    the wall tops, which would close a hole in the roof above them."""
    return [{"name": "roof", "corner": [-half_x + edge, -half_y + edge, height],
             "u": [2 * (half_x - edge), 0.0, 0.0], "v": [0.0, 2 * (half_y - edge), 0.0],
             "direction": [0.0, 0.0, -1.0], "depth": height - (eaves - below)}]


def box_areas(name, low, high, axis, sign, standoff, depth, edge=0.3):
    """One face of a box-shaped stated part as an ``opaque`` area: rays along ``axis`` (0 x, 1 y, 2 z) in the
    ``sign`` direction, over the box's other two extents less ``edge``, from ``standoff`` outside its face."""
    others = [a for a in range(3) if a != axis]
    corner = [0.0, 0.0, 0.0]
    corner[axis] = (low[axis] if sign > 0 else high[axis]) - sign * standoff
    u, v = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    corner[others[0]], u[others[0]] = low[others[0]] + edge, high[others[0]] - low[others[0]] - 2 * edge
    corner[others[1]], v[others[1]] = low[others[1]] + edge, high[others[1]] - low[others[1]] - 2 * edge
    direction = [0.0, 0.0, 0.0]
    direction[axis] = float(sign)
    return {"name": name, "corner": corner, "u": u, "v": v, "direction": direction, "depth": depth}


CHECKS = {"opaque": _check_opaque}
