# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout in Blender (uv_layout, doc/angelo/uv_layout.rst) and Pack Islands' Search.

Run by ``run_native_suites.py --include test_blender_uv_layout`` (BACKGROUND). Proves on meshes made here:

* Pack Islands' ``search``: never less efficient than one pass, the same layout twice, tighter on some
  irregular kit (box and plate kits, Fraction and Scaled margins, the AABB shape);
* a fixed texel density lands on its target, and spills to more UDIM tiles with every island inside its tile;
* padding: islands of different parts stay ``2^(mips+1)`` texels apart;
* stacking: congruent parts (turned round bolts here) share every face corner's UVs, a different part does not;
* grouping by material puts each material in its own tile;
* the agent's route: ``angelo.uv_layout`` from its properties alone (EXEC_DEFAULT) on an active, unselected mesh;
* a mesh changed between two packs stops the layout, named; the cleanup never raises; the tables are read-only;
* too little room for the islands (the held-out soup and scan) is reported with a fix; an ordinary kit is not;
* a margin nobody set drops to the most mip levels that leave room, said in the receipt (soup 3, scan 2, others 4);
* recorded, not asserted: efficiency and milliseconds of one pass and of the search on each mesh.

No provider, account or network. Writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import random
import time
import traceback
from pathlib import Path

import bmesh
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import uv_layout, uv_layout_math as uvm  # noqa: E402

report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
SCENE = bpy.context.scene


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def fresh():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    bpy.context.view_layer.update()   # Its object list otherwise still holds the removed objects (as None).


def mesh_object(name, build):
    bm = bmesh.new()
    build(bm)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(name, mesh)
    SCENE.collection.objects.link(obj)
    return obj


def boxes(name, count, seed):
    """A kit of boxes of spread sizes: many islands of different shapes once smart-projected."""
    rng = random.Random(seed)

    def build(bm):
        for _ in range(count):
            made = bmesh.ops.create_cube(bm, size=1.0)["verts"]
            sx, sy, sz = (0.05 + rng.random() * rng.random() * 1.5 for _ in range(3))
            offset = [rng.uniform(-5, 5) for _ in range(3)]
            for v in made:
                v.co = (v.co.x * sx + offset[0], v.co.y * sy + offset[1], v.co.z * sz + offset[2])
    return mesh_object(name, build)


def edit(*objects):
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in objects)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.mode_set(mode="EDIT")


def islands_of(obj):
    from bpy_extras import bmesh_utils
    bm = bmesh.from_edit_mesh(obj.data)
    layer = bm.loops.layers.uv.active
    return [[[tuple(l[layer].uv) for l in f.loops] for f in faces]
            for faces in bmesh_utils.bmesh_linked_uv_islands(bm, layer)]


def covered(obj):
    return sum(uvm.polygon_area(face) for island in islands_of(obj) for face in island)


def plates(name, count, seed):
    """Flat irregular polygons (L shapes, triangles, notched panels) of spread sizes: concave islands."""
    rng = random.Random(seed)

    def build(bm):
        for k in range(count):
            s = 0.1 + rng.random() ** 2 * 1.2
            if k % 3 == 0:
                pts = [(0, 0), (1, 0), (1, 0.35), (0.35, 0.35), (0.35, 1), (0, 1)]
            elif k % 3 == 1:
                pts = [(0, 0), (1, 0), (rng.uniform(0.2, 0.8), rng.uniform(0.4, 1.0))]
            else:
                pts = [(0, 0), (1, 0), (1, 0.6), (0.6, 0.6), (0.5, 0.3), (0.4, 0.6), (0, 0.6)]
            ox, oy, oz = rng.uniform(-8, 8), rng.uniform(-8, 8), k * 0.01
            bm.faces.new([bm.verts.new((ox + x * s * rng.uniform(0.7, 1.3), oy + y * s, oz)) for x, y in pts])
    return mesh_object(name, build)


