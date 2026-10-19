# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""angelo_view's OUTLINE on every kind of shape, DEPTH, a pair's delivery and the free cameras' clip planes.

Run by ``run_native_suites.py --gui --include test_blender_view_outline_shapes`` (offscreen drawing needs a
window). The reference views' edge cases (a torus seen through its hole, Geometry
Nodes instances, particle hair, sparse hair curves, a comb next to hair, an empty hair object, a box beside the
camera, hair past its emitter's bounds, a 1,024-pixel pair), each checked against Blender itself:

* self outline: a target's own picture, drawn by Blender, attached and compared by OUTLINE at the same camera
  must match the silhouette the host draws (by colour and as a cut-out);
* DEPTH's nearest distance is the one Blender's own ray cast finds;
* a pair at 1,024 is deliverable (at most 2,000 px wide, under the 5 MiB image limit, admitted by tool_images);
* the silhouette draw puts the GPU depth state back; a box outside the frame leaves the clip planes alone;
  particle hair near the camera is not cut;
* every call leaves the document, viewports and data-blocks unchanged.
Inspect results.json; a zero exit status alone is not proof.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import numpy as np

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-outline-shapes-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(HERE)]
from angelo_agent import scene_queries, view_cache, view_silhouette  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from view_outline_kit import Wrapped, attach, decode  # noqa: E402

