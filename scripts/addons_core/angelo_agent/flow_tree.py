# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flow node trees to and from ``angelo.flow/1`` definitions.

``tree_flow`` reads a tree as a canonical flow and ``write_flow`` applies an
agent edit back to it, so the Node Editor and the agent tools share one
definition. Invalid links (a wrong socket kind, more links than an input
accepts, a cycle) stay visible in red and are left out of the flow rather than
rejecting the whole tree; an incomplete recipe interface reads as a plain flow.

A Subflow node's sockets follow its recipe's interface (``sync_sockets``). A
recipe can't contain a Subflow node yet, which also rules out recipe cycles.
"""

import json
import uuid

from . import flow_nodes
from .flow_nodes import IDNAME_BY_TYPE, SOCKET_IDS, SOCKET_KINDS, TREE, TYPE_BY_IDNAME
from .flow_spec import GENERATOR_TYPES, INPUTS, SOURCE_TYPES, FlowError, interface_of, normalize

SUBFLOW_NODE = flow_nodes.ANGELO_FlowSubflowNode.bl_idname


def _real_source(link):
    """Follow reroutes back to the flow node and socket a link comes from."""
    seen = 0
    while link.from_node.bl_idname == "NodeReroute" and seen < 64:
        upstream = link.from_node.inputs[0].links
        if not upstream:
            return None
        link, seen = upstream[0], seen + 1
    return link if link.from_node.bl_idname in TYPE_BY_IDNAME else None


def _input_links(node):
    """{input identifier: [(source link, tree link)]} in multi-input order."""
    result = {}
    for socket in node.inputs:
        for link in sorted(socket.links, key=lambda l: getattr(l, "multi_input_sort_id", 0)):
            source = _real_source(link)
            result.setdefault(socket.identifier, []).append((source, link))
    return result


def recipe_interface(tree):
    """The interface Subflow nodes using ``tree`` get, or None when it isn't a usable recipe."""
    if tree is None or tree.bl_idname != TREE or not tree.angelo_recipe_output:
        return None
    if any(node.bl_idname == SUBFLOW_NODE for node in tree.nodes):
        return None
    return interface_of(tree_flow(tree))


def recipe_problem(tree):
    """Why a tree marked as a recipe can't be used as one, or None."""
    if not tree.angelo_recipe_output or recipe_interface(tree):
        return None
    if any(node.bl_idname == SUBFLOW_NODE for node in tree.nodes):
        return "A recipe can't contain a Subflow node yet"
    try:
        normalize(dict(_definition(tree), recipe=_recipe_fields(tree)))
    except FlowError as error:
        return str(error)
    return None


def _inputs(node, kind):
    if kind != "SUBFLOW":
        return INPUTS.get(kind, {})
    interface = recipe_interface(node.recipe)
    return {name: (socket, 1) for name, socket in interface["inputs"].items()} if interface else {}


def _interfaces(tree):
    found = {}
    for node in tree.nodes:
        if node.bl_idname == SUBFLOW_NODE and node.recipe and node.recipe.name not in found:
            interface = recipe_interface(node.recipe)
            if interface:
                found[node.recipe.name] = interface
    return found


def mark_invalid_links(tree):
    """Show wrong-kind and over-limit links in red; the flow ignores them."""
    for node in tree.nodes:
        kind = TYPE_BY_IDNAME.get(node.bl_idname)
        if kind is None:
            continue
        inputs = _inputs(node, kind)
        for name, links in _input_links(node).items():
            expected, limit = inputs.get(name, (None, 0))
            for index, (source, link) in enumerate(links):
                valid = bool(source) and SOCKET_KINDS.get(source.from_socket.bl_idname) == expected and index < limit
                if link.is_valid != valid:
                    link.is_valid = valid


def _recipe_fields(tree):
    return {"inputs": [node.name for node in tree.nodes
                       if TYPE_BY_IDNAME.get(node.bl_idname) in SOURCE_TYPES and node.exposed],
            "output": tree.angelo_recipe_output}