def search_never_looser():
    """Search on box and plate kits with both margin methods, on the AABB shape: the Concave shape's own packer takes
    tens of seconds per pass on these kits in Blender as shipped, which the benchmark records, not this suite."""
    gained = []
    for kind, make, count, seed in (("boxes", boxes, 30, 1), ("boxes", boxes, 80, 2), ("plates", plates, 60, 5),
                                    ("plates", plates, 90, 8), ("plates", plates, 150, 6)):
        for method, margin in (("FRACTION", 1 / 512), ("SCALED", 0.001)):
            rows = {}
            for trials in (0, 32, 0, 32):   # ABAB.
                fresh()
                obj = make("Kit", count, seed)
                edit(obj)
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.0)
                started = time.perf_counter()
                bpy.ops.uv.pack_islands(margin_method=method, margin=margin, rotate=True, rotate_method="ANY",
                                        shape_method="AABB", search=trials)
                spent = (time.perf_counter() - started) * 1000
                rows.setdefault(trials, []).append({"efficiency": round(covered(obj), 6), "ms": round(spent, 1)})
            name = "%s_%d_%s" % (kind, count, method.lower())
            report["observed"]["search_" + name] = rows
            check("search_never_looser_" + name,
                  min(r["efficiency"] for r in rows[32]) >= max(r["efficiency"] for r in rows[0]) - 1e-6)
            check("search_same_layout_twice_" + name, rows[32][0]["efficiency"] == rows[32][1]["efficiency"])
            if kind == "plates":
                gained.append(min(r["efficiency"] for r in rows[32]) > max(r["efficiency"] for r in rows[0]) + 1e-6)
    # Irregular islands are where another order can fit tighter (the search probe: every plate kit but one setting).
    check("search_finds_a_tighter_layout_on_some_plate_kit", any(gained), gained=gained)


def density_and_tiles():
    fresh()
    obj = mesh_object("Floor", lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5))
    edit(obj)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.0)
    receipt = uv_layout.run([obj], {"texture_size": 1024, "density": 512, "mips": 2, "search": 0})
    check("density_lands_on_its_target", abs(receipt["density_px_per_m"]["mean"] - 512) <= 512 * 0.01,
          receipt=receipt)
    fresh()
    parts = [boxes("Part%d" % i, 6, 10 + i) for i in range(4)]
    edit(*parts)
    # At 400 px/m on 512 px some faces span 1.2 tiles: a fixed density cannot keep them in one tile, so it refuses.
    before = [islands_of(obj) for obj in parts]
    try:
        uv_layout.run(parts, {"texture_size": 512, "density": 400, "unwrap": "SMART", "mips": 3, "search": 0})
        refused = ""
    except ValueError as error:
        refused = str(error)
    check("an_island_wider_than_a_tile_is_refused_clearly", "wider than a tile" in refused, refused=refused)
    check("a_refused_layout_leaves_the_uvs_as_they_were", [islands_of(obj) for obj in parts] == before)
    # At 300 px/m the widest spans 0.9 of a tile and the kit still needs about ten tiles.
    receipt = uv_layout.run(parts, {"texture_size": 512, "density": 300, "unwrap": "SMART", "mips": 3,
                                    "search": 4})
    inside = True
    for obj in parts:
        for island in islands_of(obj):
            us = [uv[0] for face in island for uv in face]
            vs = [uv[1] for face in island for uv in face]
            inside &= math.floor(min(us) + 1e-6) == math.floor(max(us) - 1e-6) and \
                math.floor(min(vs) + 1e-6) == math.floor(max(vs) - 1e-6)
    check("fixed_density_spills_to_more_tiles_each_island_in_one", len(receipt["tiles"]) > 1 and inside,
          receipt=receipt)
    # Spilled islands go to the next tile with room, so the tiles stay near what the area needs (17.5 at 400 px/m
    # scaled to 300: about 10 tiles of area, planned at 72 % each).
    check("spilling_keeps_the_tile_count_near_the_area", len(receipt["tiles"]) <= 20, tiles=receipt["tiles"])


def padding():
    fresh()
    obj = boxes("Pad", 12, 7)
    edit(obj)
    receipt = uv_layout.run([obj], {"texture_size": 256, "mips": 3, "unwrap": "SMART", "search": 8})
    islands = islands_of(obj)
    nearest = math.inf
    for i, a in enumerate(islands):
        for b in islands[i + 1:]:
            for p in {uv for face in a for uv in face}:
                for face in b:
                    for k in range(len(face)):
                        q, r = face[k], face[(k + 1) % len(face)]
                        dx, dy = r[0] - q[0], r[1] - q[1]
                        t = max(0.0, min(1.0, ((p[0] - q[0]) * dx + (p[1] - q[1]) * dy) / max(dx * dx + dy * dy,
                                                                                               1e-12)))
                        nearest = min(nearest, math.hypot(p[0] - q[0] - t * dx, p[1] - q[1] - t * dy))
    gap_px = nearest * 256
    check("islands_keep_the_gap_for_their_mips", gap_px >= 2 ** (3 + 1) * 0.98, gap_px=gap_px, receipt=receipt)


