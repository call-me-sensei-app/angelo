# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lens and film look's compositor node group, written by code from resolved stages (doc/angelo/camera_look.rst).

``build(stages)`` makes one new ``CompositorNodeTree`` with an Image input and output and the stages in physical order
(``look_spec.ORDER``), only those the look has: lens (Lens Distortion), vignette (cos^4 of each pixel's angle from the
optical axis, from the group's Focal Length and Sensor Width inputs, plus a mechanical cut-off), glare (Bloom, Streaks,
Ghosts), halation (a warm bloom of the highlights), film (exposure, contrast, saturation, faded blacks; white balance is
colour management's, set by ``look_system``) and grain (luminance noise a share of the frame's height across, moving
with the Frame input). ``insert`` puts it before the scene's compositing output, keeping what the person built.
"""

import bpy

GROUP_NAME = "Angelo Look"
NODE_NAME = "Angelo Look"          # The group node in the scene's compositing group; reapplying replaces it.
HALATION_TINT = (1.0, 0.38, 0.18, 1.0)   # Film halation is red-orange: the base reflects what the red layer saw.
MECHANICAL_EDGE = (0.75, 1.45)     # Mechanical cut-off: from 3/4 of the way to the side to past the corner.
GRAIN_SHAPE = 0.9                  # Noise detail: one octave reads as grain, more as clouds.

# name: (socket type, default): the group's inputs; drivers keep Focal Length and Frame current (look_system).
INPUTS = {"Image": ("NodeSocketColor", (0.0, 0.0, 0.0, 1.0)), "Focal Length": ("NodeSocketFloat", 50.0),
          "Sensor Width": ("NodeSocketFloat", 36.0), "Frame": ("NodeSocketFloat", 1.0)}


class _Tree:
    def __init__(self, tree):
        self.tree, self.x = tree, 0.0

    def node(self, idname, inputs=None, **props):
        node = self.tree.nodes.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        node.location = (self.x, 0.0)
        self.x += 200.0
        for key, value in (inputs or {}).items():
            self.set(node, key, value)
        return node

    @staticmethod
    def socket(sockets, key):
        if isinstance(key, int):
            return [s for s in sockets if s.enabled][key]
        enabled = [s for s in sockets if s.name == key and s.enabled]
        return enabled[0] if enabled else sockets[key]

    def set(self, node, key, value):
        target = self.socket(node.inputs, key)
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, target)
        else:
            target.default_value = value

    def out(self, node, key=0):
        return self.socket(node.outputs, key)

    def math(self, operation, a, b=0.0, c=0.0):
        node = self.node("ShaderNodeMath", operation=operation)
        shown = sum(s.enabled for s in node.inputs)
        for index, value in enumerate((a, b, c)[:shown]):
            self.set(node, index, value)
        return self.out(node)

    def mix(self, blend, factor, a, b):
        return self.out(self.node("ShaderNodeMix", {"Factor": factor, "A": a, "B": b}, data_type="RGBA",
                                  blend_type=blend, clamp_result=False))


def _frame_xy(t, image):
    """Each pixel's place on the frame in half-widths (x from -1 to 1 across, y in the same unit: the sensor fitted
    horizontally), half the frame's width and its height, in pixels."""
    coords = t.out(t.node("CompositorNodeImageCoordinates", {"Image": image}), "Pixel")
    size = t.out(t.node("CompositorNodeImageInfo", {"Image": image}), "Resolution")
    pixel = t.node("ShaderNodeSeparateXYZ", {"Vector": coords})
    res = t.node("ShaderNodeSeparateXYZ", {"Vector": size})
    width, height = t.out(res, "X"), t.out(res, "Y")
    half = t.math("MULTIPLY", width, 0.5)
    x = t.math("DIVIDE", t.math("SUBTRACT", t.math("ADD", t.out(pixel, "X"), 0.5), half), half)
    y = t.math("DIVIDE", t.math("SUBTRACT", t.math("ADD", t.out(pixel, "Y"), 0.5), t.math("MULTIPLY", height, 0.5)),
               half)
    return x, y, half, height


def _vignette(t, image, values, inputs):
    x, y, _half, _height = _frame_xy(t, image)
    radius = t.math("SQRT", t.math("ADD", t.math("MULTIPLY", x, x), t.math("MULTIPLY", y, y)))
    off_axis = t.math("MULTIPLY", radius, t.math("MULTIPLY", t.out(inputs, "Sensor Width"), 0.5))
    tangent = t.math("DIVIDE", off_axis, t.math("MAXIMUM", t.out(inputs, "Focal Length"), 1e-3))
    falloff = t.math("POWER", t.math("ADD", 1.0, t.math("MULTIPLY", tangent, tangent)), -2.0)
    light = t.math("SUBTRACT", 1.0, t.math("MULTIPLY", values["amount"], t.math("SUBTRACT", 1.0, falloff)))
    if values["mechanical"] > 0.0:
        cut = t.out(t.node("ShaderNodeMapRange", {"Value": radius, "From Min": MECHANICAL_EDGE[0],
                                                  "From Max": MECHANICAL_EDGE[1], "To Min": 0.0, "To Max": 1.0},
                           interpolation_type="SMOOTHSTEP", clamp=True))
        light = t.math("MULTIPLY", light, t.math("SUBTRACT", 1.0, t.math("MULTIPLY", values["mechanical"], cut)))
    return t.mix("MULTIPLY", 1.0, image, light)


def _glare(t, image, kind, strength, threshold, **extra):
    node = t.node("CompositorNodeGlare", {"Image": image, "Type": kind, "Quality": "High", "Threshold": threshold,
                                          "Strength": strength, **extra})
    return t.out(node, "Image")


