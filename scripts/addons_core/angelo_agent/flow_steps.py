# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a Generation Flow step's settings and per-use state live, and recipe lookups.

After Subflow expansion a step is either a node of the tree or ``<subflow>/<step>``:
a recipe step whose settings are the recipe tree's node and whose per-use state
(chosen output, last input key, scene capture) is kept on the Subflow node, so
two uses of one recipe never share a choice. ``step_slot`` finds it, and ``slot_get``/``slot_set`` hide
that difference from the host. Main thread only.
"""

import bpy

from . import flow_nodes, flow_tree
from .flow_subflows import inner_name


def flow_trees():
    return [tree for tree in bpy.data.node_groups if tree.bl_idname == flow_nodes.TREE]


def per_step(tree, field, types):
    """{step name: value} of a per-step field, including each Subflow use's inner steps."""
    values = {}
    for node in tree.nodes:
        if node.bl_idname == flow_tree.SUBFLOW_NODE:
            values.update({inner_name(node.name, step): value for step, value in node.inner(field).items()})
        elif flow_nodes.TYPE_BY_IDNAME.get(node.bl_idname) in types and getattr(node, field):
            values[node.name] = getattr(node, field)
    return values


def recipe_trees():
    return [tree for tree in flow_trees() if tree.angelo_recipe_output]


def recipe_interfaces():
    """{recipe name: interface} of saved recipes and this file's recipes (the file's copy wins)."""
    found = saved_interfaces()
    for tree in recipe_trees():
        interface = flow_tree.recipe_interface(tree)
        if interface:
            found[tree.name] = interface
    return found


_PRIORITY = ("FAILED", "BLOCKED", "STOPPED", "RUNNING", "STALE", "PARTIAL", "MISSING", "WAITING", "READY")


def subflow_summaries(flow, states):
    """Per Subflow use: its most pressing step state, and its output step's state."""
    summaries = {}
    for node in flow["nodes"]:
        if node.get("subflow") and node["id"] in states:
            summary = summaries.setdefault(node["subflow"], {"steps": []})
            summary["steps"].append(node["id"])
            if node.get("recipe_output"):
                summary.update(output_step=node["id"], output=states[node["id"]])
    for summary in summaries.values():
        step = min(summary["steps"], key=lambda s: _PRIORITY.index(states[s]["state"]))
        state = states[step]
        reason = state.get("reason")
        summary.update(state=state["state"], reason=f"{step}: {reason}" if reason else None, step=step,
                       setup=state.get("setup"))   # flow_status: that step only lacks setup, or a real block.
    return summaries


def step_slot(tree, flow, name):
    """(settings node, state holder, inner step or None) of a step of the expanded flow, or None."""
    entry = next((node for node in flow["nodes"] if node["id"] == name), None)
    if entry is None:
        return None
    if not entry.get("subflow"):
        node = tree.nodes.get(name)
        return (node, node, None) if node is not None else None
    holder = tree.nodes.get(entry["subflow"])
    if holder is None or holder.bl_idname != flow_tree.SUBFLOW_NODE or holder.recipe is None:
        return None
    settings = holder.recipe.nodes.get(entry["step"])
    return (settings, holder, entry["step"]) if settings is not None else None


def slot_get(slot, field):
    _, holder, step = slot
    return holder.inner(field).get(step, "") if step else getattr(holder, field)


def slot_set(slot, field, value):
    _, holder, step = slot
    if step:
        holder.set_inner(field, step, value)
    elif getattr(holder, field) != value:
        setattr(holder, field, value)


def import_recipe(name):
    """Copy a saved recipe into this file as a recipe flow named ``name``; returns the tree."""
    from . import flow_recipe_library
    from .flow_spec import FlowError
    flow = flow_recipe_library.load_all().get(name)
    if flow is None:
        raise FlowError(f"There's no recipe named '{name}' in this file or the recipe library")
    if bpy.data.node_groups.get(name) is not None:
        raise FlowError(f"'{name}' in this file isn't a recipe; rename it to use the saved recipe")
    tree = bpy.data.node_groups.new(name, flow_nodes.TREE)
    if tree.name != name:
        bpy.data.node_groups.remove(tree)
        raise FlowError(f"Blender couldn't create a flow named '{name}'")
    flow_tree.write_flow(tree, flow)
    return tree


def missing_recipes(edit):
    """Recipe names an edit's Subflow nodes use that aren't recipe flows in this file yet."""
    names = {node.get("fields", {}).get("recipe") for node in edit.get("nodes", []) if isinstance(node, dict)
             and isinstance(node.get("fields"), dict)}
    present = {tree.name for tree in recipe_trees()}
    return sorted(name for name in names if isinstance(name, str) and name and name not in present)


def saved_interfaces():
    """{name: interface} of the recipe library; an unreadable library reads as empty."""
    from . import flow_recipe_library
    try:
        return flow_recipe_library.interfaces()
    except (OSError, ValueError):
        return {}
