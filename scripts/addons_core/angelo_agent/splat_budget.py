# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A splat collision mesh within its triangle budget with its surface in place (``splat_mesh``, step 3).

Decimate Collapse (quadric error) thins the surface OpenVDB made, kept only when it fits, stays closed, does not pass
through itself and lies no further off the splats than that surface beyond sampling noise. When it can't, the voxel
grows, every splat widened with it, which moves every surface outward; so the search refines toward finer voxels
while each kept result lies measurably closer to the splats, and always ends within the budget. Standard
library, NumPy and Blender's own mesh tools.
"""

import math
import time

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

from . import splat_measure

TEMP = "angelo_splat_mesh_temp"   # The name of every temporary data-block the splat tools make.


def triangles(mesh):
    if not len(mesh.polygons):
        return 0
    sizes = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", sizes)
    return int((sizes - 2).sum())


def _closed(mesh):
    """Every edge has exactly two faces (no hole, no fin): what a collision mesh must stay through every reduction.
    Counted over the face corners' edges, without building a BMesh."""
    if not len(mesh.edges):
        return False
    edges = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("edge_index", edges)
    return bool(np.all(np.bincount(edges, minlength=len(mesh.edges)) == 2))


def _crossings(mesh, voxel):
    """Pairs of triangles that intersect: a surface passing through itself, which a closed mesh can still do
    (quadric collapse can pull a thin shell's two sheets through each other where its triangles grow longer than the
    shell is thick), and which a solid test by rays then misreads. Blender's overlap test skips triangles sharing an
    edge, and with a tolerance also a pair sharing one vertex that only touch there; a pair sharing a vertex whose
    faces pass through each other still counts (the review of 02ea9dd4782, SHOULD-2). The tolerance is float noise,
    not a size of the shape: a thousandth of the voxel, or a millionth of the mesh's extent if that is larger.
    Coplanar overlaps are not seen (``isect_tri_tri_v3`` doesn't test them); in float they don't occur."""
    mesh.calc_loop_triangles()
    if not len(mesh.loop_triangles):
        return 0
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(-1, 3)
    triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", triangles)
    extent = float(np.linalg.norm(coords.max(axis=0) - coords.min(axis=0)))
    tolerance = max(1e-3 * voxel, 1e-6 * extent)
    tree = BVHTree.FromPolygons(coords.tolist(), triangles.reshape(-1, 3).tolist(), all_triangles=True,
                                epsilon=tolerance * tolerance)   # Compared with the squared crossing length.
    pairs = tree.overlap(tree)
    if not pairs:
        return 0
    pairs = np.sort(np.array(pairs, dtype=np.int64), axis=1)   # Each pair counted once, whichever order it came in.
    return int(len(np.unique(pairs, axis=0)))


