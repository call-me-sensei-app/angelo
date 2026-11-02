# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenes, materials and a synchronous driver for the texture-set bake suites (test_blender_bake_maps_*).

Blender-only helpers (they import ``bpy``). Every material is built from block
images: 16 x 16 random blocks of 4 x 4 pixels, read with Closest interpolation on
a plane whose UVs fill 0..1, so a 64-pixel bake's texel holds one block's value
exactly away from block edges (``interior``), whatever the bake's sub-texel
jitter. ``DEV`` materials were used while developing the bake; ``HELD_OUT`` ones
were written beside them and never used to tune it (the general-fix rule).

``bake`` runs a texture set exactly as the job does (``bake_maps_plan``,
``bake_maps_steps``, ``bake_maps_write``) but with the bake operator's EXEC, so a
background instance without a window can check its numbers.
"""

import bpy
import numpy

N, BLOCK = 64, 4


def plane(name, uvs=((0, 0), (1, 0), (1, 1), (0, 1)), location=(0.0, 0.0, 0.0)):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    layer = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(layer.data, uvs):
        loop.uv = uv
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def split_plane(name, uvs_left, uvs_right):
    """Two quads side by side (left x<0, right x>0), each with its own UVs and material index."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-1, -1, 0), (0, -1, 0), (0, 1, 0), (-1, 1, 0), (1, -1, 0), (1, 1, 0)], [],
                     [(0, 1, 2, 3), (1, 4, 5, 2)])
    layer = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(layer.data, (*uvs_left, *uvs_right)):
        loop.uv = uv
    mesh.polygons[1].material_index = 1
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def blocks(rng, channels=3, low=0.0, high=1.0):
    """A (N, N, channels) float array of 4 x 4 blocks."""
    small = rng.uniform(low, high, (N // BLOCK, N // BLOCK, channels)).astype(numpy.float32)
    return numpy.repeat(numpy.repeat(small, BLOCK, axis=0), BLOCK, axis=1)


def interior():
    """Texels at least one texel from every block edge."""
    index = numpy.arange(N) % BLOCK
    inside = (index >= 1) & (index <= BLOCK - 2)
    return inside[:, None] & inside[None, :]


def image_of(name, values):
    """A non-colour float image holding ``values`` (N, N, k) exactly."""
    image = bpy.data.images.new(name, N, N, alpha=True, float_buffer=True, is_data=True)
    rgba = numpy.ones((N, N, 4), dtype=numpy.float32)
    rgba[..., :values.shape[-1]] = values
    if values.shape[-1] == 1:
        rgba[..., 1] = rgba[..., 2] = values[..., 0]
    image.pixels.foreach_set(rgba.ravel())
    image.pack()
    return image


class Tree:
    """A material's node tree, built from scratch."""

    def __init__(self, name):
        self.material = bpy.data.materials.new(name)
        if self.material.node_tree is None:
            self.material.use_nodes = True
        self.nodes, self.links = self.material.node_tree.nodes, self.material.node_tree.links
        self.nodes.clear()
        self.output = self.nodes.new("ShaderNodeOutputMaterial")

    def node(self, idname, **inputs):
        node = self.nodes.new(idname)
        for name, value in inputs.items():
            if isinstance(value, bpy.types.NodeSocket):
                self.links.new(value, node.inputs[name])
            else:
                node.inputs[name].default_value = value
        return node

    def texture(self, image):
        node = self.nodes.new("ShaderNodeTexImage")
        node.image, node.interpolation = image, 'Closest'
        return node.outputs["Color"]

    def surface(self, socket):
        self.links.new(socket, self.output.inputs["Surface"])
        return self.material


def principled(tree, **inputs):
    return tree.node("ShaderNodeBsdfPrincipled", **inputs).outputs[0]


def mix_shader(tree, fac, a, b):
    node = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(fac, node.inputs[0]) if isinstance(fac, bpy.types.NodeSocket) else None
    tree.links.new(a, node.inputs[1])
    tree.links.new(b, node.inputs[2])
    return node.outputs[0]


def lerp(fac, a, b):
    return a + (b - a) * fac


def scene_setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.use_denoising = False
    scene.cycles.samples = 4
    return scene


def native_bake(target, image, bake_type, pass_filter=None, sources=(), **extra):
    """A plain Blender bake of ``target``'s own materials into ``image`` (the reference side)."""
    material_nodes = []
    for slot in target.material_slots:
        nodes = slot.material.node_tree.nodes
        node = nodes.new("ShaderNodeTexImage")
        node.image = image
        nodes.active, node.select = node, True
        material_nodes.append((nodes, node))
    objects = [*sources, target]
    properties = {"type": bake_type, "use_clear": True, "margin": 0, "use_selected_to_active": bool(sources),
                  **extra}
    if pass_filter is not None:
        properties["pass_filter"] = pass_filter
    with bpy.context.temp_override(active_object=target, object=target, selected_objects=objects,
                                   selected_editable_objects=objects):
        bpy.ops.object.bake(**properties)
    for nodes, node in material_nodes:
        nodes.remove(node)
    values = numpy.empty(len(image.pixels), dtype=numpy.float32)
    image.pixels.foreach_get(values)
    return values.reshape(image.size[1], image.size[0], 4)


def _principled_nodes():
    """Every Principled BSDF in every material and node group."""
    trees = [m.node_tree for m in bpy.data.materials if m.node_tree] + list(bpy.data.node_groups)
    return [node for tree in trees for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"]


def reference(target, bake_type, pass_filter=None, samples=8, size=N):
    """A native bake into a new float image, removed afterwards.

    A Principled BSDF's diffuse colour is its base colour less the energy its specular layer reflects (3 to 4 % for
    a dielectric), so Cycles' Diffuse-colour bake equals the base colour only with that layer off: for that
    reference the Principled nodes' Specular IOR Level is 0 during the reference bake, and put back.
    """
    bpy.context.scene.cycles.samples = samples
    image = bpy.data.images.new("Reference", size, size, alpha=True, float_buffer=True, is_data=True)
    held = []
    if bake_type == "DIFFUSE":
        for node in _principled_nodes():
            socket = node.inputs["Specular IOR Level"]
            if not socket.is_linked:
                held.append((socket, socket.default_value))
                socket.default_value = 0.0
    try:
        return native_bake(target, image, bake_type, pass_filter)
    finally:
        for socket, value in held:
            socket.default_value = value
        bpy.data.images.remove(image)


def tree_state(material):
    """What the person's node tree looks like: nodes, links, active output and active node."""
    tree = material.node_tree
    return (sorted((node.name, node.bl_idname, getattr(node, "is_active_output", None)) for node in tree.nodes),
            sorted((link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier)
                   for link in tree.links),
            tree.nodes.active.name if tree.nodes.active else None)


def bake(targets, request, folder, sources=(), cage=None):
    """The job's texture set, synchronously: ``[(plan, entries, facts, sums)]`` (``sums`` before finishing)."""
    from angelo_agent import bake_maps_steps
    scene = bpy.context.scene
    request = dict(request)
    request.setdefault("extrusion", float(scene.render.bake.cage_extrusion))
    request.setdefault("ray_distance", float(scene.render.bake.max_ray_distance))
    results = []
    notes = bake_maps_steps.new_notes()
    from angelo_agent import job_settings
    held = {path: job_settings.get(scene, path) for path in bake_maps_steps.SCENE_VALUES}
    for path, value in bake_maps_steps.SCENE_VALUES.items():   # As the job applies them (Overrides).
        job_settings.put(scene, path, value)
    try:
        for target in targets:
            results.append(_bake_one(target, request, folder, sources, cage, notes))
    finally:
        for path, value in held.items():
            job_settings.put(scene, path, value)
    return results


def _bake_one(target, request, folder, sources, cage, notes):
    from angelo_agent import bake_maps_plan, bake_maps_steps, bake_maps_write
    from angelo_agent import bake_maps_spec as spec
    scene = bpy.context.scene
    plan = bake_maps_plan.plan(target, list(sources), cage, request)
    sums = {}
    for step in plan.steps:
        run = bake_maps_steps.StepRun(plan, step, tuple(request["resolution"]), notes, request.get("samples"))
        try:
            run.prepare()
            scene.cycles.samples = step.samples
            objects = run.objects()
            with bpy.context.temp_override(active_object=target, object=target, selected_objects=objects,
                                           selected_editable_objects=objects):
                bpy.ops.object.bake(**run.operator_properties(request, request["margin"]))
            assert run.wrote(), "the bake wrote nothing"
            sums.update(run.read())
        finally:
            run.undo()
    sums.update(plan.constants)
    work = {"target": target.name, "stem": spec.safe_stem(target.name), "tiles": list(plan.tiles), "size": tuple(request["resolution"]),
            "sums": dict(sums), "channels": list(plan.channels), "request": request, "folder": str(folder)}
    entries, facts = bake_maps_write.write_set(work)
    return plan, entries, facts, sums


def read_file(path):
    """A written map as Blender reads it, raw values: (h, w, channels) float32."""
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.is_data = True
    values = numpy.empty(len(image.pixels), dtype=numpy.float32)
    image.pixels.foreach_get(values)
    values = values.reshape(image.size[1], image.size[0], image.channels)
    bpy.data.images.remove(image)
    return values


def errors(got, expected, mask=None):
    """Mean and largest absolute error over ``mask`` (every texel when None)."""
    diff = numpy.abs(numpy.asarray(got, dtype=numpy.float64) - numpy.asarray(expected, dtype=numpy.float64))
    if mask is not None:
        diff = diff[mask]
    return {"mean": round(float(diff.mean()), 6), "max": round(float(diff.max()), 6),
            "p99": round(float(numpy.percentile(diff, 99)), 6)}
