# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Gaussian splats become a clean collision mesh and a coloured visual mesh, and export back to PLY, in a real
background Blender.

Blender 5.3 imports Gaussian splats from PLY as a point cloud (``io/ply/importer/ply_import_gsplat.cc``: attributes
``radiance:base`` with the decoded opacity in ``w``, ``scale`` already exponentiated, ``rotation``) and renders them,
but gives nothing a game engine, a physics engine or a modeller can use, and cannot write them back (Blender
#159470). The Blender 5.3 launch thread asked for clean collision geometry from splats (the Blender 5.3 launch
feedback notes of 2026-11-14, item 6; the 2.1.0 release plan, section 4.3).

The contract, checked on every shape below, each imported through Blender's own PLY importer from a file this suite
writes in the standard 3D Gaussian Splatting layout:

* ``angelo.splat_to_mesh`` with ``kind='COLLISION'`` makes a closed mesh (no non-manifold edge) within the
  requested triangle budget, that lies on the captured surface (true surface samples are close to it, relative to
  the shape's size) and leaves out floaters: sparse, faint splats away from the surface get no geometry; it carries
  convex-hull colliders (``collision_fit``, the module the game-ready job shares) within the hull budget, so every
  engine gets collision, not only Godot through OMI; the collision mesh itself is concave static collision
  (Game Physics Mesh shape, Collision Only), which each engine's export writes in its own form;
* ``kind='VISUAL'`` makes a mesh with a colour attribute whose mean matches the splats' own colour (Blender keeps the
  raw DC coefficient in ``radiance:base``; the colour is 0.5 + 0.2821 x that);
* the source point cloud is left as it was; the result reports kept and dropped splats;
* ``angelo.export_splat_ply`` writes the point cloud back so that Blender's importer reads the same positions,
  opacities, scales and colours (a round trip).

The shapes differ in kind, so nothing is tuned to one: a closed sphere seen from outside, a room seen from inside
(walls, floor and ceiling as open surfaces), a torus (a hole), two separate objects with empty space between them,
and four held out while the budget path was fixed: a box with sharp edges, a capped cylinder, a sphere with 1%
surface noise and an L-shaped corridor seen from inside. Floaters are added to each (2% of the splats, faint, spread
through a box twice the shape's size). A collision mesh must also lie on the surface (mesh to surface, 95th
percentile under 2% of the diagonal) and never pass through itself. Each shape records how it met the budget
(``splat_mesh.build``'s ``reduction``); collapse and a coarser voxel must each run on some case, the latter on a
room and a cylinder whose collapse is made to stop short, where the next voxel must be grown from the fewest
triangles the collapse reached (judged from the voxels). A ~20 m floor plan of six rooms with 0.15 m interior walls is
a shape too, and at a million splats a collision case. A collision mesh's offset (mesh to surface, 95th percentile of
points spread by area) must be under 2% of the diagonal wherever the hole-free surface at the splats' own radius,
built here from the same cloud, is (a sparse capture's shell sits about a splat radius off thin walls, which no
budget path can undo); every result must pass the product's coverage gate against that surface (no lost feature);
a grown result must never sit at a voxel coarser than the raw-count rule's. The offset and its reference are
recorded for every shape. The million-splat floor plan runs at the budget derived for 2%
(``splat_mesh_cases.budget_for_two_percent``). ``splat_mesh_cases`` adds the two detectors on meshes built with
known answers; a closed room meshed at eight placements of the voxel grid, which must keep every wall (and lose one
at the sqrt(3)/2 the module used before); the coverage gate refusing a room with a wall deleted and accepting a cloud
with splats inside a solid; a kept collapse refused on purpose (as open, and as crossing itself) so each refusal path
runs and the search still ends within the budget; and a box and the corridor at 1,000 and 100 triangles. Time per
million splats is recorded, not asserted.

Run through ``run_native_suites.py --include test_blender_splat_mesh``; check ``splat-mesh.json`` for ``success``.
Expected to fail on 2.0.0 (06259fa5bb0): neither operator exists there.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-splat-mesh-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import splat_budget, splat_measure, splat_mesh, ui  # noqa: E402  (bundled add-on path)
import splat_mesh_cases as cases  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False

