# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded read-only base-data measurements for scene validation.

Each ValidationReader owns disposable per-call caches and an explicit element
budget. No Blender operator, evaluated mesh, frame/depsgraph change or I/O is
performed here; raw coordinates stay internal to this reader and its predicates.
Target resolution accepts only an already-bound context, never object names.
"""

import math

from .check_defaults import value_tolerance

from .validation_spec import (
    MAX_OBJECTS, MAX_VERTICES, MAX_MESH_ELEMENTS, MAX_TOTAL_ELEMENTS,
    MAX_MATERIALS, MAX_ANIMATION_CURVES, MAX_KEYFRAMES,
)


class Unobservable(RuntimeError):
    """A world-space or evaluated measure of an object its depsgraph does not evaluate, which the base data
    cannot reproduce (modifiers, constraints, animation, a non-object parent): the check certifies nothing and the
    run is partial, rather than certifying or refusing from a stale ``matrix_world`` or cached bounds."""


class Inconclusive(RuntimeError):
    """A supported assertion cannot be proven by a safe bounded observation."""


def context_value(context, key, default=None):
    """Read one named field of the already-bound context representation."""
    return context.get(key, default) if isinstance(context, dict) else getattr(context, key, default)


def name_of(context):
    """How a check reads an object's, material's or node group's name: ``shown_name`` on a stage, else its own."""
    shown = context_value(context, "shown_name")
    return shown if callable(shown) else _own_name


def _own_name(block):
    return block.name


def resolve_target(context, alias):
    """Resolve an explicit context alias, never a global Blender object name."""
    resolver = context_value(context, "resolve_target")
    if callable(resolver):
        return resolver(alias)
    targets = context_value(context, "targets", context_value(context, "objects"))
    if isinstance(targets, dict):
        return targets.get(alias)
    raise Inconclusive("No explicit bound target resolver was provided")


def number_vector(values, size=None):
    """Convert numeric observations; finiteness is checked by the predicate."""
    result = [float(v) for v in values]
    if size is not None and len(result) != size:
        raise Inconclusive("Unsupported coordinate dimensionality")
    return result


def finite_values(values):
    return all(math.isfinite(v) for v in values)


# Parents whose deformation Blender runs as a virtual modifier when the object is parented with PARSKEL, which
# RNA reads as parent_type ARMATURE (LATTICE is the same value) (BKE_modifiers_get_virtual_modifierlist).
DEFORMING_PARENTS = frozenset({"ARMATURE", "CURVE", "LATTICE"})


def deformed(obj):
    """Whether Blender evaluates ``obj``'s geometry differently from its base data, so its dimensions are the
    evaluated bounds: a viewport-enabled modifier, shape keys, or an armature, curve or lattice parent's deform,
    the last two run as virtual modifiers even with ``obj.modifiers`` empty (modifier.cc). Read from its
    base data alone, a shape-keyed mesh read [4, 2, 2] where Blender's dimensions are [8, 2, 2]."""
    if any(getattr(modifier, "show_viewport", True) for modifier in getattr(obj, "modifiers", ())):
        return True
    if getattr(getattr(obj, "data", None), "shape_keys", None) is not None:
        return True
    parent = getattr(obj, "parent", None)
    return (parent is not None and getattr(obj, "parent_type", "OBJECT") in ("ARMATURE", "LATTICE")
            and getattr(parent, "type", None) in DEFORMING_PARENTS)


def _object_axes_scaled(extents, matrix):
    """Object-space ``extents`` times the length of each object axis in the 4x4 world ``matrix`` (rows)."""
    return [extents[axis] * math.sqrt(sum(matrix[row][axis] ** 2 for row in range(3))) for axis in range(3)]


def values_near(actual, expected, tolerance):
    """Whether read values equal expected ones within ``tolerance``; None: each number at its own float32 precision."""
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(values_near(a, e, tolerance) for a, e in zip(actual, expected))
    if type(expected) in (int, float):
        limit = value_tolerance(expected) if tolerance is None else tolerance
        return type(actual) in (int, float) and math.isfinite(actual) and abs(actual - expected) <= limit
    return type(actual) is type(expected) and actual == expected