report = {"checks": {}, "measurements": {}, "binary": bpy.app.binary_path}
ctx = {}
FRONT = {"location": [0.0, -4.0, 0.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 3.0}
TOP = {"location": [0.0, 0.0, 4.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 3.0}
# A shape's own picture compared with its silhouette: within a pixel for surfaces; strands, which Blender draws
# antialiased and the host as lines, within two at the 95th percentile. Legacy particle hair, whose strands
# Workbench draws at its own width, measured 1.0 to 3.6 pixels over six seeds (agent_execution.rst, "Over the
# view, or as outlines"): its bound is that class's, at one fixed seed.
SURFACE_P95, STRANDS_P95, PARTICLE_P95 = 1.0, 2.5, 4.0
SAME_IOU, PARTICLE_IOU = 0.95, 0.93   # Particle hair over the six seeds: 0.940 to 0.963.


def check(name, condition):
    report["checks"][name] = bool(condition)


def link(obj):
    ctx["window"].scene.collection.objects.link(obj)
    return obj


def only(*shown):
    for obj in ctx["window"].scene.objects:
        obj.hide_viewport = obj not in shown and obj.type not in ("CAMERA", "LIGHT")
    ctx["window"].view_layer.update()


def node_group(name, build):
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    build(group, group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput"))
    return group


def hair(name, count, points=6, radius=0.004):
    rng = np.random.default_rng(1)
    theta = rng.uniform(0.0, 2.0 * np.pi, count)
    steps = np.arange(points)[None, :]
    positions = np.zeros((count, points, 3), np.float32)
    positions[..., 0] = np.cos(theta)[:, None] * (0.3 + 0.08 * steps)
    positions[..., 2] = np.sin(theta)[:, None] * (0.3 + 0.08 * steps)
    curves = bpy.data.hair_curves.new(name)
    if count:
        curves.add_curves([points] * count)
        curves.position_data.foreach_set("vector", positions.ravel())
        values = curves.attributes.get("radius") or curves.attributes.new("radius", "FLOAT", "POINT")
        values.data.foreach_set("value", np.full(count * points, radius, np.float32))
    return link(bpy.data.objects.new(name, curves))


def self_outline(targets, camera, label, cut_out=False):
    """The targets' own picture as the reference, compared by OUTLINE at the same camera: the outline record."""
    view_cache.clear()
    handles = [scene_queries.object_handle(obj) for obj in targets]
    drawn = ctx["wrapped"].view({"views": [dict(camera)], "targets": handles}, label=label + " picture")
    rows = decode(drawn["views"][0]["path"])
    alpha = np.where(np.abs(rows - rows[0, 0]).max(axis=2) > 3, 255, 255 if not cut_out else 0)
    reference = attach(ctx["refs"], OUT, label + ".png", np.concatenate([rows, alpha[..., None]], axis=2))
    result = ctx["wrapped"].view({"views": [dict(camera, reference=reference, compare="OUTLINE")], "targets": handles},
                                 label=label + " outline")
    found = dict(result["views"][0]["outline"], uncertainty=[note["code"] for note in result["uncertainty"]])
    report["measurements"][label] = found
    return found


def same(found, p95, iou=SAME_IOU):
    return found.get("compared") and found["iou"] >= iou and found["p95_px"] <= p95 and not found["uncertainty"]


# ---- Steps --------------------------------------------------------------------------------------------------------

def setup():
    window = bpy.context.window_manager.windows[0]
    ctx["window"] = window
    window.scene.render.resolution_x = window.scene.render.resolution_y = 512
    bpy.data.objects.remove(window.scene.objects["Cube"])
    ctx["refs"] = ReferenceStore(OUT, "5d7c1f2e-8a4b-4c3d-9e1f-2a3b4c5d6e7f")
    executor = BlenderExecutor(window)
    executor.observation_store = ctx["refs"]
    ctx["wrapped"] = Wrapped(window, executor)
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)


def surfaces():
    bpy.ops.mesh.primitive_torus_add(major_radius=0.8, minor_radius=0.25)
    torus = bpy.context.active_object
    only(torus)
    for cut_out in (False, True):
        found = self_outline([torus], TOP, "torus" + ("-cut-out" if cut_out else ""), cut_out)
        check("torus_matches_itself" + ("_as_a_cut_out" if cut_out else ""), same(found, SURFACE_P95)
              and found["reference"]["holes"] == ("COMPARED" if cut_out else "FILLED"))

    def leaves(group, given, made):
        line = group.nodes.new("GeometryNodeMeshLine")
        line.inputs["Count"].default_value = 7
        line.inputs["Start Location"].default_value = (-0.6, 0.0, 0.45)
        line.inputs["Offset"].default_value = (0.2, 0.0, 0.0)
        sphere = group.nodes.new("GeometryNodeMeshIcoSphere")
        sphere.inputs["Radius"].default_value = 0.14
        place = group.nodes.new("GeometryNodeInstanceOnPoints")
        join = group.nodes.new("GeometryNodeJoinGeometry")
        group.links.new(line.outputs["Mesh"], place.inputs["Points"])
        group.links.new(sphere.outputs["Mesh"], place.inputs["Instance"])
        group.links.new(given.outputs[0], join.inputs[0])
        group.links.new(place.outputs["Instances"], join.inputs[0])
        group.links.new(join.outputs[0], made.inputs[0])
    bpy.ops.mesh.primitive_cylinder_add(radius=0.08, depth=0.9)
    trunk = bpy.context.active_object
    trunk.modifiers.new("Leaves", "NODES").node_group = node_group("Leaves", leaves)
    only(trunk)
    check("geometry_nodes_instances_match_themselves", same(self_outline([trunk], FRONT, "instances"), SURFACE_P95))


def strands():
    dense, sparse = hair("Dense", 1500), hair("Sparse", 400)
    for obj, name in ((dense, "dense"), (sparse, "sparse")):
        only(obj)
        check("%s_hair_matches_itself" % name, same(self_outline([obj], FRONT, name + "-hair"), STRANDS_P95))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.4)
    ball = bpy.context.active_object
    ball.modifiers.new("Hair", "PARTICLE_SYSTEM")
    settings = ball.particle_systems[0].settings
    settings.type, settings.count, settings.hair_length = "HAIR", 300, 0.6
    ball.particle_systems[0].seed = 1   # The worst of the six seeds measured.
    only(ball)
    check("particle_hair_matches_itself", same(self_outline([ball], FRONT, "particle-hair"), PARTICLE_P95,
                                               PARTICLE_IOU))
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-0.6, 0.0, -0.9))
    comb = bpy.context.active_object
    comb.scale = (0.1, 0.1, 0.4)
    teeth = comb.modifiers.new("Teeth", "ARRAY")     # Ten teeth, slots a few pixels wide between them.
    teeth.count, teeth.relative_offset_displace = 10, (1.25, 0.0, 0.0)
    only(comb, sparse)
    found = self_outline([comb, sparse], FRONT, "comb-and-hair")
    check("a_mesh_beside_hair_matches_itself", same(found, STRANDS_P95))
    empty = hair("Empty", 0)
    only(empty, dense)
    found = self_outline([empty, dense], FRONT, "empty-hair-object")
    check("an_empty_hair_object_draws_nothing_and_says_so", "outline_targets_not_drawn" in found["uncertainty"]
          and found.get("not_drawn") == 1)


def depth():
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.4, segments=64, ring_count=32)
    ball = bpy.context.active_object
    only(ball)
    view_cache.clear()
    result = ctx["wrapped"].view({"views": [dict(FRONT)], "targets": [scene_queries.object_handle(ball)],
                                  "shading": "DEPTH"}, label="depth")
    record = result["views"][0]
    window = ctx["window"]
    hit, location, *_rest = window.scene.ray_cast(window.view_layer.depsgraph, FRONT["location"], (0.0, 1.0, 0.0))
    nearest = location[1] - FRONT["location"][1] if hit else None
    picture = decode(record["path"])
    pixel = FRONT["ortho_scale"] / 512
    report["measurements"]["depth"] = dict(record["depth"], blender_nearest=nearest)
    check("depth_nearest_is_blenders_ray_cast", nearest is not None
          and abs(record["depth"]["near_world"] - nearest) <= pixel)
    check("depth_picture_is_white_nearest_and_black_around", picture[256, 256].min() >= 250 and picture[5, 5].max() == 0
          and result["note"].startswith("Depth drawn by the host"))


