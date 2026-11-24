# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cases of ``test_blender_splat_mesh`` beyond the shapes (the re-read of 02ea9dd4782): a multi-room floor plan, the
two detectors on meshes built here, refusals forced so each refusal path runs, and low triangle budgets.

Each function takes the suite's ``check`` and helpers and records into its ``state``; nothing here is tuned to a
shape's numbers: the criteria are the suite's own (within the budget, closed, never crossing itself).
"""

import math

import bmesh
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

from angelo_agent import splat_measure


def floor_plan(n, rng):
    """A ~20 m x 12 m floor of six rooms seen from inside: floor, ceiling, outer walls and 0.15 m interior walls (both
    faces) with a 1 m x 2.1 m doorway in each interior wall between two rooms, the doorways' jambs and lintels
    included (surfaces like any other). Splats spread by area."""
    height, half, door, door_h = 2.8, 0.075, 0.5, 2.1
    planes = []   # (axis, value, (lo_u, hi_u), (lo_v, hi_v), doors): a rectangle at axis=value over the other axes.
    planes += [(2, 0.0, (0, 20), (0, 12), ()), (2, height, (0, 20), (0, 12), ())]
    planes += [(0, 0.0, (0, 12), (0, height), ()), (0, 20.0, (0, 12), (0, height), ()),
               (1, 0.0, (0, 20), (0, height), ()), (1, 12.0, (0, 20), (0, height), ())]
    walls = [(0, x, (0, 12), (3.0, 9.0)) for x in (20 / 3, 40 / 3)] + [(1, 6.0, (0, 20), (10 / 3, 10.0, 50 / 3))]
    for axis, value, span, centres in walls:
        planes += [(axis, value - half, span, (0, height), centres), (axis, value + half, span, (0, height), centres)]
        along = 1 if axis == 0 else 0   # The axis the wall runs along.
        for c in centres:
            for side in (c - door, c + door):   # Jambs: across the wall's thickness, up to the lintel.
                planes.append((along, side, (value - half, value + half), (0, door_h), ()))
            # The lintel's underside: across the thickness and the doorway's width.
            across, width = (value - half, value + half), (c - door, c + door)
            u, v = (across, width) if axis == 0 else (width, across)
            planes.append((2, door_h, u, v, ()))
    areas = np.array([(p[2][1] - p[2][0]) * (p[3][1] - p[3][0]) for p in planes])
    which = rng.choice(len(planes), size=n * 2, p=areas / areas.sum())
    points = []
    for index, (axis, value, (u0, u1), (v0, v1), centres) in enumerate(planes):
        count = int(np.sum(which == index))
        u, v = u0 + rng.random(count) * (u1 - u0), v0 + rng.random(count) * (v1 - v0)
        p = np.empty((count, 3))
        others = [a for a in range(3) if a != axis]
        p[:, axis], p[:, others[0]], p[:, others[1]] = value, u, v
        if centres:   # A wall face with doorways: no surface in them.
            gap = np.zeros(count, dtype=bool)
            for c in centres:
                gap |= (np.abs(u - c) < door) & (v < door_h)
            p = p[~gap]
        points.append(p)
    points = np.concatenate(points)
    return points[rng.permutation(len(points))[:n]] - [10.0, 6.0, 1.4], None


def crossings(obj):
    """The suite's own count of triangle pairs that pass through each other (not the module's): every overlapping
    pair of loop triangles, less those sharing an edge, and those sharing one vertex whose intersection is no longer
    than float noise (a millionth of the mesh's extent)."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    coords = [tuple(v.co) for v in mesh.vertices]
    tris = [tuple(t.vertices) for t in mesh.loop_triangles]
    if not tris:
        return 0
    extent = float(np.linalg.norm(np.ptp(np.array(coords), axis=0)))
    tree = BVHTree.FromPolygons(coords, tris, all_triangles=True, epsilon=(1e-6 * extent) ** 2)
    return len({tuple(sorted(pair)) for pair in tree.overlap(tree)})


def _mesh(name, verts, faces, edges=()):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, list(edges), faces)
    mesh.update()
    return mesh


