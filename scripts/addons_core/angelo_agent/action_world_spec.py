# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""World background schema and the one supported World node graph (pure Python, no bpy).

``world_configure`` edits the bound scene's World: background colour and
strength, a physical sky (``world_sky``), and an environment texture from an
image datablock that is already loaded, with an optional rotation about Z. The image is named by its document
resource ID (``dat:<epoch>:IMAGE:<uid>``, listed by ``angelo_scene_settings``),
never by a file path: loading a file is an external read outside the staged
transaction, and file access belongs to the file and Studio tools.

Only Blender's default World graph is configured: an active World Output fed
by one Background node, whose Color is either unlinked or fed by one Sky
Texture or one Environment Texture (optionally through Mapping <- Texture
Coordinate Generated). Any other node, link or linked Strength makes the graph custom,
and custom graphs are refused rather than rewritten. ``world_graph`` reads a
graph by attribute access only, so the kernel, the verification predicate and
observation share it.
"""

import math

from .action_schema_types import number, array, text, obj
from . import world_sky

IMAGE_ID_PATTERN = r"^dat:[a-f0-9]{32}:IMAGE:[1-9][0-9]{0,19}$"
IMAGE_ID = text(80, pattern=IMAGE_ID_PATTERN)
NO_ENVIRONMENT = "NONE"
# Names of the nodes world_configure adds, for readable graphs.
ENVIRONMENT_NODE, MAPPING_NODE, COORDINATES_NODE = "Angelo Environment", "Angelo Mapping", "Angelo Coordinates"

WORLD_VALUES = {"color": array(number(0, 1), 3, 3), "strength": number(0, 1000),
                "environment": {"oneOf": [IMAGE_ID, {"const": NO_ENVIRONMENT}]},
                "rotation": number(-2 * math.pi, 2 * math.pi), "sky": world_sky.SKY_VALUE}
WORLD_CONFIGURE_SCHEMA = dict(obj(WORLD_VALUES), minProperties=1)
# A colour stated beside the source that feeds the background (action_world): kept on the Background node under
# that source's link and shown once it goes, as the result says.
COLOR_UNUSED = ("the %s drives the rendered background, so this colour is stored on the Background node unused until "
                "the %s is removed (set to NONE); it is still the World colour Solid and Workbench views show")


_REFUSAL_POLICY = {
    **dict.fromkeys(("EMPTY", "COLOR_HIDDEN", "ONE_BACKGROUND", "NO_ENVIRONMENT", "OWNER_TYPE_MISMATCH"), "VALIDATION"),
    "CUSTOM_WORLD_GRAPH": "UNSUPPORTED", "ANIMATED_WORLD": "UNSUPPORTED",
    "IMAGE_EXPIRED": "STALE_DEPENDENCY", "IMAGE_NOT_FOUND": "STALE_DEPENDENCY",
    "VALUE_NOT_RETAINED": "INTERNAL_ERROR",
}


class WorldGraphError(ValueError):
    """A World graph or request outside the one supported background setup."""

    def __init__(self, code, detail):
        self.code = code
        self.refusal_code = _REFUSAL_POLICY.get(code, "UNKNOWN")
        super().__init__("world_configure %s: %s" % (code, detail))


def canonical(params):
    """Cross-field rules of schema-validated parameters.

    A rotation beside ``environment: NONE`` is dropped: it turns an environment
    texture and the request removes it, so there is nothing to refuse (a model
    that fills every field sent a whole plan again for it).
    """
    if not params:
        raise WorldGraphError("EMPTY", "set at least one of color, strength, environment, rotation or sky")
    world_sky.canonical(params, WorldGraphError)
    environment = params.get("environment")
    if environment == NO_ENVIRONMENT:
        params.pop("rotation", None)
    return params


def _linked_from(socket):
    links = list(socket.links)
    if len(links) > 1:
        raise WorldGraphError("CUSTOM_WORLD_GRAPH", "a socket has several links")
    return links[0].from_node if links else None


def world_graph(world):
    """The supported graph's nodes ``{output, background, environment, mapping, coordinates}``.

    Missing optional nodes are None. Raises WorldGraphError for anything else.
    """
    tree = getattr(world, "node_tree", None)
    if tree is None:
        raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the World has no node tree")
    nodes = list(tree.nodes)
    outputs = [node for node in nodes if node.type == "OUTPUT_WORLD" and getattr(node, "is_active_output", True)]
    if len(outputs) != 1:
        raise WorldGraphError("CUSTOM_WORLD_GRAPH", "expected exactly one active World Output")
    output = outputs[0]
    background = _linked_from(output.inputs["Surface"])
    if background is None or background.type != "BACKGROUND":
        raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the World Output surface is not a Background node")
    if output.inputs["Volume"].is_linked or background.inputs["Strength"].is_linked:
        raise WorldGraphError("CUSTOM_WORLD_GRAPH", "volume or linked strength")
    graph = {"output": output, "background": background, "environment": None, "mapping": None, "coordinates": None,
             "sky": None}
    environment = _linked_from(background.inputs["Color"])
    if environment is not None and environment.type == "TEX_SKY" and not any(s.is_linked for s in environment.inputs):
        graph["sky"], environment = environment, None  # A physical sky (world_sky).
    if environment is not None:
        if environment.type != "TEX_ENVIRONMENT":
            raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the background colour comes from a %s node" % environment.type)
        graph["environment"] = environment
        mapping = _linked_from(environment.inputs["Vector"])
        if mapping is not None:
            if mapping.type != "MAPPING" or any(socket.is_linked for socket in mapping.inputs if socket.identifier != "Vector"):
                raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the environment vector is not a plain Mapping node")
            coordinates = _linked_from(mapping.inputs["Vector"])
            if coordinates is None or coordinates.type != "TEX_COORD":
                raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the mapping is not fed by Texture Coordinate")
            link = mapping.inputs["Vector"].links[0]
            if link.from_socket.identifier != "Generated":
                raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the mapping is not fed by Generated coordinates")
            graph.update(mapping=mapping, coordinates=coordinates)
    used = {node.name for node in graph.values() if node is not None}  # Node names are unique per tree.
    if any(node.name not in used and node.type != "FRAME" for node in nodes):
        raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the graph has nodes outside the background setup")
    expected_links = sum(1 for key in ("background", "environment", "mapping", "coordinates", "sky")
                         if graph[key] is not None)
    if len(tree.links) != expected_links:
        raise WorldGraphError("CUSTOM_WORLD_GRAPH", "the graph has links outside the background setup")
    return graph


def graph_values(graph):
    """Plain values of a supported graph: colour, strength, rotation and the environment image ID block."""
    background = graph["background"]
    values = {"color": [float(value) for value in list(background.inputs["Color"].default_value)[:3]],
              "strength": float(background.inputs["Strength"].default_value)}
    if graph["environment"] is not None:
        values["image"] = graph["environment"].image
        values["rotation"] = float(graph["mapping"].inputs["Rotation"].default_value[2]) if graph["mapping"] else 0.
    if graph["sky"] is not None:
        values["sky"] = world_sky.values(graph["sky"])
    return values
