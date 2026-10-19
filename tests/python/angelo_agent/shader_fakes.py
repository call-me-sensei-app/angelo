# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stand-in shader node trees and meshes for the host tests of the grader's graph reading.

``Tree().add(kind)`` makes a node with Blender 5.2's socket identifiers, types
and defaults (``SPECS``); ``Tree.link`` connects an output to an input, and
sockets derive ``links``/``is_linked`` from the tree as Blender does.
``material`` and ``world`` wrap a tree; ``Mesh`` is a small mesh with
attributes, for readings per face corner. Used by
``test_native_world_background``, ``test_native_scenario_checks``,
``test_native_oneshot_checks`` and ``test_native_oneshot_scenes``.
"""

from types import SimpleNamespace as NS

_COLOUR = (0.8, 0.8, 0.8, 1.0)
# (inputs, outputs) per node type: (identifier, type, default).
SPECS = {
    "OUTPUT_WORLD": ([("Surface", "SHADER", None), ("Volume", "SHADER", None)], []),
    "OUTPUT_MATERIAL": ([("Surface", "SHADER", None), ("Volume", "SHADER", None),
                         ("Displacement", "VECTOR", (0.0, 0.0, 0.0))], []),
    "BACKGROUND": ([("Color", "RGBA", (0.051, 0.051, 0.051, 1.0)), ("Strength", "VALUE", 1.0)],
                   [("Background", "SHADER", None)]),
    "EMISSION": ([("Color", "RGBA", (1.0, 1.0, 1.0, 1.0)), ("Strength", "VALUE", 1.0)], [("Emission", "SHADER", None)]),
    "BSDF_PRINCIPLED": ([("Base Color", "RGBA", _COLOUR), ("Metallic", "VALUE", 0.0), ("Roughness", "VALUE", 0.5),
                         ("Emission Color", "RGBA", (1.0, 1.0, 1.0, 1.0)), ("Emission Strength", "VALUE", 0.0),
                         ("Transmission Weight", "VALUE", 0.0)], [("BSDF", "SHADER", None)]),
    "BSDF_DIFFUSE": ([("Color", "RGBA", _COLOUR), ("Roughness", "VALUE", 0.0)], [("BSDF", "SHADER", None)]),
    "TEX_SKY": ([("Vector", "VECTOR", (0.0, 0.0, 0.0))], [("Color", "RGBA", None)]),
    "TEX_NOISE": ([("Vector", "VECTOR", (0.0, 0.0, 0.0)), ("W", "VALUE", 0.0), ("Scale", "VALUE", 5.0),
                   ("Detail", "VALUE", 2.0), ("Roughness", "VALUE", 0.5), ("Lacunarity", "VALUE", 2.0),
                   ("Offset", "VALUE", 0.0), ("Gain", "VALUE", 1.0), ("Distortion", "VALUE", 0.0)],
                  [("Fac", "VALUE", None), ("Color", "RGBA", None)]),
    "TEX_VORONOI": ([("Vector", "VECTOR", (0.0, 0.0, 0.0)), ("W", "VALUE", 0.0), ("Scale", "VALUE", 5.0),
                     ("Detail", "VALUE", 0.0), ("Roughness", "VALUE", 0.5), ("Lacunarity", "VALUE", 2.0),
                     ("Smoothness", "VALUE", 1.0), ("Exponent", "VALUE", 0.5), ("Randomness", "VALUE", 1.0)],
                    [("Distance", "VALUE", None), ("Color", "RGBA", None), ("Position", "VECTOR", None),
                     ("W", "VALUE", None), ("Radius", "VALUE", None)]),
    "TEX_BRICK": ([("Vector", "VECTOR", (0.0, 0.0, 0.0)), ("Color1", "RGBA", (0.8, 0.8, 0.8, 1.0)),
                   ("Color2", "RGBA", (0.2, 0.2, 0.2, 1.0)), ("Mortar", "RGBA", (0.0, 0.0, 0.0, 1.0)),
                   ("Scale", "VALUE", 5.0), ("Mortar Size", "VALUE", 0.02), ("Mortar Smooth", "VALUE", 0.1),
                   ("Bias", "VALUE", 0.0), ("Brick Width", "VALUE", 0.5), ("Row Height", "VALUE", 0.25)],
                  [("Color", "RGBA", None), ("Fac", "VALUE", None)]),
    "TEX_CHECKER": ([("Vector", "VECTOR", (0.0, 0.0, 0.0)), ("Color1", "RGBA", (0.8, 0.8, 0.8, 1.0)),
                     ("Color2", "RGBA", (0.2, 0.2, 0.2, 1.0)), ("Scale", "VALUE", 5.0)],
                    [("Color", "RGBA", None), ("Fac", "VALUE", None)]),
    "TEX_IMAGE": ([("Vector", "VECTOR", (0.0, 0.0, 0.0))], [("Color", "RGBA", None), ("Alpha", "VALUE", None)]),
    "TEX_COORD": ([], [(name, "VECTOR", None) for name in
                       ("Generated", "Normal", "UV", "Object", "Camera", "Window", "Reflection")]),
    "NEW_GEOMETRY": ([], [(name, "VECTOR", None) for name in ("Position", "Normal", "Incoming")]),
    "ATTRIBUTE": ([], [("Color", "RGBA", None), ("Vector", "VECTOR", None), ("Fac", "VALUE", None),
                       ("Alpha", "VALUE", None)]),
    "VERTEX_COLOR": ([], [("Color", "RGBA", None), ("Alpha", "VALUE", None)]),
    "SEPXYZ": ([("Vector", "VECTOR", (0.0, 0.0, 0.0))], [("X", "VALUE", None), ("Y", "VALUE", None),
                                                         ("Z", "VALUE", None)]),
    "VALTORGB": ([("Fac", "VALUE", 0.5)], [("Color", "RGBA", None), ("Alpha", "VALUE", None)]),
    "MATH": ([("Value", "VALUE", 0.5), ("Value_001", "VALUE", 0.5), ("Value_002", "VALUE", 0.5)],
             [("Value", "VALUE", None)]),
    "MAP_RANGE": ([("Value", "VALUE", 1.0), ("From Min", "VALUE", 0.0), ("From Max", "VALUE", 1.0),
                   ("To Min", "VALUE", 0.0), ("To Max", "VALUE", 1.0), ("Steps", "VALUE", 4.0)],
                  [("Result", "VALUE", None), ("Vector", "VECTOR", None)]),
    "MIX": ([("Factor_Float", "VALUE", 0.5), ("A_Float", "VALUE", 0.0), ("B_Float", "VALUE", 0.0),
             ("A_Color", "RGBA", (0.5, 0.5, 0.5, 1.0)), ("B_Color", "RGBA", (0.5, 0.5, 0.5, 1.0))],
            [("Result_Float", "VALUE", None), ("Result_Color", "RGBA", None)]),
    "MIX_SHADER": ([("Fac", "VALUE", 0.5), ("Shader", "SHADER", None), ("Shader_001", "SHADER", None)],
                   [("Shader", "SHADER", None)]),
    "LIGHT_PATH": ([], [(name, "VALUE", None) for name in ("Is Camera Ray", "Is Shadow Ray", "Is Diffuse Ray",
                                                           "Is Glossy Ray", "Ray Length", "Ray Depth")]),
    "TEX_GRADIENT": ([("Vector", "VECTOR", (0.0, 0.0, 0.0))], [("Color", "RGBA", None), ("Fac", "VALUE", None)]),
    "HUE_SAT": ([("Hue", "VALUE", 0.5), ("Saturation", "VALUE", 1.0), ("Value", "VALUE", 1.0), ("Fac", "VALUE", 1.0),
                 ("Color", "RGBA", _COLOUR)], [("Color", "RGBA", None)]),
    "GAMMA": ([("Color", "RGBA", _COLOUR), ("Gamma", "VALUE", 1.0)], [("Color", "RGBA", None)]),
    "RGB": ([], [("Color", "RGBA", (0.5, 0.5, 0.5, 1.0))]),
    "VALUE": ([], [("Value", "VALUE", 0.5)]),
    "GROUP": ([], [("Socket_0", "RGBA", None)]),
    "GROUP_OUTPUT": ([("Socket_0", "RGBA", (0.0, 0.0, 0.0, 1.0))], []),
}


# Inputs Blender hides for the default variant of a node (a 3D Noise's W, an F1 Voronoi's Smoothness).
HIDDEN = {"TEX_NOISE": ("W", "Offset", "Gain"), "TEX_VORONOI": ("W", "Smoothness", "Exponent")}
# The settings a node type starts with, as Blender 5.2 sets them.
SETTINGS = {"TEX_NOISE": {"noise_dimensions": "3D", "noise_type": "FBM", "normalize": True},
            "TEX_VORONOI": {"voronoi_dimensions": "3D", "feature": "F1", "distance": "EUCLIDEAN", "normalize": False},
            "TEX_BRICK": {"offset": 0.5, "offset_frequency": 2, "squash": 1.0, "squash_frequency": 2}}


class Ramp:
    """``ColorRamp`` in linear RGB mode: ``evaluate`` holds the end colours beyond the first and last stops."""

    def __init__(self, *stops):
        self.elements = [NS(position=position, color=colour) for position, colour in stops]

    def evaluate(self, position):
        stops = self.elements
        if position <= stops[0].position:
            return stops[0].color
        for low, high in zip(stops, stops[1:]):
            if position <= high.position:
                t = (position - low.position) / ((high.position - low.position) or 1.0)
                return tuple(a + (b - a) * t for a, b in zip(low.color, high.color))
        return stops[-1].color


class Socket(NS):
    """A stand-in ``NodeSocket``: ``links``/``is_linked`` come from the tree, as Blender derives them."""

    @property
    def links(self):
        return [link for link in self.tree.links if link.to_socket is self or link.from_socket is self]

    @property
    def is_linked(self):
        return bool(self.links)


class Sockets(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next(socket for socket in self if socket.identifier == key or socket.name == key)
        return list.__getitem__(self, key)

    def __contains__(self, key):
        return any(socket.identifier == key or socket.name == key for socket in self)


class Tree:
    """A stand-in ``ShaderNodeTree``: ``add`` a node by type, ``link`` an output to an input."""

    def __init__(self):
        self.nodes, self.links = [], []

    def add(self, kind, name=None, unavailable=None, **props):
        inputs, outputs = SPECS[kind]
        unavailable = HIDDEN.get(kind, ()) if unavailable is None else unavailable
        props = dict(SETTINGS.get(kind, {}), **props)
        node = NS(type=kind, name=name or "%s.%03d" % (kind, len(self.nodes)), mute=False, internal_links=[], **props)
        node.inputs = Sockets(Socket(identifier=i, name=i.split("_")[0], type=t, default_value=d, node=node,
                                     tree=self, is_unavailable=i in unavailable) for i, t, d in inputs)
        node.outputs = Sockets(Socket(identifier=i, name=i.split("_")[0], type=t, default_value=d, node=node,
                                      tree=self) for i, t, d in outputs)
        if kind in {"OUTPUT_WORLD", "OUTPUT_MATERIAL"}:
            node.is_active_output, node.target = props.get("is_active_output", True), props.get("target", "ALL")
        self.nodes.append(node)
        return node

    def link(self, source, output, target, input, muted=False):
        link = NS(from_node=source, from_socket=source.outputs[output], to_node=target, to_socket=target.inputs[input],
                  is_muted=muted, is_valid=True)
        self.links.append(link)
        return link


def colour_mix(tree, blend="MIX", **props):
    """A Mix node in colour mode, its float sockets hidden as Blender hides them."""
    return tree.add("MIX", data_type="RGBA", blend_type=blend, clamp_factor=True, clamp_result=False,
                    unavailable=("A_Float", "B_Float", "Result_Float"), **props)


def material(name, base=None, roughness=0.5, metallic=0.0, emission=None, strength=0.0, build=None,
             diffuse=(0.8, 0.8, 0.8, 1.0)):
    """A material whose output's Surface is a Principled BSDF; ``build(tree, bsdf)`` wires its inputs."""
    tree = Tree()
    output = tree.add("OUTPUT_MATERIAL", name="Material Output")
    bsdf = tree.add("BSDF_PRINCIPLED", name="Principled BSDF")
    tree.link(bsdf, "BSDF", output, "Surface")
    if base is not None:
        bsdf.inputs["Base Color"].default_value = base
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = emission
    bsdf.inputs["Emission Strength"].default_value = strength
    if build is not None:
        build(tree, bsdf)
    return NS(name=name, use_nodes=True, node_tree=tree, diffuse_color=diffuse, roughness=roughness)