CUBE_V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_F = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def detectors(check, state, budget):
    """``splat_budget._closed`` and ``_crossings`` on meshes whose answer is known by construction."""
    closed = {
        "closed cube": (_mesh("c1", CUBE_V, CUBE_F), True),
        "cube missing a face": (_mesh("c2", CUBE_V, CUBE_F[:-1]), False),
        "fin on an edge": (_mesh("c3", CUBE_V + [(0.5, -1, 0.5)], CUBE_F + [(0, 1, 8)]), False),
        "loose edge": (_mesh("c4", CUBE_V + [(3, 3, 3)], CUBE_F, edges=[(6, 8)]), False),
    }
    tet = [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]
    a = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0.3, 0.3, 1)]
    b = [(p[0] + 0.2, p[1] + 0.2, p[2] + 0.3) for p in a]
    far = [(p[0] + 5, p[1], p[2]) for p in a]
    crossing = {
        "closed cube": (_mesh("x1", CUBE_V, CUBE_F), False),
        "two tetrahedra apart": (_mesh("x2", a + far, tet + [tuple(i + 4 for i in t) for t in tet]), False),
        "two tetrahedra through each other": (_mesh("x3", a + b, tet + [tuple(i + 4 for i in t) for t in tet]), True),
        # Sharing one vertex, B's face passes through A's along (0,0,0)-(0.3,0.3,0) (the review's case).
        "pierce at a shared vertex": (_mesh("x4", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0.3, 0.3, 1), (0.3, 0.3, -1)],
                                            [(0, 1, 2), (0, 3, 4)]), True),
        "two faces sharing only an edge": (_mesh("x5", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0.2)],
                                                 [(0, 1, 2), (1, 3, 2)]), False),
    }
    seen = {"closed": {}, "crossing": {}}
    for name, (mesh, expected) in closed.items():
        seen["closed"][name] = budget._closed(mesh)
    for name, (mesh, expected) in crossing.items():
        seen["crossing"][name] = budget._crossings(mesh, 1.0) > 0
    state["detectors"] = seen
    check("closed_detector", all(seen["closed"][k] == e for k, (_, e) in closed.items()))
    check("crossing_detector", all(seen["crossing"][k] == e for k, (_, e) in crossing.items()))
    for mesh, _ in list(closed.values()) + list(crossing.values()):
        bpy.data.meshes.remove(mesh)


def forced_refusals(check, state, budget, mesh_module, cloud, run_kind):
    """A kept collapse refused once on purpose, as open and as crossing itself: the step records why and has no
    crossings measured when open; the voxel then grows from the surface as made, and the search ends within the
    budget at a voxel between the refused one and that one."""
    results = {}
    for reason, name in (("open", "_closed"), ("crossing", "_crossings")):
        real = getattr(budget, name)
        calls = {"n": 0}

        def once(*args, real=real, reason=reason, calls=calls):
            calls["n"] += 1
            if calls["n"] == 1:
                return False if reason == "open" else 1
            return real(*args)

        setattr(budget, name, once)
        try:
            result, made = run_kind(cloud, 'COLLISION')
        finally:
            setattr(budget, name, real)
        report = dict(mesh_module.last_report)
        steps = report.get("steps") or [{}]
        first = steps[0]
        results[reason] = {"steps": steps, "voxel": report.get("voxel_size")}
        ok = (result == {'FINISHED'} and first.get("refused") == reason and report.get("within_budget") is True
              and (reason != "open" or "crossings" not in first) and len(steps) >= 2
              and first.get("voxel", 0) < (report.get("voxel_size") or 0))
        check("refused_%s_searches" % reason, ok)
        for obj in made:
            for child in obj.children:
                bpy.data.objects.remove(child)
            bpy.data.objects.remove(obj)
    state["forced_refusals"] = results


