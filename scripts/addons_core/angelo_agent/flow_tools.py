# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``angelo_flow_*`` agent tools, on the existing controller and executor path.

Edits (build, choose) are ordinary approved document changes with Undo. A run
is paid usage: its precondition plans the run and becomes the itemized
approval card, the user's unticks and review stops ride on the call
(``flow_selection``), and the result waits like a Studio generation
(``FlowPending``) until the run reaches a review stop or ends. Results carry
asset IDs and states, never file paths, and stay short because every byte is
model input.
"""

import bpy

from . import flow_host, flow_nodes, flow_prices, flow_steps, flow_tree
from .flow_graph import check_selection, plan, select
from .flow_spec import FlowError, apply_edit, revision, validate_flow_tool
from .studio_agent import StudioPending

RUN_STEP_SECONDS = 1300  # A 3D task's provider timeout plus margin, per dependent step.
MAX_RUN_SECONDS = 4 * 3600


class FlowReview:
    """A run's precondition: the plan the card shows. Equal when the plan is unchanged."""

    def __init__(self, tree_name, flow, run_plan):
        self.tree_name, self.flow, self.plan = tree_name, flow, run_plan
        self.digest = run_plan["digest"]

    def __eq__(self, other):
        return isinstance(other, FlowReview) and other.digest == self.digest

    def __hash__(self):
        return hash(self.digest)

    def selection(self, choices=None):
        choices = choices or {}
        return select(self.plan, self.flow, excluded=choices.get("excluded", ()), stops=choices.get("stops"))


class FlowPending(StudioPending):
    """Deferred run result: the tool returns at a review stop or the end of the run."""

    def __init__(self, run, timeout):
        super().__init__(lambda: run.result() if run.finished else None, lambda: flow_host.stop_run(run.id))
        self.run_id, self.timeout = run.id, timeout


def _tree(name, required=True):
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.bl_idname != flow_nodes.TREE:
        raise FlowError(f"'{name}' is a different kind of node group; choose another flow name")
    if tree is None and required:
        raise FlowError(f"There's no Generation Flow named '{name}'; list flows with angelo_flow_inspect")
    return tree


def _check_revision(tree, expected):
    current = revision(flow_tree.tree_flow(tree)) if tree else None
    if expected != current:
        raise FlowError(f"The flow changed (current revision {current}); inspect it again before editing"
                        if tree else "This flow doesn't exist yet; create it with expected_revision null")
    return current


def _compact_states(flow, states):
    """One short entry per node: type, state, and outputs for generators. Recipe steps name their Subflow."""
    result = []
    for node in flow["nodes"]:
        state = states.get(node["id"], {})
        entry = {"id": node["id"], "type": node["type"], "state": state.get("state")}
        if node.get("subflow"):
            entry["in"] = node["subflow"]
        if state.get("reason"):
            entry["reason"] = state["reason"]
        if state.get("outputs"):
            entry["outputs"] = state["outputs"]
            entry["chosen"] = state.get("chosen")
        result.append(entry)
    return result


def _estimate(flow, states):
    run_plan = plan(flow, states, price=flow_prices.price)
    selection = select(run_plan, flow, stops={})
    later = select(run_plan, flow, stops={line["node"]: False for line in run_plan["lines"]})
    return {"paid_generations": later["count"], "estimate": flow_prices.describe(later["total"], later["unknown_prices"]),
            "first_card": selection["count"]}


def precondition(window, name, arguments):
    args = validate_flow_tool(name, arguments)
    if name == "angelo_flow_inspect":
        return None
    tree = _tree(args["flow"], required=name != "angelo_flow_build")
    current = _check_revision(tree, args.get("expected_revision"))
    if name == "angelo_flow_build":
        # Refuse an invalid edit before approval; saved recipes count, as execute copies them in.
        apply_edit(flow_tree.tree_flow(tree) if tree else {}, args, flow_steps.recipe_interfaces())
        return current
    if name == "angelo_flow_choose":
        state = flow_host.refresh(tree)["states"].get(args["node"]) or {}
        if args["asset_id"] not in state.get("outputs", []):
            raise FlowError(f"That asset isn't a current output of '{args['node']}'; inspect the flow")
        return current, args["asset_id"]
    from .flow_ui import build_plan
    flow, run_plan = build_plan(tree, window, args["scope"], args.get("node"))
    review = FlowReview(tree.name, flow, run_plan)
    check_selection(review.selection())
    return review


def usage_label(review):
    accounts = sorted({line["account"] for line in review.plan["lines"] if line["action"] == "GENERATE"})
    return " + ".join(accounts) + " · API billing"


