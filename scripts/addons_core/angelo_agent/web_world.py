# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a web export carries the scene's World: as a colour, as its HDRI, or as a baked picture. Main thread.

``plan(scene)`` reads the World the way the renderer does (``world_surface``: its active output and the nodes it
reaches) and picks the cheapest faithful form:

* ``NONE``: no World, or nothing reaches its Surface. Blender adds no light and renders black.
* ``COLOR``: a Background (or Emission) whose Color is unlinked or a plain RGB node and whose Strength is a value:
  every direction shows that colour. Written as a colour; nothing is made.
* ``IMAGE``: that shader's Color comes straight from an Environment Texture (equirectangular, with an image),
  read along the view direction or through a Mapping node turning it about Z. The image itself is reduced
  (``web_environment``), turned by the Mapping's angle; the Strength stays a separate intensity.
* ``BAKE``: anything else (a Sky Texture, gradients, mixes, linked strengths, groups). A background Blender renders
  the World into an equirectangular picture (``web_world_bake``) with the strength in it.

Besides the form, the plan says what still differs (``web_compat`` findings): a World volume, a Light Path node
(the camera and the lighting see different worlds; a bake shows what the camera sees), and a missing image. ``key``
holds everything the picture depends on (``web_world_key``), so it is made once per World state and never reused
after a change.
"""

import math
import os

from . import web_world_key, world_surface
from .blend_file_paths import located


def _mapping_angle(node, feeds):
    """The Z angle (radians, as the texture turns) of a Mapping node that only turns the view direction, else None."""
    if node.vector_type not in {"POINT", "VECTOR", "TEXTURE"}:
        return None
    inputs = {socket.identifier: socket for socket in node.inputs}
    for name in ("Location", "Rotation", "Scale"):
        socket = inputs.get(name)
        if socket is not None and feeds.get((node.name, socket.identifier)):
            return None
    rotation = list(inputs["Rotation"].default_value)
    scale = list(inputs["Scale"].default_value)
    location = list(inputs["Location"].default_value) if "Location" in inputs else [0.0] * 3
    if any(abs(value) > 1e-6 for value in rotation[:2]) or any(abs(value - 1.0) > 1e-6 for value in scale):
        return None
    if node.vector_type == "POINT" and any(abs(value) > 1e-6 for value in location):
        return None
    source = world_surface._source(node, feeds, inputs["Vector"])
    link = feeds.get((node.name, inputs["Vector"].identifier))
    if source is None or source.type != "TEX_COORD" or link is None or link.from_socket.identifier != "Generated":
        return None
    return -rotation[2] if node.vector_type == "TEXTURE" else rotation[2]


def _environment(texture, feeds):
    """(image, Z angle) when ``texture`` is an equirectangular Environment Texture read along the view, else None."""
    if texture.type != "TEX_ENVIRONMENT" or texture.projection != "EQUIRECTANGULAR" or texture.image is None:
        return None
    vector = texture.inputs["Vector"]
    if feeds.get((texture.name, vector.identifier)) is None:
        return texture.image, 0.0
    source = world_surface._source(texture, feeds, vector)
    link = feeds.get((texture.name, vector.identifier))
    if source is not None and source.type == "TEX_COORD" and link.from_socket.identifier == "Generated":
        return texture.image, 0.0
    if source is not None and source.type == "MAPPING":
        angle = _mapping_angle(source, feeds)
        if angle is not None:
            return texture.image, angle
    return None


def _missing(image):
    if image.packed_file or image.source != "FILE":
        return False
    import bpy
    path = located(image.filepath, image.library, bpy.path.abspath)
    return not path or not os.path.isfile(path)


def settings(scene):
    """What ``plan`` reads from the scene itself: its World, whether the film shows it, the engine (which World
    output renders) and the frame. The World's own edits are updates of the World; these are the scene's, so the
    live preview keys on them (``web_facts.scene_settings``)."""
    return {"world": scene.world, "background": not scene.render.film_transparent, "engine": scene.render.engine,
            "frame": (getattr(scene, "frame_current", 0), getattr(scene, "frame_subframe", 0.0))}


def plan(scene):
    """How a web export of ``scene`` carries its World (module docstring). Reads the scene only through
    ``settings``."""
    read = settings(scene)
    world, background, engine, frame = read["world"], read["background"], read["engine"], read["frame"]
    if world is None:
        return {"kind": "NONE", "name": None, "background": background}
    base = {"name": world.name, "background": background, "volume": False, "light_path": False}
    tree = world.node_tree
    if tree is None:
        return dict(base, kind="COLOR", color=[float(v) for v in world.color[:3]], intensity=1.0)
    output = world_surface.active_output(tree, engine)
    if output is None:
        return dict(base, kind="NONE")
    feeds = world_surface._feeds(tree)
    used = world_surface.used_nodes(tree, output, feeds)
    volume = output.inputs.get("Volume")
    base["volume"] = bool(volume is not None and feeds.get((output.name, volume.identifier)))
    base["light_path"] = any(node.type == "LIGHT_PATH" for node in used)
    if not used:
        return dict(base, kind="NONE")
    shader = world_surface._source(output, feeds, output.inputs["Surface"])
    if shader is not None and shader.type in world_surface.SHADERS and not base["light_path"] \
            and not feeds.get((shader.name, shader.inputs["Strength"].identifier)):
        strength = float(shader.inputs["Strength"].default_value)
        colour_node = world_surface._source(shader, feeds, shader.inputs["Color"])
        if colour_node is None:
            return dict(base, kind="COLOR", color=[float(v) for v in shader.inputs["Color"].default_value[:3]],
                        intensity=strength)
        if colour_node.type == "RGB":
            return dict(base, kind="COLOR", color=[float(v) for v in colour_node.outputs[0].default_value[:3]],
                        intensity=strength)
        found = _environment(colour_node, feeds)
        if found is not None:
            image, angle = found
            if _missing(image):
                return dict(base, kind="NONE", missing=image.name)
            turn = (angle / (2.0 * math.pi)) % 1.0
            return dict(base, kind="IMAGE", image=image.name, intensity=strength, turn=round(turn, 6),
                        key=web_world_key.image_key(image, round(turn, 6), frame))
    return dict(base, kind="BAKE", intensity=1.0, key=web_world_key.key(world, engine, frame))

