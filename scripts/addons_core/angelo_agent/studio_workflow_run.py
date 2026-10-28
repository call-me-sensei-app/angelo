# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Workflows tab, what its clicks do (``studio_workflows`` draws it). Main thread.

A run is an ordinary Generation Flow made from the template (``ensure_tree``): the card's inputs are written into it
while it is still at its input, and read back from it when a run is opened from Runs, so a run always shows and keeps
its own. Every paid step goes through ``angelo.flow_run``'s itemized approval (``_run``); declining it changes
nothing, Redo sheet included (its new take is applied only on approval).
"""

import json

import bpy

from . import flow_host, flow_nodes, flow_open, flow_templates, flow_tree, live_windows
from . import studio_workflow_view as wv
from .flow_spec import FlowError, as_definition, normalize
from .studio_workflows import INPUT_KEY, SKIP_KEY, TEMPLATE_KEY, _s, _tree, current, workflow


FIELDS = ("image", "text", "height")   # The card's inputs, as a run's flow holds them.


def _card(state):
    value = workflow(state)
    return {"image": value["image"], "text": state[INPUT_KEY], "height": round(value["height"], 3)}


def _tree_inputs(tree):
    """{image, text, height} as the run's flow holds them; image is None for a source that isn't a library asset."""
    return definition_inputs(flow_tree.tree_flow(tree))


def definition_inputs(definition):
    """``_tree_inputs`` of a flow definition (the tree's, or ``flow_host``'s cached one at draw time)."""
    nodes = {node["id"]: node for node in definition["nodes"]}
    found = {}
    if (nodes.get("Character image") or {}).get("type") == "IMAGE":
        source = nodes["Character image"].get("source") or {}
        found["image"] = source.get("id") if source.get("kind") == "ASSET" else None
    if (nodes.get("Description") or {}).get("type") == "TEXT":
        found["text"] = nodes["Description"].get("text", "")
    if flow_templates.CHARACTER_STEP in nodes:
        found["height"] = round(nodes[flow_templates.CHARACTER_STEP].get("height", 1.7), 3)
    return found


def _apply_inputs(flow, fields):
    """``flow`` with only ``fields`` (a subset of FIELDS) written from the card."""
    for node in flow["nodes"]:
        if "image" in fields and node["id"] == "Character image" and node["type"] == "IMAGE":
            node["source"] = {"kind": "ASSET", "id": fields["image"]} if fields["image"] else None
        elif "text" in fields and node["id"] == "Description" and node["type"] == "TEXT":
            node["text"] = fields["text"]
        elif "height" in fields and node["id"] == flow_templates.CHARACTER_STEP:
            node["height"] = fields["height"]
    return normalize(as_definition(flow))


def _show(state, found):
    """Put a run's own inputs on the card and remember them as what the card and the run agree on."""
    value = workflow(state)
    if "image" in found:
        value["image"] = found["image"]
        asset = _s().library().assets.get(found["image"]) if found["image"] else None
        value["image_name"] = (asset or {}).get("prompt", "")
    if "text" in found:
        state[INPUT_KEY] = found["text"]
    if "height" in found:
        value["height"] = found["height"]
    value["synced"] = dict(_card(state), **found)


def follow_flow(state, definition):
    """At draw time: an input the person changed in the Node Editor (the card unchanged since the card and the run
    last agreed) shows on the card at once, so the card and Make parts sheet's readiness follow the run."""
    value = workflow(state)
    synced = value.get("synced")
    if not synced or not definition:
        return
    card = _card(state)
    found = {name: item for name, item in definition_inputs(definition).items()
             if card[name] == synced.get(name) and item != synced.get(name)}
    if found:
        _show(state, dict({name: card[name] for name in FIELDS}, **found))


def read_inputs(state, tree):
    """The card's inputs and left-out parts taken back from a run's own flow (a run opened from Runs shows its own)."""
    try:
        _show(state, _tree_inputs(tree))
    except FlowError:
        return
    workflow(state)["skip"] = sorted(json.loads(tree.get(SKIP_KEY, "[]")))


def save_skip(value, tree):
    """The parts left out belong to the run (its flow), so Runs and a reopened file keep them."""
    if tree is not None:
        tree[SKIP_KEY] = json.dumps(sorted(value["skip"]))


def ensure_tree(state):
    """The run's Generation Flow: created from the template with the card's inputs, or brought up to date while the run
    is still at its input. Only an input the person changed on the card since the card and the run last agreed is
    written; one changed in the Node Editor (or a URL source the card can't show) is taken back onto the card. Past
    its input, a run's inputs are its own and are never overwritten from the card."""
    value = workflow(state)
    tree = _tree(value)
    if tree is not None and current(state)[2]["stage"] != wv.FILL:
        return tree
    card = _card(state)
    if tree is None:
        flow, _ = flow_templates.instantiate(value["template"], flow_open.template_entries())
        title = flow_templates.TEMPLATES[value["template"]]["title"]  # English: flows are named in files.
        name = f"{title} · {value['image_name'] or state[INPUT_KEY][:24] or 'run'}"[:63]
        tree = bpy.data.node_groups.new(name, flow_nodes.TREE)
        tree[TEMPLATE_KEY] = value["template"]  # Lists it under Runs; the flow itself is ordinary.
        value["tree"] = tree.name
        changed = card
    else:
        flow = flow_tree.tree_flow(tree)
        synced = value.get("synced") or {}
        changed = {name: card[name] for name in FIELDS if name in synced and card[name] != synced[name]}
        _show(state, {name: found for name, found in _tree_inputs(tree).items() if name not in changed})
    flow_tree.write_flow(tree, _apply_inputs(flow, changed))
    value["synced"] = _card(state)
    flow_host.refresh(tree)   # The cached flow the card follows (follow_flow) is this write, never the one before.
    flow_host.tree_changed(tree)   # And the pump prepares its previews and splits, as after any edit.
    # Studio's clicks are not undoable operators: without a step here, Undo after Place Character would return to a
    # state recorded before this flow existed and take the run with it.
    bpy.ops.ed.undo_push(message="Workflow run")  # i18n: undo history entry, as Blender's own are named in English
    return tree


