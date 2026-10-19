# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged ``world_configure`` kernel on the private stage scene's World copy.

Staging gives the stage scene a private copy of the bound scene's World when a
program configures it (or the kernel creates one when the scene has none).
Image datablocks never enter the stage: the environment node of a staged World
holds no image, and the intended image is kept by session UID in the stage's
``extras`` (``world_image``; ``world_image_original`` is the live World's own).
Commit assigns the live image to the committed World and reads it back. This
keeps bundles small and avoids duplicating images when a stage is reloaded.
"""

import bpy

from .action_world_spec import (COLOR_UNUSED, COORDINATES_NODE, ENVIRONMENT_NODE, MAPPING_NODE, NO_ENVIRONMENT,
                                WorldGraphError, graph_values, world_graph)
from . import world_sky, world_surface


def resolve_image(image_id):
    """The loaded image an ``dat:<epoch>:IMAGE:<uid>`` ID names in the current document."""
    from . import scene_queries
    _prefix, epoch, _kind, uid = image_id.split(":")
    if epoch != scene_queries.document_epoch():
        raise WorldGraphError("IMAGE_EXPIRED", "the image ID is from an earlier document epoch; read the settings again")
    image = next((image for image in bpy.data.images if int(image.session_uid) == int(uid)), None)
    if image is None:
        raise WorldGraphError("IMAGE_NOT_FOUND", "no loaded image has this ID")
    return image


def image_id(image):
    from . import scene_queries
    return None if image is None else "dat:%s:IMAGE:%d" % (scene_queries.document_epoch(), int(image.session_uid))


def _ensure_environment(tree, graph):
    if graph["environment"] is not None:
        if graph["mapping"] is None:
            mapping, coordinates = tree.nodes.new("ShaderNodeMapping"), tree.nodes.new("ShaderNodeTexCoord")
            mapping.name, coordinates.name = MAPPING_NODE, COORDINATES_NODE
            tree.links.new(coordinates.outputs["Generated"], mapping.inputs["Vector"])
            tree.links.new(mapping.outputs["Vector"], graph["environment"].inputs["Vector"])
        return
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    environment = tree.nodes.new("ShaderNodeTexEnvironment")
    coordinates.name, mapping.name, environment.name = COORDINATES_NODE, MAPPING_NODE, ENVIRONMENT_NODE
    background = graph["background"]
    coordinates.location = background.location.x - 900, background.location.y
    mapping.location = background.location.x - 700, background.location.y
    environment.location = background.location.x - 450, background.location.y
    tree.links.new(coordinates.outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
    tree.links.new(environment.outputs["Color"], background.inputs["Color"])


def _remove_environment(tree, graph):
    for key in ("environment", "mapping", "coordinates"):
        if graph[key] is not None:
            tree.nodes.remove(graph[key])


# stage.extras: the name the staged World copy takes at commit, the live World's when nothing else uses that
# one (program_identities._adopt_names). The copy itself is "World.001" while the live World exists.
COMMITTED_NAME = "world_committed_name"


def summary(world, image, name=None):
    """JSON summary of a supported World graph with an explicit environment image (``name``: shown instead)."""
    if world is None:
        return None
    name = (name or world.name)[:64]
    try:
        values = graph_values(world_graph(world))
    except WorldGraphError as error:
        return {"name": name, "supported_graph": False, "reason": str(error)[:160]}
    result = {"name": name, "supported_graph": True, "color": values["color"], "strength": values["strength"]}
    if "image" in values:
        result.update(environment=image_id(image), rotation=values["rotation"])
    if "sky" in values:
        result["sky"] = values["sky"]
    return result


def apply_world_configure(context, operation):
    """Configure the staged World's background; the image is kept as an intent until commit."""
    if operation["target"] not in getattr(context, "scene_handles", ()):
        raise WorldGraphError("OWNER_TYPE_MISMATCH", "world_configure edits the bound scene's World, not an object: "
                              "pass the scene handle as target (scene_settings.handle in the observation)")
    params = operation["parameters"]
    scene, extras = context.scene, context.extras
    world = scene.world
    if world is None:
        world = context.track_id(bpy.data.worlds.new("Angelo World"))
        world.use_nodes = True  # Creates the default Background -> World Output graph.
        if world.node_tree is not None:
            context.track_id(world.node_tree)
        scene.world = world
        extras.setdefault("world_image_original", None)
    context.require_private(world)
    animation = getattr(world.node_tree, "animation_data", None) or getattr(world, "animation_data", None)
    if animation is not None and (animation.action is not None or len(animation.drivers)):
        raise WorldGraphError("ANIMATED_WORLD", "animated or driven World settings are not configured natively")
    graph = world_graph(world)
    tree, background = world.node_tree, graph["background"]
    if "strength" in params:
        background.inputs["Strength"].default_value = params["strength"]
    if world_sky.configure(tree, graph, params, WorldGraphError):
        extras["world_image"] = None  # The sky replaced an environment texture.
    graph = world_graph(world)
    if "color" in params:  # Kept under a sky's or an environment's link, which the result says (COLOR_UNUSED).
        background.inputs["Color"].default_value = list(params["color"]) + [1.]
        world.color = params["color"]  # The viewport/Workbench World colour follows the background.
    environment = params.get("environment")
    if environment == NO_ENVIRONMENT:
        _remove_environment(tree, graph)
        extras["world_image"] = None
    elif environment is not None:
        image = resolve_image(environment)
        if image in getattr(context, "owned_ids", ()):
            raise WorldGraphError("IMAGE_NOT_FOUND", "the image belongs to the private stage")
        _ensure_environment(tree, graph)
        extras["world_image"] = int(image.session_uid)
    graph = world_graph(world)
    ignored = "rotation" in params and graph["environment"] is None
    if ignored and len(params) == 1:  # Nothing else was asked for: say so instead of reporting an empty edit as done.
        raise WorldGraphError("NO_ENVIRONMENT", "set an environment image before rotating it")
    if "rotation" in params and not ignored:
        if graph["mapping"] is None:
            _ensure_environment(tree, graph)
            graph = world_graph(world)
        rotation = list(graph["mapping"].inputs["Rotation"].default_value)
        graph["mapping"].inputs["Rotation"].default_value = rotation[:2] + [params["rotation"]]
    if graph["environment"] is not None:
        graph["environment"].image = None  # Images stay out of the stage; commit assigns the live one.
    values = graph_values(world_graph(world))
    for key in ("strength", "rotation"):
        if key in params and key in values and abs(values[key] - params[key]) > max(1e-6, 4e-7 * abs(params[key])):
            raise WorldGraphError("VALUE_NOT_RETAINED", key)
    result = {"kind": "world_configure", "target": operation["target"],
              "world": summary(world, intended_image(extras), extras.get(COMMITTED_NAME)),
              "shows": world_surface.shows(world, scene.render.engine)}  # What the camera sees: sky or colour.
    if ignored:  # A plain colour has no direction: the rest of the request was applied.
        result["rotation_ignored"] = "the World has no environment texture to turn"
    source = "sky" if graph["sky"] is not None else "environment" if graph["environment"] is not None else None
    if "color" in params and source:  # Set, and stored on the Background node; the source drives the background.
        result["color_unused"] = COLOR_UNUSED % (source, source)
    return result


def intended_image(extras):
    """The live image the committed World's environment node will use, or None."""
    uid = extras["world_image"] if "world_image" in extras else extras.get("world_image_original")
    if uid is None:
        return None
    return next((image for image in bpy.data.images if int(image.session_uid) == uid), None)
