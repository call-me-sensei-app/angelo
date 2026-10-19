# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tiny scenes and result checks for the production job suites (high-to-low bakes, UDIM, stereo, disk caches).

Blender-only helpers (they import ``bpy``); no provider, account or network.
Every scene has a planted detail whose place in the result is known, so a suite
checks pixels and caches, not only that a job ended:

* ``high_pyramid`` is flat except for a pyramid over the +X +Y quarter of
  ``low_plane``. A tangent-space normal bake of it onto the plane is neutral
  everywhere else and leans towards +X, -X, +Y and -Y on the pyramid's four
  faces (``NORMAL_PROBES``).
"""

from array import array

import bpy

# UV positions on ``low_plane`` and the normal-map colour a high-to-low bake of ``high_pyramid`` gives there.
# The pyramid's faces rise 0.3 over a run of 0.5: their normals lean 0.514 off +Z (0.5 + 0.514 / 2 = 0.757).
NORMAL_PROBES = {"flat": (0.25, 0.25), "plus_x": (0.917, 0.75), "minus_x": (0.583, 0.75),
                 "plus_y": (0.75, 0.917), "minus_y": (0.75, 0.583)}
NEUTRAL, LEAN_UP, LEAN_DOWN = 0.502, 0.757, 0.243


def low_plane(scene, name="LowPlane"):
    """A 2x2 quad at the origin, UV-mapped one to one, with one node material and no bake image yet."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    layer = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(layer.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = uv
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    material = bpy.data.materials.new(name + "Material")
    if material.node_tree is None:
        material.use_nodes = True
    mesh.materials.append(material)
    return obj


