# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Subflow expansion: a flow that uses saved recipes, as one flat flow. No Blender dependency.

A recipe is an ordinary flow with an interface (``flow_spec.interface_of``):
some of its Text, Image or Scene Capture nodes are inputs, and one generator
is the output. A SUBFLOW node uses a recipe inside another flow.

Before evaluation, ``expand`` replaces each Subflow node with copies of its
recipe's nodes named ``<subflow>/<step>``. A link into one of the Subflow's
inputs replaces that exposed source node; an unlinked input keeps the recipe's
own source as its default. Links from the Subflow leave from the copy of the
recipe's output step. Input keys don't include node names, so evaluation,
reuse, staleness, planning, approval and runs all work on the expanded flow
unchanged: there is one run path, and two uses with identical inputs share
their outputs for free.

Each use keeps its own chosen outputs (the Subflow node's ``chosen`` map, by
inner step name); the shared recipe only supplies definitions. A Subflow that
can't be expanded stays one SUBFLOW node with a ``problem``, which evaluation
reports as blocked, so one broken use never makes the whole flow unreadable.
"""

from .flow_spec import GENERATOR_TYPES, MAX_NODES, _NAME, FlowError

SEPARATOR = "/"
MAX_EXPANDED_NODES = 4 * MAX_NODES


def inner_name(subflow, step):
    return subflow + SEPARATOR + step


def _problem(node, recipe, outer_names):
    if not node["recipe"]:
        return "choose a recipe"
    if recipe is None:
        return f"its recipe '{node['recipe']}' isn't in this file"
    if not recipe.get("recipe"):
        return f"'{node['recipe']}' has no recipe interface; set its inputs and output"
    names = [inner_name(node["id"], step["id"]) for step in recipe["nodes"]]
    too_long = [name for name in names if not _NAME.fullmatch(name)]
    if too_long:
        return f"'{too_long[0]}' is longer than 63 characters; shorten the Subflow's name"
    taken = [name for name in names if name in outer_names]
    if taken:
        return f"another node is already named '{taken[0]}'; rename it or the Subflow"
    return None


def expand(flow, recipes):
    """The flat flow ``flow`` runs as. ``recipes`` maps recipe names to canonical recipe flows.

    Expanded inner nodes carry ``subflow`` (the Subflow node's name), ``step``
    (the recipe's node name), a combined label, and ``recipe_output`` on the
    step whose output the Subflow passes on. The result is only
    evaluated and planned, never saved.
    """
    if not any(node["type"] == "SUBFLOW" for node in flow["nodes"]):
        return flow
    nodes, links, outputs = [], [], {}
    outer_names = {node["id"] for node in flow["nodes"]}
    for node in flow["nodes"]:
        if node["type"] != "SUBFLOW":
            nodes.append(node)
            continue
        recipe = recipes.get(node["recipe"]) if node["recipe"] else None
        problem = _problem(node, recipe, outer_names)
        if problem:
            nodes.append(dict(node, problem=problem))
            continue
        incoming = {link["input"]: link["from"] for link in flow["links"] if link["to"] == node["id"]}
        exposed, choices = set(recipe["recipe"]["inputs"]), node.get("chosen") or {}
        prefix = (node.get("label") or node["id"]) + " › "
        renamed = {}
        for step in recipe["nodes"]:
            if step["id"] in exposed and step["id"] in incoming:
                renamed[step["id"]] = incoming[step["id"]]  # The outer source replaces the default.
                continue
            copy = {key: value for key, value in step.items() if key not in {"location", "chosen", "label"}}
            copy.update(id=inner_name(node["id"], step["id"]), label=prefix + (step.get("label") or step["id"]),
                        subflow=node["id"], step=step["id"])
            if step["type"] in GENERATOR_TYPES and choices.get(step["id"]):
                copy["chosen"] = choices[step["id"]]
            if step["id"] == recipe["recipe"]["output"]:
                copy["recipe_output"] = True
            renamed[step["id"]] = copy["id"]
            nodes.append(copy)
        links.extend({"from": renamed[link["from"]], "to": renamed[link["to"]], "input": link["input"]}
                     for link in recipe["links"])
        outputs[node["id"]] = renamed[recipe["recipe"]["output"]]
    for link in flow["links"]:
        if link["to"] in outputs:
            continue  # Consumed above: it replaced an exposed source.
        links.append({"from": outputs.get(link["from"], link["from"]), "to": link["to"], "input": link["input"]})
    if len(nodes) > MAX_EXPANDED_NODES:
        raise FlowError(f"With its subflows this flow has {len(nodes)} steps; the limit is {MAX_EXPANDED_NODES}")
    expanded = {"format": flow["format"], "nodes": nodes, "links": links}
    if flow.get("recipe"):
        expanded["recipe"] = flow["recipe"]
    return expanded


def members(flow, name):
    """Expanded step names that belong to the Subflow node ``name``."""
    return {node["id"] for node in flow["nodes"] if node.get("subflow") == name}


def used_recipes(flow):
    return sorted({node["recipe"] for node in flow["nodes"] if node["type"] == "SUBFLOW" and node["recipe"]})
