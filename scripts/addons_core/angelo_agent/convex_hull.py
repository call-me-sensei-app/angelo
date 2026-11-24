# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The convex hull of a point cloud, correct for any cloud: what a collision hull is made of (2.1.0).

``hull(points)`` is a quickhull in float64. A point counts as outside a face only when it is farther than
``tolerance(points)`` from the face's plane (a billionth of the cloud's size, more for a cloud far from the origin), so
the many nearly coplanar points of a voxel decomposition (a part's surface samples and the corners of its seam cells)
never make a sliver face turn the wrong way. The result holds every point: each is inside, or within the tolerance of
every face plane. Faces are triangles wound outward and close a sphere. A cloud that spans less than three dimensions
raises ``Flat``. No ``bpy``: host tests check it directly (``test_native_convex_hull``).

``hull(points, max_vertices=n)`` stops once the hull has ``n`` vertices. Each step adds the point farthest outside the
hull so far, over all faces, so the result is the greedy best ``n``-vertex hull inside the full one: what a collider
capped at an engine's vertex limit is built from (``collision_fit`` then scales it to hold every point).

``bmesh.ops.convex_hull`` was used before and, on such clouds, held as little as 15% of a part's own points.
"""

import heapq
import math

import numpy as np

RELATIVE = 1e-9   # The tolerance as a share of the cloud's largest extent.


class Flat(ValueError):
    """The points lie on a plane, a line or one spot (within the tolerance): they have no solid hull."""


def tolerance(points):
    """How far outside a face's plane a point must be to count as outside it."""
    points = np.asarray(points, dtype=np.float64)
    extent = float(np.ptp(points, axis=0).max()) if len(points) else 0.0
    reach = float(np.abs(points).max()) if len(points) else 0.0
    return max(extent * RELATIVE, reach * 64 * np.finfo(np.float64).eps, np.finfo(np.float64).tiny)


EMPTY = np.empty(0, dtype=np.int64)


class _Rows:
    """Rows of the cloud as Python float tuples, made only for the points a face touches (a decomposition's corner
    clouds are mostly repeats and interior points: converting all of them per hull was a third of its time)."""

    def __init__(self, points):
        self.points, self.cache = points, {}

    def __getitem__(self, index):
        row = self.cache.get(index)
        if row is None:
            row = self.cache[index] = tuple(self.points[index].tolist())
        return row


def _plane(rows, a, b, c):
    """The plane through rows a, b, c (Python floats: a hull makes a face per step, and numpy's per-call overhead on
    3-vectors made the decomposition 3-6x slower): (unit normal, offset)."""
    ax, ay, az = rows[a]
    bx, by, bz = rows[b]
    cx, cy, cz = rows[c]
    ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0.0:
        return (0.0, 0.0, 0.0), 0.0
    nx, ny, nz = nx / length, ny / length, nz / length
    return (nx, ny, nz), nx * ax + ny * ay + nz * az


def _simplex(points, tol):
    """Four points spanning the most volume the extremes offer, or ``Flat``."""
    extremes = np.unique(np.concatenate([points.argmin(axis=0), points.argmax(axis=0)]))
    pairs = [(i, j) for i in extremes for j in extremes if i < j]
    if not pairs:
        raise Flat("one point")
    a, b = max(pairs, key=lambda pair: float(np.linalg.norm(points[pair[0]] - points[pair[1]])))
    line = points[b] - points[a]
    if float(np.linalg.norm(line)) <= tol:
        raise Flat("the points are one spot")
    off_line = np.linalg.norm(np.cross(points - points[a], line), axis=1) / np.linalg.norm(line)
    c = int(off_line.argmax())
    if off_line[c] <= tol:
        raise Flat("the points lie on a line")
    normal, offset = _plane(_Rows(points), a, b, c)
    off_plane = points @ np.array(normal) - offset
    d = int(np.abs(off_plane).argmax())
    if abs(off_plane[d]) <= tol:
        raise Flat("the points lie on a plane")
    return int(a), int(b), c, d


class _Hull:
    def __init__(self, points, tol):
        self.points, self.tol = points, tol
        self.rows = _Rows(points)
        self.faces = {}    # id -> [a, b, c, normal, offset, conflict point indices]
        self.edges = {}    # directed edge (u, v) -> face id; a face (a, b, c) owns (a, b), (b, c), (c, a)
        self.next_id = 0

    def add(self, a, b, c):
        normal, offset = _plane(self.rows, a, b, c)
        face = self.next_id
        self.next_id += 1
        self.faces[face] = [a, b, c, normal, offset, EMPTY]
        for edge in ((a, b), (b, c), (c, a)):
            self.edges[edge] = face
        return face

    def remove(self, face):
        a, b, c = self.faces.pop(face)[:3]
        for edge in ((a, b), (b, c), (c, a)):
            if self.edges.get(edge) == face:
                del self.edges[edge]

    def distance(self, face, index):
        _, _, _, (nx, ny, nz), offset, _ = self.faces[face]
        x, y, z = self.rows[index]
        return nx * x + ny * y + nz * z - offset

    def assign(self, candidates, faces):
        """Give each candidate point to the face among ``faces`` it is farthest outside, if any."""
        if len(candidates) == 0 or not faces:
            return
        normals = np.array([self.faces[f][3] for f in faces])
        offsets = np.array([self.faces[f][4] for f in faces])
        reach = self.points[candidates] @ normals.T - offsets[None, :]
        best = reach.argmax(axis=1)
        outside = reach[np.arange(len(candidates)), best] > self.tol
        for column, face in enumerate(faces):
            self.faces[face][5] = candidates[outside & (best == column)]


def hull(points, max_vertices=None):
    """``(vertices, faces)``: indices into ``points`` of the hull's vertices, and its triangles (rows of point
    indices, wound outward). Raises ``Flat`` for a cloud with no volume. With ``max_vertices`` (at least 4) it stops at
    that many vertices: the farthest-first hull inside the full one."""
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4:
        raise Flat("fewer than four points")
    tol = tolerance(points)
    a, b, c, d = _simplex(points, tol)
    state = _Hull(points, tol)
    centre = points[[a, b, c, d]].mean(axis=0)
    faces = []
    for tri in ((a, b, c), (a, c, d), (a, d, b), (b, d, c)):
        normal, offset = _plane(state.rows, *tri)
        faces.append(state.add(*(tri if float(np.dot(normal, centre)) - offset < 0 else (tri[0], tri[2], tri[1]))))
    rest = np.setdiff1d(np.arange(len(points)), [a, b, c, d])
    state.assign(rest, faces)
    heap = []

    def push(face):   # Keyed by the face's farthest outside point: the next step takes the farthest of all.
        _, _, _, normal, offset, conflict = state.faces[face]
        if len(conflict):
            heapq.heappush(heap, (-(float((points[conflict] @ normal).max()) - offset), face))

    for face in faces:
        push(face)
    count = 4
    while heap:
        _, face = heapq.heappop(heap)
        if face not in state.faces or len(state.faces[face][5]) == 0:
            continue
        if max_vertices is not None and count >= max_vertices:
            break
        conflict = state.faces[face][5]
        _, _, _, normal, offset, _ = state.faces[face]
        eye = int(conflict[int((points[conflict] @ normal).argmax())])
        visible, stack = {face}, [face]
        while stack:   # The faces the eye sees, connected to the first: their outline is the horizon.
            current = stack.pop()
            ca, cb, cc = state.faces[current][:3]
            for u, v in ((ca, cb), (cb, cc), (cc, ca)):
                neighbour = state.edges.get((v, u))
                if neighbour is not None and neighbour not in visible and state.distance(neighbour, eye) > tol:
                    visible.add(neighbour)
                    stack.append(neighbour)
        horizon = []
        for current in visible:
            ca, cb, cc = state.faces[current][:3]
            for u, v in ((ca, cb), (cb, cc), (cc, ca)):
                if state.edges.get((v, u)) not in visible:
                    horizon.append((u, v))
        orphans = np.concatenate([state.faces[f][5] for f in visible])   # Each point is in one face's list only.
        orphans = orphans[orphans != eye]
        for current in visible:
            state.remove(current)
        made = [state.add(u, v, eye) for u, v in horizon]
        state.assign(orphans, made)
        for new in made:
            push(new)
        count += 1
    triangles = np.array([state.faces[f][:3] for f in sorted(state.faces)], dtype=np.int64)
    return np.unique(triangles), triangles


def volume(points, faces):
    """The volume a closed, outward-wound triangle mesh encloses."""
    points = np.asarray(points, dtype=np.float64)
    a, b, c = (points[faces[:, k]] for k in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0