def low_budgets(check, state, make_cloud, import_ply, mesh_module, non_manifold_edges, budgets=(1000, 100)):
    """Two held-out shapes at low triangle budgets (a game's collision is often 1,000 or fewer): every result fits,
    closed and never crossing itself."""
    results = {}
    for budget in budgets:
        ok = True
        for name in ("cube", "corridor"):
            path, _, _, _ = make_cloud(name, 60000)
            cloud = import_ply(path)
            before = set(bpy.data.objects)
            with bpy.context.temp_override(object=cloud, active_object=cloud, selected_objects=[cloud]):
                result = bpy.ops.angelo.splat_to_mesh(kind='COLLISION', max_triangles=budget, max_hulls=8)
            made = [o for o in set(bpy.data.objects) - before if o.parent is None]
            report = dict(mesh_module.last_report)
            mesh = made[0].data if made else None
            tris = sum(len(p.vertices) - 2 for p in mesh.polygons) if mesh else None
            record = {"result": sorted(result), "triangles": tris, "reduction": report.get("reduction"),
                      "within_budget": report.get("within_budget"), "steps": len(report.get("steps") or []),
                      "non_manifold": non_manifold_edges(mesh) if mesh else None,
                      "crossings": crossings(made[0]) if made else None}
            results["%s@%d" % (name, budget)] = record
            ok = ok and (result == {'FINISHED'} and len(made) == 1 and tris <= budget and record["within_budget"]
                         and record["non_manifold"] == 0 and record["crossings"] == 0)
            for obj in made:
                for child in obj.children:
                    bpy.data.objects.remove(child)
                bpy.data.objects.remove(obj)
            bpy.data.objects.remove(cloud)
        check("low_budget_%d" % budget, ok)
    state["low_budgets"] = results


def offsets(coords, triangles, near, diagonal):
    """Distances, as shares of the diagonal, from points spread over a mesh by area (``splat_measure``, seeded) to the
    nearest true-surface sample: how far its surface lies off, the same measure for a result and its reference."""
    from mathutils.kdtree import KDTree
    tree = KDTree(len(near))
    for index, point in enumerate(near):
        tree.insert(point, index)
    tree.balance()
    points = splat_measure.area_points(coords, triangles)
    return np.array([tree.find(point)[2] for point in points]) / diagonal


def mesh_triangles(mesh, matrix=None):
    """(coords, triangles) of ``mesh``'s loop triangles, in world space when ``matrix`` is given."""
    mesh.calc_loop_triangles()
    coords = np.array([v.co for v in mesh.vertices], dtype=np.float64)
    if matrix is not None:
        m = np.array(matrix)
        coords = coords @ m[:3, :3].T + m[:3, 3]
    return coords, np.array([tuple(t.vertices) for t in mesh.loop_triangles], dtype=np.int64)


def reference(splat_mesh, cloud, near, diagonal, budget):
    """What the code is judged against, built here from this same cloud (the coordinator's rulings on the re-reads of
    02ea9dd4782 and f3436a404a9): the offsets (shares of the diagonal) of the hole-free surface at the splats' own
    radius (the first voxel, before any budget), that voxel as a share of the diagonal, and the voxel the raw-count
    rule (grow from the surface as made until it fits: 4235812eb92's rule without its planar dissolve, which grew
    from a smaller count) would end at, in metres: the fix may never end coarser than that."""
    positions, opacity, radius, _ = splat_mesh.splats(cloud)
    keep = splat_mesh.keep_mask(positions, opacity, radius)
    positions, radius = positions[keep], radius[keep]
    voxel = float(np.median(radius)) / splat_mesh.SHEET

    def surface(v):
        return splat_mesh._surface(positions, np.maximum(radius, splat_mesh.SHEET * v), v, 0.0)

    mesh = surface(voxel)
    try:
        coords, triangles = mesh_triangles(mesh)   # World space: _surface builds from world positions.
        found = offsets(coords, triangles, near, diagonal)
        old, count = voxel, len(triangles)
    finally:
        bpy.data.meshes.remove(mesh)
    for _ in range(64):
        if count <= budget:
            break
        old *= math.sqrt(count / budget) * 1.1
        mesh = surface(old)
        count = sum(len(p.vertices) - 2 for p in mesh.polygons)
        bpy.data.meshes.remove(mesh)
    return found, voxel / diagonal, old