def _grain(t, image, values, inputs):
    x, y, half, height = _frame_xy(t, image)
    across = t.math("MAXIMUM", t.math("MULTIPLY", height, values["size"]), 1e-3)   # grain_pixels, in nodes.
    position = t.node("ShaderNodeCombineXYZ", {"X": x, "Y": y, "Z": t.math("MULTIPLY", t.out(inputs, "Frame"), 7.31)})
    scale = t.math("DIVIDE", half, across)   # Grains per half-width: the noise's unit is a half-width.
    noise = t.node("ShaderNodeTexNoise", {"Vector": t.out(position), "Scale": scale, "Detail": 0.0,
                                          "Roughness": GRAIN_SHAPE}, noise_dimensions="3D")
    centred = t.math("MULTIPLY", t.math("SUBTRACT", t.out(noise, "Factor"), 0.5), 2.0 * values["amount"])
    return t.mix("ADD", 1.0, image, centred)


def _film(t, image, values):
    if values["exposure"]:
        image = t.out(t.node("CompositorNodeExposure", {"Image": image, "Exposure": values["exposure"]}))
    if values["contrast"] or values["saturation"] != 1.0:
        image = t.out(t.node("CompositorNodeColorCorrection", {"Image": image}))
        node = image.node
        node.inputs[2].default_value = values["saturation"]     # Master saturation.
        node.inputs[3].default_value = 1.0 + values["contrast"]  # Master contrast.
    if values["fade"]:
        image = t.mix("MIX", values["fade"], image, (1.0, 1.0, 1.0, 1.0))
    return image


def build(stages, name=GROUP_NAME):
    """A new compositor node group for ``stages`` (``look_spec.resolve``)."""
    tree = bpy.data.node_groups.new(name, "CompositorNodeTree")
    for key, (kind, default) in INPUTS.items():
        socket = tree.interface.new_socket(key, in_out="INPUT", socket_type=kind)
        socket.default_value = default
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    t = _Tree(tree)
    inputs = t.node("NodeGroupInput")
    image = t.out(inputs, "Image")
    if "lens" in stages:
        values = stages["lens"]
        image = t.out(t.node("CompositorNodeLensdist", {"Image": image, "Type": "Radial",
                                                        "Distortion": values["distortion"],
                                                        "Dispersion": values["dispersion"], "Fit": False}))
    if "vignette" in stages:
        image = _vignette(t, image, stages["vignette"], inputs)
    if "glare" in stages:
        values = stages["glare"]
        for kind, strength, extra in (("Bloom", values["bloom"], {}), ("Ghosts", values["ghosts"], {}),
                                      ("Streaks", values["streaks"], {"Streaks": 2, "Streaks Angle": 0.0})):
            if strength > 0.0:
                image = _glare(t, image, kind, strength, values["threshold"], **extra)
    if "halation" in stages and stages["halation"]["amount"] > 0.0:
        values = stages["halation"]
        image = _glare(t, image, "Bloom", values["amount"], values["threshold"], Tint=HALATION_TINT,
                       Size=min(1.0, values["radius"] / 0.05))
    if "film" in stages:
        image = _film(t, image, stages["film"])
    if "grain" in stages and stages["grain"]["amount"] > 0.0:
        image = _grain(t, image, stages["grain"], inputs)
    output = t.node("NodeGroupOutput")
    tree.links.new(image, output.inputs[0])
    return tree


def compositing_group(scene):
    """The scene's compositing group, made (Render Layers to the output) when it has none."""
    group = scene.compositing_node_group
    if group is not None:
        return group, False
    group = bpy.data.node_groups.new("Compositing", "CompositorNodeTree")
    group.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    layers = group.nodes.new("CompositorNodeRLayers")
    output = group.nodes.new("NodeGroupOutput")
    output.location = (400.0, 0.0)
    group.links.new(layers.outputs["Image"], output.inputs[0])
    scene.compositing_node_group = group
    return group, True


def insert(scene, look):
    """Put ``look`` before the scene's compositing output (replacing an earlier look's node); returns the node."""
    group, _made = compositing_group(scene)
    node = group.nodes.get(NODE_NAME)
    if node is not None and node.bl_idname == "CompositorNodeGroup":
        old, node.node_tree = node.node_tree, look
        if old is not None and old != look and old.users == 0:
            bpy.data.node_groups.remove(old)   # The earlier look's group, which nothing else uses.
        return node
    output = next((n for n in group.nodes if n.bl_idname == "NodeGroupOutput" and n.is_active_output), None) \
        or next((n for n in group.nodes if n.bl_idname == "NodeGroupOutput"), None)
    if output is None:
        raise ValueError("the scene's compositor has no output")
    target = output.inputs[0]
    source = target.links[0].from_socket if target.links else None
    node = group.nodes.new("CompositorNodeGroup")
    node.node_tree, node.name, node.label = look, NODE_NAME, NODE_NAME
    node.location = (output.location.x, output.location.y)
    output.location.x += 220.0
    if source is not None:
        group.links.new(source, node.inputs["Image"])
    group.links.new(node.outputs["Image"], target)
    return node


def remove(scene):
    """Take the look out of the scene's compositor, reconnecting what fed it; True if there was one."""
    group = scene.compositing_node_group
    node = group.nodes.get(NODE_NAME) if group else None
    if node is None:
        return False
    source = node.inputs["Image"].links[0].from_socket if node.inputs["Image"].links else None
    targets = [link.to_socket for link in node.outputs["Image"].links]
    look = node.node_tree
    group.nodes.remove(node)
    if look is not None and look.users == 0:
        bpy.data.node_groups.remove(look)
    for target in targets if source is not None else ():
        group.links.new(source, target)
    return True
