# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact closed catalog Geometry Nodes, never universal graph/RNA admission.

The one reserved provenance property is integrity evidence, NOT authority. Its
validated schema and the actual native graph must both pass. No custom values,
paths, resource defaults, drivers, evaluated meshes or pointers are retained.
"""

import json
from .legacy_names import NATIVE_GRAPH
from .scene_read_values import hash_value, label

PROVENANCE_KEY = NATIVE_GRAPH[0]  # Written by action_nodes; pre-rename files use NATIVE_GRAPH[1].
MAX_METADATA_BYTES = 65536
INTERFACE_DEFAULTS = {"hide_value": False, "hide_in_modifier": False, "force_non_field": False,
                      "is_inspect_output": False, "is_panel_toggle": False, "layer_selection_field": False,
                      "attribute_domain": "POINT", "default_attribute_name": "", "structure_type": "AUTO", "default_input": "VALUE"}
TREE_FIELDS = ("is_modifier", "is_tool", "is_mode_object", "is_mode_edit", "is_mode_sculpt", "is_mode_paint",
               "is_type_mesh", "is_type_curve", "is_type_pointcloud", "is_type_grease_pencil", "use_wait_for_click")
SOCKET_FIELDS = ("enabled", "hide_value", "is_unavailable", "is_multi_input", "link_limit")
NODE_SETTINGS = {"EXTRUDE_MESH": ("mode",), "SET_SHADE_SMOOTH": ("domain",)}
# Identifiers/types are fixed native declarations, not arbitrary RNA traversal.
INPUT_TYPES = {
    "TRANSFORM_GEOMETRY": {"Geometry": "GEOMETRY", "Mode": "MENU", "Translation": "VECTOR", "Rotation": "ROTATION", "Scale": "VECTOR", "Transform": "MATRIX"},
    "SET_POSITION": {"Geometry": "GEOMETRY", "Selection": "BOOLEAN", "Position": "VECTOR", "Offset": "VECTOR"},
    "SUBDIVIDE_MESH": {"Mesh": "GEOMETRY", "Level": "INT"},
    "EXTRUDE_MESH": {"Mesh": "GEOMETRY", "Selection": "BOOLEAN", "Offset": "VECTOR", "Offset Scale": "VALUE", "Individual": "BOOLEAN"},
    "MESH_CUBE": {"Size": "VECTOR", "Vertices X": "INT", "Vertices Y": "INT", "Vertices Z": "INT"},
    "MESH_GRID": {"Size X": "VALUE", "Size Y": "VALUE", "Vertices X": "INT", "Vertices Y": "INT"},
    "JOIN_GEOMETRY": {"Geometry": "GEOMETRY"},
    "SET_SHADE_SMOOTH": {"Geometry": "GEOMETRY", "Selection": "BOOLEAN", "Shade Smooth": "BOOLEAN"},
}
OUTPUT_TYPES = {
    "TRANSFORM_GEOMETRY": {"Geometry": "GEOMETRY"}, "SET_POSITION": {"Geometry": "GEOMETRY"},
    "SUBDIVIDE_MESH": {"Mesh": "GEOMETRY"}, "EXTRUDE_MESH": {"Mesh": "GEOMETRY", "Top": "BOOLEAN", "Side": "BOOLEAN"},
    "MESH_CUBE": {"Mesh": "GEOMETRY", "UV Map": "VECTOR"}, "MESH_GRID": {"Mesh": "GEOMETRY", "UV Map": "VECTOR"},
    "JOIN_GEOMETRY": {"Geometry": "GEOMETRY"}, "SET_SHADE_SMOOTH": {"Geometry": "GEOMETRY"},
}


class CatalogGraphUnsupported(ValueError):
    """Closed actionable limitation, never an arbitrary RNA/JSON exception."""


def _unsupported(reason):
    raise CatalogGraphUnsupported("catalog_geometry_" + reason)


def _keys(value, allowed, work, *, exact=False):
    """Check only key presence in closed graph-owned groups; never their values."""
    try:
        keys = value.keys()
    except TypeError:
        keys = ()  # Native structs with no allocated custom-property group.
    work.take(items=1)
    if len(keys) > len(allowed):
        _unsupported("untracked_properties")
    present = set(keys)
    if not present <= set(allowed) or exact and present != set(allowed):
        _unsupported("untracked_properties")


def _provenance_key(tree):
    """The reserved key the graph's provenance uses, judged from key names only (values stay unread).

    Files saved before the product rename carry it under the pre-rename key.
    """
    try:
        keys = tree.keys()
    except TypeError:
        return PROVENANCE_KEY
    return NATIVE_GRAPH[1] if NATIVE_GRAPH[1] in keys and PROVENANCE_KEY not in keys else PROVENANCE_KEY


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _unsupported("duplicate_metadata_keys")
        result[key] = value
    return result


def _parameters(tree, work):
    from .action_catalog import validate_operation
    key = _provenance_key(tree)
    _keys(tree, {key}, work, exact=True)
    encoded = tree.get(key)
    if type(encoded) is not str or len(encoded) > MAX_METADATA_BYTES:
        _unsupported("metadata_missing_or_size")
    work.take(elements=len(encoded), items=1)
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        _unsupported("metadata_missing_or_size")
    try:
        raw = json.loads(encoded, object_pairs_hook=_json_object,
                         parse_constant=lambda value: _unsupported("nonfinite_metadata"))
        return validate_operation({"kind": "geometry_nodes", "target": "metadata", "parameters": raw})["parameters"]
    except (ValueError, TypeError, RecursionError, OverflowError):
        _unsupported("invalid_metadata")


def _interface(tree, metadata, work):
    items = tree.interface.items_tree
    if len(items) != 2 or any(s.item_type != "SOCKET" or s.socket_type != "NodeSocketGeometry" for s in items):
        _unsupported("interface")
    result = []
    for socket in items:
        work.take(items=1 + len(INTERFACE_DEFAULTS))
        _keys(socket, set(), work)
        if socket.name != "Geometry" or socket.bl_socket_idname != "NodeSocketGeometry":
            _unsupported("interface")
        settings = metadata.settings(socket, tuple(INTERFACE_DEFAULTS))
        if settings != INTERFACE_DEFAULTS:
            _unsupported("interface_settings")
        result.append({"name": socket.name, "identifier": socket.identifier, "type": socket.socket_type,
                       "direction": socket.in_out, "settings": settings})
    if {item["direction"] for item in result} != {"INPUT", "OUTPUT"}:
        _unsupported("interface")
    return result


def _modifier_inputs(modifier, interface, work):
    # Blender 5.2 uses generated system PropertyGroups, not modifier IDProps.
    # A Geometry-only catalog interface has one fallback and NO value/resource
    # inputs or output attributes. Extra key values are never inspected.
    inputs = [s for s in interface if s["direction"] == "INPUT"]
    identifier = inputs[0]["identifier"]
    props = modifier.properties
    _keys(props.inputs, {identifier}, work, exact=True)
    _keys(props.outputs, set(), work)
    _keys(props.panels, set(), work)
    value = getattr(props.inputs, identifier)
    _keys(value, {"type"}, work, exact=True)
    # Prove the one native enum's storage kind too. Do not call its RNA enum
    # getter on an ID/string replacing this known slot (possible in PropertyGroups).
    raw_type = value["type"]
    if type(raw_type) is not int or raw_type != 0:
        _unsupported("modifier_input_storage")
    if value.type != "FALLBACK" or len(modifier.bakes) or len(modifier.panels):
        _unsupported("modifier_inputs_or_bakes")
    return {"input": identifier, "type": "FALLBACK", "output_attributes": [], "panels": []}


def _sockets(collection, expected, metadata, work):
    if len(collection) != len(expected):
        _unsupported("socket_layout")
    result = []
    for socket in collection:
        work.take(items=1 + len(SOCKET_FIELDS))
        if expected.get(socket.identifier) != socket.type:
            _unsupported("socket_type_or_dependency")
        _keys(socket, set(), work)
        item = {"name": label(socket.name), "identifier": socket.identifier, "type": socket.type,
                "linked": bool(socket.is_linked), "settings": metadata.settings(socket, SOCKET_FIELDS)}
        if socket.type == "CUSTOM":
            if socket.bl_idname != "NodeSocketVirtual" or socket.is_linked:
                _unsupported("virtual_socket")
        elif socket.type in {"VALUE", "INT", "BOOLEAN", "VECTOR", "ROTATION", "MATRIX"}:
            if hasattr(socket, "default_value"):
                item["default"] = metadata.safe_value(socket.default_value)
        elif socket.type == "MENU":
            if socket.identifier != "Mode" or socket.default_value != "Components" or socket.is_linked:
                _unsupported("transform_mode")
            item["default"] = "Components"
        result.append(item)
    return result


def _generic_node(node, kind, metadata, work):
    """Exact data of a generic node: type, writable settings and every socket's default (no datablocks)."""
    from .generic_settings import values
    if node.bl_idname != kind:
        _unsupported("node_type")
    _keys(node, set(), work)
    work.take(items=8 + len(node.inputs) + len(node.outputs))
    sockets = {}
    for direction, collection in (("inputs", node.inputs), ("outputs", node.outputs)):
        sockets[direction] = []
        for socket in collection:
            _keys(socket, set(), work)
            item = {"identifier": socket.identifier, "type": socket.type, "linked": bool(socket.is_linked),
                    "enabled": bool(getattr(socket, "enabled", True))}
            default = getattr(socket, "default_value", None)
            if hasattr(default, "users"):
                _unsupported("socket_datablock")  # A material, object or image the revision cannot track.
            if default is not None and socket.type not in {"GEOMETRY", "SHADER", "CUSTOM"}:
                item["default"] = metadata.safe_value(default)
            sockets[direction].append(item)
    return {"name": label(node.name), "label": label(node.label), "type": node.bl_idname, "mute": bool(node.mute),
            "settings": values(node, lambda value: label(value.name)), **sockets}


