# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A node_edit of a Geometry Nodes tree stays inside the closed catalog contract, bounded before evaluation.

A Geometry Nodes modifier is bindable and stageable only while its group is a
closed catalog graph: the provenance it carries (``legacy_names.NATIVE_GRAPH``,
the ``geometry_nodes`` parameters) matches the real nodes and links exactly
(``action_node_graphs._audit_geometry_tree``) and its evaluated size has a
static bound (``node_generic.tree_bound``). After an edit, ``refresh``
describes the edited graph again as catalog parameters (every node as a generic
definition with its unlinked literal inputs, every link), validates them with
the catalog's own rules (one connected Group Output, acyclic, allowed types,
at most 16 nodes and 32 links, identifier names), audits the tree against them
and stores them; then the modifier stack is bounded again, before any
evaluation, so raising a Subdivide level or a Distribute density over the
budget is refused here. Frames, mutes, groups and interface sockets are outside
the contract and refused by the steps.
"""

import copy
import json

from .refusal_codes import CODES, refusal_code, refusal_details
from .node_edit_spec import NodeEditError

# Group Input and Output sockets go by name ("Geometry"), as catalog graphs name them; others by identifier.
_BY_NAME = frozenset({"NodeGroupInput", "NodeGroupOutput"})


def _key(socket):
    return socket.name if socket.node.bl_idname in _BY_NAME else socket.identifier


def _literal(value):
    """A JSON value for a socket default the catalog can state, else None (left to the revision)."""
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value if value and all(32 <= ord(char) != 127 for char in value) and len(value) <= 1024 else None
    if hasattr(value, "users") or value is None:
        return None
    try:
        items = list(value)
    except TypeError:
        return None
    return [float(item) for item in items] if 1 <= len(items) <= 32 and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in items) else None


def provenance(tree, name):
    """Catalog ``geometry_nodes`` parameters describing the real graph (no evaluation)."""
    nodes = []
    for node in tree.nodes:
        inputs = {}
        for socket in node.inputs:
            if socket.is_linked or socket.type in {"GEOMETRY", "CUSTOM"} or getattr(socket, "hide_value", False):
                continue
            value = _literal(getattr(socket, "default_value", None))
            if value is not None:
                inputs[socket.identifier] = value
        nodes.append({"id": node.name, "type": node.bl_idname, "settings": {}, "inputs": inputs})
    links = [{"from_node": link.from_node.name, "from_socket": _key(link.from_socket), "to_node": link.to_node.name,
              "to_socket": _key(link.to_socket)} for link in tree.links]
    return {"name": name, "nodes": nodes, "links": links}


def refresh(context, obj, tree):
    """Store the edited graph's catalog parameters and return the stack's evaluated bound (or refuse)."""
    from .action_catalog import validate_operation
    from .action_node_graphs import _audit_geometry_tree
    from .action_budget import _modifier_bound
    from .legacy_names import NATIVE_GRAPH
    from .stage_names import public
    previous = context.node_graphs.get(tree) or {}
    name = previous["name"] if "name" in previous else public(getattr(context, "extras", {}), tree.name)[:64]
    raw = provenance(tree, name)  # A staged copy's private name never names the graph.
    try:
        parameters = validate_operation({"kind": "geometry_nodes", "target": "metadata", "parameters": raw})["parameters"]
        _audit_geometry_tree(tree, parameters)
    except ValueError as error:
        raise NodeEditError("GN_CONTRACT", "the edited graph leaves the closed Geometry Nodes catalog contract: "
                            + str(error)[:240],
                            refusal_code=refusal_code(error), details=refusal_details(error)) from error
    tree[NATIVE_GRAPH[0]] = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
    context.node_graphs[tree] = copy.deepcopy(parameters)
    try:
        bound = _modifier_bound(context, obj)  # Before any evaluation; refuses an over-budget or unbounded graph.
    except ValueError as error:
        # The stack's refusals carry their closed policy code (LIMIT_EXCEEDED, UNSUPPORTED...) as ``code``; a policy
        # code is never a node_edit domain code, so they stay BUDGET and keep their policy as the refusal code.
        code = getattr(error, "code", None)
        raise NodeEditError(code if isinstance(code, str) and code not in CODES else "BUDGET", str(error)[:300],
                            refusal_code=refusal_code(error), details=refusal_details(error)) from error
    return bound
