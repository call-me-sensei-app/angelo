# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What each PBR channel of a material really is, read from its node tree, and the temporary graph that bakes it.

Contract: doc/angelo/long_jobs.rst, "Texture sets". Cycles has no bake type for
metallic, alpha or a Principled input, and its Diffuse and Glossy colour bakes
weigh the colour by the shading. So a channel is baked as emission: the closure
tree feeding the Material Output (Mix and Add Shader, node groups, reroutes,
muted nodes) is followed to its BSDFs, and each closure is replaced by the colour
it carries for that channel (``LEAVES``), combined with the same factors the
shaders use: a Mix Shader becomes a colour mix with its own factor, an Add
Shader a sum. The colour drives one Emission shader in a temporary Material
Output for Cycles only, which Cycles' Emit bake then writes into the image. The
inputs feeding a BSDF (textures, procedural nodes, maths, groups) are linked
as they are, so the bake reads the material's actual values.

Cycles' own rules are kept: a Principled BSDF's Alpha weighs everything it adds
(its weight and emission); a shader passed into a group is read through the
group's input; a colour linked into a shader socket is emission; a colour linked
into a float input is converted to one value as Angelo converts it (``scalar``).
Three sums carry the weights (``SUMS``): ``WEIGHT`` is the BSDFs' share of the
surface, ``TRANSPARENCY`` the transparent share (alpha is one minus it), and the
colour channels are weighted sums the packer divides by ``WEIGHT`` where it is
not constant one (``bake_maps_pack``). A sub-tree whose every input is unlinked
folds to a constant (``Const``) without nodes; a channel that folds to one
constant on every material is filled, never baked.

The graph is added beside the person's nodes, never in place of one: the temporary
nodes, the copies of node groups that output a closure, and the output that
Cycles uses are recorded as plain data and removed again (``restore``, also from
a file saved while the bake ran); the material's own Material Output keeps the
viewport's (EEVEE's) look, since the temporary one targets Cycles only.

