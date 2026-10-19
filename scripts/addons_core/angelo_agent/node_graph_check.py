# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What node_edit can change, in the object revision; and the closed ``node_graph`` check predicate.

Revision. The materials and modifiers fields already hash node names, types,
labels, mutes, socket defaults and links. ``trees_revision`` adds, for every
node of the object's material trees and Geometry Nodes groups (nested groups
too, four levels): its frame, and every natively writable setting of the
node's installed RNA (colour, collapse, width, options and type-specific
settings) except selection and position, which node_edit never writes on an
existing node; plus each group's interface sockets. A manual edit of any of
them after observation therefore makes a plan stale instead of being
overwritten. A frame that shows a Text datablock is a revision limitation.

Check. ``evaluate`` reads one tree of a live or staged object (by material
name or modifier name) or of a scene's World, optionally inside a group node,
and proves expected nodes (by exact name and/or bl_idname, with frame, input and
output defaults and RNA settings), absent nodes, links between nodes (by name or
type and socket identifier or name) and unlinked inputs. It never evaluates
anything; an unknown tree fails with its reason.
"""

from .scene_read_values import hash_value, label

_SKIP = frozenset({"select", "location_absolute"})  # Editor state; writable_settings leaves out a node's place.
MAX_DEPTH = 4
_PATHS = {}
_SPLIT = {}  # node type -> [(path, segments)], for the revision's per-node reads.


def writable_paths(node):
    """A node type's natively writable settings paths (nested structs included), selection and place excluded."""
    key = getattr(node, "bl_idname", type(node).__name__)
    if key not in _PATHS:
        from .rna_rules import writable_settings
        _PATHS[key] = [entry["path"] for entry in writable_settings(node.bl_rna, limit=600) if entry["path"] not in _SKIP]
    return _PATHS[key]


def holder(node, path):
    """(struct holding the leaf, leaf name) of a settings path, or (None, name) when a struct is missing."""
    *parents, name = path.split(".")
    value = node
    for segment in parents:
        value = getattr(value, segment, None)
        if value is None:
            return None, name
    return value, name


def _plain(value):
    from .rna_rules import plain
    if hasattr(value, "users"):
        return [value.bl_rna.identifier, label(value.name)]
    return plain(value)


def node_values(node):
    """Every natively writable setting of a node (nested settings structs included), selection and place excluded."""
    values = {}
    if getattr(node, "bl_rna", None) is None:
        return values  # A plain stand-in has no RNA definition.
    key = node.bl_idname
    if key not in _SPLIT:
        _SPLIT[key] = [(path, path.split(".")) for path in writable_paths(node)]
    for path, segments in _SPLIT[key]:
        value = node
        for segment in segments:
            value = getattr(value, segment, None)
            if value is None:
                break
        values[path] = _plain(value)
    return values


def _tree_state(tree, work, unsupported, depth, seen):
    pointer = tree.as_pointer() if hasattr(tree, "as_pointer") else id(tree)
    if pointer in seen or depth > MAX_DEPTH:
        unsupported.append("node_group_cycle_or_depth")
        return None
    seen = seen | {pointer}
    work.take(items=1 + len(tree.nodes))
    nodes = []
    for node in tree.nodes:
        parent = getattr(node, "parent", None)  # Observation stand-ins lack most RNA attributes.
        entry = [label(node.name), node.bl_idname, label(parent.name) if parent else None, node_values(node)]
        if node.bl_idname == "NodeFrame" and getattr(node, "text", None) is not None:
            unsupported.append("node_frame_text")
        group = getattr(node, "node_tree", None)
        if group is not None:
            entry.append(_tree_state(group, work, unsupported, depth + 1, seen))
        nodes.append(entry)
    interface = []
    for item in getattr(getattr(tree, "interface", None), "items_tree", ()):
        work.take(items=1)
        interface.append([item.item_type, label(item.name), getattr(item, "identifier", None), getattr(item, "in_out", None),
                          getattr(item, "socket_type", None),
                          *[_plain(getattr(item, name, None)) for name in ("default_value", "min_value", "max_value",
                                                                           "hide_value", "subtype")]])
    return [nodes, interface]


def trees_revision(obj, work, unsupported):
    """One revision field for the object's material trees and Geometry Nodes groups, or None when it has none."""
    trees = [slot.material.node_tree for slot in getattr(obj, "material_slots", ())
             if slot.material is not None and slot.material.node_tree is not None]
    trees += [modifier.node_group for modifier in getattr(obj, "modifiers", ())
              if getattr(modifier, "node_group", None) is not None]
    if not trees:
        return None
    from .scene_shared_reads import shared_read  # A material many objects share is read once per pass.
    return hash_value([shared_read(work, ("tree_state", tree.as_pointer()) if hasattr(tree, "as_pointer") else None,
                                   lambda budget, limits, _resource, tree=tree: _tree_state(tree, budget, limits, 0, frozenset()),
                                   unsupported) for tree in trees])