def grid_cases(check, state, splat_mesh, make_cloud, import_ply):
    """The wall-vanishing class itself: a closed room (its walls thin slabs of splats) meshed at its first voxel and
    two coarser ones, each at eight placements of the voxel grid (the cloud shifted by eighths of a voxel along each
    axis). At the module's SHEET every wall must keep its surface at every placement: the worst wall's splats lie on
    average within their own bound, r' + voxel / 2, of the surface. At the sqrt(3)/2 the module used before, some
    placement must lose a wall (the room lost walls at 3 to 5 of 8 placements, 2026-11-21), so this case sees the
    class."""
    path, _, _, _ = make_cloud("room", 60000)
    cloud = import_ply(path)
    positions, opacity, radius, _ = splat_mesh.splats(cloud)
    keep = splat_mesh.keep_mask(positions, opacity, radius)
    positions, radius = positions[keep].astype(np.float64), radius[keep]
    half = np.array([4.0, 3.0, 2.5]) / 2   # The room's walls (test_blender_splat_mesh.room).
    walls = [np.abs(positions[:, a] - s * half[a]) < 1e-4 for a in range(3) for s in (-1, 1)]
    results = {}
    for label, sheet in (("module", splat_mesh.SHEET), ("sqrt(3)/2", math.sqrt(3) / 2)):
        first = float(np.median(radius)) / sheet
        rows = []
        for voxel in (first, first * 1.07, first * 1.15):
            bound = max(float(np.median(radius)), sheet * voxel) + voxel / 2
            kept = []
            for k in range(8):
                shift = np.array([k, 3 * k, 5 * k]) % 8 / 8.0 * voxel
                mesh = splat_mesh._surface(positions + shift, np.maximum(radius, sheet * voxel), voxel, 0.0)
                coords, triangles = mesh_triangles(mesh)
                bpy.data.meshes.remove(mesh)
                tree = BVHTree.FromPolygons(coords.tolist(), triangles.tolist(), all_triangles=True)
                worst = max(float(np.mean([tree.find_nearest(p + shift)[3] for p in positions[w][::80]]))
                            for w in walls)
                kept.append(worst <= bound)
            rows.append({"voxel": round(voxel, 5), "every_wall_kept": kept})
        results[label] = rows
    state["grid_cases"] = results
    module = all(all(row["every_wall_kept"]) for row in results["module"])
    old_loses = any(not all(row["every_wall_kept"]) for row in results["sqrt(3)/2"])
    check("no_wall_vanishes", module and old_loses)
    bpy.data.objects.remove(cloud)