def _definition(tree):
    """Nodes and valid links, before cycle filtering."""
    nodes, links = [], []
    for node in tree.nodes:
        kind = TYPE_BY_IDNAME.get(node.bl_idname)
        if kind is None:
            continue
        entry = {"id": node.name, "type": kind, "fields": node.fields(),
                 "location": [round(node.location.x, 1), round(node.location.y, 1)]}
        if node.label:
            entry["label"] = node.label[:64]
        if kind in GENERATOR_TYPES and node.chosen:
            entry["chosen"] = node.chosen
        if kind == "SUBFLOW" and node.inner("chosen"):
            entry["chosen"] = node.inner("chosen")
        nodes.append(entry)
        inputs = _inputs(node, kind)
        for name, input_links in _input_links(node).items():
            expected, limit = inputs.get(name, (None, 0))
            for index, (source, link) in enumerate(input_links):
                if source and SOCKET_KINDS.get(source.from_socket.bl_idname) == expected and index < limit:
                    links.append({"from": source.from_node.name, "to": node.name, "input": name})
    return {"nodes": nodes, "links": links}


def tree_flow(tree):
    """The canonical flow for a node tree, skipping invalid links (and any that would close a cycle)."""
    interfaces = _interfaces(tree)
    definition = _definition(tree)
    accepted = []
    for link in definition["links"]:
        try:
            normalize({"nodes": definition["nodes"], "links": accepted + [link]}, interfaces)
            accepted.append(link)
        except FlowError:
            continue  # A cycle; the editor shows it, the flow leaves it out.
    definition["links"] = accepted
    if tree.angelo_recipe_output:
        try:
            return normalize(dict(definition, recipe=_recipe_fields(tree)), interfaces)
        except FlowError:
            pass  # An incomplete recipe reads as a plain flow; recipe_problem says why.
    return normalize(definition, interfaces)


def flow_id(tree):
    if not tree.angelo_flow_id:
        tree.angelo_flow_id = str(uuid.uuid4())
    return tree.angelo_flow_id


def sync_sockets(node):
    """Make a Subflow node's sockets match its recipe's interface. Links to removed sockets go with them."""
    interface = recipe_interface(node.recipe) if node.recipe else None
    wanted = interface["inputs"] if interface else {}
    for socket in list(node.inputs):
        kind = wanted.get(socket.identifier)
        if kind is None or socket.bl_idname != SOCKET_IDS[kind]:
            node.inputs.remove(socket)
    present = {socket.identifier for socket in node.inputs}
    for name, kind in wanted.items():
        if name not in present:
            node.inputs.new(SOCKET_IDS[kind], name, identifier=name)
    output = interface["output"] if interface else None
    for socket in list(node.outputs):
        if output is None or socket.bl_idname != SOCKET_IDS[output]:
            node.outputs.remove(socket)
    if output and not len(node.outputs):
        node.outputs.new(SOCKET_IDS[output], output.title(), identifier="output")


def sync_subflows(tree):
    for node in tree.nodes:
        if node.bl_idname == SUBFLOW_NODE:
            sync_sockets(node)


def _recipe_tree(name):
    import bpy
    tree = bpy.data.node_groups.get(name)
    if tree is None or tree.bl_idname != TREE or not tree.angelo_recipe_output:
        raise FlowError(f"There's no recipe flow named '{name}' in this file; give a flow a recipe interface first")
    return tree