def resolve_tree(owner, check, name_of=None):
    """(tree, None) for the check's tree of a live or staged object or scene, else (None, reason)."""
    if check.get("world"):
        world = getattr(owner, "world", None)  # Only a scene has a World; an object target finds none.
        tree = getattr(world, "node_tree", None)
    elif "material" in check:
        material = next((slot.material for slot in getattr(owner, "material_slots", ())
                         if slot.material is not None and (name_of or _own)(slot.material) == check["material"]), None)
        tree = getattr(material, "node_tree", None)
    else:
        modifier = getattr(owner, "modifiers", {}).get(check["modifier"]) if hasattr(owner, "modifiers") else None
        tree = getattr(modifier, "node_group", None)
    if tree is None:
        return None, "tree_not_found"
    if "group" in check:
        node = tree.nodes.get(check["group"])
        tree = getattr(node, "node_tree", None) if node is not None else None
        if tree is None:
            return None, "group_not_found"
    return tree, None


def _socket(sockets, name):
    from .node_generic import find_socket
    return find_socket(sockets, name)


def _close(actual, expected, tolerance):
    from .rna_rules import plain, same
    actual = plain(actual)
    if tolerance is not None and isinstance(expected, (int, float, list)) and not isinstance(expected, bool):
        items = expected if isinstance(expected, list) else [expected]
        got = actual if isinstance(actual, list) else [actual]
        return len(items) == len(got) and all(isinstance(a, (int, float)) and abs(a - b) <= tolerance
                                              for a, b in zip(got, items))
    return same(expected, actual)


def _node_matches(node, expected, tolerance):
    from .generic_settings import evaluate
    if "name" in expected and node.name != expected["name"]:
        return False
    if "type" in expected and node.bl_idname != expected["type"]:
        return False
    if "parent" in expected and (node.parent.name if node.parent else None) != expected["parent"]:
        return False
    for sockets, values in ((node.inputs, expected.get("inputs", {})), (node.outputs, expected.get("outputs", {}))):
        for name, value in values.items():
            socket = _socket(sockets, name)
            if socket is None or not hasattr(socket, "default_value") or not _close(socket.default_value, value, tolerance):
                return False
    if expected.get("settings"):
        passed, _ = evaluate(node, expected["settings"], tolerance, lambda reference: None)
        if not passed:
            return False
    return True


def _end(node, socket, name, kind, socket_name):
    if name is not None and node.name != name or kind is not None and node.bl_idname != kind:
        return False
    return socket_name in (socket.identifier, socket.name)


def _own(block):
    return block.name


def evaluate(owner, check, name_of=None):
    """(passed, observed) for a node_graph check; ``name_of`` reads a material's or group's name (validation_reader)."""
    tree, reason = resolve_tree(owner, check, name_of)
    if tree is None:
        return False, {"tree": None, "reason": reason}
    tolerance, failed = check.get("tolerance"), []
    nodes = list(tree.nodes)
    for index, expected in enumerate(check.get("nodes", [])):
        if not any(_node_matches(node, expected, tolerance) for node in nodes):
            failed.append("nodes[%d]" % index)
    for index, expected in enumerate(check.get("absent", [])):
        if any(_node_matches(node, expected, None) for node in nodes):
            failed.append("absent[%d]" % index)
    for index, expected in enumerate(check.get("links", [])):
        if not any(link.is_valid and not link.is_muted
                   and _end(link.from_node, link.from_socket, expected.get("from_node"), expected.get("from_type"),
                            expected["from_socket"])
                   and _end(link.to_node, link.to_socket, expected.get("to_node"), expected.get("to_type"),
                            expected["to_socket"]) for link in tree.links):
            failed.append("links[%d]" % index)
    for index, expected in enumerate(check.get("unlinked", [])):
        reference = {key: expected[field] for key, field in (("name", "node"), ("type", "type")) if field in expected}
        sockets = [_socket(node.inputs, expected["socket"]) for node in nodes if _node_matches(node, reference, None)]
        if not any(socket is not None and not socket.is_linked for socket in sockets):
            failed.append("unlinked[%d]" % index)
    observed = {"tree": label((name_of or _own)(tree)), "nodes": len(nodes), "links": len(tree.links)}
    if failed:
        observed["mismatched"] = failed
    return not failed, observed