Main thread only. Model-facing text is English.
"""

PREFIX = "Angelo Bake "
OUTPUT_SOCKET = "Angelo Bake"
SUMS = ("WEIGHT", "TRANSPARENCY")
# Closures that scatter light from the surface; every other closure (emission, transparency, holdout) adds no
# surface value. A node type absent from these sets is treated as a BSDF and reported as approximated.
NOT_SURFACE = frozenset({"ShaderNodeEmission", "ShaderNodeBsdfTransparent", "ShaderNodeHoldout",
                         "ShaderNodeBackground"})
METALS = frozenset({"ShaderNodeBsdfMetallic", "ShaderNodeBsdfGlossy", "ShaderNodeBsdfAnisotropic"})
# Fully rough by nature: a Diffuse BSDF's own Roughness is Oren-Nayar's, not the PBR one.
MATTE = frozenset({"ShaderNodeBsdfDiffuse", "ShaderNodeBsdfTranslucent", "ShaderNodeSubsurfaceScattering",
                   "ShaderNodeBsdfSheen"})
KNOWN = frozenset({"ShaderNodeBsdfPrincipled", "ShaderNodeBsdfGlass", "ShaderNodeBsdfRefraction",
                   "ShaderNodeBsdfToon", "ShaderNodeBsdfHair", "ShaderNodeBsdfHairPrincipled",
                   "ShaderNodeBsdfRayPortal"}) | NOT_SURFACE | METALS | MATTE
NORMAL_INPUTS = ("Normal", "Coat Normal")
BLACK, WHITE = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)


class Const:
    """A value every texel shares."""
    __slots__ = ("rgb",)

    def __init__(self, rgb):
        self.rgb = tuple(float(v) for v in rgb)

    def __eq__(self, other):
        return isinstance(other, Const) and all(abs(a - b) <= 1e-6 for a, b in zip(self.rgb, other.rgb))

    def __hash__(self):
        return hash(tuple(round(v, 6) for v in self.rgb))

    def __repr__(self):
        return "Const%r" % (self.rgb,)


VARYING = "varying"   # Analysis only: a value that differs between texels.


def _grey(value):
    return Const((value, value, value))


def cycles_output(tree):
    """The Material Output Cycles renders (Blender's ``ntreeShaderOutputNode``): a Cycles one over an All one, the
    active one among equals."""
    best = None
    for node in tree.nodes:
        if node.bl_idname != "ShaderNodeOutputMaterial" or node.target not in {"ALL", "CYCLES"}:
            continue
        if best is None or (best.target == "ALL" and node.target == "CYCLES") or (
                best.target == node.target and node.is_active_output and not best.is_active_output):
            best = node
    return best


def _source(socket):
    """The output socket feeding an input, through reroutes; None when nothing valid is linked."""
    seen = 0
    while socket.is_linked and seen < 256:
        link = next((link for link in socket.links if link.is_valid and not link.is_muted), None)
        if link is None:
            return None
        node = link.from_node
        if node.bl_idname != "NodeReroute":
            return link.from_socket
        socket, seen = node.inputs[0], seen + 1
    return None


def _by_id(sockets, identifier):
    """A socket by its identifier (names repeat: a Mix node has three inputs named A)."""
    return next((socket for socket in sockets if socket.identifier == identifier), None)


def _default(socket):
    value = getattr(socket, "default_value", None)
    if value is None:
        return Const(BLACK)
    if isinstance(value, (int, float)):
        return _grey(value)
    values = tuple(value)
    return Const((values + (0.0, 0.0, 0.0))[:3])


class Graph:
    """One material's channel graphs. With ``build`` False it only folds (planning); True it adds the nodes."""

    def __init__(self, material, build=False, record=None):
        self.material, self.build = material, build
        self.record = record if record is not None else new_record(material)
        self.approximated, self.normal_used, self.displacement = set(), False, None
        self._bindings = []   # Analysis: the parent's values of the inputs of the group being read.
        # The channel each shader-typed input of the group being read carries: a value (analysis), or a socket of
        # the group copy (build).
        self._closures = []

    # -- values ---------------------------------------------------------------------------------------------
    def value(self, socket, tree):
        """An input's value: its linked source (a socket), else its own default (a constant)."""
        source = _source(socket)
        if source is None:
            return _default(socket)
        if source.type == 'SHADER':
            return _default(socket)   # A closure plugged into a value: Cycles reads nothing from it either.
        if not self.build:
            if source.node.bl_idname == "NodeGroupInput" and self._bindings:
                return self._bindings[-1].get(source.identifier, VARYING)
            return VARYING
        return source

    def scalar(self, socket, tree):
        """A float input's value as the node reads it: a colour or vector linked into it is converted by Angelo's
        own implicit conversion (a colour's luminance, a vector's average), never kept as three channels."""
        value = self.value(socket, tree)
        if isinstance(value, Const) or value is VARYING or value.type in {'VALUE', 'INT'}:
            return value
        node = self._new(tree, "ShaderNodeMath")
        node.operation = 'ADD'
        node.inputs[1].default_value = 0.0
        tree.links.new(value, node.inputs[0])
        return node.outputs[0]

    def _new(self, tree, idname):
        node = tree.nodes.new(idname)
        node.name = node.label = PREFIX + idname.removeprefix("ShaderNode")
        node.location = (-10000.0, -10000.0)   # Out of the person's view in the node editor.
        node.select = False
        if tree == self.material.node_tree:
            self.record["nodes"].append(node.name)
        return node

    def _link(self, tree, value, socket):
        if isinstance(value, Const):
            if socket.type in {'VALUE', 'INT'}:
                socket.default_value = value.rgb[0]
            elif len(socket.default_value) == 4:
                socket.default_value = (*value.rgb, 1.0)
            else:
                socket.default_value = value.rgb
        else:
            tree.links.new(value, socket)

    def _math(self, tree, operation, a, b):
        node = self._new(tree, "ShaderNodeVectorMath")
        node.operation = operation
        self._link(tree, a, node.inputs[0])
        self._link(tree, b, node.inputs[1])
        return node.outputs[0]

    def lerp(self, tree, fac, a, b):
        if isinstance(fac, Const) and isinstance(a, Const) and isinstance(b, Const):
            f = min(1.0, max(0.0, fac.rgb[0]))
            return Const(tuple(x + (y - x) * f for x, y in zip(a.rgb, b.rgb)))
        if isinstance(fac, Const) and fac.rgb[0] <= 0.0:
            return a
        if isinstance(fac, Const) and fac.rgb[0] >= 1.0:
            return b
        if isinstance(a, Const) and a == b:
            return a
        if not self.build:
            return VARYING
        node = self._new(tree, "ShaderNodeMix")
        node.data_type, node.blend_type, node.clamp_factor = 'RGBA', 'MIX', True   # A Mix Shader clamps it too.
        self._link(tree, fac, _by_id(node.inputs, "Factor_Float"))
        self._link(tree, a, _by_id(node.inputs, "A_Color"))
        self._link(tree, b, _by_id(node.inputs, "B_Color"))
        return _by_id(node.outputs, "Result_Color")

    def add(self, tree, a, b):
        if isinstance(a, Const) and isinstance(b, Const):
            return Const(tuple(x + y for x, y in zip(a.rgb, b.rgb)))
        if isinstance(a, Const) and a == Const(BLACK):
            return b
        if isinstance(b, Const) and b == Const(BLACK):
            return a
        return self._math(tree, 'ADD', a, b) if self.build else VARYING

    def multiply(self, tree, a, b):
        if isinstance(a, Const) and isinstance(b, Const):
            return Const(tuple(x * y for x, y in zip(a.rgb, b.rgb)))
        for one, other in ((a, b), (b, a)):
            if isinstance(one, Const) and one == Const(BLACK):
                return one
            if isinstance(one, Const) and one == Const(WHITE):
                return other
        return self._math(tree, 'MULTIPLY', a, b) if self.build else VARYING

    def one_minus(self, tree, a):
        if isinstance(a, Const):
            return Const(tuple(1.0 - x for x in a.rgb))
        return self._math(tree, 'SUBTRACT', Const(WHITE), a) if self.build else VARYING

    # -- closures -------------------------------------------------------------------------------------------
    def surface(self, channel):
        """The material's channel as Cycles shades its surface (a ``Const``, a socket or ``VARYING``)."""
        tree = self.material.node_tree
        output = cycles_output(tree) if tree is not None else None
        if output is None:
            return Const(BLACK) if channel not in {"WEIGHT", "TRANSPARENCY"} else _grey(0.0)
        return self.closure_input(output.inputs["Surface"], tree, channel)

    def closure_input(self, socket, tree, channel):
        source = _source(socket)
        if source is None:
            return _grey(0.0)   # No closure: black and opaque (Cycles), with no surface weight.
        if source.type != 'SHADER':
            # A colour or value linked into a shader socket: Cycles makes it an emission of that colour.
            if channel != "EMISSION":
                return _grey(0.0)
            return source if self.build else VARYING
        return self.closure(source, tree, channel)

    def closure(self, socket, tree, channel):
        node = socket.node
        if node.mute:
            passed = next((link.from_socket for link in node.internal_links if link.to_socket == socket), None)
            return self.closure_input(passed, tree, channel) if passed is not None else _grey(0.0)
        idname = node.bl_idname
        if idname == "ShaderNodeMixShader":
            fac = self.value(node.inputs[0], tree)
            return self.lerp(tree, fac, self.closure_input(node.inputs[1], tree, channel),
                             self.closure_input(node.inputs[2], tree, channel))
        if idname == "ShaderNodeAddShader":
            return self.add(tree, self.closure_input(node.inputs[0], tree, channel),
                            self.closure_input(node.inputs[1], tree, channel))
        if idname == "ShaderNodeGroup" and node.node_tree is not None:
            return self.group(node, socket, tree, channel)
        if idname == "NodeGroupInput":   # A closure passed into the group: what the parent's input carries.
            if self._closures:
                return self._closures[-1].get(socket.identifier, _grey(0.0))
            return _grey(0.0) if self.build else VARYING   # Read alone (a copy's fold check): unknown here.
        return self.leaf(node, tree, channel)

    def leaf(self, node, tree, channel):
        """The value one closure node adds to ``channel`` (weights per ``SUMS``)."""
        idname, inputs = node.bl_idname, node.inputs
        if idname not in KNOWN:
            self.approximated.add(node.name)
        surface = idname not in NOT_SURFACE
        if surface and any(name in inputs and inputs[name].is_linked for name in NORMAL_INPUTS):
            self.normal_used = True
        # A Principled BSDF's Alpha weighs everything it adds (Cycles: the rest becomes a transparent closure).
        principled = idname == "ShaderNodeBsdfPrincipled"
        alpha = self.scalar(inputs["Alpha"], tree) if principled else _grey(1.0)
        if channel == "WEIGHT":
            return alpha if surface else _grey(0.0)
        if channel == "TRANSPARENCY":
            if idname in {"ShaderNodeBsdfTransparent", "ShaderNodeHoldout"}:
                return _grey(1.0)
            return self.one_minus(tree, alpha) if principled else _grey(0.0)
        if channel == "EMISSION":
            if idname == "ShaderNodeEmission":
                return self.multiply(tree, self.value(inputs["Color"], tree), self.scalar(inputs["Strength"], tree))
            if principled:
                glow = self.multiply(tree, self.value(inputs["Emission Color"], tree),
                                     self.scalar(inputs["Emission Strength"], tree))
                return self.multiply(tree, glow, alpha)
            return Const(BLACK)
        if not surface:
            return Const(BLACK)
        if channel == "BASE_COLOR":
            value = next((self.value(inputs[name], tree) for name in ("Base Color", "Color") if name in inputs),
                         _grey(0.8))
        elif channel == "METALLIC":
            value = self.scalar(inputs["Metallic"], tree) if principled else _grey(1.0 if idname in METALS else 0.0)
        elif channel == "ROUGHNESS":
            if idname in MATTE:
                value = _grey(1.0)
            else:
                value = self.scalar(inputs["Roughness"], tree) if "Roughness" in inputs else _grey(0.5)
        else:
            raise ValueError("Not a material channel: %s" % channel)
        return self.multiply(tree, value, alpha)

    def group(self, node, socket, tree, channel):
        """A node group that outputs a closure: read inside it; to build, a copy of it outputs the channel."""
        if not self.build:
            bindings = {item.identifier: self.value(item, tree) for item in node.inputs if item.type != 'SHADER'}
            closures = {item.identifier: self.closure_input(item, tree, channel) for item in node.inputs
                        if item.type == 'SHADER'}
            self._bindings.append(bindings)
            self._closures.append(closures)
            try:
                return self._group_closure(node.node_tree, socket.identifier, channel)
            finally:
                self._bindings.pop()
                self._closures.pop()
        check = Graph(self.material)
        check._closures = [{key: value if isinstance(value, Const) else VARYING for key, value in level.items()}
                           for level in self._closures]
        folded = check.group_constant(node, socket, tree, channel)
        if folded is not None:
            return folded
        copy = node.node_tree.copy()
        copy.name = PREFIX + node.node_tree.name
        self.record["groups"].append(copy.name)
        item = copy.interface.new_socket(OUTPUT_SOCKET, in_out='OUTPUT', socket_type='NodeSocketColor')
        closures, carried = self._carry_closures(node, tree, copy, channel)
        self._closures.append(closures)
        try:
            inner = self._group_closure(copy, socket.identifier, channel)
        finally:
            self._closures.pop()
        group_output = _group_output(copy)
        if isinstance(inner, Const):
            item.default_value = (*inner.rgb, 1.0)
        else:
            copy.links.new(inner, _by_id(group_output.inputs, item.identifier))
        twin = self._new(tree, "ShaderNodeGroup")
        twin.node_tree = copy
        for original in node.inputs:
            target = _by_id(twin.inputs, original.identifier)
            if target is None:
                continue
            source = _source(original)
            if source is not None:
                tree.links.new(source, target)
            elif hasattr(original, "default_value") and hasattr(target, "default_value"):
                target.default_value = original.default_value
        for value, identifier in carried:
            tree.links.new(value, _by_id(twin.inputs, identifier))
        return _by_id(twin.outputs, item.identifier)

    def _carry_closures(self, node, tree, copy, channel):
        """Each shader input of ``node`` as the channel it carries: a constant, or a new colour input of ``copy``
        fed from the parent (``carried``: ``(parent socket, input identifier)`` to link once the twin exists)."""
        closures, carried = {}, []
        for original in node.inputs:
            if original.type != 'SHADER':
                continue
            value = self.closure_input(original, tree, channel)
            if isinstance(value, Const):
                closures[original.identifier] = value
                continue
            entry = copy.interface.new_socket(OUTPUT_SOCKET, in_out='INPUT', socket_type='NodeSocketColor')
            inputs = [n for n in copy.nodes if n.bl_idname == "NodeGroupInput"] or [copy.nodes.new("NodeGroupInput")]
            closures[original.identifier] = _by_id(inputs[0].outputs, entry.identifier)
            carried.append((value, entry.identifier))
        return closures, carried

    def group_constant(self, node, socket, tree, channel):
        """The group's value when it folds to a constant with the parent's inputs, else None."""
        value = self.group(node, socket, tree, channel)
        self.approximated.clear()
        return value if isinstance(value, Const) else None

    def _group_closure(self, group_tree, identifier, channel):
        output = _group_output(group_tree)
        socket = _by_id(output.inputs, identifier) if output is not None else None
        if socket is None:
            return _grey(0.0)
        return self.closure_input(socket, group_tree, channel)

    # -- displacement ---------------------------------------------------------------------------------------
    def height(self):
        """The displacement height ``(Height - Midlevel) * Scale`` of a Displacement node on the output, else None.

        ``self.displacement`` says what was found: "height", "vector" (a vector displacement, not a height map)
        or None.
        """
        tree = self.material.node_tree
        output = cycles_output(tree) if tree is not None else None
        source = _source(output.inputs["Displacement"]) if output is not None else None
        if source is None:
            return None
        node = source.node
        if node.bl_idname != "ShaderNodeDisplacement" or node.mute:
            self.displacement = "vector"
            return None
        self.displacement = "height"
        if self.material.displacement_method in {'BUMP', 'BOTH'}:
            self.normal_used = True
        height = self.scalar(node.inputs["Height"], tree)
        offset = self.add(tree, height, self.multiply(tree, self.scalar(node.inputs["Midlevel"], tree), _grey(-1.0)))
        return self.multiply(tree, offset, self.scalar(node.inputs["Scale"], tree))


def _group_output(tree):
    outputs = [node for node in tree.nodes if node.bl_idname == "NodeGroupOutput"]
    return next((node for node in outputs if node.is_active_output), outputs[0] if outputs else None)


def new_record(material):
    return {"material": material.name, "nodes": [], "groups": [], "active": None}


def analyse(material):
    """``{channel: Const or VARYING}`` for every material channel and sum, with what the tree uses."""
    graph = Graph(material)
    values = {channel: graph.surface(channel) for channel in ("BASE_COLOR", "METALLIC", "ROUGHNESS", "EMISSION",
                                                              *SUMS)}
    height = graph.height()
    values["HEIGHT"] = height
    return {"values": values, "normal": graph.normal_used, "displacement": graph.displacement,
            "approximated": sorted(graph.approximated)}
