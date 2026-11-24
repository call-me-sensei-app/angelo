# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scatter system's Geometry Nodes, written by code from checked layers (doc/angelo/scatter.rst).

``build(layers, assets, radii)`` makes one new node group, never editing an existing one: the surface (an Object
input, read in world space) gets each layer's points (Poisson-disk, ``Density Max`` the layer's density, ``Density
Factor`` the product of its rules as ``scatter_spec.factor`` defines them, ``Distance Min`` twice its collision
radius), minus the points within the sum of both radii of an earlier layer's, instanced from its assets by weight,
with its transform. The modifier's inputs (``INPUTS``) change the system without a rebuild: density scale, seed,
camera culling, viewport display share and proxy points.

Instances carry ``angelo_layer`` (the layer's index) on the instance domain, so a reader can tell layers apart.
"""

import math

import bpy

GROUP_NAME = "Angelo Scatter"
LAYER_ATTRIBUTE = "angelo_layer"
NOISE_EDGE = (0.45, 0.55)    # The noise rule's patches: a soft step in the middle of Blender's noise range.
DEGREES = 180.0 / math.pi

# name: (socket type, default, min, max): the modifier's inputs, read by the operator and the card.
INPUTS = {
    "Surface": ("NodeSocketObject", None, None, None),
    "Density Scale": ("NodeSocketFloat", 1.0, 0.0, 1.0),
    "Seed": ("NodeSocketInt", 0, 0, 2 ** 31 - 1),
    "Cull Camera": ("NodeSocketObject", None, None, None),
    "Cull Viewport": ("NodeSocketBool", False, None, None),
    "Cull Render": ("NodeSocketBool", False, None, None),
    "Cull Distance": ("NodeSocketFloat", 0.0, 0.0, 1e6),     # 0: no distance limit.
    "Cull Tan X": ("NodeSocketFloat", 0.5, 0.0, 1e3),        # The camera's half field of view, as tangents.
    "Cull Tan Y": ("NodeSocketFloat", 0.5, 0.0, 1e3),
    "Viewport Display": ("NodeSocketFloat", 1.0, 0.0, 1.0),   # The share of instances drawn in the viewport.
    "Proxy": ("NodeSocketBool", False, None, None),           # The viewport draws points, not the assets.
}


class _Tree:
    """Adding nodes and links, laid out in columns so a person can read the group."""

    def __init__(self, tree):
        self.tree, self.column, self.row = tree, 0, 0

    def node(self, idname, inputs=None, **props):
        node = self.tree.nodes.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        node.location = (self.column * 220.0, -self.row * 160.0)
        self.row += 1
        for key, value in (inputs or {}).items():
            self.set(node, key, value)
        return node

    def next_column(self):
        self.column, self.row = self.column + 1, 0

    def socket(self, node, key, outputs=False):
        sockets = node.outputs if outputs else node.inputs
        if isinstance(key, int):   # Among the sockets the node's mode shows.
            return [s for s in sockets if s.enabled][key]
        enabled = [s for s in sockets if s.name == key and s.enabled]
        return enabled[0] if enabled else sockets[key]

    def set(self, node, key, value):
        target = self.socket(node, key)
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, target)
        else:
            target.default_value = value

    def out(self, node, key=0):
        return self.socket(node, key, outputs=True)

    # -- arithmetic --------------------------------------------------------------------------------------------
    def math(self, operation, a, b=0.0, c=0.0):
        node = self.node("ShaderNodeMath", operation=operation)
        shown = sum(s.enabled for s in node.inputs)
        for index, value in enumerate((a, b, c)[:shown]):
            self.set(node, index, value)
        return self.out(node)

    def compare(self, operation, a, b):
        return self.out(self.node("FunctionNodeCompare", {"A": a, "B": b}, data_type="FLOAT", operation=operation))

    def boolean(self, operation, a, b=False):
        node = self.node("FunctionNodeBooleanMath", operation=operation)
        self.set(node, 0, a)
        if operation != "NOT":
            self.set(node, 1, b)
        return self.out(node)

    def smoothstep(self, value, edge0, edge1, to_min=0.0, to_max=1.0):
        return self.out(self.node("ShaderNodeMapRange", {"Value": value, "From Min": edge0, "From Max": edge1,
                                                         "To Min": to_min, "To Max": to_max},
                                  data_type="FLOAT", interpolation_type="SMOOTHSTEP", clamp=True))

    def band(self, value, low, high, soft):
        """``scatter_spec.band`` in nodes: 1 between ``low`` and ``high``, eased to 0 over ``soft`` outside."""
        if soft <= 0.0:
            inside = True
            if math.isfinite(low):
                inside = self.compare("GREATER_EQUAL", value, low)
            if math.isfinite(high):
                upper = self.compare("LESS_EQUAL", value, high)
                inside = upper if inside is True else self.boolean("AND", inside, upper)
            return 1.0 if inside is True else self.math("ADD", inside, 0.0)   # Bool to float.
        factor = 1.0
        if math.isfinite(low):
            factor = self.smoothstep(value, low - soft, low)
        if math.isfinite(high):
            fall = self.smoothstep(value, high, high + soft, 1.0, 0.0)
            factor = fall if factor == 1.0 else self.math("MULTIPLY", factor, fall)
        return factor

    def product(self, factors):
        out = 1.0
        for factor in factors:
            out = factor if out == 1.0 else (out if factor == 1.0 else self.math("MULTIPLY", out, factor))
        return out


def _interface(tree):
    sockets = {}
    for name, (kind, default, low, high) in INPUTS.items():
        socket = tree.interface.new_socket(name, in_out="INPUT", socket_type=kind)
        if default is not None:
            socket.default_value = default
        if low is not None:
            socket.min_value, socket.max_value = low, high
        sockets[name] = socket
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    return sockets


def _objects_geometry(t, objects):
    """The objects' geometry in world space, joined (curves as their wires, so a path's distance is its line)."""
    join = t.node("GeometryNodeJoinGeometry")
    for obj in objects:
        obj = bpy.data.objects[obj] if isinstance(obj, str) else obj   # A layer names its objects.
        info = t.node("GeometryNodeObjectInfo", {"Object": obj}, transform_space="RELATIVE")
        geometry = t.out(info, "Geometry")
        if obj.type in {"CURVE", "CURVES"}:
            geometry = t.out(t.node("GeometryNodeCurveToMesh", {"Curve": geometry}))
        t.tree.links.new(geometry, t.socket(join, "Geometry"))
    return t.out(join)


def _rules_factor(t, rules, surface, inputs):
    """The product of a layer's rules as a field on the surface, times the system's density scale."""
    factors = [t.out(inputs, "Density Scale")]
    if "slope" in rules:
        rule = rules["slope"]
        normal = t.out(t.node("GeometryNodeInputNormal"), "Normal")
        up = t.node("ShaderNodeVectorMath", operation="DOT_PRODUCT")
        t.set(up, 0, normal)
        t.set(up, 1, (0.0, 0.0, 1.0))
        cosine = t.math("MINIMUM", t.math("MAXIMUM", t.out(up, "Value"), -1.0), 1.0)
        degrees = t.math("MULTIPLY", t.math("ARCCOSINE", cosine), DEGREES)
        factors.append(t.band(degrees, rule["min"], rule["max"], rule["soft"]))
    if "height" in rules:
        rule = rules["height"]
        position = t.out(t.node("GeometryNodeInputPosition"))
        z = t.out(t.node("ShaderNodeSeparateXYZ", {"Vector": position}), "Z")
        if rule.get("relative"):
            stats = t.node("GeometryNodeAttributeStatistic", {"Geometry": surface, "Attribute": z},
                           data_type="FLOAT", domain="POINT")
            span = t.math("MAXIMUM", t.out(stats, "Range"), 1e-9)
            z = t.math("DIVIDE", t.math("SUBTRACT", z, t.out(stats, "Min")), span)
        factors.append(t.band(z, rule["min"], rule["max"], rule["soft"]))
    if "mask" in rules:
        rule = rules["mask"]
        value = t.out(t.node("GeometryNodeInputNamedAttribute", {"Name": rule["name"]}, data_type="FLOAT"),
                      "Attribute")
        value = t.math("MINIMUM", t.math("MAXIMUM", value, 0.0), 1.0)
        factors.append(t.math("SUBTRACT", 1.0, value) if rule["invert"] else value)
    if "distance" in rules:
        rule = rules["distance"]
        target = _objects_geometry(t, rule["objects"])
        position = t.out(t.node("GeometryNodeInputPosition"))
        nearest = None
        for element in ("FACES", "EDGES"):
            proximity = t.node("GeometryNodeProximity", {"Target": target, "Source Position": position},
                               target_element=element)
            valid = t.out(proximity, "Is Valid")
            distance = t.out(t.node("GeometryNodeSwitch", {"Switch": valid, "False": 1e9,
                                                           "True": t.out(proximity, "Distance")},
                                    input_type="FLOAT"))
            nearest = distance if nearest is None else t.math("MINIMUM", nearest, distance)
        if "keep_away" in rule:
            factors.append(t.band(nearest, rule["keep_away"] + rule["falloff"], math.inf, rule["falloff"]))
        else:
            factors.append(t.band(nearest, -math.inf, rule["near"], rule["falloff"]))
    if "noise" in rules:
        rule = rules["noise"]
        position = t.out(t.node("GeometryNodeInputPosition"))
        noise = t.node("ShaderNodeTexNoise", {"Vector": position, "Scale": 1.0 / rule["scale"]},
                       noise_dimensions="3D")
        patches = t.smoothstep(t.out(noise, "Factor"), *NOISE_EDGE)
        factors.append(t.math("SUBTRACT", 1.0, t.math("MULTIPLY", rule["amount"], t.math("SUBTRACT", 1.0, patches))))
    return t.product(factors)


def _seed(t, inputs, offset):
    return t.math("ADD", t.out(inputs, "Seed"), float(offset))


def _pick(t, weights, seed):
    """The index of an asset drawn by weight: how many cumulative weights a random value in [0, total) reaches."""
    if len(weights) == 1:
        return 0
    total = float(sum(weights))
    value = t.out(t.node("FunctionNodeRandomValue", {"Min": 0.0, "Max": total, "Seed": seed}, data_type="FLOAT"))
    index, running = 0.0, 0.0
    for weight in weights[:-1]:
        running += weight
        reached = t.math("ADD", t.compare("GREATER_EQUAL", value, running), 0.0)
        index = reached if index == 0.0 else t.math("ADD", index, reached)
    return index


def _rotation(t, transform, normal, seed):
    rotation = t.out(t.node("FunctionNodeAlignRotationToVector", {"Factor": transform["align"], "Vector": normal},
                            axis="Z", pivot_axis="AUTO"))
    if transform["yaw"]:
        angle = t.out(t.node("FunctionNodeRandomValue", {"Min": 0.0, "Max": 2.0 * math.pi, "Seed": seed},
                             data_type="FLOAT"))
        yaw = t.out(t.node("FunctionNodeAxisAngleToRotation", {"Axis": (0.0, 0.0, 1.0), "Angle": angle}))
        rotation = t.out(t.node("FunctionNodeRotateRotation", {"Rotation": rotation, "Rotate By": yaw},
                                rotation_space="LOCAL"))
    if transform["tilt"] > 0.0:
        tilt = math.radians(transform["tilt"])
        euler = t.out(t.node("FunctionNodeRandomValue", {"Min": (-tilt, -tilt, 0.0), "Max": (tilt, tilt, 0.0),
                                                         "Seed": t.math("ADD", seed, 1.0)}, data_type="FLOAT_VECTOR"))
        lean = t.out(t.node("FunctionNodeEulerToRotation", {"Euler": euler}))
        rotation = t.out(t.node("FunctionNodeRotateRotation", {"Rotation": rotation, "Rotate By": lean},
                                rotation_space="LOCAL"))
    return rotation


def _culled(t, inputs, radius):
    """True for a point the camera does not see (outside its view widened by ``radius``, behind it, or farther than
    the cull distance), when culling is on for this evaluation (viewport or render) and a camera is set."""
    camera_input = t.out(inputs, "Cull Camera")
    camera = t.node("GeometryNodeObjectInfo", {"Object": camera_input}, transform_space="ORIGINAL")
    inverse = t.out(t.node("FunctionNodeInvertMatrix", {"Matrix": t.out(camera, "Transform")}))
    local = t.out(t.node("FunctionNodeTransformPoint", {"Vector": t.out(t.node("GeometryNodeInputPosition")),
                                                        "Transform": inverse}))
    xyz = t.node("ShaderNodeSeparateXYZ", {"Vector": local})
    depth = t.math("MULTIPLY", t.out(xyz, "Z"), -1.0)    # A camera looks down its -Z.
    seen = t.compare("GREATER_THAN", depth, -radius)
    for axis, tangent in (("X", "Cull Tan X"), ("Y", "Cull Tan Y")):
        reach = t.math("MULTIPLY_ADD", t.math("MAXIMUM", depth, 0.0), t.out(inputs, tangent), radius)
        seen = t.boolean("AND", seen, t.compare("LESS_EQUAL", t.math("ABSOLUTE", t.out(xyz, axis)), reach))
    length = t.node("ShaderNodeVectorMath", operation="LENGTH")
    t.set(length, 0, local)
    limit = t.out(inputs, "Cull Distance")
    near = t.boolean("OR", t.compare("LESS_EQUAL", limit, 0.0),
                     t.compare("LESS_EQUAL", t.math("SUBTRACT", t.out(length, "Value"), radius), limit))
    seen = t.boolean("AND", seen, near)
    no_camera = t.out(t.node("FunctionNodeCompare", {"A": camera_input}, data_type="OBJECT", operation="EQUAL"))
    viewport = t.out(t.node("GeometryNodeIsViewport"))
    wanted = t.out(t.node("GeometryNodeSwitch", {"Switch": viewport, "False": t.out(inputs, "Cull Render"),
                                                 "True": t.out(inputs, "Cull Viewport")}, input_type="BOOLEAN"))
    active = t.boolean("AND", wanted, t.boolean("NOT", no_camera))
    return t.boolean("AND", active, t.boolean("NOT", seen))


def _layer(t, index, layer, objects, radius, surface, inputs, earlier):
    """One layer's points (for later layers to avoid) and its instances (or proxy points in the viewport)."""
    t.next_column()
    density = _rules_factor(t, layer["rules"], surface, inputs)
    seed = _seed(t, inputs, layer["seed"] * 7 + index * 101)
    points = t.node("GeometryNodeDistributePointsOnFaces", distribute_method="POISSON")
    for key, value in (("Mesh", surface), ("Distance Min", 2.0 * radius), ("Density Max", layer["density"]),
                       ("Density Factor", density), ("Seed", seed)):
        t.set(points, key, value)
    normal = t.out(points, "Normal")
    geometry = t.out(points, "Points")
    drop = False
    for other_points, other_radius in earlier:
        proximity = t.node("GeometryNodeProximity", {"Target": other_points}, target_element="POINTS")
        close = t.compare("LESS_THAN", t.out(proximity, "Distance"), radius + other_radius)
        close = t.boolean("AND", t.out(proximity, "Is Valid"), close)
        drop = close if drop is False else t.boolean("OR", drop, close)
    if drop is not False:
        geometry = t.out(t.node("GeometryNodeDeleteGeometry", {"Geometry": geometry, "Selection": drop},
                                domain="POINT"))
    placed = geometry
    viewport = t.out(t.node("GeometryNodeIsViewport"))
    shown = t.out(t.node("FunctionNodeRandomValue", {"Probability": t.out(inputs, "Viewport Display"),
                                                     "Seed": t.math("ADD", seed, 2.0)}, data_type="BOOLEAN"))
    hidden = t.boolean("AND", viewport, t.boolean("NOT", shown))
    geometry = t.out(t.node("GeometryNodeDeleteGeometry", {"Geometry": geometry,
                                                           "Selection": t.boolean("OR", hidden,
                                                                                  _culled(t, inputs, radius))},
                            domain="POINT"))
    t.next_column()
    pieces = t.node("GeometryNodeJoinGeometry")
    for obj in objects:
        info = t.node("GeometryNodeObjectInfo", {"Object": obj, "As Instance": True}, transform_space="ORIGINAL")
        t.tree.links.new(t.out(info, "Geometry"), t.socket(pieces, "Geometry"))
    instances = t.node("GeometryNodeInstanceOnPoints", {
        "Points": geometry, "Instance": t.out(t.node("GeometryNodeGeometryToInstance", {"Geometry": t.out(pieces)})),
        "Pick Instance": len(objects) > 1,
        "Instance Index": _pick(t, [asset["weight"] for asset in layer["assets"]], t.math("ADD", seed, 3.0)),
        "Rotation": _rotation(t, layer["transform"], normal, t.math("ADD", seed, 4.0)),
        "Scale": t.out(t.node("FunctionNodeRandomValue", {"Min": layer["transform"]["scale"][0],
                                                          "Max": layer["transform"]["scale"][1],
                                                          "Seed": t.math("ADD", seed, 6.0)}, data_type="FLOAT"))})
    tagged = t.out(t.node("GeometryNodeStoreNamedAttribute", {"Geometry": t.out(instances), "Name": LAYER_ATTRIBUTE,
                                                              "Value": index}, data_type="INT", domain="INSTANCE"))
    proxy = t.boolean("AND", viewport, t.out(inputs, "Proxy"))
    dots = t.out(t.node("GeometryNodeSetPointRadius", {"Points": geometry, "Radius": radius}))
    shown = t.out(t.node("GeometryNodeSwitch", {"Switch": proxy, "False": tagged, "True": dots},
                         input_type="GEOMETRY"))
    return placed, shown


def build(layers, assets, radii, name=GROUP_NAME):
    """A new node group for checked ``layers``; ``assets[i]`` lists layer i's objects (in its assets' order) and
    ``radii[i]`` its collision radius in scene units."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    _interface(tree)
    t = _Tree(tree)
    inputs = t.node("NodeGroupInput")
    surface_info = t.node("GeometryNodeObjectInfo", {"Object": t.out(inputs, "Surface")}, transform_space="RELATIVE")
    surface = t.out(surface_info, "Geometry")
    join = None
    earlier, shown = [], []
    for index, layer in enumerate(layers):
        placed, layer_out = _layer(t, index, layer, assets[index], radii[index], surface, inputs, earlier)
        earlier.append((placed, radii[index]))
        shown.append(layer_out)
    t.next_column()
    join = t.node("GeometryNodeJoinGeometry")
    for item in shown:
        tree.links.new(item, t.socket(join, "Geometry"))
    output = t.node("NodeGroupOutput")
    tree.links.new(t.out(join), output.inputs[0])
    return tree