def stacking_and_groups():
    fresh()

    def bolts(bm):
        for i in range(4):
            made = bmesh.ops.create_cone(bm, cap_ends=True, segments=7, radius1=0.05, radius2=0.03, depth=0.2)
            bmesh.ops.rotate(bm, verts=made["verts"], cent=(0, 0, 0),
                             matrix=__import__("mathutils").Matrix.Rotation(0.4 * i, 3, (1, 0.3, 0)))
            bmesh.ops.translate(bm, verts=made["verts"], vec=(i * 0.5, 0, 0))
        plate = bmesh.ops.create_cube(bm, size=1.0)["verts"]
        bmesh.ops.translate(bm, verts=plate, vec=(0, 2, 0))
    obj = mesh_object("Bolts", bolts)
    edit(obj)
    receipt = uv_layout.run([obj], {"stack": True, "unwrap": "SMART", "search": 0})
    check("congruent_parts_share_uvs", receipt["stacked"] >= 3, receipt=receipt)
    bpy.ops.object.mode_set(mode="OBJECT")
    layer, bolts = obj.data.uv_layers.active.data, {}
    for poly in obj.data.polygons:   # Each bolt's face corners' UVs: every turned bolt shares the first one's.
        if poly.center.y < 1:
            bolts.setdefault(round(poly.center.x / 0.5), set()).update(
                (round(layer[i].uv.x, 5), round(layer[i].uv.y, 5)) for i in poly.loop_indices)
    check("turned_round_parts_share_every_corner", len(bolts) == 4 and all(bolts[i] == bolts[0] for i in bolts),
          bolts=len(bolts), sharing=sum(bolts[i] == bolts[0] for i in bolts))
    fresh()
    a, b = boxes("Red", 3, 21), boxes("Blue", 3, 22)
    b.data.materials.append(bpy.data.materials.new("Blue"))
    a.data.materials.append(bpy.data.materials.new("Red"))
    joined = [a, b]
    edit(*joined)
    receipt = uv_layout.run(joined, {"unwrap": "SMART", "group_by": "OBJECT", "search": 0})
    check("each_group_its_own_tile", receipt["tiles"] == [1001, 1002], receipt=receipt)


def operator_exec():
    """angelo.uv_layout from its properties alone (EXEC_DEFAULT), as the shared typed operation runs it."""
    from angelo_agent import uv_layout_ui
    if not hasattr(bpy.types, "ANGELO_OT_uv_layout"):
        bpy.utils.register_class(uv_layout_ui.ANGELO_OT_uv_layout)
    fresh()
    crate = boxes("Crate", 5, 31)
    for obj in bpy.context.scene.objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = crate   # Active, not selected: the staged target.
    result = bpy.ops.angelo.uv_layout("EXEC_DEFAULT", texture_size="1024", use_density=True, density=200.0,
                                      unwrap="SMART", search=4)
    back_in_object_mode = bpy.context.mode == "OBJECT"
    edit(crate)
    check("exec_lays_out_the_active_mesh", result == {"FINISHED"} and back_in_object_mode and covered(crate) > 0,
          result=sorted(result), covered=covered(crate))


