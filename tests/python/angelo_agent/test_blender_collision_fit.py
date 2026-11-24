# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Collision for every engine: convex hulls and a multi-hull decomposition, as Game Physics colliders, in a real
background Blender.

Game-engine users' most repeated collision request is fitted collision that reaches their engine (a survey of
game-engine users' requests, 2026-11-14, theme 1: Godot proposals, Unreal forum UCX threads,
Send to Unreal issues, seven paid add-ons). Angelo's Game Physics (``doc/angelo/game_physics.rst``) fits primitives
and a single convex hull and writes OMI, which Godot reads, but has no decomposition: a concave prop collides as its
hull (a chair as a block, a doorway shut). ``collision_fit`` is the one module the splat collision mesh and the
game-ready job share (plan ``release-2.1.0-plan.md``, section 7).

The contract, on shapes of unlike kinds (none tuned to one):

* ``angelo.fit_collision`` with ``method='DECOMPOSE'`` makes at most ``max_hulls`` convex hull objects below the
  mesh, each a Game Physics collider (``angelo_physics.shape == 'CONVEX'``, Collision Only, not rendered), each with
  at most ``max_hull_vertices`` vertices (engines cap a convex hull's size);
* together they cover the solid (at least 95% of random points inside the mesh are inside some hull) and stay tight
  (their volumes sum to at most 1.5 x the solid's), so a concave shape keeps its concavity: an L-shaped block's
  inner corner and a torus's hole are inside no hull;
* ``method='CONVEX'`` makes exactly one hull; an open mesh (not a solid) gets one hull per loose part and the report
  says the mesh is open, since inside and outside are undefined;
* the source mesh is unchanged; running it again replaces its hulls instead of adding more;
* every hull holds the points it is made from, uncapped, on dense and nearly coplanar clouds like a decomposition
  part's (a torus band with its seam's grid, a sphere's surface, a thin slab, voxel corners): the hull
  ``bmesh.ops.convex_hull`` gave held as little as 15% of a torus part's own points (2026-11-15);
* ``collision_fit.unreal_name`` gives Unreal's convex-collision names (``UCX_<mesh>_00``...), which the Unreal export
  writes on its exported copy.

Run through ``run_native_suites.py --include test_blender_collision_fit``; check ``collision-fit.json`` for
``success``. Expected to fail on 2.0.0 (06259fa5bb0): the operator and module do not exist there.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import tempfile
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-collision-fit-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False

REPORT = OUT / "collision-fit.json"
RNG = np.random.default_rng(20261114)
state = {"success": False, "checks": {}, "shapes": {}}


def check(name, value):
    state["checks"][name] = bool(value)


def solid(name, build, matrix=None):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    build(bm)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if matrix is not None:
        obj.matrix_world = matrix
    return obj


def prism(outline, height):
    """A closed prism from a counter-clockwise 2D outline."""
    def build(bm):
        bottom = [bm.verts.new((x, y, 0)) for x, y in outline]
        face = bm.faces.new(bottom)
        extruded = bmesh.ops.extrude_face_region(bm, geom=[face])
        top = [e for e in extruded["geom"] if isinstance(e, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=top, vec=(0, 0, height))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return build


def torus_build(big=1.5, small=0.45, u_count=32, v_count=12):
    def build(bm):
        rings = []
        for u in range(u_count):
            a = 2 * math.pi * u / u_count
            ring = []
            for v in range(v_count):
                b = 2 * math.pi * v / v_count
                ring.append(bm.verts.new(((big + small * math.cos(b)) * math.cos(a),
                                          (big + small * math.cos(b)) * math.sin(a), small * math.sin(b))))
            rings.append(ring)
        for u in range(u_count):
            for v in range(v_count):
                bm.faces.new((rings[u][v], rings[(u + 1) % u_count][v], rings[(u + 1) % u_count][(v + 1) % v_count],
                              rings[u][(v + 1) % v_count]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return build


def inside_mesh(obj, points):
    """Ray parity against the evaluated mesh in world space."""
    tree = BVHTree.FromObject(obj, bpy.context.evaluated_depsgraph_get())
    to_local = obj.matrix_world.inverted()
    result = []
    for p in points:
        origin, hits = to_local @ Vector(p), 0
        direction = Vector((0.5773, 0.5774, 0.5773))
        while True:
            location, _, _, _ = tree.ray_cast(origin, direction)
            if location is None:
                break
            hits += 1
            origin = location + direction * 1e-5
        result.append(hits % 2 == 1)
    return np.array(result)


def hull_planes(hull):
    bm = bmesh.new()
    bm.from_mesh(hull.data)
    bm.transform(hull.matrix_world)
    bm.normal_update()
    planes = [(np.array(f.normal), np.array(f.calc_center_median())) for f in bm.faces]
    volume = abs(bm.calc_volume())
    verts = len(bm.verts)
    bm.free()
    return planes, volume, verts


def in_any_hull(hulls, points):
    inside = np.zeros(len(points), dtype=bool)
    for planes, _, _ in hulls:
        here = np.ones(len(points), dtype=bool)
        for normal, centre in planes:
            here &= (points - centre) @ normal <= 1e-4
        inside |= here
    return inside


def colliders_of(obj):
    return [c for c in obj.children if c.type == 'MESH' and c.angelo_physics.shape == 'CONVEX']


def fit(obj, **options):
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        return bpy.ops.angelo.fit_collision(**options)


def solid_volume(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    volume = abs(bm.calc_volume())
    bm.free()
    return volume


def decompose_case(name, obj, empty_points, max_hulls=8, max_hull_vertices=64):
    record = {}
    before = obj.data.vertices[0].co.copy(), len(obj.data.vertices)
    result = fit(obj, method='DECOMPOSE', max_hulls=max_hulls, max_hull_vertices=max_hull_vertices)
    hulls_objects = colliders_of(obj)
    hulls = [hull_planes(h) for h in hulls_objects]
    lo, hi = np.array(obj.bound_box[0]), np.array(obj.bound_box[6])
    samples = np.array([obj.matrix_world @ Vector(p) for p in lo + RNG.random((3000, 3)) * (hi - lo)])
    inside = inside_mesh(obj, samples)
    covered = in_any_hull(hulls, samples[inside]).mean() if inside.any() else 0.0
    total = sum(v for _, v, _ in hulls)
    record.update(result=sorted(result), hulls=len(hulls), coverage=round(float(covered), 3),
                  volume_ratio=round(total / solid_volume(obj), 3), max_vertices=max((v for _, _, v in hulls), default=0),
                  empty_points_in_hulls=int(in_any_hull(hulls, np.array(empty_points)).sum()),
                  colliders_flagged=all(h.angelo_physics.collision_only and h.hide_render for h in hulls_objects))
    ok = (result == {'FINISHED'} and 2 <= len(hulls) <= max_hulls and record["coverage"] >= 0.95
          and record["volume_ratio"] <= 1.5 and record["max_vertices"] <= max_hull_vertices
          and record["empty_points_in_hulls"] == 0 and record["colliders_flagged"])
    check("%s_decompose" % name, ok)
    check("%s_source_unchanged" % name, (obj.data.vertices[0].co.copy(), len(obj.data.vertices)) == before)
    fit(obj, method='DECOMPOSE', max_hulls=max_hulls, max_hull_vertices=max_hull_vertices)
    check("%s_rerun_replaces" % name, len(colliders_of(obj)) == len(hulls))
    state["shapes"][name] = record


def clouds():
    """Dense, nearly coplanar clouds of unlike kinds, from fixed seeds."""
    rng = np.random.default_rng(20261118)
    u, v = rng.uniform(0.0, 0.7, 4000), rng.uniform(0, 2 * np.pi, 4000)
    band = np.stack([(1 + 0.3 * np.cos(v)) * np.cos(u), (1 + 0.3 * np.cos(v)) * np.sin(u), 0.3 * np.sin(v)], axis=1)
    seam = np.array([[x, 0.0, z] for x in np.linspace(0.7, 1.3, 13) for z in np.linspace(-0.3, 0.3, 13)])
    sphere = rng.normal(size=(3000, 3))
    sphere /= np.linalg.norm(sphere, axis=1, keepdims=True)
    slab = np.concatenate([rng.uniform(-1, 1, (4000, 3)) * (1, 1, 1e-3), [[0, 0, 0.05]]])
    cells = np.array([(x, y, z) for x in range(6) for y in range(6) for z in range(3) if x < 2 or y < 2])
    offsets = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)])
    corners = ((cells[:, None, :] + offsets[None]) * 0.1).reshape(-1, 3)
    return {"torus_band": np.concatenate([band, seam]), "sphere": sphere, "slab": slab, "voxel_corners": corners}


def hulls_hold_their_points(collision_fit):
    """Each cloud's hull, uncapped, has every one of the cloud's points inside every face plane (a millionth of the
    cloud's size allowed)."""
    record = {}
    for name, points in clouds().items():
        bm = collision_fit.hull(points, 10 ** 6)
        bm.normal_update()
        reach = max((float(((points - np.array(f.verts[0].co)) @ np.array(f.normal)).max()) for f in bm.faces),
                    default=float("inf"))
        extent = float(np.ptp(points, axis=0).max())
        inside = np.ones(len(points), dtype=bool)
        for face in bm.faces:
            inside &= (points - np.array(face.verts[0].co)) @ np.array(face.normal) <= 1e-6 * extent
        record[name] = {"faces": len(bm.faces), "verts": len(bm.verts), "held": round(float(inside.mean()), 4),
                        "reach": reach}
        bm.free()
    state["clouds"] = record
    check("hulls_hold_their_points", all(r["held"] == 1.0 and r["faces"] > 0 for r in record.values()))


def main():
    try:
        if not hasattr(bpy.ops.angelo, "fit_collision"):
            state["missing"] = "angelo.fit_collision"
            check("operator_exists", False)
        else:
            check("operator_exists", True)
            ell = solid("LBlock", prism([(0, 0), (2, 0), (2, 0.6), (0.6, 0.6), (0.6, 2), (0, 2)], 0.8))
            decompose_case("l_block", ell, [(1.4, 1.4, 0.4)])           # The inner corner.
            ring = solid("Torus", torus_build(), Matrix.Rotation(0.4, 4, 'X'))
            decompose_case("torus", ring, [(0, 0, 0)], max_hulls=16)  # The hole.
            u_shape = solid("UChannel", prism([(0, 0), (3, 0), (3, 2), (2.4, 2), (2.4, 0.6), (0.6, 0.6),
                                                (0.6, 2), (0, 2)], 1.0), Matrix.Translation((5, 0, 0)))
            decompose_case("u_channel", u_shape, [(6.5, 1.4, 0.5)])     # Between the arms.

            box = solid("Box", lambda bm: bmesh.ops.create_cube(bm, size=1.0), Matrix.Rotation(0.7, 4, 'Z'))
            result = fit(box, method='CONVEX', max_hulls=8, max_hull_vertices=64)
            check("convex_one_hull", result == {'FINISHED'} and len(colliders_of(box)) == 1)

            def two_planes(bm):
                bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=1.0)
                bmesh.ops.translate(bm, verts=bmesh.ops.create_grid(bm, x_segments=2, y_segments=2,
                                                                    size=1.0)["verts"], vec=(4, 0, 1))
            sheet = solid("OpenSheets", two_planes)
            result = fit(sheet, method='DECOMPOSE', max_hulls=8, max_hull_vertices=64)
            check("open_mesh_one_hull_per_part", result == {'FINISHED'} and len(colliders_of(sheet)) == 2)

            from angelo_agent import collision_fit
            hulls_hold_their_points(collision_fit)
            check("unreal_names", [collision_fit.unreal_name("Crate", i) for i in (0, 1, 12)]
                  == ["UCX_Crate_00", "UCX_Crate_01", "UCX_Crate_12"])
        state["success"] = len(state["checks"]) == 1 + 3 * 3 + 4 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_COLLISION_FIT", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