REPORT = OUT / "splat-mesh.json"
RNG = np.random.default_rng(20261114)
SH_C0 = 0.28209479177387814   # The DC spherical-harmonic constant: colour = 0.5 + SH_C0 * f_dc.
BUDGET = 5000
HULLS = 16
state = {"success": False, "checks": {}, "shapes": {}, "timing": {}}
reductions = set()   # How each collision mesh met its budget (splat_mesh.build's report).


def check(name, value):
    state["checks"][name] = bool(value)


# Surface samples of each shape: (points (n, 3), outward normals (n, 3)). Sizes in metres.
def sphere(n):
    v = RNG.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v * 1.0, v


def room(n):
    size = np.array([4.0, 3.0, 2.5])
    faces = RNG.integers(0, 6, n)
    p = (RNG.random((n, 3)) - 0.5) * size
    normals = np.zeros((n, 3))
    for face in range(6):
        axis, sign = face // 2, (1 if face % 2 else -1)
        mask = faces == face
        p[mask, axis] = sign * size[axis] / 2
        normals[mask, axis] = -sign   # Seen from inside.
    return p, normals


def torus(n):
    big, small = 1.5, 0.4
    u, v = RNG.random(n) * 2 * math.pi, RNG.random(n) * 2 * math.pi
    centre = np.stack([np.cos(u) * big, np.sin(u) * big, np.zeros(n)], axis=1)
    normal = np.stack([np.cos(u) * np.cos(v), np.sin(u) * np.cos(v), np.sin(v)], axis=1)
    return centre + small * normal, normal


def two_objects(n):
    half = n // 2
    a, na = sphere(half)
    b, nb = sphere(n - half)
    return np.concatenate([a * 0.6 + [-2, 0, 0], b * 0.6 + [2, 0, 0]]), np.concatenate([na, nb])


def cube(n):
    """A 1.2 m box seen from outside: six flat faces with sharp edges."""
    face = RNG.integers(0, 6, n)
    p = RNG.random((n, 3)) - 0.5
    for index in range(6):
        p[face == index, index // 2] = 0.5 if index % 2 else -0.5
    return p * 1.2, None


def cylinder(n):
    """A capped cylinder: a curved side and two flat caps."""
    side = int(n * 0.7)
    u, z = RNG.random(side) * 2 * math.pi, (RNG.random(side) - 0.5) * 2
    caps = n - side
    r, a = np.sqrt(RNG.random(caps)), RNG.random(caps) * 2 * math.pi
    top = np.where(RNG.random(caps) < 0.5, -1.0, 1.0)
    side_points = np.stack([np.cos(u), np.sin(u), z], 1)
    return np.concatenate([side_points, np.stack([r * np.cos(a), r * np.sin(a), top], 1)]), None


def noisy_sphere(n):
    """A sphere whose splats lie off the surface by 1% (a capture's noise)."""
    v, _ = sphere(n)
    return v * (1 + RNG.normal(scale=0.01, size=(n, 1))), None


def corridor(n):
    """An L of two 6 m x 2 m x 2.5 m halls seen from inside: floors, ceilings and two outer walls as sheets."""
    k = n // 6
    x, y = RNG.random(k) * 6, RNG.random(k) * 2
    x2, y2 = RNG.random(k) * 2, 2 + RNG.random(k) * 4
    parts = [np.stack([x, y, np.zeros(k)], 1), np.stack([x, y, np.full(k, 2.5)], 1),
             np.stack([x2, y2, np.zeros(k)], 1), np.stack([x2, y2, np.full(k, 2.5)], 1),
             np.stack([RNG.random(k) * 6, np.zeros(k), RNG.random(k) * 2.5], 1),
             np.stack([np.zeros(k), RNG.random(k) * 6, RNG.random(k) * 2.5], 1)]
    return np.concatenate(parts), None


# The first four were the suite's own; the last four were held out while fixing the budget path (2026-11-20) and
# are kept so every reduction runs on shapes nothing was tuned to.
SHAPES = {"sphere": (sphere, (0.9, 0.2, 0.15)), "room": (room, (0.7, 0.7, 0.65)),
          "torus": (torus, (0.2, 0.5, 0.9)), "two_objects": (two_objects, (0.3, 0.8, 0.3)),
          "cube": (cube, (0.5, 0.5, 0.5)), "cylinder": (cylinder, (0.6, 0.4, 0.2)),
          "noisy_sphere": (noisy_sphere, (0.9, 0.2, 0.15)), "corridor": (corridor, (0.7, 0.7, 0.65)),
          # Flat architecture at scale, held out while 02ea9dd4782 was reviewed: six rooms, thin interior walls.
          "floor_plan": (lambda n: cases.floor_plan(n, RNG), (0.75, 0.72, 0.68))}
# Collision only, at a capture's density: the floor plan at a million splats, held to the 2% offset.
DENSE = {"floor_plan_1m": (lambda n: cases.floor_plan(n, RNG), (0.75, 0.72, 0.68))}


def write_splat_ply(path, positions, colour, opacity, scale):
    """A binary PLY in the 3D Gaussian Splatting layout: activations stored as the trainers store them (opacity as a
    logit, scale as a log), colour as the DC spherical-harmonic coefficient, rotation as a unit quaternion."""
    n = len(positions)
    f_dc = (np.asarray(colour, dtype=np.float32) - 0.5) / SH_C0
    fields = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
              "rot_0", "rot_1", "rot_2", "rot_3"]
    data = np.zeros((n, len(fields)), dtype=np.float32)
    data[:, 0:3] = positions
    data[:, 3:6] = f_dc
    data[:, 6] = np.log(opacity / (1 - opacity))
    data[:, 7:10] = np.log(scale)
    data[:, 10] = 1.0
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n
    header += "".join("property float %s\n" % f for f in fields) + "end_header\n"
    with open(path, "wb") as stream:
        stream.write(header.encode("ascii"))
        stream.write(data.astype("<f4").tobytes())