def _collapse(mesh, max_triangles):
    """A new mesh: ``mesh`` thinned to ``max_triangles`` by Blender's Decimate Collapse (quadric error, so the
    surface stays in place), on a temporary object; ``mesh`` is left as it was."""
    holder = bpy.data.objects.new(TEMP, mesh)
    bpy.context.scene.collection.objects.link(holder)
    modifier = holder.modifiers.new(TEMP, 'DECIMATE')
    modifier.decimate_type = 'COLLAPSE'
    modifier.ratio = min(1.0, 0.95 * max_triangles / max(1, triangles(mesh)))   # A margin for its rounding.
    modifier.use_collapse_triangulate = True
    try:
        return bpy.data.meshes.new_from_object(holder.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    finally:
        bpy.data.objects.remove(holder)


def _distances(mesh, samples):
    """Distance from each of ``samples`` (splat centres) to ``mesh``: how closely a surface lies on the capture."""
    if samples is None or not len(samples) or not len(mesh.polygons):
        return np.zeros(1)
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    mesh.calc_loop_triangles()   # Read through NumPy: a Python loop over a first voxel's million faces took seconds.
    corners = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", corners)
    tree = BVHTree.FromPolygons(coords.reshape(-1, 3).tolist(), corners.reshape(-1, 3).tolist(), all_triangles=True)
    return np.array([tree.find_nearest(p)[3] for p in samples])


def _fitted(mesh, max_triangles, step, voxel, judge):
    """``(result, fewest)``: ``mesh`` within ``max_triangles`` with its surface where it is, or None, and the fewest
    triangles the collapse reached. ``mesh`` is freed when a result is returned and left intact on None. Decimate
    Collapse is kept when it fits, stays closed, does not pass through itself and ``judge(result, step)`` finds it
    keeps the capture (``fit``'s coverage gate). ``step`` records what it did and, when it fit but was refused, why
    ("open", "crossing" or "off")."""
    thinner = _collapse(mesh, max_triangles)
    try:
        count = step["collapse"] = triangles(thinner)
        if count and count <= max_triangles:
            if not _closed(thinner):
                step["refused"] = "open"
            else:
                step["crossings"] = _crossings(thinner, voxel)
                if step["crossings"]:
                    step["refused"] = "crossing"
                elif not judge(thinner, step):
                    step["refused"] = "off"
                else:
                    bpy.data.meshes.remove(mesh)
                    result, thinner = thinner, None
                    return result, count
        return None, max(count, 1)
    finally:
        if thinner is not None:
            bpy.data.meshes.remove(thinner)


PASSES = 12   # Voxels tried before the search; each is at least 1.1 x coarser, so the loop always ends.
REFINE = 4    # Halvings, in log space, between a refused voxel and one kept: the bracket ends ~1/16 of its log width.


def _grown(voxel, count, max_triangles):
    """The voxel whose surface should hold ``max_triangles`` when this voxel's held ``count`` triangles (the count
    falls with the square of the voxel), with a 10% margin, and at least 10% coarser."""
    return voxel * max(1.1, math.sqrt(count / max_triangles) * 1.1)


def fit(surface_at, voxel, max_triangles, steps, samples=None, sample_radius=None, sheet=1.0, resolution=0.0):
    """``(mesh, voxel, reduction, step)``: the collision surface (``surface_at(voxel)`` makes one) within
    ``max_triangles`` where it fits as made or a collapse of it is kept (``_fitted``), and the step that made it;
    ``steps`` gets what each voxel tried.

    Every kept result must keep the capture (``splat_measure``'s coverage): of the splat centres ``samples`` (radii
    ``sample_radius``), no more lie beyond their own bound, max(r, sheet * voxel) + voxel / 2, than lie beyond it on
    the first voxel's surface as made, correct by construction, beyond binomial noise; a result that lost a wall is
    refused ("off") whether a collapse lost it or the surface it came from already had.

    A collapse that stops short grows the voxel from the count it reached (growing from the surface as made widened
    every splat further than needed: the review of c8a2cabdc1c). A refused result (open, crossing itself, or off the
    capture) marks a voxel too fine: the voxel then grows from the surface as made, never past the raw-count rule's
    voxel from the first step, and the voxels between are searched by halving (the review of 02ea9dd4782, MUST-1:
    growing 1.1 x at a time could run out of passes over budget). A finer kept result replaces the one found only
    when it lies closer to the splats beyond sampling noise (mean distance); the first that doesn't ends the search,
    since finer voxels cost more and measurably fit no better. Refining also stops once the result lies within the
    capture's own resolution (``resolution``, the median splat radius): a fit tighter than the splats themselves
    can define is not detail the capture supports."""
    reference = {}
    total = 0 if samples is None else len(samples)

    def judge(result, step):
        """Measure ``result``'s distances to the samples into ``step``; True when it keeps the capture."""
        distances = _distances(result, samples)
        step["_distances"] = distances
        if not total:
            return True
        allowed = np.maximum(sample_radius, sheet * step["voxel"]) + step["voxel"] / 2
        step["beyond"] = splat_measure.exceedance(distances, allowed)
        return not splat_measure.exceeds_reference(step["beyond"], reference["beyond"], total)

    def attempt(v):
        began = time.perf_counter()
        mesh = surface_at(v)
        step = {"voxel": v, "triangles": triangles(mesh)}
        steps.append(step)
        try:
            first = not reference
            if first:   # The first voxel's surface as made: the coverage every result is held to.
                distances = _distances(mesh, samples)
                allowed = (np.maximum(sample_radius, sheet * v) + v / 2) if total else distances
                reference["beyond"] = splat_measure.exceedance(distances, allowed) if total else 0
                step["reference_beyond"] = reference["beyond"]
            if step["triangles"] <= max_triangles or not len(mesh.polygons):
                if first:   # It is the reference itself: judged already.
                    step["_distances"], step["beyond"] = distances, reference["beyond"]
                elif len(mesh.polygons) and not judge(mesh, step):
                    step["refused"] = "off"
                    bpy.data.meshes.remove(mesh)
                    return None, step
                step["kept"] = "as made"
                return mesh, step
            fitted, step["fewest"] = _fitted(mesh, max_triangles, step, v, judge)
        except BaseException:
            bpy.data.meshes.remove(mesh)
            raise
        finally:
            step["seconds"] = round(time.perf_counter() - began, 3)
        if fitted is not None:
            step["kept"] = "collapse"
            return fitted, step
        bpy.data.meshes.remove(mesh)
        return None, step

    refused, found, cap = None, None, None
    for _ in range(PASSES + 64):   # Past PASSES, only from the surface as made (never reached in practice).
        mesh, step = attempt(voxel)
        if mesh is not None:
            found = (mesh, voxel, step)
            break
        if cap is None:   # The raw-count rule from the first step: growth never ends coarser (SHOULD-2).
            cap = _grown(voxel, step["triangles"], max_triangles)
        late = len(steps) > PASSES
        if "refused" in step:
            refused = voxel
        grown = _grown(voxel, step["triangles"] if "refused" in step or late else step["fewest"], max_triangles)
        voxel = min(grown, cap) if "refused" in step and cap > voxel and not late else grown
    if found is None:   # Unreachable in practice: the surface as made, judged, so nothing ships unjudged.
        mesh = surface_at(voxel)
        step = {"voxel": voxel, "triangles": triangles(mesh), "kept": "last resort"}
        steps.append(step)
        if len(mesh.polygons) and not judge(mesh, step):
            step["coverage"] = False   # The report says so and the operator warns.
        found = (mesh, voxel, step)
    mesh, best, step = found
    if refused is not None:
        low, high = refused, best
        try:
            near = step.get("_distances", np.zeros(1))
            for _ in range(REFINE):
                if float(np.mean(near)) <= resolution:   # Within the capture's own resolution: nothing finer to fit.
                    step["stopped"] = "within the splats' resolution"
                    break
                middle = math.sqrt(low * high)
                candidate, trial = attempt(middle)
                if candidate is None:
                    low = middle
                    continue
                high = middle
                there = trial.get("_distances", np.zeros(1))
                # A finer candidate wins only when it lies closer to the splats beyond sampling noise (mean distance,
                # its standard error); when it doesn't, finer voxels would cost more for no measurable fit, so the
                # search stops there (the coordinator's ruling, 2026-11-21: diminishing returns end it).
                if not splat_measure.within_noise(near, there, "mean"):
                    bpy.data.meshes.remove(mesh)
                    mesh, best, step, near = candidate, middle, trial, there
                else:
                    bpy.data.meshes.remove(candidate)
                    trial["stopped"] = "no closer"
                    break
        except BaseException:
            bpy.data.meshes.remove(mesh)
            raise
    for entry in steps:   # Distances were for judging; the report keeps their means.
        distances = entry.pop("_distances", None)
        if distances is not None and len(distances) > 1:
            entry["distance"] = round(float(np.mean(distances)), 6)
    if len(steps) == 1:
        reduction = "none" if step["kept"] == "as made" else "collapse"
    else:
        reduction = "voxel"
    return mesh, best, reduction, step
