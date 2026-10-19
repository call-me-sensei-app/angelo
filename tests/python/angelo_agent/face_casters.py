# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How the face and hair graders see a head: orthographic views and two ray casters with one interface.

* ``View``: an orthographic view given like a fixed camera (location,
  target). Image coordinates ``(u, v)`` are metres in the view plane from the
  target: ``u`` along the view's right, ``v`` along its up. Front (looking
  +Y): u = x. Side (from -X, looking +X): u = -y, the face to the right. Back
  (looking -Y): u = -x. Top (looking -Z, +Y up): u = x, v = y.
* ``SceneCaster`` (inside Blender): the evaluated scene's ray casts, so any
  build is read the same way: a sculpted mesh, a remeshed one, Multires,
  Geometry Nodes or instances. A matched read meets only the matching
  objects (their own BVH), whatever else is in front. This is how both sides
  of a trial are measured.
* ``FieldCaster`` (NumPy only): the same answers for a ground truth given as
  signed distance fields (``head_sdf``), by sphere tracing; this is how the
  truth is measured, from the design itself, never from a mesh or a stored
  picture. ``face_calibration`` measures how far the two casters disagree on
  the ground truth's own mesh (the meshing and ray-cast error).

Both answer ``first(us, vs, match)`` for arrays of image points: the planar
depth of the first hit (``inf`` where none) and the hit object's name.
``hair_strands`` reads every hair strand of a scene: evaluated hair curves
objects that match a target (their own Geometry Nodes included), hair
particle systems with their children, on any object, and the splines of
matching curve objects (a strand each: the briefs ask for strands, not for
one of Blender's data types, and agents on every side build them this way).
"""

import math

import numpy

import head_sdf

FAR = 1.0e4
TRACE_STEPS = 400
TRACE_EPSILON = 2.0e-6
# ``ParticleSystem.co_hair`` returns world coordinates in Blender 5.2 (checked by face_calibration's particle build).
PARTICLE_HAIR_IN_OBJECT_SPACE = False


class View:
    """An orthographic view: its location, unit right, up and forward vectors (as ``scenario_capture``)."""

    def __init__(self, spec):
        location = numpy.asarray(spec["location"], dtype=numpy.float64)
        target = numpy.asarray(spec["target"], dtype=numpy.float64)
        forward = target - location
        forward /= numpy.linalg.norm(forward)
        up = numpy.array((0.0, 1.0, 0.0)) if abs(forward[2]) > 0.99 else numpy.array((0.0, 0.0, 1.0))
        right = numpy.cross(forward, up)
        right /= numpy.linalg.norm(right)
        self.location, self.target, self.forward = location, target, forward
        self.right, self.up = right, numpy.cross(right, forward)
        self.ortho_scale = spec.get("ortho_scale")

    def origins(self, us, vs):
        """World ray origins on the camera's plane for image points (arrays of the same shape): the image
        centre is where the line through the target along the view meets that plane."""
        us, vs = numpy.broadcast_arrays(numpy.asarray(us, dtype=numpy.float64), numpy.asarray(vs, dtype=numpy.float64))
        centre = self.target - self.forward * numpy.dot(self.target - self.location, self.forward)
        return centre + us.reshape(-1, 1) * self.right + vs.reshape(-1, 1) * self.up

    def project(self, points):
        """``(u, v, depth)`` of world points (N x 3); depth along the view from the camera's plane."""
        points = numpy.asarray(points, dtype=numpy.float64).reshape(-1, 3)
        relative = points - self.target
        return relative @ self.right, relative @ self.up, (points - self.location) @ self.forward

    def pixel_size(self, width=1280):
        """Metres per pixel of a reference picture of this view ``width`` pixels across."""
        return self.ortho_scale / width if self.ortho_scale else None


def name_matcher(targets):
    """A test of an object name (and its parent's) against check targets (``scenario_checks.matches``)."""
    import scenario_checks

    def match(*names):
        return any(scenario_checks.matches(name, target) for name in names if name for target in targets)
    return match


# ---------------------------------------------------------------- the scene, inside Blender


def mesh_trees(depsgraph, match):
    """``[(name, BVHTree)]`` in world space of every evaluated mesh object or instance that ``match`` accepts
    (its modifiers and Geometry Nodes applied, instances placed): what a matched ray or distance reads."""
    from mathutils.bvhtree import BVHTree
    trees = []
    for instance in depsgraph.object_instances:
        obj = instance.object
        parent = obj.parent.original.name if obj.parent else None
        if obj.type != "MESH" or not match(obj.original.name, parent):
            continue
        mesh = obj.data
        count = len(mesh.vertices)
        if not count or not len(mesh.polygons):
            continue
        local = numpy.empty(count * 3, dtype=numpy.float32)
        mesh.vertices.foreach_get("co", local)
        world = numpy.asarray(instance.matrix_world, dtype=numpy.float64)
        vertices = local.reshape(-1, 3).astype(numpy.float64) @ world[:3, :3].T + world[:3, 3]
        loops = numpy.empty(len(mesh.loops), dtype=numpy.int32)
        mesh.loops.foreach_get("vertex_index", loops)
        starts = numpy.empty(len(mesh.polygons), dtype=numpy.int32)
        mesh.polygons.foreach_get("loop_start", starts)
        polygons = [polygon.tolist() for polygon in numpy.split(loops, starts[1:])]
        trees.append((obj.original.name, BVHTree.FromPolygons(vertices.tolist(), polygons)))
    return trees


class SceneCaster:
    """Ray casts of the evaluated scene, one per image point.

    With ``match`` the ray meets only the matching objects (``mesh_trees``: their own world-space BVH, so no
    number of other objects in front, hair cards or a backdrop, can hide them); without it, the scene's own
    first hit of anything (``Scene.ray_cast``).
    """

    def __init__(self, scene, depsgraph, view, trees=None):
        """``trees``: a dict shared by the casters of one reading, so each object's BVH is built once."""
        from mathutils import Vector
        self.scene, self.depsgraph, self.view = scene, depsgraph, view
        self.direction = Vector(view.forward.tolist())
        self._vector = Vector
        self._trees = {} if trees is None else trees
        self.rays = 0

    def _matched(self, match):
        if match not in self._trees:
            self._trees[match] = mesh_trees(self.depsgraph, match)
        return self._trees[match]

    def first(self, us, vs, match=None):
        origins = self.view.origins(us, vs)
        depth = numpy.full(len(origins), numpy.inf)
        names = numpy.empty(len(origins), dtype=object)
        trees = self._matched(match) if match is not None else None
        direction = self.direction
        for index, origin in enumerate(origins.tolist()):
            start = self._vector(origin)
            self.rays += 1
            if trees is None:
                hit, location, _normal, _face, obj, _matrix = self.scene.ray_cast(self.depsgraph, start, direction,
                                                                                    distance=FAR)
                if hit:
                    depth[index], names[index] = (location - start).length, obj.original.name
                continue
            for name, tree in trees:
                location, _normal, _face, distance = tree.ray_cast(start, direction, FAR)
                if location is not None and distance < depth[index]:
                    depth[index], names[index] = distance, name
        return depth, names


def scene_bounds(depsgraph, view, match):
    """``(u0, u1, v0, v1)`` of the evaluated bounding boxes of matching objects and instances, or None."""
    from mathutils import Vector
    corners = []
    for instance in depsgraph.object_instances:
        obj = instance.object
        parent = obj.parent.original.name if obj.parent else None
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES"} or \
                not match(obj.original.name, parent):
            continue
        corners += [tuple(instance.matrix_world @ Vector(corner)) for corner in obj.bound_box]
    if not corners:
        return None
    u, v, _ = view.project(numpy.asarray(corners))
    return float(u.min()), float(u.max()), float(v.min()), float(v.max())


def object_centres(depsgraph, match):
    """World centre of the evaluated bounds of every matching object, by name."""
    from mathutils import Vector
    centres = {}
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META"} or not match(obj.original.name):
            continue
        corners = numpy.asarray([tuple(instance.matrix_world @ Vector(corner)) for corner in obj.bound_box])
        centres.setdefault(obj.original.name, []).append((corners.min(axis=0) + corners.max(axis=0)) * 0.5)
    return {name: numpy.mean(found, axis=0) for name, found in centres.items()}


def _curves_strands(curves, matrix):
    count = len(curves.points)
    if not count:
        return []
    positions = numpy.empty(count * 3, dtype=numpy.float32)
    curves.attributes["position"].data.foreach_get("vector", positions)
    offsets = numpy.empty(len(curves.curves) + 1, dtype=numpy.int32)
    curves.curve_offset_data.foreach_get("value", offsets)
    world = numpy.asarray(matrix, dtype=numpy.float64)
    points = positions.reshape(-1, 3).astype(numpy.float64) @ world[:3, :3].T + world[:3, 3]
    return numpy.split(points, offsets[1:-1])


def _particle_strands(obj, system):
    steps = 2 ** system.settings.display_step + 1
    total = len(system.particles) + len(system.child_particles)
    world = numpy.asarray(obj.matrix_world, dtype=numpy.float64)
    found = []
    for particle in range(total):
        points = numpy.array([tuple(system.co_hair(obj, particle_no=particle, step=step)) for step in range(steps)])
        found.append(points @ world[:3, :3].T + world[:3, 3] if PARTICLE_HAIR_IN_OBJECT_SPACE else points)
    return found


def _spline_strands(curve, matrix):
    """A legacy curve's splines of two points or more, as world points (their control points)."""
    world = numpy.asarray(matrix, dtype=numpy.float64)
    found = []
    for spline in curve.splines:
        points = spline.bezier_points if spline.type == "BEZIER" else spline.points
        if len(points) < 2:
            continue
        flat = numpy.empty(len(points) * (3 if spline.type == "BEZIER" else 4), dtype=numpy.float64)
        points.foreach_get("co", flat)
        local = flat.reshape(len(points), -1)[:, :3]
        found.append(local @ world[:3, :3].T + world[:3, 3])
    return found


def hair_strands(depsgraph, match, limit=500000):
    """Every hair strand as an (n, 3) array of world points, and where they came from."""
    found, sources = [], []
    for instance in depsgraph.object_instances:
        obj = instance.object
        parent = obj.parent.original.name if obj.parent else None
        if obj.type == "CURVES" and match(obj.original.name, parent):
            made = _curves_strands(obj.data, instance.matrix_world)
            found += made
            sources.append({"object": obj.original.name, "kind": "hair curves", "strands": len(made)})
        elif obj.type == "CURVE" and not instance.is_instance and match(obj.original.name, parent):
            made = _spline_strands(obj.original.data, instance.matrix_world)
            found += made
            sources.append({"object": obj.original.name, "kind": "curve splines", "strands": len(made)})
        elif obj.type == "MESH" and not instance.is_instance:
            for system in obj.particle_systems:
                if system.settings.type == "HAIR":
                    made = _particle_strands(obj, system)
                    found += made
                    sources.append({"object": obj.original.name, "kind": "particle hair", "strands": len(made)})
        if len(found) > limit:
            sources.append({"truncated_at": limit})
            break
    return found[:limit], sources


# ---------------------------------------------------------------- a ground truth given as fields


class FieldCaster:
    """Sphere tracing of named signed distance fields: ``{name: callable(points) -> distance}`` with a box each."""

    def __init__(self, view, fields):
        self.view = view
        self.fields = fields   # name -> (distance function, (low, high))
        self.rays = 0

    def _trace(self, origins, function, box):
        direction = self.view.forward
        low, high = numpy.asarray(box[0], dtype=numpy.float64), numpy.asarray(box[1], dtype=numpy.float64)
        with numpy.errstate(divide="ignore", invalid="ignore"):
            inverse = numpy.where(direction != 0, 1.0 / direction, numpy.inf)
            t1, t2 = (low - origins) * inverse, (high - origins) * inverse
        near = numpy.nanmax(numpy.minimum(t1, t2), axis=1)
        far = numpy.nanmin(numpy.maximum(t1, t2), axis=1)
        depth = numpy.full(len(origins), numpy.inf)
        active = numpy.flatnonzero(far > numpy.maximum(near, 0.0))
        t = numpy.maximum(near[active], 0.0)
        previous = t.copy()
        for _ in range(TRACE_STEPS):
            if not len(active):
                break
            value = function(origins[active] + direction * t[:, None])
            inside = value < 0
            if inside.any():   # Stepped past the surface: bisect back between the last outside point and here.
                lo, hi = previous[inside], t[inside]
                rows = origins[active[inside]]
                for _ in range(40):
                    mid = (lo + hi) * 0.5
                    behind = function(rows + direction * mid[:, None]) < 0
                    lo, hi = numpy.where(behind, lo, mid), numpy.where(behind, mid, hi)
                depth[active[inside]] = (lo + hi) * 0.5
            done = inside | (value < TRACE_EPSILON)
            hit_close = (~inside) & (value < TRACE_EPSILON)
            depth[active[hit_close]] = t[hit_close]
            gone = t > far[active]
            keep = ~(done | gone)
            previous = t[keep]
            t = t[keep] + numpy.maximum(value[keep] * 0.8, 2.0e-5)
            active = active[keep]
        self.rays += len(origins)
        return depth

    def first(self, us, vs, match=None):
        origins = self.view.origins(us, vs)
        best = numpy.full(len(origins), numpy.inf)
        names = numpy.empty(len(origins), dtype=object)
        for name, (function, box) in self.fields.items():
            if match is not None and not match(name, None):
                continue
            depth = self._trace(origins, function, box)
            nearer = depth < best
            best[nearer], names[nearer] = depth[nearer], name
        return best, names


def design_field(design):
    return lambda points: head_sdf.distance(design, points)


def eye_field(centre, radius, scale=(1.0, 1.0, 1.0)):
    radii = numpy.asarray(scale, dtype=numpy.float64) * radius
    step = {"kind": "ellipsoid", "centre": tuple(centre), "radii": tuple(radii)}
    return lambda points: head_sdf.primitive_distance(step, points)