def high_pyramid(scene, name="HighPyramid"):
    """Flat at z=0 except for a pyramid (apex 0.3) over the +X +Y quarter: the planted detail."""
    vertices = [(-1, -1, 0), (0, -1, 0), (1, -1, 0), (-1, 0, 0), (0, 0, 0), (1, 0, 0), (-1, 1, 0), (0, 1, 0),
                (1, 1, 0), (0.5, 0.5, 0.3)]
    faces = [(0, 1, 4, 3), (1, 2, 5, 4), (3, 4, 7, 6), (4, 5, 9), (5, 8, 9), (8, 7, 9), (7, 4, 9)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def cage_above(scene, low, height=0.6, name="Cage"):
    """The low mesh again (same faces and corners), raised above the pyramid: rays start here."""
    obj = bpy.data.objects.new(name, low.data.copy())
    obj.location = (0, 0, height)
    scene.collection.objects.link(obj)
    return obj


def data_image(name, size=64):
    """A black, non-colour image to bake a normal map into."""
    return bpy.data.images.new(name, size, size, is_data=True)


def pixel(image, uv):
    """RGBA of the first tile at a UV position, rounded to three places."""
    width, height = image.size
    values = array('f', [0.0]) * len(image.pixels)
    image.pixels.foreach_get(values)
    index = (int(uv[1] * height) * width + int(uv[0] * width)) * 4
    return [round(value, 3) for value in values[index:index + 4]]


def shows_pyramid(image, tolerance=0.03):
    """Whether a tangent-space normal map holds the pyramid: neutral on the flat part, each face leaning its way."""
    near = lambda value, expected: abs(value - expected) <= tolerance  # noqa: E731
    flat, plus_x, minus_x, plus_y, minus_y = (pixel(image, NORMAL_PROBES[key]) for key in
                                              ("flat", "plus_x", "minus_x", "plus_y", "minus_y"))
    return (near(flat[0], NEUTRAL) and near(flat[1], NEUTRAL) and near(flat[2], 1.0)
            and near(plus_x[0], LEAN_UP) and near(plus_x[1], NEUTRAL)
            and near(minus_x[0], LEAN_DOWN) and near(minus_x[1], NEUTRAL)
            and near(plus_y[1], LEAN_UP) and near(plus_y[0], NEUTRAL)
            and near(minus_y[1], LEAN_DOWN) and near(minus_y[0], NEUTRAL))


def is_flat_normal(image, tolerance=0.03):
    """Whether the pyramid quarter is neutral or untouched: nothing was hit there."""
    values = [pixel(image, NORMAL_PROBES[key]) for key in ("plus_x", "minus_x", "plus_y", "minus_y")]
    return all(abs(value[0] - value[1]) <= tolerance for value in values)


def selection_state(window):
    """What a job must leave exactly as the person had it: selection, active object and mode."""
    layer = window.view_layer
    active = layer.objects.active
    return {"selected": sorted(obj.name for obj in layer.objects if obj.select_get(view_layer=layer)),
            "active": active.name if active else None, "mode": active.mode if active else None,
            "context_mode": bpy.context.mode}


def node_state(material):
    """A material's node tree as the person arranged it: nodes, the active one, selection and the shown texture."""
    nodes = material.node_tree.nodes
    return {"nodes": sorted(node.name for node in nodes), "active": nodes.active.name if nodes.active else None,
            "selected": sorted(node.name for node in nodes if node.select),
            "textured": sorted(node.name for node in nodes if getattr(node, "show_texture", False)),
            "links": len(material.node_tree.links)}


def bake_settings(scene):
    """The person's own bake panel settings (a job passes its own values to the operator, never through these)."""
    bake = scene.render.bake
    return {name: getattr(bake, name) for name in ("use_selected_to_active", "use_cage", "cage_extrusion",
                                                    "max_ray_distance", "margin", "use_clear", "target")} | {
        "cage_object": bake.cage_object.name if bake.cage_object else None}


def wire_normal_map(material, image):
    """Use a baked normal map in a material: Image Texture -> Normal Map -> the Principled BSDF's Normal."""
    tree = material.node_tree
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    normal = tree.nodes.new("ShaderNodeNormalMap")
    shader = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
    tree.links.new(texture.outputs["Color"], normal.inputs["Color"])
    tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    for node in (texture, normal):
        node.select = False
    return texture


# Renders: stereo views and compositor File Output nodes ---------------------------------------------------

def stereo_scene(scene, far_apart=0.2):
    """Multi-view on with the default left and right views, the eyes far enough apart that the two differ.

    The left eye is the camera itself (Blender's default pivot); at 0.2 the
    right eye sees the default cube several pixels to the side in a 64-pixel frame.
    """
    scene.render.use_multiview = True
    scene.render.views_format = 'STEREO_3D'
    scene.camera.data.stereo.interocular_distance = far_apart
    scene.render.image_settings.views_format = 'INDIVIDUAL'


def compositor(scene, own_directory):
    """A compositor whose File Output nodes point at ``own_directory`` with a name template: the person's setup.

    ``Passes`` writes the image and its alpha as PNG and has an unlinked item
    (Blender writes nothing for it); ``Layers`` is a multi-layer EXR of both;
    ``Inner Out`` sits in a group; ``Muted`` is muted; ``Constant`` is fed one
    colour, not an image, so Blender writes no file for it either. Returns the tree.
    """
    tree = bpy.data.node_groups.new("JobCompositing", "CompositorNodeTree")
    tree.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    layers, result = tree.nodes.new("CompositorNodeRLayers"), tree.nodes.new("NodeGroupOutput")
    tree.links.new(layers.outputs["Image"], result.inputs[0])

    def file_output(owner, name, items, source, png=True):
        node = owner.nodes.new("CompositorNodeOutputFile")
        node.name = name
        if png:
            node.format.media_type, node.format.file_format = 'IMAGE', 'PNG'
        node.directory, node.file_name = str(own_directory), "{blend_name}_own_"
        for item, output in items:
            node.file_output_items.new('RGBA', item)
            if output is not None:
                owner.links.new(source.outputs[output], node.inputs[item])
        return node

    file_output(tree, "Passes", (("Beauty", "Image"), ("Mask", "Alpha"), ("Loose", None)), layers)
    file_output(tree, "Layers", (("Beauty", "Image"), ("Mask", "Alpha")), layers, png=False)
    file_output(tree, "Muted", (("Beauty", "Image"),), layers).mute = True
    file_output(tree, "Constant", (("Flat", 0),), tree.nodes.new("CompositorNodeRGB"))
    group = bpy.data.node_groups.new("JobInner", "CompositorNodeTree")
    group.interface.new_socket("Image", in_out='INPUT', socket_type='NodeSocketColor')
    file_output(group, "Inner Out", (("Grouped", 0),), group.nodes.new("NodeGroupInput"))
    instance = tree.nodes.new("CompositorNodeGroup")
    instance.name, instance.node_tree = "Inner", group
    tree.links.new(layers.outputs["Image"], instance.inputs[0])
    scene.compositing_node_group = tree
    return tree


def file_output_nodes(scene):
    """Every File Output node of the fixture compositor, the grouped one included, by name."""
    tree = scene.compositing_node_group
    found = {node.name: node for node in tree.nodes if node.bl_idname == "CompositorNodeOutputFile"}
    found["Inner Out"] = tree.nodes["Inner"].node_tree.nodes["Inner Out"]
    return found


def output_settings(scene):
    """Everything about where and how the scene's own renders are written: a job must leave it exactly so."""
    settings = scene.render.image_settings
    state = {"filepath": scene.render.filepath, "use_file_extension": scene.render.use_file_extension,
             "use_overwrite": scene.render.use_overwrite, "use_placeholder": scene.render.use_placeholder,
             "frame_step": scene.frame_step, "resolution": (scene.render.resolution_x, scene.render.resolution_y),
             "format": {name: getattr(settings, name) for name in ("media_type", "file_format", "color_mode",
                                                                   "color_depth", "views_format")}}
    if scene.compositing_node_group is not None:
        state["nodes"] = {name: (node.directory, node.file_name, node.use_file_extension, node.mute)
                          for name, node in file_output_nodes(scene).items()}
    return state


def files_below(folder):
    """Relative posix paths of every file below ``folder``, hidden ones included."""
    from pathlib import Path
    folder = Path(folder)
    return sorted(str(path.relative_to(folder).as_posix()) for path in folder.rglob("*") if path.is_file())


def image_stats(path):
    """``(width, height, mean of each channel)`` of an image file, read as a temporary image."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        values = array('f', [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        count = max(1, len(values) // 4)
        return image.size[0], image.size[1], [round(sum(values[channel::4]) / count, 4) for channel in range(4)]
    finally:
        bpy.data.images.remove(image)


def pixel_hash(path):
    """A digest of an image file's decoded pixels (files also carry the date and render time, so bytes differ)."""
    import hashlib
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        values = array('f', [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        return "%dx%d:%s" % (image.size[0], image.size[1], hashlib.sha256(values.tobytes()).hexdigest()[:16])
    finally:
        bpy.data.images.remove(image)


def picture_centre(path):
    """Where the picture in an image file sits: the ``(x, y)`` centre of its brightness over black, in pixels.

    Two stereo views of one object differ in this by their parallax, whatever
    colour management a file went through on the way.
    """
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = array('f', [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        total = x_sum = y_sum = 0.0
        for index in range(width * height):
            base = index * 4
            weight = (values[base] + values[base + 1] + values[base + 2]) * values[base + 3]
            total += weight
            x_sum += weight * (index % width)
            y_sum += weight * (index // width)
        return (round(x_sum / total, 2), round(y_sum / total, 2)) if total else None
    finally:
        bpy.data.images.remove(image)


def point_scene_at(scene, folder):
    """Send the scene's own render and File Output files below ``folder``, named as a job names them.

    This is Blender rendering by itself, for comparison: the same files a job
    writes, written without a job.
    """
    from angelo_agent import job_render_outputs, job_render_scene
    scene.render.filepath = job_render_scene.escape(folder / job_render_outputs.MAIN_PREFIX)
    taken = set()
    for path, node in job_render_scene.file_output_nodes(scene):
        node.directory = job_render_scene.escape(folder / job_render_outputs.folder_name(node.name, taken))
        node.file_name = "layers" if node.format.file_format == "OPEN_EXR_MULTILAYER" else ""


def show_image_through_compositor(scene, path):
    """Make the scene's render the image at ``path`` itself: a later render must reproduce it."""
    image = bpy.data.images.load(str(path), check_existing=False)
    tree = bpy.data.node_groups.new("JobShowImage", "CompositorNodeTree")
    tree.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    source, result = tree.nodes.new("CompositorNodeImage"), tree.nodes.new("NodeGroupOutput")
    source.image = image
    tree.links.new(source.outputs["Image"], result.inputs[0])
    scene.compositing_node_group = tree
    return image


# UDIM bakes ------------------------------------------------------------------------------------------------

UDIM_QUADS = {1001: (0.1, 0.9), 1002: (1.1, 1.9)}  # Each quad's U range; both span V 0.1 to 0.9.


def udim_object(scene, name="UdimPlane", emission=(0.8, 0.2, 0.1)):
    """Two quads whose UVs lie in tiles 1001 and 1002 (none in 1003), with a material that emits one colour.

    An emission bake therefore paints that colour inside each quad's UV square
    (0.1 to 0.9 of its tile) and nothing else.
    """
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-2, -1, 0), (0, -1, 0), (0, 1, 0), (-2, 1, 0), (0, -1, 0), (2, -1, 0), (2, 1, 0), (0, 1, 0)], [],
                     [(0, 1, 2, 3), (4, 5, 6, 7)])
    layer = mesh.uv_layers.new(name="UVMap")
    corners = [(u, v) for low, high in UDIM_QUADS.values() for u, v in ((low, 0.1), (high, 0.1), (high, 0.9), (low, 0.9))]
    for loop, uv in zip(layer.data, corners):
        loop.uv = uv
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (0, 0, 3)
    scene.collection.objects.link(obj)
    material = bpy.data.materials.new(name + "Material")
    if material.node_tree is None:
        material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    emit, surface = nodes.new("ShaderNodeEmission"), nodes.new("ShaderNodeOutputMaterial")
    emit.name, emit.inputs["Color"].default_value = "Emit", (*emission, 1.0)
    links.new(emit.outputs[0], surface.inputs["Surface"])
    for node in nodes:
        node.select = False
    mesh.materials.append(material)
    return obj


def udim_image(name, folder, tiles=(1001, 1002, 1003), size=32, color=(0.2, 0.4, 0.6, 1.0)):
    """A UDIM image of one colour saved as ``<folder>/<name>.<UDIM>.png``: file-backed, without unsaved changes."""
    folder.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new(name, size, size, tiled=True)
    for number in tiles[1:]:
        image.tiles.new(tile_number=number)
    with bpy.context.temp_override(edit_image=image):
        for tile in image.tiles:
            image.tiles.active = tile
            bpy.ops.image.tile_fill(color=color, generated_type='BLANK', width=size, height=size, float=False, alpha=True)
    image.filepath_raw, image.file_format = str(folder / (name + ".<UDIM>.png")), 'PNG'
    image.save()
    assert not image.is_dirty and image.source == 'TILED'
    return image


def tile_pixels(image, folder):
    """Each tile's pixels, read from a copy saved into ``folder``: ``{number: {"centre", "corner", "hash"}}``.

    Python reads only an image's first tile; a saved copy of every tile is the
    way to look at the others.
    """
    import hashlib
    import shutil
    from pathlib import Path
    folder = Path(folder)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    image.save(filepath=str(folder / "tile.<UDIM>.png"), save_copy=True)
    result = {}
    for path in sorted(folder.glob("tile.*.png")):
        tile = bpy.data.images.load(str(path), check_existing=False)
        try:
            values = array('f', [0.0]) * len(tile.pixels)
            tile.pixels.foreach_get(values)
            result[int(path.name.split(".")[1])] = {
                "centre": pixel(tile, (0.5, 0.5)), "corner": pixel(tile, (0.02, 0.02)),
                "hash": hashlib.sha256(values.tobytes()).hexdigest()[:16]}
        finally:
            bpy.data.images.remove(tile)
    return result


def file_hashes(folder):
    """A digest of every file below ``folder``: the person's own tile files must stay byte-identical."""
    import hashlib
    from pathlib import Path
    return {name: hashlib.sha256((Path(folder) / name).read_bytes()).hexdigest()[:16] for name in files_below(folder)}


def near(first, second, tolerance=0.02):
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))


# Disk-cached simulation bakes ---------------------------------------------------------------------------------

def bake_settings_of(obj):
    """Where each geometry-nodes modifier of ``obj`` keeps its bakes: what a job must restore or point exactly."""
    return {modifier.name: {"target": modifier.bake_target, "directory": modifier.bake_directory,
                            "bakes": [(bake.bake_target, bake.use_custom_path, bake.directory) for bake in modifier.bakes]}
            for modifier in obj.modifiers if modifier.type == 'NODES'}


def simulated_height(obj, frame=10):
    """The simulation's height at ``frame`` after a jump from frame 1: only a bake gives more than one step."""
    scene = bpy.context.scene
    scene.frame_set(1)
    scene.frame_set(frame)
    height = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices[0].co.z
    scene.frame_set(1)
    return round(height, 5)
