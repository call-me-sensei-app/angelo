# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tiny scenes and checks for the background-job suites (test_blender_long_jobs, test_blender_job_card).

Blender-only helpers (they import ``bpy``); no provider, account or network.
Scenes are small enough to run on the CPU in seconds, yet slow enough per frame
or sample that a cancel lands while the job runs.
"""

import hashlib
import json
from array import array
from pathlib import Path

import bpy


def cycles_scene(scene):
    """Cycles on the CPU, 16 samples, 64x64, frames 1-40: the person's settings jobs must restore."""
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.frame_start, scene.frame_end = 1, 40
    scene.render.filepath = "/tmp/"


def is_png(path, width, height):
    """A whole PNG of this size: signature, IHDR size and a final IEND chunk."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    return (data[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(data[16:20], "big") == width
            and int.from_bytes(data[20:24], "big") == height and data[-8:-4] == b"IEND")


def pixels(image):
    values = array('f', [0.0]) * len(image.pixels)
    image.pixels.foreach_get(values)
    return hashlib.sha256(values.tobytes()).hexdigest()


def bake_plane(scene):
    """A UV-mapped plane above the default cube with one material baking into a 128x128 image."""
    mesh = bpy.data.meshes.new("BakePlane")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    layer = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(layer.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = uv
    plane = bpy.data.objects.new("BakePlane", mesh)
    plane.location = (0, 0, 2)
    scene.collection.objects.link(plane)
    material = bpy.data.materials.new("BakeMaterial")
    mesh.materials.append(material)
    image = bpy.data.images.new("BakeTarget", 128, 128)
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material.node_tree.nodes.active = node
    return plane, image


def _grid(name, scene, location, cuts):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cuts, y_subdivisions=cuts, size=2, location=location)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    return obj


def prebaked_softbody(scene):
    """A soft body the person already baked (frames 1-10): a physics job must leave it as it is."""
    obj = _grid("PreBaked", scene, (4, 0, 1), 4)
    cache = obj.modifiers.new("Softbody", 'SOFT_BODY').point_cache
    cache.frame_end = 10
    with bpy.context.temp_override(point_cache=cache):
        bpy.ops.ptcache.bake(bake=True)
    assert cache.is_baked
    return cache


def cloth(scene, frame_end):
    """A 60x60 cloth grid: a few milliseconds a frame."""
    obj = _grid("ClothGrid", scene, (0, 4, 2), 60)
    cache = obj.modifiers.new("Cloth", 'CLOTH').point_cache
    cache.frame_end = frame_end
    return cache


def clear_physics(scene):
    bpy.ops.ptcache.free_bake_all()
    for name in ("PreBaked", "ClothGrid"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj)


def heavy_animation(obj):
    obj.location.z = 0
    obj.keyframe_insert("location", frame=1)
    obj.location.z = 3
    obj.keyframe_insert("location", frame=250)


def remove_heavy_animation(obj):
    obj.animation_data_clear()
    obj.location.z = 2


def _simulation_tree(grid):
    """Stores one quad but evaluates a grid x grid mesh every frame: slow to bake, tiny when packed."""
    tree = bpy.data.node_groups.new("JobSimulation", 'GeometryNodeTree')
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    group_in, group_out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    sim_in, sim_out = nodes.new("GeometryNodeSimulationInput"), nodes.new("GeometryNodeSimulationOutput")
    sim_in.pair_with_output(sim_out)
    big = nodes.new("GeometryNodeMeshGrid")
    big.inputs["Vertices X"].default_value = big.inputs["Vertices Y"].default_value = grid
    statistic = nodes.new("GeometryNodeAttributeStatistic")
    statistic.data_type = 'FLOAT_VECTOR'
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation, scale.inputs["Scale"].default_value = 'SCALE', 1e-6
    offset = nodes.new("ShaderNodeVectorMath")
    offset.operation, offset.inputs[1].default_value = 'ADD', (0, 0, 0.001)
    move = nodes.new("GeometryNodeSetPosition")
    links.new(group_in.outputs[0], sim_in.inputs[0])
    links.new(big.outputs[0], statistic.inputs["Geometry"])
    links.new(nodes.new("GeometryNodeInputPosition").outputs[0], statistic.inputs["Attribute"])
    links.new(statistic.outputs["Mean"], scale.inputs[0])
    links.new(scale.outputs[0], offset.inputs[0])
    links.new(sim_in.outputs[1], move.inputs[0])
    links.new(offset.outputs[0], move.inputs["Offset"])
    links.new(move.outputs[0], sim_out.inputs[1])
    links.new(sim_out.outputs[0], group_out.inputs[0])
    return tree


def simulation(scene, grid=600, frames=250):
    """An object whose simulation zone moves its quad up 0.001 per frame."""
    mesh = bpy.data.meshes.new("SimHost")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new("SimHost", mesh)
    obj.location = (-4, 0, 0)
    scene.collection.objects.link(obj)
    obj.modifiers.new("Simulation", 'NODES').node_group = _simulation_tree(grid)
    scene.frame_start, scene.frame_end = 1, frames
    scene.frame_set(1)
    return obj


def has_bake(obj, frame=10):
    """Whether the simulation has baked frames: jumping to ``frame`` shows its ninth step only from a bake."""
    scene = bpy.context.scene
    scene.frame_set(1)
    scene.frame_set(frame)
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    height = evaluated.data.vertices[0].co.z
    scene.frame_set(1)
    return height > 0.005


def crash_reconciliation(state_dir, workspace):
    """A record left running by a crash: interrupted on the next start, its staging (only) removed."""
    from angelo_agent import job_outputs, job_ui
    from angelo_agent.job_manager import JobManager
    from angelo_agent.job_store import JobStore, new_record
    from angelo_agent.files import FileWorkspace
    state_dir.mkdir(parents=True, exist_ok=True)
    output = job_outputs.admit_output(FileWorkspace(workspace), "crashed/anim")
    held = job_outputs.Staging(workspace, output, "j7").create()
    staging = held.path
    (staging / "frame_0001.png").write_bytes(b"partial")
    held._chain.close()  # The process ended: nothing is held any more.
    foreign = workspace / "crashed/.angelo-job-j8"
    foreign.mkdir()  # No marker: not a job's folder, so it stays.
    record = new_record("j7", "chat", "call", {"kind": "RENDER_ANIMATION", "output": "crashed/anim", "minutes": 5.0},
                        now=1.0)
    record.update(status="running", started=1.0, detail={"staging": str(staging)})
    store = JobStore(state_dir)
    store.add(record)
    manager = JobManager(JobStore(state_dir))
    job_ui._reconcile(manager)
    saved = json.loads((state_dir / "jobs.json").read_text())["jobs"][0]
    result = (saved["status"] == "interrupted" and saved["reason"] == "app_closed" and "staging" not in saved["detail"]
              and saved["receipt"]["note"] == job_ui.APP_CLOSED and not staging.exists() and foreign.is_dir())
    foreign.rmdir()
    return result


def packed_texture_material(obj):
    """A material whose Base Color is a packed 8x8 image, as a USD probe used."""
    image = bpy.data.images.new("JobTexture", width=8, height=8)
    image.generated_color = (1, 0, 0, 1)
    image.pack()
    material = bpy.data.materials.new("JobTextured")
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material.node_tree.links.new(node.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.clear()
    obj.data.materials.append(material)


DANGLING_USDA = """#usda 1.0
def Shader "Texture"
{
    asset inputs:file = @./textures/missing.png@
}
"""


def dangling_usd_refused(window, workspace):
    """An export whose USD references a file that isn't there: failed, nothing published, staging removed."""
    from angelo_agent import job_outputs, job_runners
    from angelo_agent.files import FileWorkspace
    from angelo_agent.job_spec import validate_job
    request = validate_job("angelo_job_start", {"kind": "EXPORT_USD", "output": "exports/dangling.usda"})
    runner = job_runners.build(window, request, FileWorkspace(workspace))
    runner.identity = "jd"
    runner.staging = job_outputs.Staging(workspace, runner.output, "jd").create()
    (runner.staging.path / "dangling.usda").write_text(DANGLING_USDA)
    status, reason, receipt = runner.finish(False)
    return (status == "failed" and reason == job_runners.DANGLING and receipt.get("missing") == ["missing.png"]
            and not (workspace / "exports/dangling.usda").exists()
            and not (workspace / "exports/.angelo-job-jd").exists())
