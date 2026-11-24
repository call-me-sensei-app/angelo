# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pre-review checks' geometry: facts about a model's surfaces, as an artist would find them by orbiting, found by
measuring instead. NumPy only, never ``bpy``: the host reads world-space arrays (``game_ready_mesh.read``) and calls
``check`` off the main thread. Contract: doc/angelo/character_checks.rst.

* **Shells**: triangles joined through shared corners after welding corners at the same place (split normals and UV
  seams don't split a shell).
* ``flipped_faces``: faces wound against their neighbours in a shell (two faces that cross a shared edge the same
  way). The minority is reported, so a shell is never called all wrong for one face.
* ``inward_shell``: a closed shell (every edge on exactly two faces) whose consistent winding encloses negative
  volume: it is inside out, and shades and culls as if seen from within. Open surfaces (cards, planes) are never
  judged here: which side should face out is the artist's choice.
* ``floating_part``: a shell farther than ``TOUCH_SHARE`` of the figure's height from every other shell, measured
  surface to surface (``triangle_distance``: faces that cross, edges that meet and coplanar overlaps are all at
  zero), so a part resting flush on a face, or pushed through it, touches it. ``gap`` is its true nearest distance.
* ``unweighted_part``: a shell no bone moves (every corner's largest weight is zero), when weights are given.
* ``see_through``: holes in a view's silhouette, background enclosed by the model, with their size and place.

Facts never pass or fail anything and hold no names or paths: the host adds object names.
"""

import numpy

from . import triangle_distance, view_outline

TOUCH_SHARE = 0.01           # Closer than this share of the figure's height is touching.
MAX_FACES_LISTED = 20
MAX_WHERE = 5
ORDER = ("flipped_faces", "inward_shell", "floating_part", "unweighted_part")


def weld(positions, triangles, tolerance):
    """``triangles`` with corners at the same place (within ``tolerance``) given one index."""
    keys = numpy.round(numpy.asarray(positions, dtype=numpy.float64) / max(tolerance, 1e-12)).astype(numpy.int64)
    _unique, inverse = numpy.unique(keys, axis=0, return_inverse=True)
    return inverse.reshape(-1)[numpy.asarray(triangles)]


def shells(triangles, vertex_count):
    """The shell index of each triangle (0, 1, ... in order of first triangle)."""
    parent = numpy.arange(vertex_count)

    def find(index):
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    for a, b, c in numpy.asarray(triangles):
        ra, rb, rc = find(a), find(b), find(c)
        low = min(ra, rb, rc)
        parent[ra] = parent[rb] = parent[rc] = low
    roots = numpy.array([find(a) for a in numpy.asarray(triangles)[:, 0]]) if len(triangles) else numpy.zeros(0, int)
    _unique, ids = numpy.unique(roots, return_inverse=True)
    order = {}
    for value in ids:
        order.setdefault(int(value), len(order))
    return numpy.array([order[int(value)] for value in ids], dtype=numpy.int64)


def signed_volume(positions, triangles):
    p = numpy.asarray(positions, dtype=numpy.float64)[numpy.asarray(triangles)]
    return float(numpy.einsum("ij,ij->i", p[:, 0], numpy.cross(p[:, 1], p[:, 2])).sum() / 6.0)


def _orientation(faces):
    """(sign per face, closed) for one shell's faces (welded indices): +1/-1 relative to its first face, by walking
    shared edges; a non-manifold edge (three or more faces) is not walked across."""
    edges = {}
    for index, (a, b, c) in enumerate(faces):
        for u, v in ((a, b), (b, c), (c, a)):
            edges.setdefault((min(u, v), max(u, v)), []).append((index, (u, v)))
    closed = all(len(users) == 2 for users in edges.values())
    sign = numpy.zeros(len(faces), dtype=numpy.int8)
    for seed in range(len(faces)):
        if sign[seed]:
            continue
        sign[seed] = 1
        stack = [seed]
        while stack:
            face = stack.pop()
            a, b, c = faces[face]
            for u, v in ((a, b), (b, c), (c, a)):
                users = edges[(min(u, v), max(u, v))]
                if len(users) != 2:
                    continue
                other, direction = users[0] if users[1][0] == face else users[1]
                if other == face or sign[other]:
                    continue
                # Consistent neighbours cross a shared edge in opposite directions.
                same_way = direction == (u, v)
                sign[other] = -sign[face] if same_way else sign[face]
                stack.append(other)
    return sign, closed


def check(positions, triangles, *, weights=None, height=None, weld_tolerance=None):
    """``{"facts", "shells", "height", "touch", "work"}`` for one model in world space. ``weights``: each corner's largest
    bone weight, or None for an unrigged model; ``height``: the figure's height (default: the largest extent)."""
    positions = numpy.asarray(positions, dtype=numpy.float64)
    triangles = numpy.asarray(triangles, dtype=numpy.int64).reshape(-1, 3)
    if not len(triangles):
        return {"facts": [], "shells": 0, "height": 0.0, "touch": 0.0,
                "work": {"node_pairs": 0, "triangle_pairs": 0}}
    extent = positions.max(axis=0) - positions.min(axis=0)
    height = float(height) if height else float(extent.max())
    welded = weld(positions, triangles, weld_tolerance or max(height * 1e-6, 1e-9))
    shell_of = shells(welded, int(welded.max()) + 1)
    count = int(shell_of.max()) + 1
    centroids = positions[triangles].mean(axis=1)
    facts = []
    tris_of, verts_of = {}, {}
    for shell in range(count):
        faces = numpy.flatnonzero(shell_of == shell)
        tris_of[shell] = triangles[faces]
        verts_of[shell] = numpy.unique(triangles[faces])
        sign, closed = _orientation(welded[faces])
        minority = faces[sign == (-1 if (sign == 1).sum() >= (sign == -1).sum() else 1)]
        if len(minority):
            facts.append({"code": "flipped_faces", "severity": "WARN", "shell": shell, "count": int(len(minority)),
                          "faces": minority[:MAX_FACES_LISTED].tolist(),
                          "where": centroids[minority[:MAX_WHERE]].round(5).tolist(), "measured": {}})
        if closed:
            majority = 1 if (sign == 1).sum() >= (sign == -1).sum() else -1
            consistent = numpy.where((sign == majority)[:, None], triangles[faces], triangles[faces][:, ::-1])
            volume = signed_volume(positions, consistent)
            if volume < 0:
                facts.append({"code": "inward_shell", "severity": "WARN", "shell": shell, "count": int(len(faces)),
                              "faces": faces[:MAX_FACES_LISTED].tolist(),
                              "where": [centroids[faces].mean(axis=0).round(5).tolist()],
                              "measured": {"volume": round(-volume, 9)}})
    touch = TOUCH_SHARE * height
    work = {"node_pairs": 0, "triangle_pairs": 0}
    if count > 1:
        tree = triangle_distance.Tree(positions[triangles], shell_of)
        near = triangle_distance.touching(tree, count, touch, work=work)
        floating = [shell for shell in range(count) if not near[shell]]
        if len(floating) == count:   # No two shells touch: the largest is the model the others float from.
            floating.remove(max(range(count), key=lambda shell: len(tris_of[shell])))
        for shell in floating:
            faces = numpy.flatnonzero(shell_of == shell)
            part = triangle_distance.Tree(positions[triangles[faces]], shell_of[faces])
            gap = triangle_distance.gap(part, tree, shell, work=work)
            facts.append({"code": "floating_part", "severity": "WARN", "shell": shell, "count": int(len(faces)),
                          "faces": faces[:MAX_FACES_LISTED].tolist(),
                          "where": [centroids[faces].mean(axis=0).round(5).tolist()],
                          "measured": {"gap": round(gap, 6), "touch": round(touch, 6)}})
    if weights is not None:
        weights = numpy.asarray(weights, dtype=numpy.float64)
        for shell in range(count):
            if not (weights[verts_of[shell]] > 0).any():
                faces = numpy.flatnonzero(shell_of == shell)
                facts.append({"code": "unweighted_part", "severity": "WARN", "shell": shell, "count": int(len(faces)),
                              "faces": faces[:MAX_FACES_LISTED].tolist(),
                              "where": [centroids[faces].mean(axis=0).round(5).tolist()], "measured": {}})
    facts.sort(key=lambda fact: (ORDER.index(fact["code"]), fact["shell"]))
    return {"facts": facts, "shells": count, "height": round(height, 6), "touch": round(touch, 6), "work": work}


def see_through(mask, min_area_px=1):
    """Holes in a silhouette: background regions enclosed by the model (4-connected, not touching the picture's
    border), largest first: ``[{"area_px", "bbox_px": [x0, y0, x1, y1] (x1, y1 exclusive)}]``."""
    mask = numpy.asarray(mask, dtype=bool)
    runs, roots = view_outline._labels(~mask)
    height, width = mask.shape
    outside, area, box = set(), {}, {}
    for (row, start, end), root in zip(runs, roots):
        if row in (0, height - 1) or start == 0 or end == width:
            outside.add(root)
        area[root] = area.get(root, 0) + end - start
        x0, y0, x1, y1 = box.get(root, (start, row, end, row + 1))
        box[root] = (min(x0, start), min(y0, row), max(x1, end), max(y1, row + 1))
    holes = [{"area_px": int(area[root]), "bbox_px": list(box[root])} for root in area
             if root not in outside and area[root] >= min_area_px]
    return sorted(holes, key=lambda hole: (-hole["area_px"], hole["bbox_px"]))
