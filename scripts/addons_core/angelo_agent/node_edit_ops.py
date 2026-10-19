# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``node_edit`` steps on one private staged tree, and the graph validity they must keep.

Every step names nodes by their exact names. New nodes are never selected (the
artist's selection in the tree is kept as it was) and get exactly the requested
name (a taken name is refused, never suffixed). Settings go through
``rna_rules`` (deny-list, installed RNA, read-back), sockets through
``node_generic.find_socket`` (identifier, then enabled name). Links are made
only between compatible socket types and must be valid once made; links
dropped by Delete with Reconnect, Detach or Swap are reported, not hidden.
Groups, duplicates and interface sockets are ``node_copy``.
"""

from .refusal_codes import refusal_code, refusal_details
from .node_edit_spec import MAX_TREE_LINKS, MAX_TREE_NODES, OUTPUT_TYPES, SURFACE_OUTPUTS, NodeEditError
from . import node_generic

WRITABLE_SOCKETS = frozenset({"VALUE", "INT", "BOOLEAN", "VECTOR", "RGBA", "ROTATION"})
OUTPUT_DEFAULT_NODES = frozenset({"ShaderNodeValue", "ShaderNodeRGB"})
# Properties Blender's Swap Node carries to the new node when both have them.
SWAP_PROPERTIES = ("color", "hide", "label", "mute", "show_options", "show_preview", "show_texture", "use_alpha",
                   "use_clamp", "use_custom_color", "operation", "domain", "data_type", "interpolation")
MATH_NODES = frozenset({"ShaderNodeMath", "ShaderNodeVectorMath", "FunctionNodeIntegerMath", "FunctionNodeBooleanMath",
                        "FunctionNodeBitMath"})


class EditState:
    """One tree being edited by one operation, and what changed (for the read-back)."""

    def __init__(self, context, tree, family, nested=False, world=False):
        self.context, self.tree, self.family, self.nested, self.world = context, tree, family, nested, world
        self.added, self.removed, self.changed, self.dropped = [], [], [], []
        self.placed = set()  # New nodes placed automatically (no requested location).
        self.outputs = sorted(node.name for node in tree.nodes if node.bl_idname in OUTPUT_TYPES)
        self.surfaces = sorted(node.name for node in tree.nodes if node.bl_idname in SURFACE_OUTPUTS
                               and node.inputs[SURFACE_OUTPUTS[node.bl_idname]].is_linked)

    def node(self, name):
        node = self.tree.nodes.get(name)
        if node is None:
            names = ", ".join(repr(item.name[:64]) for item in list(self.tree.nodes)[:16])
            raise NodeEditError("NODE_NOT_FOUND", "no node is named %r; nodes: %s" % (name[:64], names))
        return node

    def new_name(self, name):
        if self.tree.nodes.get(name) is not None:
            raise NodeEditError("NAME_TAKEN", "a node is already named %r" % name[:64])
        if self.family == "GEOMETRY":
            import re
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", name):
                raise NodeEditError("GN_CONTRACT", "Geometry Nodes catalog node names are identifiers (letters, digits, _)",
                                    refusal_code="UNSUPPORTED")
        return name

    def named(self, node, name):
        node.name = name
        node.select = False
        if node.name != name:
            self.tree.nodes.remove(node)
            raise NodeEditError("NAME_TAKEN", "a node is already named %r" % name[:64], refusal_code="INTERNAL_ERROR")
        return node

    def summary(self):
        result = {"added": self.added[:32], "removed": self.removed[:32], "changed": sorted(set(self.changed))[:32]}
        if self.dropped:
            result["dropped_links"] = self.dropped[:32]
        return result

    def validate(self):
        tree = self.tree
        if len(tree.nodes) > MAX_TREE_NODES or len(tree.links) > MAX_TREE_LINKS:
            raise NodeEditError("TREE_SIZE", "at most %d nodes and %d links" % (MAX_TREE_NODES, MAX_TREE_LINKS))
        if sorted(node.name for node in tree.nodes if node.bl_idname in OUTPUT_TYPES) != self.outputs:
            raise NodeEditError("OUTPUT_NODES", "a tree keeps its output nodes; edit the links into them")
        for node in [node for node in tree.nodes if node.name in self.surfaces]:
            if not next(socket for socket in node.inputs if socket.name == SURFACE_OUTPUTS[node.bl_idname]).is_linked:
                raise NodeEditError("OUTPUT_DISCONNECTED", "%r lost its Surface link" % node.name[:64])
        incoming = {node.as_pointer(): [] for node in tree.nodes}
        for link in tree.links:
            if not link.is_valid:
                raise NodeEditError("INVALID_LINK", "%s.%s -> %s.%s is invalid (a cycle or incompatible types)" % (
                    link.from_node.name[:64], link.from_socket.name, link.to_node.name[:64], link.to_socket.name))
            incoming[link.to_node.as_pointer()].append(link.from_node.as_pointer())
        state = {}

        def visit(pointer):
            if state.get(pointer) == 1:
                raise NodeEditError("CYCLE", "node graphs must be acyclic")
            if state.get(pointer) != 2:
                state[pointer] = 1
                for parent in incoming[pointer]:
                    visit(parent)
                state[pointer] = 2

        for pointer in incoming:
            visit(pointer)


def check_type(state, kind):
    """Refuse a node type this tree must not gain (outputs, groups, denied or wrong-family nodes)."""
    if kind in OUTPUT_TYPES:
        raise NodeEditError("OUTPUT_NODES", "a tree keeps its output nodes; edit the links into them")
    if kind == "NodeFrame":
        if state.family == "GEOMETRY":
            raise NodeEditError("GN_CONTRACT", "frames are outside the closed Geometry Nodes catalog graph",
                                refusal_code="UNSUPPORTED")
        return
    if kind in {"ShaderNodeGroup", "GeometryNodeGroup"}:
        raise NodeEditError("NODE_TYPE_DENIED", "make a group with the group step")
    try:
        node_generic.check_type(kind, state.family)
    except node_generic.NodeRuleError as error:
        raise NodeEditError("NODE_TYPE_DENIED", str(error),
                            refusal_code=refusal_code(error), details=refusal_details(error)) from error


def write_settings(state, node, settings):
    from . import rna_rules
    from .node_edit_spec import DENIED_SETTINGS
    resolved = []
    for path, value in settings.items():
        if path in DENIED_SETTINGS:
            raise NodeEditError("SETTING_DENIED", path + " is editor state")
        if state.family == "GEOMETRY" and path == "mute" and value:
            raise NodeEditError("GN_CONTRACT", "muted nodes are outside the closed Geometry Nodes catalog graph",
                                refusal_code="UNSUPPORTED")
        holder, prop = rna_rules.resolve(node, path, owner_kind="STRUCT")
        if prop.type == "POINTER":
            raise NodeEditError("NO_POINTERS", "node settings take no object or datablock pointers: " + path)
        rna_rules.write(holder, prop, path, value)
        resolved.append((path, value, holder, prop))
    for path, value, holder, prop in resolved:  # Read back after all writes: a later one cannot undo an earlier one.
        if not rna_rules.same(value, rna_rules.read(holder, prop)):
            raise NodeEditError("VALUE_NOT_RETAINED", "setting %s (RNA range or type)" % path)


def write_sockets(node, values, outputs=False):
    from . import rna_rules
    if outputs and values and node.bl_idname not in OUTPUT_DEFAULT_NODES:
        raise NodeEditError("SOCKET_TYPE", "only Value and RGB nodes take output values")
    for name, value in values.items():
        socket = node_generic.find_socket(node.outputs if outputs else node.inputs, name)
        if socket is None:
            names = ", ".join(repr(item.identifier) for item in (node.outputs if outputs else node.inputs))
            raise NodeEditError("SOCKET_NOT_FOUND", "%s has no %s socket %r; sockets: %s" % (
                node.name[:64], "output" if outputs else "input", name, names[:400]))
        prop = socket.bl_rna.properties.get("default_value")
        if socket.type not in WRITABLE_SOCKETS or prop is None:
            raise NodeEditError("SOCKET_TYPE", "%s %s sockets take no value here" % (name, socket.type))
        rna_rules.write(socket, prop, name, value)
        if not rna_rules.same(value, rna_rules.read(socket, prop)):
            raise NodeEditError("VALUE_NOT_RETAINED", "socket %s (RNA range or type)" % name)


def write_image(node, image_id):
    """An image node names a loaded image by ID; the stage holds only the ID and commit assigns the image."""
    from .action_node_graphs import IMAGE_MARKER
    from .action_world import resolve_image
    if not hasattr(node, "image"):
        raise NodeEditError("NOT_AN_IMAGE_NODE", node.name[:64] + " takes no image")
    resolve_image(image_id)  # Fails early for a missing or expired image ID.
    node[IMAGE_MARKER] = image_id
    node.image = None


def socket_of(node, name, outputs):
    socket = node_generic.find_socket(node.outputs if outputs else node.inputs, name)
    if socket is None:
        raise NodeEditError("SOCKET_NOT_FOUND", "%s has no %s socket %r" % (node.name[:64], "output" if outputs else "input",
                                                                              name))
    return socket


def link(state, output, input_socket):
    if output.node == input_socket.node:
        raise NodeEditError("INVALID_LINK", "a node cannot feed itself")
    if not node_generic.compatible(output, input_socket):
        raise NodeEditError("INCOMPATIBLE_SOCKETS", "%s (%s) cannot feed %s (%s)" % (
            output.name, output.type, input_socket.name, input_socket.type))
    made = state.tree.links.new(output, input_socket)
    if not made.is_valid:
        state.tree.links.remove(made)
        raise NodeEditError("INVALID_LINK", "Blender rejects %s -> %s (a cycle or a field into a value)" % (
            output.name, input_socket.name))
    return made


def relink(state, pairs):
    """Make each (output, input) link that is still possible; report the rest as dropped."""
    for output, input_socket in pairs:
        try:
            link(state, output, input_socket)
        except NodeEditError as error:
            state.dropped.append("%s.%s -> %s.%s: %s" % (output.node.name[:64], output.name, input_socket.node.name[:64],
                                                         input_socket.name, error.code))


def through(node):
    """(source output, target input) pairs a node passes on, as its internal (mute) links route them."""
    pairs = []
    for internal in node.internal_links:
        sources = [item.from_socket for item in internal.from_socket.links if not item.is_muted]
        targets = [item.to_socket for item in internal.to_socket.links]
        pairs.extend((source, target) for source in sources for target in targets)
    return pairs


def _placement(state):
    nodes = [node for node in state.tree.nodes if node.bl_idname != "NodeFrame"]
    if not nodes:
        return (0.0, 0.0)
    return (min(node.location_absolute[0] for node in nodes) - 250.0, max(node.location_absolute[1] for node in nodes))


def _attach(state, nodes, frame_name):
    frame = None
    if frame_name is not None:
        frame = state.node(frame_name)
        if frame.bl_idname != "NodeFrame":
            raise NodeEditError("NOT_A_FRAME", frame_name[:64] + " is not a frame")
    for node in nodes:
        ancestor = frame
        while ancestor is not None:
            if ancestor == node:
                raise NodeEditError("PARENT", "a frame cannot be placed inside itself")
            ancestor = ancestor.parent
        node.parent = frame
        state.changed.append(node.name)


def add(state, edit):
    check_type(state, edit["type"])
    name = state.new_name(edit["node"])
    try:
        node = state.tree.nodes.new(edit["type"])
    except RuntimeError as error:
        raise NodeEditError("NODE_TYPE_UNAVAILABLE", "%s (%s)" % (edit["type"], str(error)[:160]),
                            refusal_code=refusal_code(error), details=refusal_details(error)) from error
    state.named(node, name)
    node.location_absolute = edit["location"] if edit["location"] is not None else _placement(state)
    if edit["location"] is None:
        state.placed.add(name)
    write_settings(state, node, edit["settings"])
    write_sockets(node, edit["inputs"])
    if edit["image"] is not None:
        write_image(node, edit["image"])
    if edit["parent"] is not None:
        if state.family == "GEOMETRY":
            raise NodeEditError("GN_CONTRACT", "frames are outside the closed Geometry Nodes catalog graph",
                                refusal_code="UNSUPPORTED")
        _attach(state, [node], edit["parent"])
    state.added.append(name)


def remove(state, edit):
    node = state.node(edit["node"])
    if node.bl_idname in OUTPUT_TYPES:
        raise NodeEditError("OUTPUT_NODES", "a tree keeps its output nodes")
    pairs = through(node) if edit["reconnect"] else []
    state.tree.nodes.remove(node)
    relink(state, pairs)
    state.removed.append(edit["node"])


def set_values(state, edit):
    node = state.node(edit["node"])
    write_settings(state, node, edit["settings"])
    write_sockets(node, edit["inputs"])
    write_sockets(node, edit["outputs"], outputs=True)
    if edit["image"] is not None:
        write_image(node, edit["image"])
    state.changed.append(node.name)


def make_link(state, edit):
    output = socket_of(state.node(edit["from_node"]), edit["from_socket"], True)
    link(state, output, socket_of(state.node(edit["to_node"]), edit["to_socket"], False))
    state.changed.append(edit["to_node"])


def unlink(state, edit):
    input_socket = socket_of(state.node(edit["to_node"]), edit["to_socket"], False)
    found = [item for item in input_socket.links
             if (edit["from_node"] is None or item.from_node.name == edit["from_node"])
             and (edit["from_socket"] is None or edit["from_socket"] in (item.from_socket.identifier, item.from_socket.name))]
    if not found:
        raise NodeEditError("LINK_NOT_FOUND", "no matching link into %s.%s" % (edit["to_node"][:64], edit["to_socket"]))
    for item in found:
        state.tree.links.remove(item)
    state.changed.append(edit["to_node"])


def detach(state, edit):
    node = state.node(edit["node"])
    pairs = through(node) if edit["reconnect"] else []
    for item in [item for item in state.tree.links if node in (item.from_node, item.to_node)]:
        state.tree.links.remove(item)
    relink(state, pairs)
    state.changed.append(node.name)


def parent(state, edit):
    if state.family == "GEOMETRY":
        raise NodeEditError("GN_CONTRACT", "frames are outside the closed Geometry Nodes catalog graph",
                            refusal_code="UNSUPPORTED")
    _attach(state, [state.node(name) for name in edit["nodes"]], edit["frame"])


def insert(state, edit):
    node = state.node(edit["node"])
    if node.bl_idname in OUTPUT_TYPES or node.bl_idname == "NodeFrame":
        raise NodeEditError("NOT_INSERTABLE", node.name[:64] + " cannot sit on a link")
    output = socket_of(state.node(edit["from_node"]), edit["from_socket"], True)
    input_socket = socket_of(state.node(edit["to_node"]), edit["to_socket"], False)
    existing = next((item for item in input_socket.links if item.from_socket == output), None)
    if existing is None:
        raise NodeEditError("LINK_NOT_FOUND", "no link %s.%s -> %s.%s" % (edit["from_node"][:64], edit["from_socket"],
                                                                           edit["to_node"][:64], edit["to_socket"]))

    def first(sockets, other, as_input):
        usable = [socket for socket in sockets if getattr(socket, "enabled", True) and not socket.hide
                  and (node_generic.compatible(other, socket) if as_input else node_generic.compatible(socket, other))]
        same = [socket for socket in usable if socket.type == other.type]
        if not (same or usable):
            raise NodeEditError("INCOMPATIBLE_SOCKETS", "%s has no %s for %s" % (
                node.name[:64], "input" if as_input else "output", other.type))
        return (same or usable)[0]

    node_in = socket_of(node, edit["input"], False) if edit["input"] is not None else first(node.inputs, output, True)
    node_out = socket_of(node, edit["output"], True) if edit["output"] is not None else first(node.outputs, input_socket, False)
    state.tree.links.remove(existing)
    link(state, output, node_in)
    link(state, node_out, input_socket)
    if node.name in state.placed:  # A node made for this link without a location sits between its ends.
        start, end = output.node.location_absolute, input_socket.node.location_absolute
        node.location_absolute = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    state.changed.append(node.name)


def swap(state, edit):
    """Replace a node with another type, keeping its name, place, frame, matching values and links (Swap Node)."""
    old = state.node(edit["node"])
    if old.bl_idname in OUTPUT_TYPES | {"NodeFrame", "NodeGroupInput", "ShaderNodeGroup", "GeometryNodeGroup"}:
        raise NodeEditError("NOT_SWAPPABLE", "%s nodes are not swapped natively" % old.bl_idname)
    check_type(state, edit["type"])
    try:
        new = state.tree.nodes.new(edit["type"])
    except RuntimeError as error:
        raise NodeEditError("NODE_TYPE_UNAVAILABLE", "%s (%s)" % (edit["type"], str(error)[:160]),
                            refusal_code=refusal_code(error), details=refusal_details(error)) from error
    new.select = False
    for name in SWAP_PROPERTIES:
        if hasattr(old, name) and hasattr(new, name):
            try:
                setattr(new, name, getattr(old, name))
            except (TypeError, ValueError, AttributeError):
                pass  # Blender's Swap Node skips a value the new type cannot take.
    by_index = old.bl_idname in MATH_NODES and new.bl_idname in MATH_NODES
    for index, socket in enumerate(old.inputs):
        target = (new.inputs[index] if index < len(new.inputs) else None) if by_index else new.inputs.get(socket.name)
        if target is not None and target.type == socket.type and hasattr(socket, "default_value") \
                and socket.type in WRITABLE_SOCKETS:
            target.default_value = socket.default_value
    incoming = [(item.from_socket, index, socket.name) for index, socket in enumerate(old.inputs) for item in socket.links]
    outgoing = [(index, socket.name, item.to_socket) for index, socket in enumerate(old.outputs) for item in socket.links]
    name, frame, place = old.name, old.parent, tuple(old.location_absolute)
    state.tree.nodes.remove(old)
    state.named(new, name)
    new.parent, new.location_absolute = frame, place

    def socket(sockets, index, label):
        found = (sockets[index] if index < len(sockets) else None) if by_index else sockets.get(label)
        return found if found is not None and getattr(found, "enabled", True) and not found.hide else None

    pairs = []
    for source, index, label in incoming:
        target = socket(new.inputs, index, label)
        if target is None:
            state.dropped.append("%s.%s -> %s.%s: NO_MATCHING_SOCKET" % (source.node.name[:64], source.name, name[:64], label))
        else:
            pairs.append((source, target))
    for index, label, target in outgoing:
        source = socket(new.outputs, index, label)
        if source is None:
            state.dropped.append("%s.%s -> %s.%s: NO_MATCHING_SOCKET" % (name[:64], label, target.node.name[:64], target.name))
        else:
            pairs.append((source, target))
    relink(state, pairs)
    state.changed.append(name)


def apply(state, edit):
    from . import node_copy
    handlers = {"add": add, "remove": remove, "set": set_values, "link": make_link, "unlink": unlink, "detach": detach,
                "parent": parent, "insert": insert, "swap": swap, "duplicate": node_copy.duplicate,
                "group": node_copy.make_group, "ungroup": node_copy.ungroup, "interface": node_copy.interface}
    handlers[edit["op"]](state, edit)
