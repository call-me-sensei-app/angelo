# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic shader and geometry nodes: any registered node type, RNA-checked, with bounded geometry.

A node definition whose ``type`` is a node ``bl_idname`` (``ShaderNodeTexVoronoi``,
``GeometryNodeMeshUVSphere``...) instead of a typed catalog key is created as
that type. Its ``settings`` are RNA paths on the node and its ``inputs`` socket
default values (by socket name or identifier), both checked against the
installed definitions; an image setting names a loaded image by ID, as the
typed image texture does. Links are made between sockets of compatible types
(shader to shader, geometry to geometry, data sockets among themselves) and
must be valid once made.

Denied: script, group and file-reading nodes, zones (repeat, simulation, for
each), volumes, nodes reading other objects, images, fonts or the scene's
camera, and tool context. A geometry node with a geometry output is admitted
only with a bound rule (``GEOMETRY_BOUNDS``), so the evaluated size of a graph
is known from its unlinked inputs before Blender evaluates anything; field and
value nodes have no geometry output and need none.

Distribute Points on Faces is bounded by area (``AREA_BOUNDS``): at most its
literal density (Density Max for Poisson disk) times the surface area of its
mesh, plus one point per triangle of rounding. That area is known only when
the mesh comes from the group input of a Geometry Nodes modifier first in the
stack (the base mesh's triangulated area), from a Grid or Cube with literal
sizes, or through nodes that cannot enlarge a surface (``AREA_KEEPING``); any
other source refuses the graph before evaluation.
"""

import math
import re

from .refusal_codes import RefusalError, refusal_code, refusal_details

TYPE_PATTERN = r"^(?:ShaderNode|GeometryNode|FunctionNode|NodeReroute|NodeGroupInput|NodeGroupOutput)[A-Za-z0-9]*$"
SOCKET_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.\-()/]{0,63}$"
SHADER_PREFIXES = ("ShaderNode", "NodeReroute")
GEOMETRY_PREFIXES = ("GeometryNode", "FunctionNode", "ShaderNode", "NodeReroute", "NodeGroupInput", "NodeGroupOutput")
DENIED_NODES = frozenset({
    "ShaderNodeScript", "ShaderNodeGroup", "ShaderNodeTexIES", "ShaderNodeTexPointDensity", "ShaderNodeOutputLineStyle",
    "GeometryNodeGroup", "GeometryNodeRepeatInput", "GeometryNodeRepeatOutput", "GeometryNodeSimulationInput",
    "GeometryNodeSimulationOutput", "GeometryNodeForeachGeometryElementInput",
    "GeometryNodeForeachGeometryElementOutput", "GeometryNodeBake", "GeometryNodeObjectInfo",
    "GeometryNodeCollectionInfo", "GeometryNodeImageTexture", "GeometryNodeImageInfo", "GeometryNodeInputImage",
    "GeometryNodeStringToCurves", "GeometryNodeInputActiveCamera", "GeometryNodeSelfObject",
    "GeometryNodeDistributePointsInVolume", "GeometryNodeDistributePointsInGrid"})
DENIED_PREFIXES = ("GeometryNodeImport", "GeometryNodeTool", "GeometryNodeVolume", "GeometryNodeSDF", "GeometryNodeGrid",
                   "GeometryNodeMeshToVolume", "GeometryNodePointsToVolume", "GeometryNodeMeshToSDF", "GeometryNodeGizmo")
COMPATIBLE_DATA = frozenset({"VALUE", "INT", "BOOLEAN", "VECTOR", "RGBA", "ROTATION", "MATRIX"})


def _count(name):
    return lambda node, parents: _literal(node, name)


# bl_idname -> bound(node, parent geometry bounds) for nodes with a geometry output.
GEOMETRY_BOUNDS = {
    **{name: (lambda node, parents: max(parents, default=0)) for name in (
        "GeometryNodeSetPosition", "GeometryNodeSetShadeSmooth", "GeometryNodeSetMaterial",
        "GeometryNodeSetMaterialIndex", "GeometryNodeStoreNamedAttribute", "GeometryNodeRemoveAttribute",
        "GeometryNodeTransform", "GeometryNodeTranslateInstances", "GeometryNodeRotateInstances",
        "GeometryNodeScaleInstances", "GeometryNodeMergeByDistance", "GeometryNodeDeleteGeometry",
        "GeometryNodeSeparateGeometry", "GeometryNodeFlipFaces", "GeometryNodeSetID", "GeometryNodeSetCurveRadius",
        "GeometryNodeSetCurveTilt", "GeometryNodeSetCurveHandlePositions", "GeometryNodeCurveSetHandles",
        "GeometryNodeCurveSplineType", "GeometryNodeSetSplineCyclic", "GeometryNodeSetSplineResolution",
        "GeometryNodeSetPointRadius", "GeometryNodeCaptureAttribute", "GeometryNodeConvexHull",
        "GeometryNodeMeshToPoints", "GeometryNodePointsToVertices", "GeometryNodeMeshToCurve",
        "GeometryNodeSeparateComponents", "GeometryNodeSortElements", "GeometryNodeSetInstanceTransform",
        "GeometryNodeRealizeInstances", "GeometryNodeScaleElements", "GeometryNodeSetCurveNormal",
        "GeometryNodeTrimCurve", "GeometryNodeReverseCurve", "GeometryNodeSetFaceSet", "GeometryNodeSetGeometryName", "GeometryNodeSplitToInstances",
        "GeometryNodeInstancesToPoints", "GeometryNodeSetMeshNormal")},
    **{name: (lambda node, parents: sum(parents)) for name in (
        "GeometryNodeJoinGeometry", "GeometryNodeSwitch", "GeometryNodeIndexSwitch", "GeometryNodeMenuSwitch",
        "GeometryNodeGeometryToInstance")},
    "GeometryNodeBoundBox": lambda node, parents: 8,
    "GeometryNodeMeshGrid": lambda node, parents: _literal(node, "Vertices X") * _literal(node, "Vertices Y"),
    "GeometryNodeMeshCube": lambda node, parents: 2 * sum(_literal(node, "Vertices " + a) * _literal(node, "Vertices " + b)
                                                         for a, b in (("X", "Y"), ("Y", "Z"), ("Z", "X"))),
    "GeometryNodeMeshCircle": lambda node, parents: _literal(node, "Vertices") + 1,
    "GeometryNodeMeshLine": _count("Count"),
    "GeometryNodeMeshUVSphere": lambda node, parents: _literal(node, "Segments") * (_literal(node, "Rings") + 1),
    "GeometryNodeMeshIcoSphere": lambda node, parents: 10 * 4 ** min(_literal(node, "Subdivisions"), 10) + 2,
    "GeometryNodeMeshCylinder": lambda node, parents: _literal(node, "Vertices") * (
        _literal(node, "Side Segments") + 1 + 2 * _literal(node, "Fill Segments")) + 2,
    "GeometryNodeMeshCone": lambda node, parents: _literal(node, "Vertices") * (
        _literal(node, "Side Segments") + 1 + 2 * _literal(node, "Fill Segments")) + 2,
    "GeometryNodePoints": _count("Count"),
    "GeometryNodeCurvePrimitiveCircle": _count("Resolution"),
    "GeometryNodeCurveArc": _count("Resolution"),
    "GeometryNodeCurvePrimitiveLine": lambda node, parents: 2,
    "GeometryNodeCurvePrimitiveQuadrilateral": lambda node, parents: 4,
    "GeometryNodeCurvePrimitiveBezierSegment": _count("Resolution"),
    "GeometryNodeCurveSpiral": lambda node, parents: _literal(node, "Resolution") * max(1, math.ceil(_literal(node, "Rotations"))),
    "GeometryNodeCurveStar": lambda node, parents: 2 * _literal(node, "Points"),
    "GeometryNodeSubdivideMesh": lambda node, parents: max(parents, default=0) * 4 ** _literal(node, "Level"),
    "GeometryNodeSubdivisionSurface": lambda node, parents: max(parents, default=0) * 4 ** _literal(node, "Level"),
    "GeometryNodeExtrudeMesh": lambda node, parents: max(parents, default=0) * 6,
    "GeometryNodeDuplicateElements": lambda node, parents: max(parents, default=0) * max(1, _literal(node, "Amount")),
    "GeometryNodeTriangulate": lambda node, parents: max(parents, default=0) * 2,
    "GeometryNodeDualMesh": lambda node, parents: max(parents, default=0) * 4,
    "GeometryNodeSplitEdges": lambda node, parents: max(parents, default=0) * 4,
    "GeometryNodeFillCurve": lambda node, parents: max(parents, default=0) * 2,
    "GeometryNodeResampleCurve": lambda node, parents: max(parents, default=0) * max(1, _literal(node, "Count")),
    "GeometryNodeCurveToPoints": lambda node, parents: max(parents, default=0) * max(1, _literal(node, "Count")),
    "GeometryNodeSubdivideCurve": lambda node, parents: max(parents, default=0) * (_literal(node, "Cuts") + 1),
    "GeometryNodeFilletCurve": lambda node, parents: max(parents, default=0) * max(1, _literal(node, "Count")),
    "GeometryNodeCurveToMesh": lambda node, parents: max(1, parents[0] if parents else 0) * max(1, parents[1] if len(parents) > 1 else 1),
    "GeometryNodeInstanceOnPoints": lambda node, parents: max(1, parents[0] if parents else 0) * max(1, parents[1] if len(parents) > 1 else 1),
    "GeometryNodeMeshBoolean": lambda node, parents: sum(parents) + 2 * math.prod(max(1, p) for p in parents),
}


# Nodes whose mesh has at most the summed surface area of their geometry inputs.
AREA_KEEPING = frozenset({
    "NodeReroute", "GeometryNodeSetShadeSmooth", "GeometryNodeSetMaterial", "GeometryNodeSetMaterialIndex",
    "GeometryNodeStoreNamedAttribute", "GeometryNodeRemoveAttribute", "GeometryNodeCaptureAttribute",
    "GeometryNodeSetID", "GeometryNodeFlipFaces", "GeometryNodeSetFaceSet", "GeometryNodeSetGeometryName",
    "GeometryNodeSortElements", "GeometryNodeDeleteGeometry", "GeometryNodeSeparateGeometry",
    "GeometryNodeJoinGeometry", "GeometryNodeSwitch", "GeometryNodeIndexSwitch", "GeometryNodeMenuSwitch"})
# Primitives whose surface area their literal sizes state.
AREA_SOURCES = {
    "GeometryNodeMeshGrid": lambda node: _literal(node, "Size X") * _literal(node, "Size Y"),
    "GeometryNodeMeshCube": lambda node: (lambda x, y, z: 2 * (x * y + y * z + z * x))(*_vector(node, "Size")),
}


def _distribute(node, parents, area):
    """Distribute Points on Faces: density times area, plus one rounding point per triangle."""
    density = _literal(node, "Density Max" if getattr(node, "distribute_method", "RANDOM") == "POISSON" else "Density")
    if area is None:
        raise NodeRuleError("DISTRIBUTE_AREA_UNBOUNDED: Distribute Points on Faces needs its mesh from the group input "
                            "of a Geometry Nodes modifier first in the stack, a Grid or Cube, or area-keeping nodes",
                            code="UNSUPPORTED")
    return math.ceil(density * area) + max(parents, default=0)


# bl_idname -> bound(node, parent geometry bounds, mesh area or None) for nodes bounded by area.
AREA_BOUNDS = {"GeometryNodeDistributePointsOnFaces": _distribute}


class NodeRuleError(RefusalError):
    """A typed node refusal; unclassified construction defaults to UNKNOWN."""


def mesh_area(mesh):
    """A base mesh's triangulated surface area in its own space: the triangles Distribute samples."""
    import numpy
    triangles = mesh.loop_triangles
    areas = numpy.empty(len(triangles), dtype=numpy.float64)
    triangles.foreach_get("area", areas)
    return float(areas.sum())


def is_generic(kind):
    return bool(re.fullmatch(TYPE_PATTERN, kind or ""))


def check_type(kind, family):
    """Refuse a node type the generic path never builds in this family (pure)."""
    prefixes = SHADER_PREFIXES if family == "SHADER" else GEOMETRY_PREFIXES
    if kind in DENIED_NODES or kind.startswith(DENIED_PREFIXES):
        raise NodeRuleError("NODE_TYPE_DENIED: %s runs scripts, reads files, other objects or images, or "
                            "loops; it is reviewed Python" % kind, code="UNSUPPORTED")
    if not kind.startswith(prefixes):
        raise NodeRuleError("%s is not a %s node type" % (kind, family.lower()), code="VALIDATION")


def screen_definition(definition, family):
    """Host-side checks of one generic node definition (no bpy)."""
    from .rna_rules import json_value, screen_path
    check_type(definition["type"], family)
    for path, value in definition.get("settings", {}).items():
        screen_path(path, struct="NODE")
        if path != "image":
            json_value(value, path)
    for name, value in definition.get("inputs", {}).items():
        if not re.fullmatch(SOCKET_PATTERN, name):
            raise NodeRuleError("Name a socket by its name or identifier", code="VALIDATION")
        json_value(value, name)


def _literal(node, name):
    """An unlinked numeric input's value; a linked amplifier input has no static bound."""
    value = _unlinked(node, name).default_value
    return max(0, value) if isinstance(value, (int, float)) else 0


def _vector(node, name):
    """An unlinked vector input's component sizes."""
    return [abs(float(value)) for value in _unlinked(node, name).default_value]


def _unlinked(node, name):
    socket = find_socket(node.inputs, name)
    if socket is None:
        raise NodeRuleError("%s has no %s input in this build" % (node.bl_idname, name), code="UNSUPPORTED")
    if socket.is_linked:
        raise NodeRuleError("AMPLIFIER_INPUT_LINKED: %s %s must be a literal value" % (node.bl_idname, name), code="UNSUPPORTED")
    return socket


def find_socket(collection, name):
    """A socket by identifier, else the first enabled one with this name."""
    for socket in collection:
        if socket.identifier == name:
            return socket
    for socket in collection:
        if socket.name == name and getattr(socket, "enabled", True):
            return socket
    return None


def compatible(output, input_socket):
    """Whether Blender can carry this output into this input without an invalid link."""
    if output.type == input_socket.type:
        return True
    if "SHADER" in (output.type, input_socket.type):
        return output.type in COMPATIBLE_DATA | {"SHADER"} and input_socket.type == "SHADER"
    return output.type in COMPATIBLE_DATA and input_socket.type in COMPATIBLE_DATA


def tree_bound(tree, input_count, input_area=None):
    """The evaluated-vertex bound of a geometry tree read from its nodes, or raise (no evaluation).

    ``input_area`` returns the group input's surface area, or is None when it is unknown.
    """
    incoming = {node: [] for node in tree.nodes}
    for link in tree.links:
        if link.to_socket.type == "GEOMETRY" and link.from_socket.type == "GEOMETRY":
            incoming[link.to_node].append(link.from_node)
    memo, visiting, areas = {}, set(), {}

    def area(node):
        """Upper bound of the surface area a node outputs, or None; only reached on acyclic inputs."""
        if node not in areas:
            if node.bl_idname == "NodeGroupInput":
                areas[node] = input_area() if input_area is not None else None
            elif node.bl_idname in AREA_SOURCES:
                areas[node] = AREA_SOURCES[node.bl_idname](node)
            elif node.bl_idname in AREA_KEEPING:
                upstream = [area(parent) for parent in incoming[node]]
                areas[node] = None if None in upstream else sum(upstream)
            else:
                areas[node] = None
        return areas[node]

    def bound(node):
        if node in memo:
            return memo[node]
        if node in visiting:
            raise NodeRuleError("Node graphs must be acyclic", code="VALIDATION")
        visiting.add(node)
        parents = [bound(parent) for parent in incoming[node]]
        visiting.discard(node)
        if node.bl_idname == "NodeGroupInput":
            count = input_count
        elif node.bl_idname in AREA_BOUNDS:
            surfaces = [area(parent) for parent in incoming[node]]
            count = int(AREA_BOUNDS[node.bl_idname](node, parents, None if None in surfaces else sum(surfaces)))
        elif node.bl_idname in GEOMETRY_BOUNDS:
            count = int(GEOMETRY_BOUNDS[node.bl_idname](node, parents))
        elif any(socket.type == "GEOMETRY" for socket in node.outputs) and node.bl_idname not in {"NodeGroupOutput", "NodeReroute"}:
            raise NodeRuleError("GEOMETRY_NODE_UNBOUNDED: %s has no static size bound" % node.bl_idname, code="UNSUPPORTED")
        else:
            count = max(parents, default=0)
        memo[node] = count
        return count

    return max((bound(node) for node in tree.nodes), default=0)


def build(tree, definition, image_marker):
    """Create one generic node with its settings and input defaults (staged tree only)."""
    from . import rna_rules
    kind = definition["type"]
    try:
        node = tree.nodes.new(kind)
    except RuntimeError as error:
        raise NodeRuleError("NODE_TYPE_UNAVAILABLE: %s (%s)" % (kind, str(error)[:120]),
                            code=refusal_code(error), details=refusal_details(error)) from error
    for path, value in definition.get("settings", {}).items():
        if path == "image" and hasattr(node, "image"):
            from .action_world import resolve_image
            resolve_image(value)  # A loaded image by ID; commit assigns it (program_identities).
            node[image_marker] = value
            continue
        holder, prop = rna_rules.resolve(node, path, owner_kind="STRUCT")
        if prop.type == "POINTER":
            raise NodeRuleError("Node settings take no object or datablock pointers: " + path, code="UNSUPPORTED")
        rna_rules.write(holder, prop, path, value)
        if not rna_rules.same(value, rna_rules.read(holder, prop)):
            raise NodeRuleError("VALUE_NOT_RETAINED: node setting " + path, code="INTERNAL_ERROR")
    for name, value in definition.get("inputs", {}).items():
        socket = find_socket(node.inputs, name)
        prop = socket.bl_rna.properties.get("default_value") if socket is not None else None
        if prop is None:
            raise NodeRuleError("%s has no settable input %s" % (kind, name), code="VALIDATION")
        rna_rules.write(socket, prop, name, value)
        if not rna_rules.same(value, rna_rules.read(socket, prop)):
            raise NodeRuleError("VALUE_NOT_RETAINED: input %s (RNA range or type)" % name, code="INTERNAL_ERROR")
    return node


def link(tree, source, source_name, destination, destination_name):
    output = find_socket(source.outputs, source_name)
    input_socket = find_socket(destination.inputs, destination_name)
    if output is None or input_socket is None:
        raise NodeRuleError("No socket %s -> %s on these nodes" % (source_name, destination_name), code="VALIDATION")
    if not compatible(output, input_socket):
        raise NodeRuleError("INCOMPATIBLE_SOCKETS: %s (%s) cannot feed %s (%s)" % (
            source_name, output.type, destination_name, input_socket.type), code="VALIDATION")
    made = tree.links.new(output, input_socket)
    if not made.is_valid:
        raise NodeRuleError("INVALID_LINK: Blender rejects %s -> %s" % (source_name, destination_name), code="VALIDATION")
    return made