def _set_fields(node, entry):
    kind = entry["type"]
    if kind == "IMAGE":
        source = entry["source"] or {}
        node.asset_id = source.get("id", "") if source.get("kind") == "ASSET" else ""
        node.url = source.get("id", "") if source.get("kind") == "URL" else ""
    elif kind == "SCENE_CAPTURE":
        node.framing, node.shading, node.subject = entry["framing"], entry["shading"], entry["subject"]
        node.resolution = str(entry["resolution"])
    elif kind == "TEXT":
        node.text = entry["text"]
    elif kind in GENERATOR_TYPES:
        node.connection_id, node.model, node.prompt = entry["connection_id"], entry["model"], entry["prompt"]
        node.options_json = json.dumps(entry["options"], sort_keys=True)
        node.variants, node.review = entry["variants"], entry["review"]
        node.chosen = entry.get("chosen", "")
    elif kind == "SUBFLOW":
        recipe = _recipe_tree(entry["recipe"]) if entry["recipe"] else None
        if recipe is not None and recipe == node.id_data:
            raise FlowError("A flow can't use itself as a Subflow")
        if node.recipe != recipe:
            node.recipe = recipe
        sync_sockets(node)
        node.chosen_json = json.dumps(entry["chosen"], sort_keys=True) if entry.get("chosen") else ""


# Approximate drawn heights (Node Editor units) for stacking auto-placed nodes;
# generators include their output thumbnails and run buttons.
_HEIGHTS = {"IMAGE": 260, "SCENE_CAPTURE": 300, "TEXT": 110, "GENERATE_IMAGE": 400, "GENERATE_3D": 400,
            "PLACE_IN_SCENE": 170, "SUBFLOW": 300}


def _auto_location(flow, identity, placed):
    """Columns by dependency depth; within a column, stacked below the nodes already placed."""
    depth = {}
    for node in flow["nodes"]:
        upstream = [l["from"] for l in flow["links"] if l["to"] == node["id"]]
        depth[node["id"]] = 1 + max((depth.get(u, 0) for u in upstream), default=-1)
    column = depth.get(identity, 0)
    kinds = {node["id"]: node["type"] for node in flow["nodes"]}
    above = sum(_HEIGHTS[kinds[other]] + 40 for other in placed if depth.get(other) == column)
    return (column * 320.0, -float(above))


def write_flow(tree, flow):
    """Make ``tree`` match a canonical flow. Inputs whose links already match are left alone,
    so reroutes and frames the user arranged survive agent edits."""
    wanted = {node["id"]: node for node in flow["nodes"]}
    for node in list(tree.nodes):
        if node.bl_idname in TYPE_BY_IDNAME and node.name not in wanted:
            tree.nodes.remove(node)
    placed = set()
    exposed = set((flow.get("recipe") or {}).get("inputs", ()))
    for entry in flow["nodes"]:
        node = tree.nodes.get(entry["id"])
        if node is not None and TYPE_BY_IDNAME.get(node.bl_idname) != entry["type"]:
            tree.nodes.remove(node)
            node = None
        if node is None:
            node = tree.nodes.new(IDNAME_BY_TYPE[entry["type"]])
            node.select = False  # An agent edit must not replace the user's selection.
            node.name = entry["id"]
            if node.name != entry["id"]:
                raise FlowError(f"Blender couldn't name a node '{entry['id']}'; choose another name")
            node.location = entry.get("location") or _auto_location(flow, entry["id"], placed)
        elif entry.get("location"):
            node.location = entry["location"]
        placed.add(entry["id"])
        node.label = entry.get("label", "")
        _set_fields(node, entry)
        if entry["type"] in SOURCE_TYPES and node.exposed != (entry["id"] in exposed):
            node.exposed = entry["id"] in exposed
    output = (flow.get("recipe") or {}).get("output", "")
    if tree.angelo_recipe_output != output:
        tree.angelo_recipe_output = output
    for entry in flow["nodes"]:
        node = tree.nodes[entry["id"]]
        current = _input_links(node)
        for name in _inputs(node, entry["type"]):
            desired = [l["from"] for l in flow["links"] if l["to"] == entry["id"] and l["input"] == name]
            existing = [source.from_node.name if source else None for source, _ in current.get(name, [])]
            if desired == existing:
                continue
            socket = next(s for s in node.inputs if s.identifier == name)
            for _, link in current.get(name, []):
                tree.links.remove(link)
            for upstream in desired:
                tree.links.new(tree.nodes[upstream].outputs["output"], socket)
    flow_id(tree)
    mark_invalid_links(tree)
