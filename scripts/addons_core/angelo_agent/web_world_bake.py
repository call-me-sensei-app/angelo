# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render a World into an equirectangular picture. Runs inside a background Blender, never in the app.

``Angelo -b --factory-startup --disable-autoexec --python web_world_bake.py -- <world.blend> <world name> <engine>
<out.exr> <width> <height> <samples> <frame>``

``web_environment`` writes the person's World (with what it uses) to ``world.blend`` and starts this. The World is
appended into the factory scene, which holds nothing else, and seen by a panoramic equirectangular camera whose
picture follows Blender's own Environment Texture convention (the same direction lands on the same pixel), so the
web gets exactly what the World shows a camera in every direction. Cycles renders it: only camera rays that hit the
World, so a few samples only smooth the edges. The output the scene's engine uses is the one rendered: the others
are removed from this copy. Its animation and drivers are removed too: the file holds the values they gave at the
moment it was written (Blender writes them back to the data it saves), which is what the World showed, while this
copy has none of the objects a driver reads. The scene is set to the person's frame, for image sequences and
movies. The picture is written as a linear 32-bit EXR. Standalone on purpose: no Angelo add-on runs in this
process.
"""

import math
import sys


def _keep_output(world, engine):
    """Remove every World Output but the one ``engine`` evaluates (Blender's rule: its own target, then ALL)."""
    tree = world.node_tree
    if tree is None:
        return
    target = {"CYCLES": "CYCLES", "BLENDER_EEVEE": "EEVEE"}.get(engine)
    outputs = [node for node in tree.nodes if node.type == "OUTPUT_WORLD"]
    pool = [node for node in outputs if target is not None and node.target == target] or \
        [node for node in outputs if node.target == "ALL"] or outputs
    pool.sort(key=lambda node: not node.is_active_output)
    for node in outputs:
        if pool and node != pool[0]:
            tree.nodes.remove(node)
    if pool:
        pool[0].target = "ALL"
        pool[0].is_active_output = True


def _still(world):
    """Clear the animation of ``world`` and of every node tree it uses: their saved values stay as they are."""
    trees, seen = [world.node_tree] if world.node_tree is not None else [], set()
    world.animation_data_clear()
    while trees:
        tree = trees.pop()
        if tree.name in seen:
            continue
        seen.add(tree.name)
        tree.animation_data_clear()
        trees.extend(node.node_tree for node in tree.nodes if getattr(node, "node_tree", None) is not None)


def bake(library, name, engine, out, width, height, samples, frame=1):
    import bpy
    with bpy.data.libraries.load(library, link=False) as (_source, target):
        target.worlds = [name]
    world = target.worlds[0]
    if world is None:
        raise RuntimeError("the World was not in the file")
    _keep_output(world, engine)
    _still(world)
    scene = bpy.context.scene
    scene.frame_set(frame)
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj)
    scene.world = world
    camera = bpy.data.cameras.new("Angelo World Bake")
    camera.type = "PANO"
    camera.panorama_type = "EQUIRECTANGULAR"
    holder = bpy.data.objects.new("Angelo World Bake", camera)
    scene.collection.objects.link(holder)
    scene.camera = holder
    holder.rotation_euler = (math.pi / 2.0, 0.0, -math.pi / 2.0)   # Blender's equirectangular world convention.
    render = scene.render
    render.engine = "CYCLES"
    render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
    render.film_transparent = False
    render.use_compositing = False
    render.use_sequencer = False
    scene.cycles.device = "CPU"   # Starts at once; a GPU's kernels may need minutes to load the first time.
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    settings = render.image_settings
    settings.file_format = "OPEN_EXR"
    settings.color_depth = "32"
    settings.exr_codec = "ZIP"
    render.filepath = out
    render.use_file_extension = False
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:]
    library, name, engine, out = arguments[:4]
    bake(library, name, engine, out, *(int(value) for value in arguments[4:8]))