def _run(tree, scope, node="", exclude=(), retake=""):
    bpy.ops.angelo.flow_run('INVOKE_DEFAULT', tree=tree.name, scope=scope, node=node, exclude=json.dumps(list(exclude)),
                            retake=retake)


def click(context, state, action, value_, rect):
    value = workflow(state)
    tree = _tree(value)
    try:
        if action == "wf_template" and tree is None:
            value["template"] = value_
        elif action in {"wf_pick_image", "wf_clear_image"} and tree is not None and current(state)[2]["stage"] != wv.FILL:
            return   # The run's input is its own once its sheet is being made.
        elif action == "wf_pick_image":
            bpy.ops.angelo.studio_workflow_image('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()))
        elif action == "wf_clear_image":
            value.update(image=None, image_name="")
        elif action == "wf_height" and (tree is None or current(state)[2]["stage"] == wv.FILL):
            value["height"] = round(min(3.0, max(0.3, value["height"] + value_)), 2)
        elif action == "wf_skip":
            required = {p["id"] for p in current(state)[2]["parts"] if p["required"]}
            skip = set(value["skip"])
            skip.symmetric_difference_update({value_} - required)
            value["skip"] = sorted(skip)
            save_skip(value, tree)
        elif action == "wf_start":
            _run(ensure_tree(state), "ALL")
        elif action == "wf_edit":
            _open_in_node_editor(context, ensure_tree(state))
        elif action == "wf_redo" and tree is not None:
            # A fresh take of the same sheet request, a new paid generation: applied only once the run is approved.
            _run(tree, "NODE", flow_templates.SHEET_STEP, retake=flow_templates.SHEET_STEP)
        elif action == "wf_parts" and tree is not None:
            skip = {f"{flow_templates.PARTS_STEP}/{part}" for part in value["skip"]}
            _run(tree, "NODE", flow_templates.PARTS_STEP, exclude=skip)
        elif action == "wf_retry" and tree is not None:
            # Only the parts that failed start ticked; the rest of the per-part steps are listed unticked.
            parts = current(state)[2]["parts"]
            _run(tree, "NODE", flow_templates.PARTS_STEP, exclude=[p["step"] for p in parts if not p["retry"]])
        elif action == "wf_stop" and tree is not None:
            for run in flow_host.active_runs(tree.name):
                flow_host.stop_run(run.id)
        elif action == "wf_place" and tree is not None:
            # undo=True: an operator called from Python pushes no undo step otherwise (WM_operator_call_py), and Undo
            # would then go back past this run's last step and take the run with the placed character.
            bpy.ops.angelo.flow_place_character('EXEC_DEFAULT', True, tree=tree.name, node=flow_templates.CHARACTER_STEP)
        elif action == "wf_check" and tree is not None:
            bpy.ops.angelo.studio_workflow_check('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()))
        elif action == "wf_fit" and tree is not None:
            bpy.ops.angelo.studio_workflow_fit('INVOKE_DEFAULT', area_pointer=str(context.area.as_pointer()))
        elif action == "wf_new":
            value.update(tree=None, skip=[], synced=None)
        elif action == "wf_open":
            opened = bpy.data.node_groups.get(value_)
            if opened is not None and opened.get(TEMPLATE_KEY) in flow_templates.studio_ids():
                value.update(tree=opened.name, template=opened[TEMPLATE_KEY])
                read_inputs(state, opened)
    except (FlowError, ValueError, RuntimeError, KeyError, AttributeError) as error:
        # The run is an ordinary flow, editable in the Node Editor: a step renamed or deleted there is a notice here.
        state["notice"] = str(error)


def _open_in_node_editor(context, tree):
    """A Generation Flow editor beside this Studio shows the run's flow; otherwise the flow window does
    (``flow_open.show``). Another kind of node editor (shaders, geometry nodes) is never switched."""
    for area in live_windows.screen_areas(context.screen):
        if area.type == 'NODE_EDITOR' and area.spaces.active.tree_type == flow_nodes.TREE:
            area.spaces.active.node_tree = tree
            area.tag_redraw()
            return
    flow_open.show(context.window, tree)


def attach(state, paths):
    """A dropped image becomes the template's character image (imported into the library), while the run is at its
    input; later the run's input is its own and a drop changes nothing."""
    value = workflow(state)
    if _tree(value) is not None and current(state)[2]["stage"] != wv.FILL:
        return
    from pathlib import Path
    asset = _s().library().import_image(Path(paths[0]))
    workflow(state).update(image=asset["id"], image_name=Path(paths[0]).name)