def emission_material(name, colour, strength):
    tree = Tree()
    output = tree.add("OUTPUT_MATERIAL", name="Material Output")
    glow = tree.add("EMISSION", name="Emission")
    glow.inputs["Color"].default_value, glow.inputs["Strength"].default_value = colour, strength
    tree.link(glow, "Emission", output, "Surface")
    return NS(name=name, use_nodes=True, node_tree=tree, diffuse_color=(0.8, 0.8, 0.8, 1.0), roughness=0.5)


def world(build=None, name="World", color=(0.051, 0.051, 0.051)):
    """A World whose output's Surface is a Background; ``build(tree, background)`` wires its Color."""
    tree = Tree()
    output = tree.add("OUTPUT_WORLD", name="World Output")
    background = tree.add("BACKGROUND", name="Background")
    tree.link(background, "Background", output, "Surface")
    if build is not None:
        build(tree, background)
    return NS(name=name, node_tree=tree, color=color)


class Attribute(NS):
    """A mesh attribute: ``data`` items carry ``color`` (4 floats) or ``value``/``vector``."""


class Attributes(list):
    def get(self, name):
        return next((attribute for attribute in self if attribute.name == name), None)


class Mesh:
    """A flat mesh of square quads in the XY plane, triangulated as Blender does, for readings per point.

    ``faces``: per face ``(material_index, area)``; face ``f`` is a square of that area at ``x = 2 f``.
    Vertices are numbered corner by corner (four per face), so the point and corner domains have the same
    length; the UV map is each corner's X and Y.
    """

    def __init__(self, faces, attributes=(), colour_attributes=()):
        self.polygons, self.loops, self.vertices, self.loop_triangles = [], [], [], []
        for face, (index, area) in enumerate(faces):
            side, base = area ** 0.5, 4 * face
            corners = [(2.0 * face, 0.0), (2.0 * face + side, 0.0), (2.0 * face + side, side), (2.0 * face, side)]
            self.vertices += [NS(co=(x, y, 0.0)) for x, y in corners]
            self.loops += [NS(vertex_index=base + corner) for corner in range(4)]
            self.polygons.append(NS(loop_total=4, area=area, material_index=index, normal=(0.0, 0.0, 1.0)))
            for triangle in ((0, 1, 2), (0, 2, 3)):
                ids = tuple(base + corner for corner in triangle)
                self.loop_triangles.append(NS(vertices=ids, loops=ids, polygon_index=face, area=area / 2.0))
        self.corner_normals = [NS(vector=(0.0, 0.0, 1.0)) for _ in self.loops]
        self.uv_layers = [NS(active_render=True, data=[NS(uv=vertex.co[:2]) for vertex in self.vertices])]
        self.texspace_location, self.texspace_size = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        self.attributes = Attributes(attributes)
        self.color_attributes = Attributes(colour_attributes)
        self.color_attributes.render_color_index = 0


def colour_attribute(name, colours, domain="CORNER", kind="FLOAT_COLOR"):
    return Attribute(name=name, domain=domain, data_type=kind, data=[NS(color=tuple(c) + (1.0,) * (4 - len(c)))
                                                                     for c in colours])


def float_attribute(name, values, domain="FACE"):
    return Attribute(name=name, domain=domain, data_type="FLOAT", data=[NS(value=v) for v in values])


def holder(name, *materials, mesh=None, **extra):
    """An object holding materials: a mesh object whose evaluated self is itself."""
    data = mesh if mesh is not None else NS(polygons=[1], materials=list(materials))
    values = dict(name=name, type="MESH", hide_render=False, data=data,
                  material_slots=[NS(material=m) for m in materials],
                  matrix_world=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0],
                                [0.0, 0.0, 0.0, 1.0]])
    values.update(extra)
    obj = NS(**values)
    obj.evaluated_get = lambda depsgraph: obj
    return obj