def delivery():
    """A 1,024 pair beside a photo-like reference is fitted to a deliverable picture."""
    noise = np.random.default_rng(3).integers(0, 256, (1024, 1024, 4)).astype(np.uint8)
    noise[..., 3] = 255
    reference = attach(ctx["refs"], OUT, "photo.png", noise)
    view_cache.clear()
    result = ctx["wrapped"].view({"views": [dict(FRONT, reference=reference)], "shading": "MATERIAL",
                                  "resolution": 1024}, label="pair at 1024")
    record = result["views"][0]
    size = Path(record["path"]).stat().st_size
    try:
        ctx["refs"].tool_images(result)
        admitted = True
    except ValueError:
        admitted = False
    report["measurements"]["pair_1024"] = {"picture": [record["width"], record["height"]], "bytes": size}
    check("a_1024_pair_is_deliverable", record["width"] <= 2000 and size < 5 * 1024 * 1024 and admitted)


def gpu_state():
    import gpu
    items, _facts = view_silhouette.geometry(ctx["window"], [obj for obj in ctx["window"].scene.objects
                                                             if not obj.hide_viewport][:1])
    camera = {"view_matrix": np.eye(4).tolist(), "projection_matrix": np.eye(4).tolist(), "details": {}}
    for test, write in (("NONE", False), ("LESS_EQUAL", True)):
        gpu.state.depth_test_set(test)
        gpu.state.depth_mask_set(write)
        view_silhouette.draw(items, camera, 64, 64)
        check("silhouette_puts_depth_state_back_%s" % test.lower(),
              (gpu.state.depth_test_get(), gpu.state.depth_mask_get()) == (test, write))
    gpu.state.depth_test_set("NONE")
    gpu.state.depth_mask_set(False)


def clip_planes():
    """A pebble beside the camera, out of frame, leaves the clip planes alone; particle hair near it is kept."""
    bpy.ops.mesh.primitive_torus_add(major_radius=0.8, minor_radius=0.25)
    torus = bpy.context.active_object
    bpy.ops.mesh.primitive_plane_add(size=3000.0, location=(0.0, 0.0, -1.0))
    ground = bpy.context.active_object
    camera = {"location": [0.0, -4.0, 0.5], "look_at": [0.0, 0.0, 0.0], "lens": 50.0}
    only(torus, ground)
    view_cache.clear()
    before = ctx["wrapped"].view({"views": [dict(camera)]}, label="clip without pebble")["views"][0]["camera"]
    bpy.ops.mesh.primitive_cube_add(size=0.004, location=(1.0, -3.984, 0.5))
    pebble = bpy.context.active_object
    only(torus, ground, pebble)
    view_cache.clear()
    result = ctx["wrapped"].view({"views": [dict(camera)]}, label="clip with pebble out of frame")
    after = result["views"][0]["camera"]
    check("a_box_outside_the_frame_leaves_the_clip_alone", after["clip_start"] == before["clip_start"]
          and "camera_clip_cuts_near_geometry" not in {note["code"] for note in result["uncertainty"]})
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3)
    ball = bpy.context.active_object
    ball.modifiers.new("Hair", "PARTICLE_SYSTEM")
    settings = ball.particle_systems[0].settings
    settings.type, settings.count, settings.hair_length = "HAIR", 300, 0.6
    only(ball)
    view_cache.clear()
    near = {"location": [0.0, -1.2, 0.0], "look_at": [0.0, 0.0, 0.0], "lens": 30.0}
    hair_camera = ctx["wrapped"].view({"views": [dict(near)]}, label="near particle hair")["views"][0]["camera"]
    report["measurements"]["clip"] = {"pebble": [before["clip_start"], after["clip_start"]],
                                      "particle_hair_clip_start": hair_camera["clip_start"]}
    # The hair reaches 0.9 from the centre, 0.3 from the camera: a near plane past that would cut it.
    check("particle_hair_near_the_camera_is_not_cut", hair_camera["clip_start"] < 0.3)


def read_only():
    report["measurements"]["wrapped_calls"] = len(ctx["wrapped"].calls)
    report["measurements"]["changed_calls"] = ctx["wrapped"].changed()
    check("every_call_left_the_document_viewports_and_data_unchanged",
          len(ctx["wrapped"].calls) >= 15 and not report["measurements"]["changed_calls"])


STEPS = [setup, surfaces, strands, depth, delivery, gpu_state, clip_planes, read_only]


def run():
    index = ctx.setdefault("step", 0)
    try:
        with bpy.context.temp_override(window=ctx.get("window") or bpy.context.window_manager.windows[0]):
            STEPS[index]()
    except Exception:
        report.update(success=False, failed_step=STEPS[index].__name__, error=traceback.format_exc())
        return finish()
    ctx["step"] += 1
    if ctx["step"] < len(STEPS):
        return 0.25
    report["success"] = all(report["checks"].values())
    return finish()


def finish():
    report.setdefault("success", False)
    if "wrapped" in ctx:
        ctx["wrapped"].close()
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_VIEW_OUTLINE_SHAPES", json.dumps({"success": report["success"], "failed": sorted(
        name for name, passed in report["checks"].items() if not passed)}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
