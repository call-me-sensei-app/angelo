# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The physical-sky variant of the World background (pure Python: nodes are read by attribute).

``world_configure`` takes ``sky``: Blender's Sky Texture node (multiple or
single scattering) feeding the Background colour, set through a closed group
of fields: the sun's elevation and rotation, its disc, size and intensity, the
altitude and the air, aerosol and ozone densities. Angles are radians like
every typed rotation. A member left out keeps the node's value (Blender's
default for a new sky), and ``"NONE"`` removes the sky and leaves the
background colour.

One source feeds the background colour: a sky replaces an environment texture
and an environment image replaces a sky, and a request for both is refused. A
colour beside a sky is set on the Background node, where the sky's link hides
it, and the result says so (``COLOR_UNUSED``): it shows again once the sky is
removed. It used to be refused, and GPT-6.1 Sol re-sent a whole program for it
in two of its bridge trials. Nothing here loads a file or
evaluates the scene, and a sky node holds no datablock, so the staged World is
committed, verified and undone exactly like a colour.
"""

from .action_schema_types import enum, number, obj

SKY_NODE, NO_SKY = "Angelo Sky", "NONE"
# Members whose names are the node's own settings; ``type`` is its sky_type.
SKY_SETTINGS = ("sun_elevation", "sun_rotation", "sun_disc", "sun_size", "sun_intensity", "altitude", "air_density",
                "aerosol_density", "ozone_density")
SKY_SCHEMA = obj({"type": enum(["MULTIPLE_SCATTERING", "SINGLE_SCATTERING"]),
                  "sun_elevation": number(-1.5708, 1.5708), "sun_rotation": number(-6.2832, 6.2832),
                  "sun_disc": {"type": "boolean"}, "sun_size": number(0, 1.57), "sun_intensity": number(0, 1000),
                  "altitude": number(0, 100000), "air_density": number(0, 1000), "aerosol_density": number(0, 1000),
                  "ozone_density": number(0, 1000)})
SKY_VALUE = {"oneOf": [SKY_SCHEMA, {"const": NO_SKY}]}


def canonical(params, error):
    """Cross-field rules of a ``sky`` request; ``error`` builds the World's own error type."""
    sky = params.get("sky")
    if sky in (None, NO_SKY):
        return
    if params.get("environment") not in (None, "NONE"):
        raise error("ONE_BACKGROUND", "a sky and an environment texture both feed the background colour; state one")


def values(node):
    """Plain values of a Sky Texture node, by typed member."""
    result = {"type": node.sky_type}
    for name in SKY_SETTINGS:
        value = getattr(node, name)
        result[name] = bool(value) if isinstance(value, bool) else float(value)
    return result


def _near(expected, actual):
    if isinstance(expected, (bool, str)):
        return expected == actual
    return abs(expected - actual) <= max(1e-6, 4e-7 * abs(expected))


def matches(expected, observed):
    """Whether a World's observed sky (``values`` or ``NO_SKY``) is the expected ``sky`` value."""
    if expected == NO_SKY or observed == NO_SKY:
        return expected == observed
    return all(_near(value, observed[name]) for name, value in expected.items())


def configure(tree, graph, params, error):
    """Apply the ``sky`` part of a request to a supported World graph; returns whether an environment was removed."""
    sky, node = params.get("sky"), graph["sky"]
    removed = False
    if node is not None and (sky == NO_SKY or params.get("environment") not in (None, "NONE")):
        tree.nodes.remove(node)  # Removed, or replaced by the environment texture this request sets.
        node = None
    if sky not in (None, NO_SKY):
        if node is None:
            for key in ("environment", "mapping", "coordinates"):  # A sky replaces an environment texture.
                if graph[key] is not None:
                    tree.nodes.remove(graph[key])
                    removed = True
            node = tree.nodes.new("ShaderNodeTexSky")
            node.name = SKY_NODE
            background = graph["background"]
            node.location = background.location.x - 300, background.location.y
            tree.links.new(node.outputs["Color"], background.inputs["Color"])
        if "type" in sky:
            node.sky_type = sky["type"]
        for name in SKY_SETTINGS:
            if name in sky:
                setattr(node, name, sky[name])
        if not matches(sky, values(node)):
            raise error("VALUE_NOT_RETAINED", "sky")
    return removed
