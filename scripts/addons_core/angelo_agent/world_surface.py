# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a World shows a camera: the Sky texture, or the flat colour, that reaches its active World Output.

A script that set ``sky_type = 'NISHITA'`` inside try/except failed silently on Blender 5.2, its fallback
left a flat grey background, and the reply still claimed a physical sky. A result that changes
the World therefore states what it shows (``shows``), read the way the renderer reads it, with the rules of
the benchmark grader's ``world_background`` (tests/python/angelo_agent):

* ``active_output``: the World Output the renderer evaluates (Blender's ``ntreeShaderOutputNode``): one
  targeting the scene's engine before one targeting all engines, the active one first;
* ``used_nodes``: the nodes its Surface reaches through links the renderer follows. A muted or invalid link,
  and a link into an unavailable socket, feed nothing; a muted node passes only its internal links;
* ``colour_family``: the grader's hue bands for a linear RGB colour.

``shows(world, engine)`` is one of:

* ``{"shows": "SKY", "sky": {"type", "sun_bearing_deg", "sun_elevation_deg", ...}}``: a Sky Texture reaches the
  Surface; ``sky`` holds its sun's direction and its settings as set (``world_light.sky``);
* ``{"shows": "COLOR", "color": [r, g, b], "family": ...}``: the Surface is a Background or Emission whose
  Color is unlinked or a plain RGB node, so every direction shows that colour;
* ``{"shows": "NODES", "color_from": [node labels]}``: other nodes drive the colour; it is not evaluated here
  (the grader evaluates it per direction);
* ``{"shows": "NOTHING"}``: nothing reaches the Surface (the background renders black), or there is no World.

``strength`` is added when the Background's Strength is a plain value. With the scene, ``shows`` adds its Sun
lights and the World's brightness (``world_light``). ``fingerprint`` is a cheap digest of the graph's
structure and input values, the Sky's settings, the Sun lights and the view transform, for telling whether a
script changed what the World shows. Duck-typed bpy reads, no Blender imports: the module works on any object
with Blender's node attributes.
"""

import colorsys
import math

ENGINE_TARGETS = {"CYCLES": "CYCLES", "BLENDER_EEVEE": "EEVEE"}
SHADERS = frozenset({"BACKGROUND", "EMISSION"})
MAX_NODES = 256
MAX_LABELS = 6
# A Sky texture's settings (properties, not sockets), so a script that changes only its sun is noticed.
SKY_PROPERTIES = ("sun_elevation", "sun_rotation", "sun_size", "sun_intensity", "sun_disc", "altitude", "air_density",
                  "aerosol_density", "ozone_density", "sun_direction", "turbidity", "ground_albedo")


def colour_family(rgb):
    """``warm`` (orange/yellow/red), ``blue`` (cyan to blue), ``green``, ``grey`` or ``other`` (the grader's bands)."""
    r, g, b = (max(0.0, float(v)) for v in list(rgb)[:3])
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    if saturation < 0.12 or value <= 0.0:
        return "grey"
    degrees = hue * 360
    if degrees < 70 or degrees >= 330:
        return "warm"
    if degrees < 165:
        return "green"
    if degrees < 260:
        return "blue"
    return "other"


def active_output(tree, engine=None):
    """The World Output the renderer evaluates, or None."""
    target = ENGINE_TARGETS.get(engine)
    outputs = [node for node in tree.nodes if node.type == "OUTPUT_WORLD"]
    exact = [node for node in outputs if target is not None and getattr(node, "target", "ALL") == target]
    pool = exact or [node for node in outputs if getattr(node, "target", "ALL") == "ALL"]
    if not pool and target is None:
        pool = outputs
    pool.sort(key=lambda node: not getattr(node, "is_active_output", False))
    return pool[0] if pool else None


def _feeds(tree):
    """The link feeding each input, keyed by (node name, socket identifier), as the renderer follows them."""
    feeds = {}
    for link in tree.links:
        if (getattr(link, "is_muted", False) or not getattr(link, "is_valid", True)
                or getattr(link.to_socket, "is_unavailable", False)):
            continue
        feeds.setdefault((link.to_node.name, link.to_socket.identifier), link)
    return feeds


def _passed_inputs(node):
    if getattr(node, "mute", False):
        return [link.from_socket for link in getattr(node, "internal_links", ())]
    return list(node.inputs)


def used_nodes(tree, output, feeds=None):
    """Every node the output's Surface reaches through links the renderer follows, nearest first."""
    feeds = _feeds(tree) if feeds is None else feeds
    found, seen, frontier = [], set(), [(output.name, output.inputs["Surface"])]
    while frontier and len(found) < MAX_NODES:
        owner, socket = frontier.pop(0)
        link = feeds.get((owner, socket.identifier))
        if link is None or link.from_node.name in seen:
            continue
        node = link.from_node
        seen.add(node.name)
        found.append(node)
        frontier.extend((node.name, upstream) for upstream in _passed_inputs(node))
    return found


def _source(node, feeds, socket):
    """The node feeding ``socket`` of ``node`` past reroutes and muted nodes, or None."""
    for _ in range(MAX_NODES):
        link = feeds.get((node.name, socket.identifier))
        if link is None:
            return None
        node = link.from_node
        if node.type != "REROUTE" and not getattr(node, "mute", False):
            return node
        passed = _passed_inputs(node)
        if not passed:
            return None
        socket = passed[0]
    return None


def _value(socket):
    value = getattr(socket, "default_value", None)
    try:
        return [round(float(v), 4) for v in value][:3]
    except TypeError:
        return round(float(value), 4) if isinstance(value, (int, float)) else None


def _label(node):
    return str(getattr(node, "label", "") or getattr(node, "name", "") or node.type)[:48]


def shows(world, engine=None, scene=None):
    """What ``world`` shows a camera (module docstring); with ``scene``, also its Sun lights and brightness.

    The Sky's sun bearing and settings are always read from its node (``world_light.sky``). ``scene`` adds what
    needs the live scene (``world_light``): the Sun lights that render beside it and, for a flat colour or a Sky
    the Background shows directly, the scene-linear colour and what the scene's view transform shows of it.
    """
    result, sky_node, direct = _shows(world, engine)
    toward = None
    if sky_node is not None:
        from . import world_light
        result["sky"], toward = world_light.sky(sky_node)
    if scene is None:
        return result
    from . import world_light
    lights, more = world_light.sun_lights(scene, toward)
    if lights:
        result["sun_lights"] = lights
        if more:
            result["sun_lights_more"] = more
    if lights or sky_node is not None:
        result["directions"] = world_light.DIRECTIONS
    strength = result.get("strength")
    if isinstance(strength, float):
        if result["shows"] == "COLOR":
            samples = [("every direction", [round(value * strength, 5) for value in result["color"]])]
            result["brightness"] = world_light.brightness(scene, samples, "the flat colour times its strength")
        elif result["shows"] == "SKY" and direct:
            samples, reason = world_light.sky_samples(sky_node, strength)
            result["brightness"] = (world_light.brightness(
                scene, samples, "the measured default sky (world_light_table) times its strength")
                if samples else {"unread": reason})
    return result


def lighting(scene, view_layer=None):
    """What lights a render of ``scene``, compactly, for a RENDERED look's ``rendered.lighting``.

    The World as ``shows`` reads it (what it shows and its strength; a flat colour's value; a Sky's sun as bearing
    and elevation), the Sun lights that render (``world_light.sun_lights``, with ``from_sky_deg`` beside a Sky)
    and how many other lights render, by type; a light renders when its collection chain is neither excluded nor
    hidden for render in a used view layer and it is not hidden itself (``world_light.rendering``). Workbench
    lights with its own setting instead (``workbench_light``). Without it, models judged a dark
    or blue render without being told what lit it. No brightness: the picture shows that. Optional evidence: None
    when unreadable.
    """
    try:
        return _lighting(scene, view_layer)
    except (ReferenceError, RuntimeError, AttributeError, TypeError, ValueError, KeyError):
        return None


def _lighting(scene, view_layer):
    from . import world_light
    engine = getattr(getattr(scene, "render", None), "engine", None)
    if engine == "BLENDER_WORKBENCH":  # Its own studio, matcap or flat light: no scene light or World lights it.
        return {"workbench_light": str(scene.display.shading.light)}
    shown, sky_node, _direct = _shows(getattr(scene, "world", None), engine)
    result = {key: shown[key] for key in ("shows", "strength", "color") if key in shown}
    toward = None
    if sky_node is not None:
        sky, toward = world_light.sky(sky_node)
        result["sky_sun"] = {key[4:]: sky[key] for key in ("sun_bearing_deg", "sun_elevation_deg") if key in sky}
    rendered = world_light.rendering(scene, view_layer)  # The look's own layer when the scene renders one.
    lights, more = world_light.sun_lights(scene, toward, rendered)
    if lights:
        result["sun_lights"] = [{key: row[key] for key in ("name", "strength", "bearing_deg", "elevation_deg",
                                                            "from_sky_deg") if key in row} for row in lights]
        if more:
            result["sun_lights_more"] = more
    if lights or sky_node is not None:
        result["directions"] = world_light.DIRECTIONS
    others = {}
    for obj in getattr(scene, "objects", ()):
        kind = getattr(getattr(obj, "data", None), "type", None) if obj.type == "LIGHT" else None
        if kind not in (None, "SUN") and world_light.renders(obj, rendered):
            others[kind] = others.get(kind, 0) + 1
    if others:
        result["other_lights"] = others
    return result


def _shows(world, engine):
    """``(shows, sky node or None, whether the Background's Color is that sky directly)``."""
    if world is None:
        return {"world": None, "shows": "NOTHING"}, None, False
    result = {"world": str(world.name)[:64]}
    tree = getattr(world, "node_tree", None)
    if tree is None:  # A World without a node graph renders its own colour.
        colour = [round(float(v), 4) for v in list(world.color)[:3]]
        return dict(result, shows="COLOR", color=colour, family=colour_family(colour), strength=1.0), None, False
    output = active_output(tree, engine)
    if output is None:
        return dict(result, shows="NOTHING"), None, False
    feeds = _feeds(tree)
    used = used_nodes(tree, output, feeds)
    if not used:
        return dict(result, shows="NOTHING"), None, False
    sky = next((node for node in used if node.type == "TEX_SKY"), None)
    shader = _source(output, feeds, output.inputs["Surface"])
    if shader is not None and shader.type in SHADERS and not feeds.get((shader.name, shader.inputs["Strength"].identifier)):
        result["strength"] = _value(shader.inputs["Strength"])
    colour_node = _source(shader, feeds, shader.inputs["Color"]) if shader is not None and shader.type in SHADERS else None
    if sky is not None:
        # Nodes compared by name (unique in a tree): Blender hands out a new Python object per link read.
        return dict(result, shows="SKY"), sky, colour_node is not None and colour_node.name == sky.name
    if shader is not None and shader.type in SHADERS:
        colour = (_value(shader.inputs["Color"]) if colour_node is None else
                  _value(colour_node.outputs[0]) if colour_node.type == "RGB" else None)
        if colour is not None:
            return dict(result, shows="COLOR", color=colour, family=colour_family(colour)), None, False
    labels = [_label(node) for node in used if node.type not in ("REROUTE", "OUTPUT_WORLD")]
    return dict(result, shows="NODES", color_from=labels[:MAX_LABELS]), None, False


def fingerprint(scene):
    """A digest of what the scene's World can show: its identity, colour, graph structure and input values."""
    world = getattr(scene, "world", None)
    engine = getattr(getattr(scene, "render", None), "engine", None)
    if world is None:
        return (engine, None)
    parts = [engine, world.name, tuple(round(float(v), 6) for v in list(world.color)[:3])]
    tree = getattr(world, "node_tree", None)
    if tree is not None:
        for node in list(tree.nodes)[:MAX_NODES]:
            parts.append((node.name, node.bl_idname, bool(getattr(node, "mute", False)),
                          bool(getattr(node, "is_active_output", False)), str(getattr(node, "target", "")),
                          tuple(repr(_value(socket)) for socket in node.inputs)))
            if node.type == "TEX_SKY":  # Its settings are properties, not inputs: every one a script can set.
                parts.append((str(node.sky_type), *(repr(getattr(node, name, None)) for name in SKY_PROPERTIES)))
        parts.extend((link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier,
                      bool(getattr(link, "is_muted", False))) for link in list(tree.links)[:MAX_NODES])
    view = getattr(scene, "view_settings", None)  # How bright the World shows (world_light.brightness).
    parts.append(tuple(repr(getattr(view, name, None)) for name in ("view_transform", "look", "exposure", "gamma")))
    parts.extend((obj.name, tuple(tuple(round(float(v), 6) for v in row) for row in obj.matrix_world),
                  float(getattr(obj.data, "energy", 0.0)), bool(getattr(obj, "hide_render", False)))
                 for obj in getattr(scene, "objects", ()) if obj.type == "LIGHT" and getattr(obj.data, "type", "") == "SUN")
    return hash(tuple(parts))


def changed(scene, before):
    """``shows`` for the scene's World, with its lights and brightness, when its ``fingerprint`` differs from
    ``before``, else None."""
    if before is None or fingerprint(scene) == before:
        return None
    return shows(scene.world, getattr(getattr(scene, "render", None), "engine", None), scene)
