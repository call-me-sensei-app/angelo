# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed final-state absence predicates for qualified native unlink steps.

These are ordinary conjunctions with a node_graph check, not execution traces.
The immutable host descriptor uses precisely the kernel's endpoint selectors.
"""

from .program_jobs import ProgramError


def _socket(node, selector, budget):
    for socket in node.inputs:
        _charge(budget)
        if socket.identifier == selector:
            return socket
    for socket in node.inputs:
        _charge(budget)
        if socket.name == selector and socket.enabled:
            return socket
    return None


def _charge(budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise ProgramError("Node-link verification exceeds the bounded fingerprint budget", code="LIMIT_EXCEEDED")


def _tree(owner, check, budget, name_of=None):
    if check.get("world"):
        tree = owner.world.node_tree if owner.world is not None else None
    elif "material" in check:
        tree = None
        for slot in owner.material_slots:
            _charge(budget)
            named = name_of(slot.material) if name_of is not None and slot.material is not None else None
            if slot.material is not None and (named or slot.material.name) == check["material"]:
                tree = slot.material.node_tree
                break
    else:
        modifier = owner.modifiers.get(check["modifier"])
        tree = modifier.node_group if modifier is not None and modifier.type == "NODES" else None
    if "group" in check and tree is not None:
        node = tree.nodes.get(check["group"])
        tree = node.node_tree if node is not None and node.bl_idname == "ShaderNodeGroup" else None
    return tree


def verify_absent_links(owner, check, descriptor, name_of=None):
    from .program_dependencies import fingerprint_budget
    from .node_edit_spec import MAX_TREE_NODES, MAX_TREE_LINKS
    budget = fingerprint_budget()
    tree = _tree(owner, check, budget, name_of) if owner is not None else None
    if tree is None:
        return {"kind": "node_link_absence", "passed": False, "status": "fail", "reason": "tree_not_found"}
    if len(tree.nodes) > MAX_TREE_NODES or len(tree.links) > MAX_TREE_LINKS:
        return {"kind": "node_link_absence", "passed": False, "status": "inconclusive",
                "reason": "The final node graph exceeds the existing bounded validator"}
    incoming = {}
    for link in tree.links:
        _charge(budget)
        incoming.setdefault(link.to_socket.as_pointer(), []).append(link)
    rows = []
    for expected in descriptor["absent"]:
        node = tree.nodes.get(expected["to_node"])
        socket = _socket(node, expected["to_socket"], budget) if node is not None else None
        matches = []
        for link in incoming.get(socket.as_pointer(), ()) if socket is not None else ():
            _charge(budget)
            if (expected.get("from_node") is None or link.from_node.name == expected["from_node"]) and (
                    expected.get("from_socket") is None or expected["from_socket"] in
                    (link.from_socket.identifier, link.from_socket.name)):
                matches.append(link)
        passed = not matches
        rows.append({"kind": "node_link_absence", "passed": passed, "status": "pass" if passed else "fail",
                     "observed": {"matching_links": len(matches), "endpoint_absent": socket is None}})
    passed = all(row["passed"] for row in rows)
    return {"kind": "node_link_absence", "passed": passed, "status": "pass" if passed else "fail", "checks": rows,
            **({} if passed else {"reason": "An explicitly unlinked edge is still present"})}