def _run_timeout(lines):
    depth, nodes = {}, {line["node"]: line for line in lines}
    def level(name):
        if name not in depth:
            depth[name] = 1 + max((level(u) for u in nodes[name]["depends_on"] if u in nodes), default=0)
        return depth[name]
    return min(MAX_RUN_SECONDS, 300 + RUN_STEP_SECONDS * max((level(n) for n in nodes), default=1))


def execute(controller, window, name, arguments):
    args = validate_flow_tool(name, arguments)
    if name == "angelo_flow_inspect":
        return _inspect(args.get("flow"))
    if name == "angelo_flow_build":
        tree = _tree(args["flow"], required=False)
        _check_revision(tree, args["expected_revision"])
        imported = [flow_steps.import_recipe(name).name for name in flow_steps.missing_recipes(args)]
        flow = apply_edit(flow_tree.tree_flow(tree) if tree else {}, args, flow_steps.recipe_interfaces())
        created = tree is None
        if created:
            tree = bpy.data.node_groups.new(args["flow"], flow_nodes.TREE)
            if tree.name != args["flow"]:
                bpy.data.node_groups.remove(tree)
                raise FlowError("Blender couldn't use that flow name; choose another")
        flow_tree.write_flow(tree, flow)
        entry = flow_host.refresh(tree)
        result = {"flow": tree.name, "revision": revision(entry["definition"]), "created": created,
                  "nodes": _compact_states(entry["flow"], entry["states"]), "generated": 0, "blender_undoable": True}
        if created:
            result["note"] = "The user can open it in a Node Editor set to Generation Flow"
        if imported:
            result["recipes_copied_into_file"] = imported
        return result
    tree = _tree(args["flow"])
    if name == "angelo_flow_choose":
        _check_revision(tree, args["expected_revision"])
        from .flow_ui import choose
        choose(tree, args["node"], args["asset_id"])
        entry = flow_host.refresh(tree)
        return {"flow": tree.name, "revision": revision(entry["definition"]), "node": args["node"], "chosen": args["asset_id"],
                "nodes": _compact_states(entry["flow"], entry["states"]), "generated": 0, "blender_undoable": True}
    # angelo_flow_run: the controller re-checked this precondition after approval.
    call = getattr(controller, "executing_call", None)
    review = getattr(call, "precondition", None)
    if call is None or not isinstance(review, FlowReview) or call.name != name:
        raise FlowError("A flow run needs the approval card from this chat; nothing ran")
    selection = review.selection(getattr(call, "flow_selection", None))
    check_selection(selection)
    run = flow_host.start_run(tree, selection, owner="chat:" + call.id)
    return FlowPending(run, _run_timeout(selection["now"]))


def _inspect(name):
    if not name:
        flows = []
        for tree in flow_host.flow_trees()[:50]:
            entry = flow_host.refresh(tree)
            definition = entry["definition"]
            item = {"flow": tree.name, "revision": revision(definition) if definition else None,
                    "nodes": len(definition["nodes"]) if definition else 0,
                    "running": bool(flow_host.active_runs(tree.name)), "error": entry["error"]}
            interface = flow_tree.recipe_interface(tree)
            if interface:
                item["recipe"] = interface
            flows.append(item)
        result = {"flows": flows}
        in_file = {tree.name for tree in flow_host.flow_trees()}
        saved = sorted((n, i) for n, i in flow_steps.saved_interfaces().items() if n not in in_file)
        if saved:
            result["saved_recipes"] = [dict(interface, name=n) for n, interface in saved[:50]]
        return result
    tree = _tree(name)
    entry = flow_host.refresh(tree)
    if entry["error"]:
        return {"flow": tree.name, "error": entry["error"]}
    flow, definition = entry["flow"], entry["definition"]
    compact = dict(zip((n["id"] for n in flow["nodes"]), _compact_states(flow, entry["states"])))
    nodes = []
    for node in definition["nodes"]:
        fields = {k: v for k, v in node.items() if k not in {"id", "type", "location", "chosen"}}
        if node["type"] != "SUBFLOW" or node["id"] in compact:  # A Subflow left unexpanded has a problem.
            nodes.append(dict(compact[node["id"]], fields=fields))
            continue
        summary = entry["subflows"].get(node["id"]) or {}
        steps = [{k: v for k, v in step.items() if k != "in"} for step in compact.values() if step.get("in") == node["id"]]
        nodes.append({"id": node["id"], "type": "SUBFLOW", "state": summary.get("state"), "fields": fields,
                      "steps": steps, **({"reason": summary["reason"]} if summary.get("reason") else {})})
    result = {"flow": tree.name, "revision": revision(definition), "nodes": nodes,
              "links": [f"{l['from']} -> {l['to']}.{l['input']}" for l in definition["links"]],
              "running": [run.id for run in flow_host.active_runs(tree.name)],
              "cost": _estimate(flow, entry["states"])}
    if definition.get("recipe"):
        result["recipe"] = definition["recipe"]
    return result