def coverage(splat_mesh, measure, cloud, result):
    """The product's coverage judgement of ``result`` (an object), recomputed here with the shared functions: (centres
    beyond their bound on it, on the first voxel's surface as made, sampled centres, refused)."""
    positions, opacity, radius, _ = splat_mesh.splats(cloud)
    keep = splat_mesh.keep_mask(positions, opacity, radius)
    positions, radius = positions[keep], radius[keep]
    every = max(1, len(positions) // 2000)
    samples, sample_radius = positions[::every], radius[::every]
    first = float(np.median(radius)) / splat_mesh.SHEET
    report = splat_mesh.last_report
    voxel = report.get("voxel", first)

    def beyond(coords, triangles, v):
        tree = BVHTree.FromPolygons(coords.tolist(), triangles.tolist(), all_triangles=True)
        distances = np.array([tree.find_nearest(p)[3] for p in samples])
        return measure.exceedance(distances, np.maximum(sample_radius, splat_mesh.SHEET * v) + v / 2)

    made = splat_mesh._surface(positions, np.maximum(radius, splat_mesh.SHEET * first), first, 0.0)
    try:
        reference = beyond(*mesh_triangles(made), first)
    finally:
        bpy.data.meshes.remove(made)
    count = beyond(*mesh_triangles(result.data, result.matrix_world), voxel)
    return count, reference, len(samples), measure.exceeds_reference(count, reference, len(samples))


def gate_cases(check, state, splat_mesh, measure, make_cloud, import_ply, run_kind):
    """The coverage gate on purpose: a kept room with one wall's faces deleted must be refused, the same room intact
    must not; a capture with splats inside a solid (centres deeper than their bound, with nothing lost) must still
    give a kept result the gate accepts."""
    records = {}
    path, _, _, _ = make_cloud("room", 60000)
    cloud = import_ply(path)
    result, made = run_kind(cloud, 'COLLISION')
    intact = coverage(splat_mesh, measure, cloud, made[0])
    mesh = made[0].data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    # The +x wall, both its sheets: faces around the plane x = 2 (test_blender_splat_mesh.room), inside its span.
    wall = [f for f in bm.faces if abs(f.calc_center_median().x - 2.0) < 0.25
            and abs(f.calc_center_median().y) < 1.4 and abs(f.calc_center_median().z) < 1.15]
    bmesh.ops.delete(bm, geom=wall, context='FACES')
    bm.to_mesh(mesh)
    bm.free()
    removed = coverage(splat_mesh, measure, cloud, made[0])
    records["room"] = {"intact": intact, "wall removed": removed, "faces deleted": len(wall)}
    check("gate_refuses_a_lost_wall", result == {'FINISHED'} and not intact[3] and removed[3])
    # Splats on a sphere's surface plus a regular grid of splats filling a ball inside it, spaced so the ball is solid
    # at every voxel: each point of it is within sqrt(3)/2 of a spacing of a centre, under the radius less the half
    # voxel Volume to Mesh keeps inside. Its deep centres then lie beyond their bound with nothing lost. (Random
    # interior splats leave voids at any affordable count, each centre near a void's surface: 0 beyond, 2026-11-21.)
    rng = np.random.default_rng(20261122)
    surface = rng.normal(size=(40000, 3))
    surface /= np.linalg.norm(surface, axis=1, keepdims=True)
    spacing = 0.05
    for _ in range(6):   # The radius follows the count (make_cloud: 1.5 x 2 x diagonal / sqrt(count)), the grid it.
        axis = np.arange(-0.35, 0.35 + 1e-9, spacing)
        grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
        grid = grid[np.linalg.norm(grid, axis=1) <= 0.35]
        spacing = 0.6 * 1.5 * 2 * math.sqrt(12) / math.sqrt(len(surface) + len(grid))
    path, _, _, _ = make_cloud("interior", 0, surface=np.concatenate([surface, grid]))
    cloud = import_ply(path)
    radius = float(np.median(splat_mesh.splats(cloud)[2]))
    solid = (axis[1] - axis[0]) * math.sqrt(3) / 2 < radius * (1 - 1 / (2 * splat_mesh.SHEET))
    result, made = run_kind(cloud, 'COLLISION')
    report = dict(splat_mesh.last_report)
    judged = coverage(splat_mesh, measure, cloud, made[0]) if made else None
    records["interior"] = {"result": sorted(result), "reduction": report.get("reduction"), "coverage": judged,
                           "grid": len(grid), "grid spacing": float(axis[1] - axis[0]), "radius": radius,
                           "solid": solid}
    # The class exercised: some centres ARE beyond their bound, on the reference and on the result, and the gate
    # still accepts (the reference absorbs them).
    check("gate_accepts_interior_splats", solid and result == {'FINISHED'} and judged is not None and not judged[3]
          and judged[1] > 0 and judged[0] > 0 and report.get("within_budget") is True
          and report.get("coverage_ok") is True)
    state["gate_cases"] = records


def floor_plan_area():
    """The floor plan's captured area in m^2 (``floor_plan``'s rectangles less its doorways), and its diagonal."""
    height, door, door_h = 2.8, 0.5, 2.1
    area = 2 * 20 * 12 + 2 * (20 + 12) * height                     # Floor, ceiling and outer walls.
    area += 2 * (2 * 12 + 20) * height                              # Interior walls, both faces.
    area -= 2 * 7 * (2 * door) * door_h                             # Seven doorways, cut from both faces.
    area += 7 * (2 * 0.15 * door_h + 2 * door * 0.15)                # Their jambs and lintels.
    return area, math.sqrt(20 ** 2 + 12 ** 2 + height ** 2)


def budget_for_two_percent():
    """The triangle budget at which the floor plan can lie within 2% of its diagonal (the coordinator's ruling on the
    1M floor plan: derived, not picked; a sufficient budget: at it the result measured 0.64%). A shell sits
    (sheet - 1/2) voxels off its splats, so 2% allows the voxel v = 0.02 * diagonal / (sheet - 1/2); the shell covers
    both sides of every captured surface (2 * area), and its two sides can't be pulled through each other while its
    triangles' edges stay within half its thickness there, (sheet - 1/2) * v = 0.02 * diagonal (so the sheet cancels);
    in equilateral triangles of that edge (area sqrt(3)/4 * edge^2):
        budget = 2 * area / (sqrt(3) / 4 * (0.02 * diagonal) ** 2)."""
    area, diagonal = floor_plan_area()
    edge = 0.02 * diagonal
    return int(math.ceil(2 * area / (math.sqrt(3) / 4 * edge ** 2)))


def stop_cases(check, state, budget, measure, mesh_module, make_cloud, import_ply, run_kind, shapes):
    """The search's stops and the product's own refusal, run on purpose. The resolution stop: some grown shape stops
    within its median splat radius, its previous kept candidate above it. The "no closer" stop: with sampling noise
    made to swallow every difference once, the search ends at the voxel found, the candidate freed, within budget.
    The coverage refusal: with the gate made to fail its first judgement, the first step is refused "off" and the
    search grows and ends within budget. No temporary data-block is left by any."""
    records = {}
    resolution = [name for name, record in shapes.items()
                  if any(s.get("stopped") == "within the splats' resolution" for s in record.get("steps") or [])]
    records["resolution_stops"] = resolution
    check("stops_at_the_resolution", bool(resolution))

    def forced(name, attribute, value):
        real = getattr(measure, attribute)
        calls = {"n": 0}

        def once(*args, **kwargs):
            calls["n"] += 1
            return value if calls["n"] == 1 else real(*args, **kwargs)

        setattr(measure, attribute, once)
        try:
            path, _, _, _ = make_cloud(name, 60000)
            cloud = import_ply(path)
            result, made = run_kind(cloud, 'COLLISION')
        finally:
            setattr(measure, attribute, real)
        report = dict(mesh_module.last_report)
        temp = [m.name for m in bpy.data.meshes if m.name.startswith(budget.TEMP)]
        for obj in made:
            for child in obj.children:
                bpy.data.objects.remove(child)
            bpy.data.objects.remove(obj)
        bpy.data.objects.remove(cloud)
        return result, report, temp

    result, report, temp = forced("cube", "within_noise", True)
    steps = report.get("steps") or []
    records["no_closer"] = {"steps": steps, "temp": temp}
    check("stops_when_no_closer", result == {'FINISHED'} and report.get("within_budget") is True and not temp
          and any(s.get("stopped") == "no closer" for s in steps))
    result, report, temp = forced("torus", "exceeds_reference", True)
    steps = report.get("steps") or [{}]
    records["off"] = {"steps": steps, "temp": temp}
    check("refuses_off_the_capture", result == {'FINISHED'} and steps[0].get("refused") == "off"
          and report.get("within_budget") is True and len(steps) > 1 and not temp)
    state["stop_cases"] = records