def make_cloud(name, n, surface=None):
    """Surface splats (opaque, small) plus 2% floaters (faint, spread through a box twice the shape's size); the shape
    named, or the points ``surface`` given."""
    if surface is None:
        shape, colour = SHAPES[name] if name in SHAPES else DENSE[name]
        surface, _ = shape(n)
    else:
        colour, n = (0.6, 0.6, 0.6), len(surface)
    lo, hi = surface.min(axis=0), surface.max(axis=0)
    centre, extent = (lo + hi) / 2, (hi - lo)
    floaters = centre + (RNG.random((n // 50, 3)) - 0.5) * extent * 2
    positions = np.concatenate([surface, floaters])
    opacity = np.concatenate([RNG.uniform(0.7, 0.99, n), RNG.uniform(0.02, 0.25, len(floaters))])
    spacing = float(np.linalg.norm(extent)) / math.sqrt(n) * 2
    scale = np.full((len(positions), 3), spacing)
    scale[:, 2] *= 0.2   # Flat splats, as trained splats on a surface are.
    colours = np.tile(colour, (len(positions), 1)) + RNG.normal(scale=0.03, size=(len(positions), 3))
    path = OUT / ("%s.ply" % name)
    write_splat_ply(path, positions, np.clip(colours, 0, 1), opacity, scale)
    return path, surface, floaters, float(np.linalg.norm(extent))


def read_attribute(attribute, width):
    """An attribute's values as (n, width), whichever per-item field its type uses (a 4-wide float attribute may read
    as ``color`` or ``vector``)."""
    flat = np.empty(len(attribute.data) * width, dtype=np.float32)
    for field in ("vector", "color", "value"):
        try:
            attribute.data.foreach_get(field, flat)
            return flat.reshape(-1, width)
        except (AttributeError, TypeError, RuntimeError):
            continue
    raise TypeError("cannot read %s (%s)" % (attribute.name, attribute.data_type))


def import_ply(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.ply_import(filepath=str(path))
    (cloud,) = set(bpy.data.objects) - before
    return cloud


def mesh_arrays(obj):
    mesh = obj.data
    verts = np.array([v.co for v in mesh.vertices]) @ np.array(obj.matrix_world.to_3x3()).T + np.array(
        obj.matrix_world.translation)
    return mesh, verts


def non_manifold_edges(mesh):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    count = sum(1 for edge in bm.edges if not edge.is_manifold)
    bm.free()
    return count


def surface_distance(obj, points):
    """Mean distance from true surface samples to the mesh."""
    tree = BVHTree.FromObject(obj, bpy.context.evaluated_depsgraph_get())
    world_to_local = obj.matrix_world.inverted()
    distances = []
    for p in points[:: max(1, len(points) // 2000)]:
        hit = tree.find_nearest(world_to_local @ Vector(p))
        distances.append(hit[3] if hit[0] is not None else math.inf)
    return float(np.mean(distances))


def nearest_vertex_distance(verts, points):
    out = []
    for p in points:
        out.append(float(np.min(np.linalg.norm(verts - p, axis=1))))
    return np.array(out)


def run_kind(cloud, kind, budget=BUDGET):
    before = set(bpy.data.objects)
    with bpy.context.temp_override(object=cloud, active_object=cloud, selected_objects=[cloud]):
        result = bpy.ops.angelo.splat_to_mesh(kind=kind, max_triangles=budget, max_hulls=HULLS)
    made = sorted((o for o in set(bpy.data.objects) - before if o.parent is None), key=lambda o: o.name)
    return result, made


def shape_case(name, n=60000, visual=True, budget=BUDGET):
    path, surface, floaters, diagonal = make_cloud(name, n)
    near = surface[:: max(1, len(surface) // 100_000)]   # True-surface samples the distances are measured against.
    cloud = import_ply(path)
    points_before = len(cloud.data.points)
    record = {"splats": points_before, "diagonal_m": round(diagonal, 3)}
    if not hasattr(bpy.ops.angelo, "splat_to_mesh"):
        record["missing"] = "angelo.splat_to_mesh"
        state["shapes"][name] = record
        check("%s_collision" % name, False)
        if visual:
            check("%s_visual" % name, False)
        return
    begun = time.perf_counter()
    result, made = run_kind(cloud, 'COLLISION', budget)
    record["budget"] = budget
    record["collision_seconds"] = round(time.perf_counter() - begun, 2)
    ok = result == {'FINISHED'} and len(made) == 1 and made[0].type == 'MESH'
    if ok:
        mesh, verts = mesh_arrays(made[0])
        tris = sum(len(p.vertices) - 2 for p in mesh.polygons)
        floater_gap = nearest_vertex_distance(verts, floaters[::5])
        far = floater_gap > diagonal * 0.05   # Floaters well off the surface must get no geometry near them.
        stray = int(np.sum(~far & (nearest_vertex_distance(near, floaters[::5]) > diagonal * 0.1)))
        record.update(triangles=tris, non_manifold=non_manifold_edges(mesh),
                      surface_error=round(surface_distance(made[0], surface) / diagonal, 4), floater_geometry=stray)
        # How far the mesh lies off the true surface (bloat from a coarse voxel shows here, not in surface_error,
        # which measures from the surface to the mesh): points spread over the mesh by area, since a thinned mesh's
        # vertices sit at its corners, furthest off.
        offset = cases.offsets(*cases.mesh_triangles(mesh, made[0].matrix_world), near, diagonal)
        record.update(mesh_offset_p95=round(splat_measure.p95(offset), 4),
                      mesh_offset_max=round(float(offset.max()), 4))
        # Against the capture's own resolution: the hole-free surface at the splats' radius, on this cloud.
        reference, unit, raw_rule = cases.reference(splat_mesh, cloud, near, diagonal, budget)
        record.update(reference_offset_p95=round(splat_measure.p95(reference), 4), reference_voxel=round(unit, 4),
                      raw_count_rule_voxel=round(raw_rule, 5))
        hulls = [c for c in made[0].children if c.type == 'MESH' and c.angelo_physics.shape == 'CONVEX']
        record["hulls"] = len(hulls)
        report = dict(splat_mesh.last_report)
        # Crossings counted here (splat_mesh_cases.crossings), not read from the module's own report.
        record.update(reduction=report.get("reduction"), crossings=cases.crossings(made[0]),
                      steps=report.get("steps"))
        reductions.add(report.get("reduction"))
        # Both directions: true surface to mesh (holes, lost walls) and mesh to true surface (a shell pushed out).
        # Every result is judged the same way (the coordinator's ruling on the re-read of f3436a404a9): the coverage
        # gate the product applies (splat_measure, against the first voxel's surface as made), offset p95 under 2%
        # of the diagonal wherever the capture is dense enough for its reference to be, and, where the voxel had to
        # grow, never a voxel coarser than the raw-count rule's (compared unrounded). There is no bound relative to
        # the reference: a collapse's chordal change is bounded by the 2% offset like any other result, lost features
        # are the gate's job, and a reference-relative bound with a deviation term is vacuous (triangle inequality).
        # The offset and its reference are recorded, so the change stays visible.
        voxel = report.get("voxel", float("inf"))
        record["voxel_size"] = round(voxel, 6)
        beyond, reference_beyond, sampled, refused = cases.coverage(splat_mesh, splat_measure, cloud, made[0])
        record.update(coverage_beyond=beyond, coverage_reference=reference_beyond, coverage_sampled=sampled)
        dense = splat_measure.p95(reference) < 0.02
        offset_ok = not refused and (not dense or record["mesh_offset_p95"] < 0.02)
        if report.get("reduction") in ("none", "collapse"):
            record["judged_by"] = "coverage gate, 2% where dense"
        else:
            record["judged_by"] = "coverage gate, 2% where dense, no coarser than the raw-count rule"
            offset_ok = offset_ok and voxel <= raw_rule
        ok = (tris <= budget and record["non_manifold"] == 0 and record["surface_error"] < 0.02 and stray == 0
              and offset_ok and record["crossings"] == 0
              and 1 <= len(hulls) <= HULLS and all(h.angelo_physics.collision_only for h in hulls)
              and made[0].angelo_physics.shape == 'TRIMESH' and made[0].angelo_physics.collision_only)
    check("%s_collision" % name, ok)
    check("%s_source_untouched" % name, len(cloud.data.points) == points_before)
    if not visual:
        state["shapes"][name] = record
        return

    result, made = run_kind(cloud, 'VISUAL')
    ok = result == {'FINISHED'} and len(made) == 1 and made[0].type == 'MESH'
    if ok:
        attrs = made[0].data.color_attributes
        record["color_attribute"] = attrs.active_color.name if attrs.active_color else None
        if attrs.active_color:
            colours = np.array([c.color[:3] for c in attrs.active_color.data])
            record["mean_colour"] = [round(float(c), 3) for c in colours.mean(axis=0)]
            ok = float(np.max(np.abs(colours.mean(axis=0) - np.array(SHAPES[name][1])))) < 0.1
        else:
            ok = False
    check("%s_visual" % name, ok)
    state["shapes"][name] = record


def fallback_case():
    """The last resort, run on purpose: with Decimate Collapse stopping short of the budget (the real collapse aimed
    at three times the budget, as it stops short on a very large surface), the voxel must grow, and from the fewest
    triangles the collapse reached, not the surface as made (the review of c8a2cabdc1c, MUST-1). Judged from the
    voxels themselves (the review of 02ea9dd4782, MUST-2), on the room and on a held-out shape."""
    ok, records = True, {}
    for name in ("room", "cylinder"):
        path, _, _, _ = make_cloud(name, 60000)
        cloud = import_ply(path)
        real = splat_budget._collapse
        splat_budget._collapse = lambda mesh, max_triangles: real(mesh, max_triangles * 3)
        try:
            result, made = run_kind(cloud, 'COLLISION')
        finally:
            splat_budget._collapse = real
        report = dict(splat_mesh.last_report)
        steps = report.get("steps") or []
        reductions.add(report.get("reduction"))
        records[name] = {"reduction": report.get("reduction"), "steps": steps}
        if len(steps) < 2 or "collapse" not in steps[0]:
            ok = False
            continue
        expected = max(1.1, math.sqrt(steps[0]["collapse"] / BUDGET) * 1.1)
        from_made = max(1.1, math.sqrt(steps[0]["triangles"] / BUDGET) * 1.1)
        ratio = steps[1]["voxel"] / steps[0]["voxel"]
        records[name].update(ratio=ratio, expected=expected, from_made=from_made)
        ok = ok and (result == {'FINISHED'} and report.get("within_budget") is True
                     and abs(ratio / expected - 1) < 1e-6 and abs(ratio / from_made - 1) > 1e-3)
    state["fallback"] = records
    check("fallback_grows_from_fewest", ok)


def export_round_trip():
    if not hasattr(bpy.ops.angelo, "export_splat_ply"):
        check("export_round_trip", False)
        state["export"] = {"missing": "angelo.export_splat_ply"}
        return
    path, _, _, _ = make_cloud("sphere", 5000)
    cloud = import_ply(path)
    out = OUT / "round-trip.ply"
    with bpy.context.temp_override(object=cloud, active_object=cloud, selected_objects=[cloud]):
        result = bpy.ops.angelo.export_splat_ply(filepath=str(out))
    again = import_ply(out)

    def values(obj):
        attributes = obj.data.attributes
        return (read_attribute(attributes["position"], 3), read_attribute(attributes["radiance:base"], 4),
                read_attribute(attributes["scale"], 3))
    a, b = values(cloud), values(again)
    errors = [float(np.max(np.abs(x - y))) for x, y in zip(a, b)]
    state["export"] = {"result": sorted(result), "max_errors": errors}
    check("export_round_trip", result == {'FINISHED'} and max(errors) < 1e-4)


def timing_million():
    if not hasattr(bpy.ops.angelo, "splat_to_mesh"):
        return
    path, _, _, _ = make_cloud("sphere", 1_000_000)
    cloud = import_ply(path)
    begun = time.perf_counter()
    run_kind(cloud, 'COLLISION')
    state["timing"]["collision_1m_splats_seconds"] = round(time.perf_counter() - begun, 2)


def main():
    try:
        for name in SHAPES:
            try:
                shape_case(name)
            except Exception:
                state["shapes"].setdefault(name, {})["error"] = traceback.format_exc()
                check("%s_collision" % name, False)
        for name in DENSE:
            try:
                shape_case(name, n=1_000_000, visual=False, budget=cases.budget_for_two_percent())
            except Exception:
                state["shapes"].setdefault(name, {})["error"] = traceback.format_exc()
                check("%s_collision" % name, False)
        fallback_case()
        # Both ways to meet the budget run on some case: collapse at the voxel the splats allow, and a coarser voxel.
        state["reductions"] = sorted(str(r) for r in reductions)
        check("every_reduction_runs", {"collapse", "voxel"} <= reductions)
        cases.detectors(check, state, splat_budget)
        cases.grid_cases(check, state, splat_mesh, make_cloud, import_ply)
        cases.gate_cases(check, state, splat_mesh, splat_measure, make_cloud, import_ply, run_kind)
        cases.stop_cases(check, state, splat_budget, splat_measure, splat_mesh, make_cloud, import_ply, run_kind,
                         state["shapes"])
        path, _, _, _ = make_cloud("torus", 60000)
        cases.forced_refusals(check, state, splat_budget, splat_mesh, import_ply(path), run_kind)
        cases.low_budgets(check, state, make_cloud, import_ply, splat_mesh, non_manifold_edges)
        export_round_trip()
        timing_million()
        # Per shape: collision, source untouched, visual (a dense case: the first two); then fallback, every
        # reduction, the two detectors, the grid cases, the two gate cases, the three stop cases, the two forced
        # refusals, two low budgets and the export.
        state["success"] = (len(state["checks"]) == 3 * len(SHAPES) + 2 * len(DENSE) + 15
                            and all(state["checks"].values()))
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_SPLAT_MESH", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
