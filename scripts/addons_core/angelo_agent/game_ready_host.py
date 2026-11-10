# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_game_ready_check`` on the host (``game_ready_spec``): the agent's and MCP apps' route.

Read-only: objects are read as arrays on the main thread (``game_ready_mesh.read``, with a snapshot of the source
mesh for Decimate), and everything else runs on a thread of its own: reductions made in memory and measured
(``game_ready_metrics``) with the native kernels of ``_bpy._angelo_reduce``, which release the GIL. The call returns a
``StudioPending`` that the controller polls, so Angelo stays responsive while a large mesh is measured; the facts are
of the meshes as they were when the call started. Nothing in the scene, its data or Undo changes. This module, the
native library and NumPy load only when the tool is called (``blender_tools`` imports it on the call), so a session
that never calls it pays nothing. doc/angelo/game_ready_check.rst is the contract.
"""

import threading
import time

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


def _hull(geometry, kernels):
    """The convex hull of a mesh's world-space points (Blender's convex_hull operator, without the GIL); None in a
    build without it (it needs Bullet, which the development preset leaves out)."""
    if not hasattr(kernels, "convex_hull"):
        return None
    made = kernels.convex_hull(np.ascontiguousarray(geometry.positions, dtype=np.float32))
    positions, tris = made if made is not None else (b"", b"")   # No faces (flat or too few points): an empty hull.
    return G.Geometry(np.frombuffer(positions, dtype=np.float32).reshape(-1, 3),
                      np.frombuffer(tris, dtype=np.int32).reshape(-1, 3).astype(np.uint32))


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


CHECK_SECONDS = 900.0   # A check's measuring at most, from its start; past it the call ends with a stated reason.
STATUS = "Checking a mesh for games"   # The live row's status (chat_titles.LIVE), the tool's own row title.


class _Stopped(Exception):
    """The call ended (Stop, its expiry) while it measured: nothing more is measured."""


class Taken:
    """What the main thread reads for a check: the objects' arrays and the source's snapshot. ``measure`` is the
    rest, on any thread."""

    def __init__(self, window, request):
        self.started = time.perf_counter()
        self.clock = _Clock()
        self.kernels = kernels = _reduce()
        self.request = request
        self.unit_scale, self.unit_note = _unit(window.scene)
        depsgraph = window.view_layer.depsgraph
        obj = _mesh_object(window, request["object"], "object")
        lod_objects = [_mesh_object(window, h, "each lod") for h in request["lods"]]
        collider_object = _mesh_object(window, request["collider"], "collider") if request["collider"] else None
        self.lod_names = [lod.name[:64] for lod in lod_objects]
        decimate = bool(request["simplify"]) and "DECIMATE" in request["simplify"]["methods"]
        started = time.perf_counter()
        try:
            self.source = M.read(obj, depsgraph, self.unit_scale, kernels, snapshot=decimate)
            self.lods = [M.read(lod, depsgraph, self.unit_scale, kernels) for lod in lod_objects]
            self.collider = (M.read(collider_object, depsgraph, self.unit_scale, kernels)
                             if collider_object else None)
        except ValueError as error:
            raise RefusalError(str(error), code="VALIDATION") from None
        self.clock.time("read", started)
        if not len(self.source.raw[1]):   # Its triangles' vertex indices: refused at once, as before any thread.
            raise RefusalError("object has no faces to measure", code="VALIDATION")

    def measure(self, stopped=lambda: False):
        try:
            return _measure(self, stopped)
        finally:
            self.source.release()   # The snapshot, as soon as nothing needs it, never at the process's exit.


def check(window, request):
    """The whole check in one call, on the main thread (the benchmark and the native suites' direct route)."""
    return Taken(window, request).measure()


def _measure(taken, stopped):
    request, kernels, clock = taken.request, taken.kernels, taken.clock

    def step():
        if stopped():
            raise _Stopped()

    started = time.perf_counter()
    for read in [taken.source] + taken.lods + ([taken.collider] if taken.collider else []):
        read.finish()
    clock.time("read", started)
    source_read = taken.source
    source = source_read.geometry
    if not source.triangle_count:
        raise RefusalError("object has no faces to measure", code="VALIDATION")
    low, high = source.bounds()
    diagonal = float(np.linalg.norm(high - low))
    started = time.perf_counter()
    samples = G.Samples(source)
    clock.time("measure", started)
    answer = {"success": True, "read_only": True, "scene_changed": False, "object": request["object"],
              "source": _source_facts(source_read, request, kernels, taken.unit_note)}
    distances = list(request["switch_distances"] or [])
    lods, geometries, refused = [], [], []
    for handle, name, lod_read in zip(request["lods"], taken.lod_names, taken.lods):
        step()
        distance = [distances.pop(0)] if request["switch_distances"] else None
        geometries.append(lod_read.geometry)
        lods.append(_lod_facts(name, "OBJECT", lod_read.geometry, source, samples, diagonal, request, clock,
                               kernels, {"object": handle, "geometry": lod_read.note, "_distances": distance}))
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
        decimated = {}
        if "DECIMATE" in simplify["methods"]:   # Every ratio first, so the snapshot is freed before measuring.
            for ratio in simplify["ratios"]:
                step()
                started = time.perf_counter()
                decimated[ratio] = M.decimate(source_read, ratio, kernels, taken.unit_scale)
                clock.time("decimate", started)
            source_read.release()
        for ratio in simplify["ratios"]:
            distance = [distances.pop(0)] if request["switch_distances"] else None
            if reducer is not None:
                step()
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
            if ratio in decimated:
                step()
                geometry, ms = decimated.pop(ratio)
                geometries.append(geometry)
                lods.append(_lod_facts(
                    "decimate %g" % ratio, "DECIMATE", geometry, source, samples, diagonal, request, clock,
                    kernels, {"ratio": ratio, "reduce_ms": round(ms, 1), "shares_lod0_vertices": False,
                              "vertices_used": int(len(np.unique(geometry.tris))),
                              "_distances": distance}))
    if geometries:
        step()
        started = time.perf_counter()
        for facts, silhouette in zip(lods, G.silhouettes(source, geometries, kernels)):
            facts["silhouette"] = silhouette
        clock.time("silhouette", started)
    answer["lods"] = lods
    if refused:
        answer["refused"] = refused
    if taken.collider:
        step()
        started = time.perf_counter()
        from .game_ready_collider import collider_fit
        answer["collider"] = dict(collider_fit(source, _hull(source, kernels), taken.collider.geometry, kernels,
                                               diagonal), object=request["collider"], geometry=taken.collider.note)
        clock.time("collider", started)
    clock.phases["total"] = round((time.perf_counter() - taken.started) * 1000.0, 1)
    answer["native_ms"] = clock.phases
    answer["measured"] = G.glossary(request["resolution"], request["fov_degrees"], request["texture_size"])
    return answer


def _meshopt_status(kernels):
    status = kernels.status()
    out = {"available": status["available"]}
    if not status["available"]:
        out["reason"] = status["reason"]
    return out


def _classified(error):
    """A failure as the refusal the agent reads: the same codes whether it was raised at once or while measuring."""
    if isinstance(error, RefusalError):
        return error
    if isinstance(error, ValueError):
        return RefusalError(str(error), code="VALIDATION")
    if isinstance(error, RuntimeError) and "meshoptimizer" in str(error):   # The native module's refusal: a library
        return RefusalError(str(error), code="UNSUPPORTED")                    # missing or too old.
    return None


def execute(window, name, request):
    """The check's answer: a ``StudioPending`` while it measures off the main thread, after the objects were read
    here (a refusal before that is raised at once)."""
    try:
        taken = Taken(window, request)
    except Exception as error:   # noqa: BLE001 - classified, or raised as it is.
        refusal = _classified(error)
        if refusal is None:
            raise
        raise refusal from None
    return _pending(taken)


def _pending(taken):
    from .refusal_codes import error_result
    from .studio_agent import StudioPending
    from . import thread_start
    stop, done, outcome = threading.Event(), threading.Event(), {}

    def run():
        try:
            outcome["result"] = taken.measure(stop.is_set)
        except _Stopped:
            pass
        except BaseException as error:   # noqa: BLE001 - delivered as the call's result by ``poll``.
            outcome["error"] = error
        finally:
            done.set()

    def poll():
        if not done.is_set():
            return None
        if "error" in outcome:
            error = outcome["error"]
            return error_result(_classified(error) or error)
        return outcome.get("result")

    thread_start.daemon("Angelo game-ready check", run)
    pending = StudioPending(poll, stop.set)
    pending.timeout, pending.status_text = CHECK_SECONDS, STATUS
    pending.activity_note = "The meshes were read; they are measured in the background, and nothing changes."
    pending.stopped_result = error_result(RefusalError(
        "The game-ready check did not finish in %d minutes; nothing was changed. Check fewer LODs or ratios, or a "
        "smaller mesh" % (CHECK_SECONDS // 60), code="LIMIT_EXCEEDED"), scene_changed=False)
    return pending
