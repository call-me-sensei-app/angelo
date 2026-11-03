# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_game_ready_check`` on the host (``game_ready_spec``): the agent's and MCP apps' route. Main thread.

Read-only: objects are read as arrays (``game_ready_mesh``), reductions are made in memory and measured
(``game_ready_metrics``) with the native kernels of ``_bpy._angelo_reduce``, and nothing in the scene, its data or
Undo changes. This module, the native library and NumPy load only when the tool is called (``blender_tools``
imports it on the call), so a session that never calls it pays nothing. doc/angelo/game_ready_check.rst is the
contract.
"""

import time

import bmesh
import bpy
import numpy as np

from . import game_ready_mesh as M
from . import game_ready_metrics as G
from .refusal_codes import RefusalError


def _reduce():
    from _bpy import _angelo_reduce
    return _angelo_reduce


def _unit(scene):
    units = scene.unit_settings
    if units.system == "NONE":
        return 1.0, "Blender units (the scene has no unit system)"
    return float(units.scale_length), "meters (the scene's unit scale applied)"


def _mesh_object(window, handle, what):
    from . import scene_queries
    obj = scene_queries.resolve_object(window, handle)
    if obj.type != "MESH":
        raise RefusalError("%s must be a mesh object" % what, code="VALIDATION")
    return obj


def _hull(read, unit_scale):
    """The convex hull of a read mesh in world space, made in a BMesh never linked to any data-block; None in a build
    without it (``bmesh.ops.convex_hull`` needs Bullet, which the development preset leaves out)."""
    from mathutils import Matrix
    if not hasattr(bmesh.ops, "convex_hull"):
        return None
    bm = bmesh.new()
    try:
        bm.from_mesh(read.mesh)
        bmesh.ops.transform(bm, matrix=Matrix.Diagonal((unit_scale,) * 3 + (1.0,)) @ read.matrix, verts=bm.verts[:])
        result = bmesh.ops.convex_hull(bm, input=bm.verts[:])
        faces = [f for f in result["geom"] if isinstance(f, bmesh.types.BMFace)]
        index, positions, tris = {}, [], []
        for face in faces:
            corners = [index.setdefault(v, len(index)) for v in face.verts]
            for k in range(1, len(corners) - 1):   # The hull's faces are triangles; fan any that are not.
                tris.append((corners[0], corners[k], corners[k + 1]))
        positions = [None] * len(index)
        for vert, i in index.items():
            positions[i] = vert.co[:]
    finally:
        bm.free()
    return G.Geometry(np.asarray(positions, dtype=np.float32).reshape(-1, 3), np.asarray(tris, dtype=np.uint32))


class _Clock:
    def __init__(self):
        self.phases = {}

    def time(self, phase, started):
        self.add(phase, (time.perf_counter() - started) * 1000.0)

    def add(self, phase, ms):
        self.phases[phase] = round(self.phases.get(phase, 0.0) + ms, 1)


def _lod_facts(name, method, geometry, source, samples, diagonal, request, clock, kernels, extra):
    started = time.perf_counter()
    distances = extra.pop("_distances", None)
    if not geometry.triangle_count:   # A reduction to nothing, or a LOD object without faces.
        facts = {"name": name, "method": method, "tris": 0, "empty": "no triangles: nothing to measure"}
        facts.update(extra)
        return facts
    low, high = geometry.bounds()
    source_low, source_high = source.bounds()
    comparison = G.Comparison(source, samples, geometry, kernels)
    hausdorff, worst = comparison.hausdorff(diagonal)
    facts = {"name": name, "method": method, "tris": geometry.triangle_count,
             "of_source_tris": G._round(geometry.triangle_count / max(source.triangle_count, 1)),
             "hausdorff": hausdorff}
    facts.update(extra)
    if (low > source_high).any() or (high < source_low).any():
        facts["apart"] = True
    facts["pixel_error"] = G.pixel_error(worst, distances, request["resolution"], request["fov_degrees"])
    facts.update(comparison.attributes())
    uv = G.uv_facts(geometry, request["texture_size"], kernels)
    if uv is not None:
        facts["uv"] = uv
    facts["topology"] = G.topology(geometry)
    clock.time("measure", started)
    return facts


def _source_facts(read, request, kernels, unit_note):
    geometry = read.geometry
    low, high = geometry.bounds()
    facts = {"tris": geometry.triangle_count, "vertices": int(len(np.unique(geometry.tris))),
             "bounds_m": [[G._round(v) for v in low], [G._round(v) for v in high]],
             "bounds_diagonal_m": G._round(float(np.linalg.norm(high - low))),
             "geometry": read.note, "space": "world space in " + unit_note}
    uv = G.uv_facts(geometry, request["texture_size"], kernels)
    facts["uv"] = uv if uv is not None else "no UV map"
    facts["topology"] = G.topology(geometry)
    if read.groups:
        facts["skin"] = {"weighted_groups": len(read.groups)}
    if getattr(geometry, "modifiers_not_applied", 0):
        facts["modifiers_not_applied"] = geometry.modifiers_not_applied
    return facts


def check(window, request):
    total = time.perf_counter()
    clock = _Clock()
    kernels = _reduce()
    scene = window.scene
    unit_scale, unit_note = _unit(scene)
    depsgraph = window.view_layer.depsgraph
    obj = _mesh_object(window, request["object"], "object")
    lod_objects = [_mesh_object(window, h, "each lod") for h in request["lods"]]
    collider_object = _mesh_object(window, request["collider"], "collider") if request["collider"] else None
    reads = []
    try:
        started = time.perf_counter()
        try:
            source_read = M.read(obj, depsgraph, unit_scale, kernels)
            reads.append(source_read)
            lod_reads = []
            for lod in lod_objects:
                lod_reads.append(M.read(lod, depsgraph, unit_scale, kernels))
                reads.append(lod_reads[-1])
            collider_read = M.read(collider_object, depsgraph, unit_scale, kernels) if collider_object else None
            if collider_read:
                reads.append(collider_read)
        except ValueError as error:
            raise RefusalError(str(error), code="VALIDATION") from None
        clock.time("read", started)
        source = source_read.geometry
        if not source.triangle_count:
            raise RefusalError("object has no faces to measure", code="VALIDATION")
        low, high = source.bounds()
        diagonal = float(np.linalg.norm(high - low))
        started = time.perf_counter()
        samples = G.Samples(source)
        clock.time("measure", started)
        answer = {"success": True, "read_only": True, "scene_changed": False, "object": request["object"],
                  "source": _source_facts(source_read, request, kernels, unit_note)}
        distances = list(request["switch_distances"] or [])
        lods, geometries, refused = [], [], []
        for handle, lod_read in zip(request["lods"], lod_reads):
            distance = [distances.pop(0)] if request["switch_distances"] else None
            geometries.append(lod_read.geometry)
            lods.append(_lod_facts(lod_objects[len(lods)].name[:64],
                                   "OBJECT", lod_read.geometry, source, samples, diagonal, request, clock, kernels,
                                   {"object": handle, "geometry": lod_read.note, "_distances": distance}))
        simplify = request["simplify"]
        if simplify:
            answer["meshoptimizer"] = _meshopt_status(kernels)
            reducer = None
            if "MESHOPT" in simplify["methods"] and answer["meshoptimizer"]["available"]:
                started = time.perf_counter()
                reducer = M.Reducer(source_read, kernels, simplify["uv_weight"], simplify["normal_weight"],
                                    simplify["skin_weight"])
                clock.time("weld", started)
                reason = reducer.refusal()
                if reason:
                    refused.append({"method": "MESHOPT", "reason": reason})
                    reducer = None
                else:
                    answer["lod0_vertex_buffer"] = int(len(reducer.positions))
            elif "MESHOPT" in simplify["methods"]:
                refused.append({"method": "MESHOPT", "reason": answer["meshoptimizer"]["reason"]})
            for ratio in simplify["ratios"]:
                distance = [distances.pop(0)] if request["switch_distances"] else None
                if reducer is not None:
                    indices, error, ms = reducer.simplify(ratio, simplify["max_error"], simplify["lock_border"])
                    clock.add("meshopt", ms)
                    geometry = reducer.geometry(indices)
                    geometries.append(geometry)
                    lods.append(_lod_facts(
                        "meshopt %g" % ratio, "MESHOPT", geometry, source, samples, diagonal, request, clock, kernels,
                        {"ratio": ratio, "reduce_ms": round(ms, 1), "shares_lod0_vertices": True,
                         "vertices_used": int(len(np.unique(indices))),
                         "meshopt_error": G._round(error),
                         "_distances": distance}))
                if "DECIMATE" in simplify["methods"]:
                    started = time.perf_counter()
                    geometry, ms = M.decimate(source_read, ratio, kernels, unit_scale)
                    clock.time("decimate", started)
                    geometries.append(geometry)
                    lods.append(_lod_facts(
                        "decimate %g" % ratio, "DECIMATE", geometry, source, samples, diagonal, request, clock,
                        kernels, {"ratio": ratio, "reduce_ms": round(ms, 1), "shares_lod0_vertices": False,
                                  "vertices_used": int(len(np.unique(geometry.tris))),
                                  "_distances": distance}))
        if geometries:
            started = time.perf_counter()
            for facts, silhouette in zip(lods, G.silhouettes(source, geometries, kernels)):
                facts["silhouette"] = silhouette
            clock.time("silhouette", started)
        answer["lods"] = lods
        if refused:
            answer["refused"] = refused
        if collider_read:
            started = time.perf_counter()
            hull = _hull(source_read, unit_scale)
            from .game_ready_collider import collider_fit
            answer["collider"] = dict(collider_fit(source, hull, collider_read.geometry, kernels, diagonal),
                                      object=request["collider"], geometry=collider_read.note)
            clock.time("collider", started)
    finally:
        for read in reads:
            read.release()
    clock.phases["total"] = round((time.perf_counter() - total) * 1000.0, 1)
    answer["native_ms"] = clock.phases
    answer["measured"] = G.glossary(request["resolution"], request["fov_degrees"], request["texture_size"])
    return answer


def _meshopt_status(kernels):
    status = kernels.status()
    out = {"available": status["available"]}
    if not status["available"]:
        out["reason"] = status["reason"]
    return out


def execute(window, name, request):
    try:
        return check(window, request)
    except RefusalError:
        raise
    except ValueError as error:
        raise RefusalError(str(error), code="VALIDATION") from None
    except RuntimeError as error:
        if "meshoptimizer" in str(error):   # The native module's refusal: a library missing or too old.
            raise RefusalError(str(error), code="UNSUPPORTED") from None
        raise