def changed_meanwhile():
    """A mesh changed between two packs (by the person, a timer or a script; the review of 86b9af0f149, S3/S4): the
    layout refuses to go on with face numbers that would name other faces, and its cleanup never raises: what it can
    put back goes back, what it cannot is named. The tables every later layout reads are read-only (S2)."""
    fresh()
    kits = [boxes("Kit%d" % i, 6, 40 + i) for i in range(2)]
    edit(*kits)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.0)
    untouched = islands_of(kits[1])
    spec, objects, state = uv_layout.begin(kits, {"texture_size": 512, "mips": 3, "search": 0})
    layout = uv_layout.steps(objects, spec)
    uv_layout.pack(spec, next(layout))
    bm = bmesh.from_edit_mesh(kits[0].data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")   # A face deleted between two packs.
    bmesh.update_edit_mesh(kits[0].data)
    try:
        next(layout)
        refused = "went on"
    except StopIteration:
        refused = "finished"
    except Exception as error:   # noqa: BLE001 - the check names what it got.
        refused = repr(error)
    check("a_mesh_changed_between_packs_stops_the_layout",
          "changed while it was laid out" in refused and "Kit0" in refused, refused=refused)
    try:
        missed = uv_layout.finish(state, restore=True)
    except Exception as error:   # noqa: BLE001
        missed = repr(error)
    check("a_changed_mesh_is_named_not_written", missed == ["Kit0"], missed=missed)
    check("an_unchanged_mesh_is_put_back", islands_of(kits[1]) == untouched)

    fresh()
    kit = boxes("Kit", 4, 50)
    edit(kit)
    spec, objects, state = uv_layout.begin([kit], {"texture_size": 512, "search": 0})
    kit.name = "Renamed"   # Renamed (or removed) before the cleanup: nothing to put back by its name.
    try:
        missed = uv_layout.finish(state, restore=True)
    except Exception as error:   # noqa: BLE001
        missed = repr(error)
    check("a_renamed_mesh_is_named_and_the_cleanup_finishes", missed == ["Kit"], missed=missed)

    from angelo_agent import uv_layout_ui
    writable = []
    for name, table, change in (("DEFAULTS", uv_layout.DEFAULTS, lambda t: t.__setitem__("rotate", False)),
                                ("PACKERS", uvm.PACKERS, lambda t: t.__setitem__("FAST", "CONCAVE")),
                                ("NAVIGATION", uv_layout_ui.NAVIGATION, lambda t: t.add("LEFTMOUSE"))):
        try:
            change(table)
            writable.append(name)
        except (TypeError, AttributeError):
            pass
    check("later_layouts_read_read_only_tables", not writable, writable=writable)


def too_little_room():
    """Never silent at near-zero coverage (uv_layout.rst, "Too little room"): the held-out set's triangle soup (3,000
    islands) and scan (10,731), smart-projected, at the default 4 mip levels on 2048 px, report too little room with a
    fix that clears the floor; an ordinary kit does not."""
    from mathutils import Vector, noise
    rng = random.Random(5303)

    def soup(bm):
        for _ in range(3000):
            base = Vector((rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(-10, 10)))
            size = 0.02 + rng.random() ** 3 * 1.5
            bm.faces.new([bm.verts.new(base + Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))) * size)
                          for _ in range(3)])

    def scan(bm):
        bmesh.ops.create_grid(bm, x_segments=120, y_segments=120, size=10.0)
        for v in bm.verts:
            v.co.z = 1.6 * noise.fractal(v.co * 0.9 + Vector((3.1, 9.4, 2.6)), 0.7, 2.2, 6)
    for name, build, angle, crowded in (("Soup", soup, 66.0, True), ("Scan", scan, 25.0, True),
                                        ("Kit", None, 66.0, False)):
        fresh()
        obj = boxes("Kit", 6, 40) if build is None else mesh_object(name, build)
        edit(obj)
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(angle), island_margin=0.0)
        room = uv_layout.run([obj], {"texture_size": 2048, "mips": 4, "search": 0, "packer": "FAST"})["room"]
        if crowded:
            fix = room["fits_with_mips"]
            check("too_little_room_said_for_" + name.lower(),
                  room["too_little"] and room["share"] < 1 / 16 and fix is not None and fix < 4, room=room)
        else:
            check("an_ordinary_kit_has_room", not room["too_little"] and room["share"] >= 1 / 16, room=room)
    fresh()
    density = boxes("Kit", 6, 41)
    edit(density)
    receipt = uv_layout.run([density], {"texture_size": 512, "density": 100, "mips": 2, "search": 0, "packer": "FAST"})
    check("a_fixed_density_is_not_judged_by_room", receipt["room"] is None, room=receipt["room"])


def adaptive_margin():
    """A margin nobody set (the coordinator's decision, 2026-11-18): each held-out mesh, laid out filling a 2048 px
    tile with no mips given, keeps 4 mip levels where its islands have room and otherwise drops to the most that leave
    them room, saying so in the receipt (soup to 3, scan to 2, the others unchanged); the same mesh with mips 4 given
    keeps 4 (``too_little_room`` checks its warning)."""
    import heldout_meshes
    expected = {"HardSurfaceKit": 4, "Organic": 4, "TriangleSoup": 3, "Architecture": 4, "Scan": 2}
    got = {}
    for build in heldout_meshes.HELD_OUT:
        fresh()
        obj, how = build()
        heldout_meshes.unwrap(obj, how)
        edit(obj)
        receipt = uv_layout.run([obj], {"texture_size": 2048, "search": 0, "packer": "FAST"})
        lowered = receipt["margin_lowered"]
        got[obj.name] = {"mips": receipt["mips_kept"], "lowered": lowered, "share": receipt["room"]["share"]}
        said = (lowered is None) == (receipt["mips_kept"] == 4) and (lowered is None or (
            lowered["from"] == 4 and lowered["to"] == receipt["mips_kept"]))
        check("margin_" + obj.name.lower(), receipt["mips_kept"] == expected[obj.name] and said
              and not receipt["room"]["too_little"], got=got[obj.name])


try:
    search_never_looser()
    density_and_tiles()
    padding()
    stacking_and_groups()
    operator_exec()
    changed_meanwhile()
    too_little_room()
    adaptive_margin()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("UV_LAYOUT failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
