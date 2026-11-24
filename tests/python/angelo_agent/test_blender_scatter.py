# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scattering with biomes in the running app (doc/angelo/scatter.rst, "Validation").

Background. On a terrain whose slope and height are known in closed form (flat where x < 0, 45 degrees where x > 0)
every instance a rule places is checked against the rule: slope, absolute and relative height, a vertex-group mask,
and distance from a path (keep away, stay near). Then: no two instances closer than the sum of their collision radii
across layers; the same seed gives the same instances and another seed others; Density Scale, Proxy and Viewport
Display change the system without a rebuild; camera culling keeps only what the camera can see (widened by the
radius) and the cull distance; the surface and assets are unchanged; ``angelo.scatter`` from its properties alone
(the agent's path) makes one object and one node group, and refuses a Library item, layers with a biome, and a call
with neither; every biome preset builds with stand-in objects. Writes scatter.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
import math
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

from angelo_agent import scatter_ops, scatter_spec, scatter_system  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-scatter"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"phase": "start", "success": False, "checks": {}, "details": {}, "facts": {}}
SIZE, CELL = 20.0, 20.0 / 80     # The terrain: 20 m square, 80 cells a side.
EDGE = CELL * 1.5                # A point near a rule's edge may sit on a face that straddles it.


def write_report():
    (OUT / "scatter.json").write_text(json.dumps(REPORT, indent=1, default=float), encoding="utf-8")


def check(name, passed, detail=""):
    REPORT["checks"][name] = bool(passed)
    REPORT["details"][name] = detail


def digest(obj):
    co = np.zeros(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    return hashlib.sha256(np.round(co, 6).tobytes() + repr(obj.matrix_world[:]).encode()).hexdigest()


def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=80, y_subdivisions=80, size=SIZE)
    ground = bpy.context.active_object
    ground.name = "Ground"
    for vertex in ground.data.vertices:
        vertex.co.z = max(0.0, vertex.co.x)            # 0 on the flat, rising to 10 at the far edge.
    mask = ground.vertex_groups.new(name="paint")
    mask.add([v.index for v in ground.data.vertices if v.co.y > 0.0], 1.0, "REPLACE")
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, -40))
    tree = bpy.context.active_object
    tree.name = "Tree"
    bpy.ops.mesh.primitive_cylinder_add(radius=0.15, depth=0.4, location=(3, 0, -40))
    rock = bpy.context.active_object
    rock.name = "Rock"
    curve = bpy.data.curves.new("Path", "CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (-SIZE, 0, 0, 1), (SIZE, 0, 0, 1)
    path = bpy.data.objects.new("Path", curve)
    bpy.context.scene.collection.objects.link(path)
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (-12.0, 0.0, 4.0)
    camera.rotation_euler = (math.radians(80), 0.0, math.radians(-90))   # Looking along +x, a little down.
    bpy.context.scene.camera = camera
    bpy.context.view_layer.update()
    return ground, tree, rock, path, camera


def system(ground, layers, assets, **inputs):
    made, radii = scatter_system.create(bpy.context, ground, scatter_spec.check_layers(layers), assets)
    if inputs:
        scatter_system.set_inputs(made, inputs)
    return made, radii, facts(made)


def facts(made):
    return scatter_system.facts(made, bpy.context.evaluated_depsgraph_get())


def one(name, assets_count=1, **layer):
    return dict({"name": name, "assets": [{"object": "x"}] * assets_count, "density": 1.0, "seed": 1}, **layer)


def rules(ground, tree, rock, path):
    cases = {
        "slope": ({"slope": {"min": 0, "max": 20}}, lambda p: p[:, 0] <= EDGE),
        "height": ({"height": {"min": 2, "max": 6}}, lambda p: (p[:, 2] >= 2 - EDGE) & (p[:, 2] <= 6 + EDGE)),
        "relative_height": ({"height": {"min": 0.5, "max": 1.0, "relative": True}}, lambda p: p[:, 2] >= 5 - EDGE),
        "mask": ({"mask": {"name": "paint"}}, lambda p: p[:, 1] >= -EDGE),
        "mask_inverted": ({"mask": {"name": "paint", "invert": True}}, lambda p: p[:, 1] <= EDGE),
        # The path runs along the x axis at z = 0: a point's distance to it is |(y, z)|, in 3D like the rule's.
        "keep_away": ({"distance": {"objects": ["Path"], "keep_away": 3}},
                      lambda p: np.hypot(p[:, 1], p[:, 2]) >= 3 - EDGE),
        "near": ({"distance": {"objects": ["Path"], "near": 2}}, lambda p: np.hypot(p[:, 1], p[:, 2]) <= 2 + EDGE),
    }
    for name, (rule, holds) in cases.items():
        made, _radii, got = system(ground, [one(name, rules=rule)], [[rock]])
        positions = got["positions"]
        bad = int((~holds(positions)).sum()) if len(positions) else 0
        check("rule_" + name, len(positions) > 20 and bad == 0, "%d instances, %d break the rule" % (len(positions),
                                                                                                   bad))
        bpy.data.objects.remove(made)


def spacing_and_lifecycle(ground, tree, rock, path, camera):
    before = {obj.name: digest(obj) for obj in (ground, tree, rock)}
    layers = [one("Trees", 2, density=0.4, rules={"slope": {"min": 0, "max": 30}}, seed=4,
                  transform={"scale": [0.8, 1.4], "tilt": 5}),
              one("Rocks", density=3.0, seed=5)]
    start = time.perf_counter()
    made, radii, got = system(ground, layers, [[tree, rock], [rock]])
    REPORT["facts"]["two_layer_build_ms"] = round((time.perf_counter() - start) * 1e3, 1)
    REPORT["facts"]["two_layer_counts"] = got["layers"]
    ratio = scatter_system.min_spacing(got["positions"], got["layer"], radii)
    check("spacing_across_layers", ratio >= 1.0 - scatter_ops.SPACING_TOLERANCE and all(got["layers"]),
          "closest pair at %.4f of their radii; counts %s" % (ratio, got["layers"]))
    again, _radii, same = system(ground, layers, [[tree, rock], [rock]])
    check("same_seed_same_instances", np.array_equal(np.round(same["positions"], 5), np.round(got["positions"], 5)))
    scatter_system.set_inputs(again, {"Seed": 99})
    other = facts(again)
    check("other_seed_other_instances", len(other["positions"]) and not (
        len(other["positions"]) == len(got["positions"]) and np.allclose(other["positions"], got["positions"])))
    bpy.data.objects.remove(again)
    scatter_system.set_inputs(made, {"Density Scale": 0.5})
    half = facts(made)["layers"][0]
    check("density_scale_halves", abs(half - got["layers"][0] / 2.0) <= max(2, 0.15 * got["layers"][0]),
          "%d of %d" % (half, got["layers"][0]))
    scatter_system.set_inputs(made, {"Density Scale": 1.0, "Viewport Display": 0.5})
    shown = facts(made)["instances"]
    check("viewport_display_share", 0.3 * got["instances"] <= shown <= 0.7 * got["instances"],
          "%d of %d" % (shown, got["instances"]))
    scatter_system.set_inputs(made, {"Viewport Display": 1.0, "Proxy": True})
    check("proxy_draws_points", facts(made)["instances"] == 0)
    scatter_system.set_inputs(made, {"Proxy": False})
    culling(made, radii, got, camera)
    after = {obj.name: digest(obj) for obj in (ground, tree, rock)}
    check("surface_and_assets_unchanged", before == after)
    bpy.data.objects.remove(made)


def culling(made, radii, full, camera):
    scene_ = bpy.context.scene
    scatter_system.set_culling(made, scene_, camera, viewport=True, render=False, distance=0.0)
    got = facts(made)
    tan_x, tan_y = scatter_system.camera_tangents(camera, scene_)
    inverse = np.array(camera.matrix_world.inverted())
    local = got["positions"] @ inverse[:3, :3].T + inverse[:3, 3]
    radius = np.asarray(radii)[got["layer"]]
    depth = -local[:, 2]
    inside = (depth > -radius) & (np.abs(local[:, 0]) <= np.maximum(depth, 0) * tan_x + radius + 1e-4) \
        & (np.abs(local[:, 1]) <= np.maximum(depth, 0) * tan_y + radius + 1e-4)
    check("culling_keeps_the_view", 0 < got["instances"] < full["instances"] and bool(inside.all()),
          "%d of %d kept, %d outside" % (got["instances"], full["instances"], int((~inside).sum())))
    scatter_system.set_culling(made, scene_, camera, viewport=True, render=False, distance=8.0)
    near = facts(made)
    distance = np.linalg.norm(near["positions"] - np.array(camera.location), axis=1)
    check("cull_distance", 0 < near["instances"] < got["instances"]
          and bool((distance <= 8.0 + np.asarray(radii)[near["layer"]] + 1e-4).all()),
          "%d kept within 8 m" % near["instances"])
    scatter_system.set_culling(made, scene_, camera, viewport=False, render=True, distance=0.0)
    check("render_culling_leaves_the_viewport", facts(made)["instances"] == full["instances"])
    scatter_system.set_culling(made, scene_, None, viewport=True, render=False, distance=0.0)
    check("no_camera_no_culling", facts(made)["instances"] == full["instances"])


def operator(ground, rock):
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = ground
    objects, groups = set(bpy.data.objects), set(bpy.data.node_groups)
    layers = json.dumps([one("Rocks", density=1.0, seed=2) | {"assets": [{"object": "Rock"}]}])
    result = bpy.ops.angelo.scatter("EXEC_DEFAULT", layers=layers, seed=3)
    new_objects, new_groups = set(bpy.data.objects) - objects, set(bpy.data.node_groups) - groups
    made = next(iter(new_objects), None)
    count = facts(made)["instances"] if made else 0
    check("operator_one_object_one_group", result == {"FINISHED"} and len(new_objects) == 1 and len(new_groups) == 1
          and count > 0, "%s, %d object(s), %d group(s), %d instances" % (result, len(new_objects), len(new_groups),
                                                                         count))
    refusals = {
        "library_item": dict(layers=json.dumps([one("L") | {"assets": [{"query": "pine tree"}]}])),
        "layers_and_biome": dict(layers=layers, biome="meadow"),
        "neither": {},
    }
    for name, props in refusals.items():
        before = set(bpy.data.objects)
        try:
            refused = bpy.ops.angelo.scatter("EXEC_DEFAULT", **props) == {"CANCELLED"}
        except RuntimeError as error:   # Background Blender raises an operator's ERROR report.
            refused = True
            REPORT["facts"]["refusal_" + name] = str(error).strip()[:200]
        check("refuses_" + name, refused and set(bpy.data.objects) == before)


def biomes(ground, rock):
    timings = {}
    for name in scatter_ops.BIOMES:
        path = scatter_spec.PRESET_DIR / ("%s.angelo-biome.json" % name)
        biome = scatter_spec.load_biome(path.read_text(encoding="utf-8"))
        assets = {layer["name"]: ["Rock"] for layer in biome["layers"]}
        bpy.context.view_layer.objects.active = ground
        before = set(bpy.data.objects)
        start = time.perf_counter()
        result = bpy.ops.angelo.scatter("EXEC_DEFAULT", biome=name, biome_assets=json.dumps(assets))
        timings[name] = round((time.perf_counter() - start) * 1e3, 1)
        made = next(iter(set(bpy.data.objects) - before), None)
        got = facts(made) if made else {"instances": 0, "layers": []}
        check("biome_" + name, result == {"FINISHED"} and made is not None,
              "%d layers, counts %s" % (len(biome["layers"]), got["layers"]))
        if made:
            bpy.data.objects.remove(made)
    REPORT["facts"]["biome_ms"] = timings


def main():
    try:
        if not hasattr(bpy.types, "ANGELO_OT_scatter"):
            from angelo_agent import scatter_ui
            scatter_ui.register()   # The add-on is not enabled in a factory-startup background run.
        ground, tree, rock, path, camera = scene()
        REPORT["phase"] = "rules"
        rules(ground, tree, rock, path)
        REPORT["phase"] = "spacing"
        spacing_and_lifecycle(ground, tree, rock, path, camera)
        REPORT["phase"] = "operator"
        operator(ground, rock)
        REPORT["phase"] = "biomes"
        biomes(ground, rock)
        REPORT["phase"] = "complete"
        REPORT["failed_checks"] = [name for name, passed in REPORT["checks"].items() if not passed]
        REPORT["success"] = bool(REPORT["checks"]) and not REPORT["failed_checks"]
    except Exception:
        REPORT["error"] = traceback.format_exc()
    write_report()
    print("ANGELO_SCATTER", "PASS" if REPORT["success"] else "FAIL")
    sys.exit(0 if REPORT["success"] else 1)


main()