class ValidationReader:
    """One read-only measurement scope; no cache outlives its verification call."""

    def __init__(self, context, max_elements=MAX_TOTAL_ELEMENTS):
        self.context = context
        self.remaining = max_elements
        self.mesh_cache = {}
        self.dimension_cache = {}
        self.assignment_cache = {}

    def spend(self, amount):
        if amount > self.remaining:
            raise Inconclusive("Verification resource budget exhausted; nothing truncated is treated as passing")
        self.remaining -= amount

    def evaluated(self, obj):
        """Whether ``obj``'s depsgraph evaluates it (its ``matrix_world`` and bounds are current)."""
        depsgraph = context_value(self.context, "depsgraph")
        if depsgraph is None:
            return True  # A caller without a depsgraph reads the live, window-evaluated scene.
        return depsgraph.id_eval_get(obj).as_pointer() != obj.as_pointer()

    def world_matrix(self, obj, depth=0):
        """The world matrix: evaluated, or for an object its depsgraph skips (disabled in viewports, excluded) the
        product of its parents' and its own base channels; Unobservable when base data cannot reproduce it."""
        if self.evaluated(obj):
            return [number_vector(row, 4) for row in obj.matrix_world]
        animation = getattr(obj, "animation_data", None)
        parent = obj.parent
        # Beyond parent world @ parent inverse @ basis, Blender's evaluation adds a curve parent's path position
        # (use_path, eval_time), a bone, vertex or lattice parent's frame, constraints, animation and rigid bodies.
        if (depth > 64 or len(getattr(obj, "constraints", ())) or (animation is not None and (
                animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)))
                or getattr(obj, "rigid_body", None) is not None
                or (parent is not None and (obj.parent_type != "OBJECT" or (
                    parent.type == "CURVE" and getattr(parent.data, "use_path", False))))):
            raise Unobservable("%r is not evaluated, and its world matrix depends on more than its base data"
                               % obj.name[:64])
        local = obj.matrix_basis
        if obj.parent is not None:
            local = _matrix(self.world_matrix(obj.parent, depth + 1)) @ obj.matrix_parent_inverse @ local
        return [number_vector(row, 4) for row in local]

    def linear_transform(self, obj):
        """Actual world basis, including the active rotation and delta channels."""
        self.spend(9)
        rows = [row[:3] for row in self.world_matrix(obj)[:3]]
        if not all(finite_values(row) for row in rows):
            raise Inconclusive("The effective world transform is nonfinite")
        return rows

    def mesh(self, obj):
        if obj.type != "MESH":
            raise Inconclusive("This predicate supports base MESH data only")
        if getattr(obj, "mode", "OBJECT") == "EDIT":
            raise Inconclusive("Edit-mode mesh data is not a complete read-only base-mesh observation")
        data = obj.data
        key = id(data)
        if key in self.mesh_cache:
            return self.mesh_cache[key]
        nv, ne, nf = len(data.vertices), len(data.edges), len(data.polygons)
        if nv > MAX_VERTICES or nv + ne + nf > MAX_MESH_ELEMENTS:
            raise Inconclusive("Base mesh exceeds deterministic verification limits")
        loops = getattr(data, "loops", None)
        nl = len(loops) if loops is not None else sum(len(p.vertices) for p in data.polygons)
        if nv + ne + nf + nl > MAX_MESH_ELEMENTS:
            raise Inconclusive("Base mesh corner density exceeds deterministic verification limits")
        self.spend(nv + ne + nf + nl)
        coordinates = [number_vector(v.co, 3) for v in data.vertices]
        edges = [tuple(e.vertices) for e in data.edges]
        faces = [tuple(p.vertices) for p in data.polygons]
        finite = all(finite_values(v) for v in coordinates)
        invalid = sum(len(e) != 2 or any(type(i) is not int or not 0 <= i < nv for i in e) for e in edges)
        invalid += sum(len(f) < 3 or any(type(i) is not int or not 0 <= i < nv for i in f) for f in faces)
        # Never compute geometry metrics from invalid indices or nonfinite inputs.
        lengths, areas = [], []
        if finite and not invalid:
            lengths = [math.dist(coordinates[a], coordinates[b]) for a, b in edges]
            for face, polygon in zip(faces, data.polygons):
                area = getattr(polygon, "area", None)
                if area is None:
                    normal = [0.0, 0.0, 0.0]
                    for a, b in zip(face, face[1:] + face[:1]):
                        av, bv = coordinates[a], coordinates[b]
                        normal[0] += (av[1] - bv[1]) * (av[2] + bv[2])
                        normal[1] += (av[2] - bv[2]) * (av[0] + bv[0])
                        normal[2] += (av[0] - bv[0]) * (av[1] + bv[1])
                    area = math.sqrt(sum(v * v for v in normal)) * 0.5
                areas.append(float(area))
            finite = finite and finite_values(lengths) and finite_values(areas)
        result = {"data": data, "coordinates": coordinates, "edges": edges, "faces": faces,
                  "vertices": nv, "edge_count": ne, "face_count": nf, "loop_count": nl,
                  "finite": finite, "invalid_indices": invalid, "lengths": lengths, "areas": areas}
        self.mesh_cache[key] = result
        return result

    def topology(self, mesh):
        if "topology" in mesh:
            return mesh["topology"]
        if not mesh["finite"] or mesh["invalid_indices"]:
            result = {"invalid_geometry": True}
            mesh["topology"] = result
            return result
        incidence = {}
        vertex_faces = {}
        for fi, face in enumerate(mesh["faces"]):
            for vertex in face:
                vertex_faces.setdefault(vertex, set()).add(fi)
            for a, b in zip(face, face[1:] + face[:1]):
                incidence.setdefault(tuple(sorted((a, b))), []).append((fi, a, b))
        edge_keys = {tuple(sorted(e)) for e in mesh["edges"]}
        missing_edges = len(set(incidence) - edge_keys)
        wire_edges = len(edge_keys - set(incidence))
        boundary = sum(len(v) == 1 for v in incidence.values())
        nonmanifold = sum(len(v) > 2 or (len(v) == 2 and (v[0][1], v[0][2]) != (v[1][2], v[1][1]))
                          or k[0] == k[1] for k, v in incidence.items())
        # Edge incidence alone misses two closed shells touching at one vertex.
        neighbors = {}
        for (a, b), edge_faces in incidence.items():
            if len(edge_faces) == 2:
                f1, f2 = edge_faces[0][0], edge_faces[1][0]
                for vertex in (a, b):
                    neighbors.setdefault((vertex, f1), set()).add(f2)
                    neighbors.setdefault((vertex, f2), set()).add(f1)
        nonmanifold_vertices = 0
        for vertex, faces in vertex_faces.items():
            seen, pending = set(), [next(iter(faces))]
            while pending:
                face = pending.pop()
                if face not in seen:
                    seen.add(face)
                    pending.extend(neighbors.get((vertex, face), set()) - seen)
            if len(seen) != len(faces):
                nonmanifold_vertices += 1
        result = {"boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
                  "nonmanifold_vertices": nonmanifold_vertices, "wire_edges": wire_edges,
                  "loose_vertices": mesh["vertices"] - len(vertex_faces), "missing_edges": missing_edges,
                  "invalid_geometry": False}
        mesh["topology"] = result
        return result

    def dimensions(self, obj, space):
        """An object's extent: WORLD along the world axes, LOCAL along its own axes with its scale applied.

        LOCAL is Blender's ``Object.dimensions``, the dimensions observations report (turn_context): the
        bounds in the object's own space times the length of each axis of its world matrix
        (``BKE_object_dimensions_get``), so parent and delta scale count and rotation does not. The bounds are
        the base mesh's, or the evaluated ones Blender measures whenever it deforms the base data
        (``deformed``). It once measured the unscaled mesh: every recorded LOCAL check
        (four, GPT and Claude) stated Blender's dimensions after a scale, failed against the 2 m
        default cube and cost a correction round (oneshot footbridge 40 s against 21 s).
        """
        key = (id(obj), space)
        if key in self.dimension_cache:
            return self.dimension_cache[key]
        if deformed(obj):
            self.dimension_cache[key] = self.evaluated_dimensions(obj, space)
            return self.dimension_cache[key]
        mesh = self.mesh(obj)
        if not mesh["coordinates"]:
            return [0.0, 0.0, 0.0]
        if not mesh["finite"]:
            return [math.nan] * 3
        coordinates = mesh["coordinates"]
        self.spend(len(coordinates))
        matrix = self.world_matrix(obj)
        if len(matrix) != 4 or not all(finite_values(row) for row in matrix):
            return [math.nan] * 3
        if space == "WORLD":
            coordinates = [[sum(matrix[row][i] * p[i] for i in range(3)) + matrix[row][3]
                            for row in range(3)] for p in coordinates]
        result = [max(v[i] for v in coordinates) - min(v[i] for v in coordinates) for i in range(3)]
        if space != "WORLD":
            result = _object_axes_scaled(result, matrix)
        self.dimension_cache[key] = result
        return result

    def evaluated_dimensions(self, obj, space):
        """Dimensions of a deformed object (viewport modifiers, shape keys, a deforming parent), as observations
        report them (a 7 m Array row, never its 1 m base mesh). Reads the evaluated
        bounds Blender caches on the object (the stage is evaluated before every check); it evaluates nothing."""
        if obj.type != "MESH":
            raise Inconclusive("This predicate supports base MESH data only")
        if getattr(obj, "mode", "OBJECT") == "EDIT":
            raise Inconclusive("Edit-mode mesh data is not a complete read-only observation")
        if not self.evaluated(obj):
            raise Unobservable("%r is not evaluated, so its modifiers' bounds are stale" % obj.name[:64])
        self.spend(24)
        corners = [number_vector(corner, 3) for corner in obj.bound_box]
        if len(corners) != 8 or not all(finite_values(corner) for corner in corners):
            return [math.nan] * 3
        matrix = self.world_matrix(obj)
        if len(matrix) != 4 or not all(finite_values(row) for row in matrix):
            return [math.nan] * 3
        if space != "WORLD":
            return _object_axes_scaled([max(v[i] for v in corners) - min(v[i] for v in corners) for i in range(3)],
                                       matrix)
        for row in (matrix[0][:3], matrix[1][:3], matrix[2][:3]):
            largest = max(abs(value) for value in row)
            if sum(abs(value) > 1e-6 * largest for value in row) != 1:
                raise Inconclusive("World dimensions with modifiers need axis-aligned rotation; check space LOCAL")
        corners = [[sum(matrix[row][i] * p[i] for i in range(3)) + matrix[row][3] for row in range(3)]
                   for p in corners]
        return [max(v[i] for v in corners) - min(v[i] for v in corners) for i in range(3)]

    def scene_objects(self):
        objects = context_value(self.context, "scene_objects")
        if objects is None:
            scene = context_value(self.context, "scene")
            objects = getattr(scene, "objects", None)
        if objects is None:
            raise Inconclusive("Object-count checks require an explicit complete bound-scene/staged overlay")
        if len(objects) > MAX_OBJECTS:
            raise Inconclusive("Scene exceeds the bounded object-count scan limit")
        return list(objects.values()) if isinstance(objects, dict) else list(objects)

    def materials(self, obj):
        slots = getattr(obj, "material_slots", ())
        if len(slots) > MAX_MATERIALS:
            raise Inconclusive("Material-slot scan limit exceeded")
        return [slot.material for slot in slots]

    def material_assignments(self, obj):
        key = id(obj)
        if key not in self.assignment_cache:
            mesh = self.mesh(obj)
            self.spend(mesh["face_count"])
            counts = {}
            for polygon in mesh["data"].polygons:
                index = polygon.material_index
                counts[index] = counts.get(index, 0) + 1
            self.assignment_cache[key] = counts
        return self.assignment_cache[key]

    def curves(self, obj):
        animation = getattr(obj, "animation_data", None)
        if animation is None or animation.action is None:
            return []
        if getattr(animation, "drivers", ()) or any(getattr(track, "strips", ()) for track in getattr(animation, "nla_tracks", ())):
            raise Inconclusive("Drivers and NLA animation output are not supported by direct-channel verification")
        if any(not getattr(c, "mute", False) and getattr(c, "influence", 1) != 0 for c in getattr(obj, "constraints", ())):
            raise Inconclusive("Constrained animation output requires a separately supported evaluator")
        action = animation.action
        curves = getattr(action, "fcurves", None)
        if curves is None:
            slot = getattr(animation, "action_slot", None)
            if slot is None:
                raise Inconclusive("Layered action has no explicitly assigned action slot")
            layers = getattr(action, "layers", ())
            if len(layers) != 1 or len(layers[0].strips) != 1:
                raise Inconclusive("Only a single direct keyframe action layer/strip is supported")
            strip = layers[0].strips[0]
            if getattr(strip, "type", "KEYFRAME") != "KEYFRAME":
                raise Inconclusive("Unsupported action-strip type")
            bag = strip.channelbag(slot, ensure=False)
            curves = bag.fcurves if bag is not None else []
        if len(curves) > MAX_ANIMATION_CURVES:
            raise Inconclusive("Animation curve count exceeds verification limits")
        total = sum(len(c.keyframe_points) for c in curves)
        if total > MAX_KEYFRAMES:
            raise Inconclusive("Animation keyframe count exceeds verification limits")
        self.spend(total)
        return list(curves)


def _matrix(rows):
    from mathutils import Matrix
    return Matrix(rows)

