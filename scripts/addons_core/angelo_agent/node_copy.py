# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact node copies for ``node_edit``: duplicate, make group, ungroup, and a node group's interface sockets.

Blender's Python API has no node copy, and the editor operators need a Node
Editor, which a program never creates. ``copy_node`` therefore rebuilds a node
from its installed RNA: every writable setting (``rna_rules.writable_settings``,
nested settings structs included), colour ramp elements, curve points, the
image and group pointers, ID properties (staging markers) and every socket's
default and hide state. It then compares a digest of both nodes and refuses
the copy (``NODE_COPY_INEXACT``) when anything differs: a node with state this
rebuild cannot reproduce (dynamic item sockets, for example) is never copied
approximately.

Make Group and Ungroup follow Blender's rules for shader trees: links that
cross the selection become group interface sockets (one per external source
or internal output); ungrouping re-links the group node's external links to the
inner nodes, and an unlinked group input gives its value to the sockets it fed
(same socket type only; a conversion is refused rather than approximated).
"""

from .refusal_codes import refusal_code, refusal_details
from .node_edit_spec import OUTPUT_TYPES, NodeEditError
from .node_graph_check import holder as _holder, writable_paths as _paths
from . import node_edit_ops as ops

INTERFACE_SOCKETS = {"VALUE": "NodeSocketFloat", "INT": "NodeSocketInt", "BOOLEAN": "NodeSocketBool",
                     "VECTOR": "NodeSocketVector", "RGBA": "NodeSocketColor", "SHADER": "NodeSocketShader"}
REQUESTED_SOCKETS = {"FLOAT": "NodeSocketFloat", "INT": "NodeSocketInt", "BOOLEAN": "NodeSocketBool",
                     "VECTOR": "NodeSocketVector", "COLOR": "NodeSocketColor", "SHADER": "NodeSocketShader"}
GROUP_NODES = {"ShaderNodeTree": "ShaderNodeGroup"}
IO_NODES = frozenset({"NodeGroupInput", "NodeGroupOutput"})


from .node_edit_spec import OUTPUT_TYPES, NodeEditError
from .node_graph_check import holder as _holder, writable_paths as _paths
from . import node_edit_ops as ops

def _plain(value):
    if hasattr(value, "as_pointer"):
        return ("ID", value.as_pointer())
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return tuple(_plain(item) for item in value)
    except TypeError:
        return str(value)


def _ramps(node):
    return [ramp for ramp in (getattr(node, "color_ramp", None),
                              getattr(getattr(node, "color_mapping", None), "color_ramp", None)) if ramp is not None]


def _mappings(node):
    mapping = getattr(node, "mapping", None)
    return [mapping] if mapping is not None and hasattr(mapping, "curves") else []


def digest(node):
    """Everything a copy must reproduce: type, settings, ramps, curves, pointers, ID properties and sockets."""
    values = []
    for path in _paths(node):
        holder, name = _holder(node, path)
        values.append((path, _plain(getattr(holder, name, None)) if holder is not None else None))
    ramps = [[(element.position, tuple(element.color)) for element in ramp.elements] for ramp in _ramps(node)]
    curves = [[sorted((tuple(point.location), point.handle_type) for point in curve.points) for curve in mapping.curves]
              for mapping in _mappings(node)]
    sockets = [[(socket.identifier, socket.type, bool(socket.hide), _plain(getattr(socket, "default_value", None))
                 if socket.type not in {"SHADER", "GEOMETRY", "CUSTOM"} else None) for socket in collection]
               for collection in (node.inputs, node.outputs)]
    pointers = [_plain(getattr(node, name, None)) for name in ("image", "node_tree")]
    return [node.bl_idname, values, ramps, curves, sockets, pointers, sorted(node.keys())]


def copy_node(source, tree):
    """An exact copy of ``source`` in ``tree`` (unselected, unparented), or NODE_COPY_INEXACT."""
    try:
        node = tree.nodes.new(source.bl_idname)
    except RuntimeError as error:
        raise NodeEditError("NODE_TYPE_UNAVAILABLE", "%s (%s)" % (source.bl_idname, str(error)[:160]),
                            refusal_code=refusal_code(error), details=refusal_details(error)) from error
    node.select = False
    if getattr(source, "node_tree", None) is not None:
        node.node_tree = source.node_tree
    for path in _paths(source):
        holder, name = _holder(source, path)
        target, _ = _holder(node, path)
        if holder is None or target is None:
            continue
        value = getattr(holder, name)
        try:
            setattr(target, name, set(value) if isinstance(value, (set, frozenset)) else value)
        except (AttributeError, TypeError, ValueError):
            continue  # The digest below decides whether the copy is still exact.
    if hasattr(source, "image"):
        node.image = source.image
    for key in source.keys():
        node[key] = source[key]
    for ramp, copied in zip(_ramps(source), _ramps(node)):
        while len(copied.elements) > len(ramp.elements):
            copied.elements.remove(copied.elements[-1])
        while len(copied.elements) < len(ramp.elements):
            copied.elements.new(0.5)
        for element, target in zip(ramp.elements, copied.elements):
            target.position, target.color = element.position, element.color
    for mapping, copied in zip(_mappings(source), _mappings(node)):
        for curve, target in zip(mapping.curves, copied.curves):
            while len(target.points) > len(curve.points):
                target.points.remove(target.points[-1])
            while len(target.points) < len(curve.points):
                target.points.new(0.5, 0.5)
            for point, copy in zip(curve.points, target.points):
                copy.location, copy.handle_type = point.location, point.handle_type
        copied.update()
    for collection, copies in ((source.inputs, node.inputs), (source.outputs, node.outputs)):
        for socket in collection:
            target = next((item for item in copies if item.identifier == socket.identifier), None)
            if target is None:
                continue
            target.hide = socket.hide
            if hasattr(socket, "default_value") and socket.type not in {"SHADER", "GEOMETRY", "CUSTOM"}:
                try:
                    target.default_value = socket.default_value
                except (AttributeError, TypeError, ValueError):
                    pass
    if digest(node) != digest(source):
        tree.nodes.remove(node)
        raise NodeEditError("NODE_COPY_INEXACT", "%s (%s) has state a native copy cannot reproduce; use reviewed "
                                                 "Python" % (source.name[:64], source.bl_idname))
    return node


def duplicate(state, edit):
    source = state.node(edit["node"])
    if source.bl_idname in OUTPUT_TYPES or source.bl_idname in IO_NODES | {"NodeFrame"}:
        raise NodeEditError("NOT_COPYABLE", "%s nodes are not duplicated natively" % source.bl_idname)
    name = state.new_name(edit["as"])
    node = state.named(copy_node(source, state.tree), name)
    place = source.location_absolute
    node.parent = source.parent
    node.location_absolute = (place[0] + edit["offset"][0], place[1] + edit["offset"][1])
    if edit["keep_inputs"]:
        for socket in source.inputs:
            target = next(item for item in node.inputs if item.identifier == socket.identifier)
            for item in socket.links:
                ops.link(state, item.from_socket, target)
    state.added.append(name)


def _shader_tree(state, what):
    if state.family != "SHADER":
        raise NodeEditError("GN_CONTRACT", "Geometry Nodes catalog graphs have no groups", refusal_code="UNSUPPORTED")
    if state.tree.bl_idname not in GROUP_NODES:
        raise NodeEditError("NOT_SUPPORTED", what + " works in shader trees")


def _centre(nodes):
    places = [tuple(node.location_absolute) for node in nodes] or [(0.0, 0.0)]
    return (sum(x for x, _ in places) / len(places), sum(y for _, y in places) / len(places))


def make_group(state, edit):
    """Group nodes into a new node group with one group node (Make Group)."""
    _shader_tree(state, "make group")
    members = [state.node(name) for name in edit["nodes"]]
    if any(node.bl_idname in OUTPUT_TYPES | IO_NODES | {"NodeFrame"} for node in members):
        raise NodeEditError("NOT_GROUPABLE", "outputs, group inputs and frames stay in their tree")
    name = state.new_name(edit["node"])
    import bpy
    if bpy.data.node_groups.get(edit["name"]) is not None:
        raise NodeEditError("NAME_TAKEN", "a node group is already named %r" % edit["name"][:64])
    tree = state.tree
    group = state.context.track_id(bpy.data.node_groups.new(edit["name"], tree.bl_idname))
    if group.name != edit["name"]:
        raise NodeEditError("NAME_TAKEN", "a node group is already named %r" % edit["name"][:64])
    centre = _centre(members)
    inner_in, inner_out = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    copies = {}
    for node in members:
        copied = copy_node(node, group)
        copied.name = node.name
        copied.location_absolute = (node.location_absolute[0] - centre[0], node.location_absolute[1] - centre[1])
        copies[node.as_pointer()] = copied
    inner_in.location_absolute = (min((c.location_absolute[0] for c in copies.values()), default=0) - 250, 0)
    inner_out.location_absolute = (max((c.location_absolute[0] for c in copies.values()), default=0) + 250, 0)
    inner = set(copies)
    incoming, outgoing, sockets = [], [], {}
    for item in tree.links:
        source, target = item.from_node.as_pointer() in inner, item.to_node.as_pointer() in inner
        if source and target:
            ops.link(_Tree(group), copies[item.from_node.as_pointer()].outputs[item.from_socket.identifier],
                     copies[item.to_node.as_pointer()].inputs[item.to_socket.identifier])
        elif target:
            incoming.append(item)
        elif source:
            outgoing.append(item)
    external = []
    for item in incoming:  # One interface input per external source socket.
        key = ("IN", item.from_socket.as_pointer())
        if key not in sockets:
            sockets[key] = _interface_socket(group, item.to_socket.name, "INPUT", item.to_socket.type)
        identifier = sockets[key].identifier
        ops.link(_Tree(group), next(s for s in inner_in.outputs if s.identifier == identifier),
                 copies[item.to_node.as_pointer()].inputs[item.to_socket.identifier])
        external.append((item.from_socket, "inputs", identifier))
    for item in outgoing:  # One interface output per internal source socket.
        key = ("OUT", item.from_socket.as_pointer())
        if key not in sockets:
            sockets[key] = _interface_socket(group, item.from_socket.name, "OUTPUT", item.from_socket.type)
            ops.link(_Tree(group), copies[item.from_node.as_pointer()].outputs[item.from_socket.identifier],
                     next(s for s in inner_out.inputs if s.identifier == sockets[key].identifier))
        external.append(("outputs", sockets[key].identifier, item.to_socket))
    frames = {node.parent.as_pointer() if node.parent else None for node in members}
    shared_frame = members[0].parent if len(frames) == 1 else None
    for node in members:
        tree.nodes.remove(node)
    group_node = state.named(tree.nodes.new(GROUP_NODES[tree.bl_idname]), name)
    group_node.node_tree = group
    group_node.parent, group_node.location_absolute = shared_frame, centre
    for entry in external:
        if entry[0] == "outputs":
            ops.link(state, next(s for s in group_node.outputs if s.identifier == entry[1]), entry[2])
        else:
            ops.link(state, entry[0], next(s for s in group_node.inputs if s.identifier == entry[2]))
    state.removed.extend(edit["nodes"])
    state.added.append(name)


class _Tree:
    """An EditState stand-in for links inside a group being built (no read-back bookkeeping)."""

    def __init__(self, tree):
        self.tree = tree


def _interface_socket(group, name, in_out, socket_type):
    kind = INTERFACE_SOCKETS.get(socket_type)
    if kind is None:
        raise NodeEditError("GROUP_SOCKET_TYPE", "%s sockets cannot cross a group boundary natively" % socket_type)
    return group.interface.new_socket(name=name, in_out=in_out, socket_type=kind)


def ungroup(state, edit):
    """Replace a group node by copies of its inner nodes, linked as the group linked them (Ungroup)."""
    _shader_tree(state, "ungroup")
    if state.world:
        raise NodeEditError("WORLD_NESTED_GROUP", "groups inside the World are not in its revision")
    group_node = state.node(edit["node"])
    group = getattr(group_node, "node_tree", None) if group_node.bl_idname == "ShaderNodeGroup" else None
    if group is None:
        raise NodeEditError("NOT_A_GROUP", edit["node"][:64] + " is not a group node with a group")
    inner = [node for node in group.nodes if node.bl_idname not in IO_NODES]
    if any(item.is_muted for item in group.links) or len(inner) + len(state.tree.nodes) > 256:
        raise NodeEditError("NOT_SUPPORTED", "muted links or a very large group are ungrouped with reviewed Python")
    tree, centre, offset = state.tree, _centre(inner), tuple(group_node.location_absolute)
    copies = {}
    for node in inner:
        copied = copy_node(node, tree)
        copied.name = node.name  # Blender suffixes a name the tree already uses.
        copied.location_absolute = (node.location_absolute[0] - centre[0] + offset[0],
                                    node.location_absolute[1] - centre[1] + offset[1])
        copies[node.as_pointer()] = copied
    for node in inner:
        copies[node.as_pointer()].parent = copies[node.parent.as_pointer()] if node.parent else group_node.parent
    sources = {socket.identifier: [item.from_socket for item in socket.links] for socket in group_node.inputs}
    values = {socket.identifier: socket for socket in group_node.inputs}
    targets = {socket.identifier: [item.to_socket for item in socket.links] for socket in group_node.outputs}
    pairs, defaults = [], []
    for item in group.links:
        into_output = item.to_node.bl_idname == "NodeGroupOutput"
        ends = targets.get(item.to_socket.identifier, []) if into_output else \
            [copies[item.to_node.as_pointer()].inputs[item.to_socket.identifier]]
        if item.from_node.bl_idname == "NodeGroupInput":
            feeds = sources.get(item.from_socket.identifier, [])
            for end in ends:
                if feeds:
                    pairs.extend((source, end) for source in feeds)
                else:
                    defaults.append((values[item.from_socket.identifier], end))
        else:
            output = copies[item.from_node.as_pointer()].outputs[item.from_socket.identifier]
            pairs.extend((output, end) for end in ends)
    linked_outputs = {item.to_socket.identifier for item in group.links if item.to_node.bl_idname == "NodeGroupOutput"}
    for identifier, ends in targets.items():
        if identifier not in linked_outputs and ends:
            output = next(node for node in group.nodes if node.bl_idname == "NodeGroupOutput" and node.is_active_output)
            defaults.extend((next(s for s in output.inputs if s.identifier == identifier), end) for end in ends)
    for index, (value, end) in enumerate(defaults):
        if value.type != end.type or value.type not in ops.WRITABLE_SOCKETS:
            raise NodeEditError("UNGROUP_CONVERSION", "%s gives a %s value to a %s socket; ungroup it with reviewed "
                                                      "Python" % (value.name, value.type, end.type))
        defaults[index] = (_value(value.default_value), end)  # Read before the group node is removed.
    state.tree.nodes.remove(group_node)
    for value, end in defaults:
        end.default_value = value
    for output, end in pairs:
        ops.link(state, output, end)
    state.removed.append(edit["node"])
    state.added.extend(node.name for node in copies.values())


def _value(value):
    return value if isinstance(value, (int, float, bool, str)) else tuple(value)


def interface(state, edit):
    """Add, remove or set one socket of the edited node group's interface."""
    if not state.nested:
        raise NodeEditError("INTERFACE_NEEDS_GROUP", "interface sockets belong to a node group")
    items = [item for item in state.tree.interface.items_tree
             if item.item_type == "SOCKET" and item.in_out == edit["in_out"] and item.name == edit["name"]]
    if edit["action"] == "ADD":
        if items:
            raise NodeEditError("NAME_TAKEN", "the group already has an %s named %r" % (edit["in_out"].lower(), edit["name"]))
        if edit["socket_type"] == "SHADER" and edit["in_out"] == "INPUT" and ("default" in edit or edit["min"] is not None):
            raise NodeEditError("INTERFACE", "shader sockets take no value")
        item = state.tree.interface.new_socket(name=edit["name"], in_out=edit["in_out"],
                                               socket_type=REQUESTED_SOCKETS[edit["socket_type"]])
    elif not items:
        raise NodeEditError("SOCKET_NOT_FOUND", "the group has no %s named %r" % (edit["in_out"].lower(), edit["name"]))
    elif edit["action"] == "REMOVE":
        state.tree.interface.remove(items[0])
        state.changed.append("interface:" + edit["name"])
        return
    else:
        item = items[0]
        if edit["new_name"] is not None:
            if any(other.item_type == "SOCKET" and other.in_out == edit["in_out"] and other.name == edit["new_name"]
                   for other in state.tree.interface.items_tree):
                raise NodeEditError("NAME_TAKEN", "the group already has a socket named %r" % edit["new_name"])
            item.name = edit["new_name"]
    _interface_values(item, edit)
    group_node = getattr(state, "group_node", None)
    if edit["action"] == "ADD" and "default" in edit and edit["in_out"] == "INPUT" and group_node is not None:
        # The edited group node's new input takes the value too (its other instances keep theirs, as in Blender).
        socket = next((socket for socket in group_node.inputs if socket.identifier == item.identifier), None)
        if socket is not None and hasattr(socket, "default_value"):
            socket.default_value = item.default_value
    state.changed.append("interface:" + item.name)


def _interface_values(item, edit):
    from . import rna_rules
    values = {key: edit[key] for key in ("min", "max") if edit.get(key) is not None}
    if "default" in edit:
        values["default"] = edit["default"]
    for key, value in values.items():
        name = {"min": "min_value", "max": "max_value", "default": "default_value"}[key]
        prop = item.bl_rna.properties.get(name)
        if prop is None or edit["in_out"] != "INPUT":
            raise NodeEditError("INTERFACE", "%s takes no %s" % (item.name[:64], key))
        rna_rules.write(item, prop, key, value)
        if not rna_rules.same(value, rna_rules.read(item, prop)):
            raise NodeEditError("VALUE_NOT_RETAINED", "interface %s (RNA range or type)" % key)