def read_catalog_geometry(modifier, metadata, work):
    """Return exact immutable data or a closed unsupported limitation.

    Actual topology/settings and typed metadata are both validated before
    native graph controls are admitted. This never evaluates Blender geometry;
    the program backend still owns independent evaluation budgets/preconditions.
    """
    from .action_node_graphs import _audit_geometry_tree
    from .action_schemas import GEOMETRY_NODE_TYPES
    tree = modifier.node_group
    if tree is None or tree.bl_idname != "GeometryNodeTree" or tree.library or not tree.is_editable:
        _unsupported("local_tree_required")
    if len(tree.nodes) > 16 or len(tree.links) > 32 or tree.animation_data is not None:
        _unsupported("size_or_animation")
    if tree.is_tool or tree.node_tool_idname:
        _unsupported("tool_tree")
    work.take(items=len(tree.nodes) + len(tree.links) + 1)
    parameters = _parameters(tree, work)
    try:
        _audit_geometry_tree(tree, parameters)
    except (ValueError, TypeError, AttributeError, RuntimeError):
        _unsupported("integrity")
    work.take()
    interface = _interface(tree, metadata, work)
    binding = _modifier_inputs(modifier, interface, work)
    result = {"resource": metadata.resource(tree), "interface": interface, "modifier_inputs": binding,
              "catalog_revision": hash_value(parameters), "settings": metadata.settings(tree, TREE_FIELDS),
              "nodes": [], "links": [], "revision_limitations": [], "scope": "CLOSED_CATALOG_GEOMETRY"}
    for definition in parameters["nodes"]:
        node = tree.nodes[definition["id"]]
        kind = definition["type"]
        if kind not in GEOMETRY_NODE_TYPES:  # A generic node (node_generic): its own sockets and settings.
            result["nodes"].append(_generic_node(node, kind, metadata, work))
            continue
        if node.bl_idname != GEOMETRY_NODE_TYPES[kind][0]:
            _unsupported("node_type")
        _keys(node, set(), work)
        if kind == "GROUP_INPUT":
            inputs, outputs = {}, {binding["input"]: "GEOMETRY", "__extend__": "CUSTOM"}
        elif kind == "GROUP_OUTPUT":
            output_id = next(s["identifier"] for s in interface if s["direction"] == "OUTPUT")
            inputs, outputs = {output_id: "GEOMETRY", "__extend__": "CUSTOM"}, {}
        else:
            inputs, outputs = INPUT_TYPES[kind], OUTPUT_TYPES[kind]
        item = {"name": label(node.name), "label": label(node.label), "type": node.bl_idname, "mute": bool(node.mute),
                "settings": metadata.settings(node, NODE_SETTINGS.get(kind, ()) + ("is_active_output",)),
                "inputs": _sockets(node.inputs, inputs, metadata, work), "outputs": _sockets(node.outputs, outputs, metadata, work)}
        if kind == "SET_POSITION" and (list(node.inputs["Position"].default_value) != [0, 0, 0] or not node.inputs["Position"].hide_value):
            _unsupported("implicit_position")
        result["nodes"].append(item)
    for link in tree.links:
        work.take(items=1)
        result["links"].append({"from_node": label(link.from_node.name), "from_socket": link.from_socket.identifier,
                                "to_node": label(link.to_node.name), "to_socket": link.to_socket.identifier,
                                "valid": bool(link.is_valid), "muted": bool(link.is_muted),
                                "multi_input_sort_id": int(getattr(link, "multi_input_sort_id", 0))})
    return result
