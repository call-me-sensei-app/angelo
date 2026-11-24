# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scatter system as an object: made, adjusted and read back (doc/angelo/scatter.rst).

``create`` adds a new empty-mesh object at the world origin (so its Geometry Nodes, which read the surface in world
space, place instances where they look) with one Geometry Nodes modifier running a new ``scatter_nodes`` group. The
surface and the assets are only read. ``set_inputs`` changes the modifier's inputs (density scale, seed, culling,
proxy) without a rebuild; ``facts`` reads what the system placed, per layer, for the receipt and the rule audit.
"""

import math

import numpy as np

import bpy

from . import scatter_nodes, scatter_spec

MODIFIER = "Angelo Scatter"
LAYERS_PROPERTY = "angelo_scatter_layers"   # The checked layers as JSON, so the card can show and rebuild them.


def asset_radius(objects, layer):
    """A layer's collision radius: the largest of its assets' (``scatter_spec.collision_radius``)."""
    scale_max = layer["transform"]["scale"][1]
    return max(scatter_spec.collision_radius(obj.dimensions, scale_max, layer["spacing"]) for obj in objects)


def _identifier(group, name):
    for item in group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == name:
            return item.identifier
    raise KeyError(name)


def modifier(system):
    mod = system.modifiers.get(MODIFIER)
    if mod is None or mod.type != "NODES" or mod.node_group is None:
        return None
    return mod


def get_input(system, name):
    mod = modifier(system)
    return getattr(mod.properties.inputs, _identifier(mod.node_group, name)).value


def set_inputs(system, values):
    """Set the system's modifier inputs by name (``scatter_nodes.INPUTS``); unknown names raise KeyError. (Blender
    5.3 keeps a modifier input's value at ``properties.inputs.<identifier>.value``.)"""
    mod = modifier(system)
    for name, value in values.items():
        if name not in scatter_nodes.INPUTS:
            raise KeyError(name)
        getattr(mod.properties.inputs, _identifier(mod.node_group, name)).value = value
    system.update_tag()


def camera_tangents(camera, scene):
    """The camera's half field of view as tangents (x, y), for the culling test."""
    data = camera.data
    if data.type == "ORTHO":
        return 1e3, 1e3   # Culling is perspective; an orthographic camera keeps everything in its sides.
    width, height = scene.render.resolution_x * scene.render.pixel_aspect_x, \
        scene.render.resolution_y * scene.render.pixel_aspect_y
    fit = data.sensor_fit
    if fit == "AUTO":
        fit = "HORIZONTAL" if width >= height else "VERTICAL"
    sensor = data.sensor_height if fit == "VERTICAL" else data.sensor_width
    half = sensor * 0.5 / data.lens
    if fit == "VERTICAL":
        return half * width / height, half
    return half, half * height / width


def create(context, surface, layers, assets, name="Scatter"):
    """A new scatter system on ``surface`` for checked ``layers``; ``assets[i]`` lists layer i's objects."""
    import json

    radii = [asset_radius(objects, layer) for objects, layer in zip(assets, layers)]
    group = scatter_nodes.build(layers, assets, radii, name=scatter_nodes.GROUP_NAME)
    system = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    collection = surface.users_collection[0] if surface.users_collection else context.scene.collection
    collection.objects.link(system)
    system.matrix_world.identity()
    mod = system.modifiers.new(MODIFIER, "NODES")
    mod.node_group = group
    set_inputs(system, {"Surface": surface})
    system[LAYERS_PROPERTY] = json.dumps(layers)
    return system, radii


def set_culling(system, scene, camera, viewport, render, distance):
    tan_x, tan_y = camera_tangents(camera, scene) if camera is not None else (0.5, 0.5)
    set_inputs(system, {"Cull Camera": camera, "Cull Viewport": bool(viewport and camera),
                        "Cull Render": bool(render and camera), "Cull Distance": float(distance),
                        "Cull Tan X": tan_x, "Cull Tan Y": tan_y})


def facts(system, depsgraph):
    """What the system placed: ``{"instances", "layers": [count per layer], "positions": [N, 3], "layer": [N]}``
    (instances in world space; proxy points are not instances)."""
    geometry = system.evaluated_get(depsgraph).evaluated_geometry()
    cloud = geometry.instances_pointcloud() if geometry is not None else None
    count = len(cloud.points) if cloud is not None else 0
    layers = len(_layers(system))
    if not count:
        return {"instances": 0, "layers": [0] * layers, "positions": np.zeros((0, 3)), "layer": np.zeros(0, int)}
    layer = np.zeros(count, dtype=np.int32)
    cloud.attributes[scatter_nodes.LAYER_ATTRIBUTE].data.foreach_get("value", layer)
    positions = np.zeros(count * 3)
    cloud.attributes["position"].data.foreach_get("vector", positions)
    return {"instances": count, "layers": [int((layer == i).sum()) for i in range(layers)],
            "positions": positions.reshape(-1, 3), "layer": layer}


def _layers(system):
    import json

    return json.loads(system.get(LAYERS_PROPERTY, "[]"))


def min_spacing(positions, layer, radii):
    """The smallest centre distance between two instances over the sum of their radii (1 or more: none overlap)."""
    worst = math.inf
    for i in range(len(positions)):
        delta = np.linalg.norm(positions[i + 1:] - positions[i], axis=1)
        if not len(delta):
            continue
        allowed = np.asarray(radii)[layer[i + 1:]] + radii[layer[i]]
        worst = min(worst, float((delta / allowed).min()))
    return worst
