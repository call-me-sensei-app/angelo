# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI render: the camera's view restyled by an image model, through an ordinary Generation Flow (1.0.8). Main thread.

Contract: doc/angelo/render_engines.rst, "AI render". The card (``ai_render_card``) is the one click; this module is
what the click does, Blender-light so it is host-tested:

* **One flow per document**, made from the ``ai_render`` template (``flow_templates``: a Scene Capture of the scene
  camera in Material preview → the AI render step, with a Style text) the first time and reused after, so it stays
  an editable flow the person can open, change and rerun, and the capture is reused while the scene is unchanged
  (``flow_capture``). It is found by its mark (``MARK``), never by name, so a rename keeps it.
* **The click is the approval.** The card shows the account, model and the price estimate before the click (the
  same ``flow_prices`` table ``angelo.flow_run``'s dialog itemizes); ``start`` then writes the style, account, model
  and the camera's aspect into the flow, plans it (which captures the camera's view: free and local), checks that the
  plan is exactly one paid generation of the AI render step at the shown price, and starts it. Anything else (an
  extra step the person added in the Node Editor, a different price) refuses and says to run the flow from its editor,
  where the itemized dialog shows it.
* **Render again** is a new take: when the style, account and scene are unchanged the step's request is the same, so
  ``start`` advances the step's take (as Media Studio's Redo does) and the click makes a new image.
* **Remembered**: the last style, account and model (``ai-render.json`` in the state folder), per person, not per
  document. The result is an ordinary Media Studio image (the step's library output).
"""

import math
from pathlib import Path

from .flow_spec import FlowError
from .ui_text import n_

MARK = "angelo_ai_render"          # Saved on the flow's node tree: this document's AI render flow.
FILE = "ai-render.json"
MAX_STYLE = 2000                   # Characters of the style the card keeps (a Text node holds more).
ASPECTS = ("1:1", "16:9", "9:16", "4:3", "3:4")   # Media Studio's ratios: every image model takes them.
NO_CAMERA = n_("This scene has no camera. Add one and make it the scene camera, then try again")
NO_ACCOUNT = n_("Connect an image account in AI Providers to restyle renders: Nano Banana (Gemini) and Seedream "
                "work best")
RUNNING_ALREADY = n_("An AI render is already running; wait for it or stop it first")
BLOCKED = n_("The AI render can't run yet: {reason}")   # The planner's reason stays English, as flows state it.
NOT_ONE = n_("This AI render flow changed since the card showed it (its steps, wiring or price). Open it and run "
             "it from its editor to review the run")


class Refusal(FlowError):
    """A refusal the card shows translated: ``template`` (an ``n_`` constant) and its ``fields``; ``str`` is English."""

    def __init__(self, template, **fields):
        super().__init__(template.format(**fields))
        self.template, self.fields = template, fields


def memory_path():
    from .ui import state_directory
    return Path(state_directory()) / FILE


def remembered(path=None):
    """``{"style", "connection", "model"}`` from the last AI render (empty strings on a first use)."""
    from .studio_state import read_json
    value = read_json(path or memory_path(), {})
    value = value if isinstance(value, dict) else {}
    return {key: value[key][:MAX_STYLE] if isinstance(value.get(key), str) else "" for key in ("style", "connection",
                                                                                                "model")}


def remember(style, connection, model, path=None):
    from .studio_state import atomic_json
    try:
        atomic_json(path or memory_path(), {"style": style[:MAX_STYLE], "connection": connection, "model": model})
    except OSError as error:   # Remembered for nothing: the render itself is unaffected.
        print("Angelo: AI render: could not remember the style:", error)


def accounts(entries):
    """The ready image accounts the card offers, the template's preferred models first (Nano Banana, then Seedream),
    then any other ready image model: ``[entry]`` with ``connection``, ``id``, ``name``, ``mode``."""
    from .flow_templates import RENDER_ROLE
    ready = [entry for entry in entries if entry.get("kind") == "IMAGE" and entry.get("ready")]
    order = {model: index for index, model in enumerate(RENDER_ROLE["prefer"])}
    return sorted(ready, key=lambda entry: (order.get(entry["id"], len(order)), entry.get("name", "")))


def chosen(choices, memory):
    """The remembered account and model when it is still offered, else the first."""
    return next((entry for entry in choices if (entry["connection"], entry["id"]) == (memory.get("connection"),
                                                                                       memory.get("model"))),
                choices[0] if choices else None)


def aspect(width, height):
    """The image ratio closest to a ``width`` x ``height`` frame (compared as logarithms: 2:1 is as far from 1:1 as
    1:2)."""
    if width <= 0 or height <= 0:
        return "1:1"
    target = math.log(width / height)

    def distance(ratio):
        w, h = (int(part) for part in ratio.split(":"))
        return abs(math.log(w / h) - target)
    return min(ASPECTS, key=distance)


def camera_aspect(scene):
    render = scene.render
    return aspect(render.resolution_x * render.pixel_aspect_x, render.resolution_y * render.pixel_aspect_y)


def options_for(entry, scene_aspect):
    """The AI render step's options for ``entry``: the template's options for its provider, at the camera's ratio."""
    from .flow_templates import RENDER_ROLE, _options
    options = _options(RENDER_ROLE, entry, {})
    options["aspect"] = scene_aspect
    return options


def price(entry, scene_aspect):
    """Estimated USD for one AI render with ``entry``, or None when the table has no price for it."""
    from . import flow_prices
    return flow_prices.price({"model": entry["id"], "options": options_for(entry, scene_aspect), "_references": 1})


def find_tree(node_groups):
    from . import flow_nodes
    return next((tree for tree in node_groups if tree.bl_idname == flow_nodes.TREE and tree.get(MARK)), None)


def ensure_tree(bpy_data, entries):
    """This document's AI render flow: the marked one, or a new one from the template."""
    from . import flow_nodes, flow_templates, flow_tree
    tree = find_tree(bpy_data.node_groups)
    if tree is not None:
        return tree
    flow, _unresolved = flow_templates.instantiate("ai_render", entries)
    tree = bpy_data.node_groups.new(flow_templates.TEMPLATES["ai_render"]["title"], flow_nodes.TREE)
    tree[MARK] = True
    flow_tree.write_flow(tree, flow)
    return tree


def configure(tree, style, entry, scene_aspect):
    """Write the style, account, model and ratio into the flow's Style and AI render steps (by name)."""
    from . import flow_templates, flow_tree
    from .flow_spec import as_definition, normalize
    flow = flow_tree.tree_flow(tree)
    nodes = {node["id"]: node for node in flow["nodes"]}
    step, text = nodes.get(flow_templates.AI_RENDER_STEP), nodes.get(flow_templates.STYLE_STEP)
    if not wired_as_shown(flow):
        raise Refusal(NOT_ONE)
    text["text"] = style
    step.update(connection_id=entry["connection"], model=entry["id"], options=options_for(entry, scene_aspect))
    flow_tree.write_flow(tree, normalize(as_definition(flow)))


def wired_as_shown(flow):
    """The flow still does what the card says: the AI render step restyles the scene camera's capture with the
    template's prompt and the Style text, and nothing else feeds it. An edit in the Node Editor (another reference,
    another prompt) is run from that editor, whose dialog itemizes it."""
    from . import flow_templates
    nodes = {node["id"]: node for node in flow["nodes"]}
    step, text = nodes.get(flow_templates.AI_RENDER_STEP), nodes.get(flow_templates.STYLE_STEP)
    camera = nodes.get(flow_templates.CAMERA_STEP)
    if (step is None or step["type"] != "GENERATE_IMAGE" or text is None or text["type"] != "TEXT" or camera is None
            or camera["type"] != "SCENE_CAPTURE" or camera.get("framing") != "CAMERA"
            or step.get("prompt") != flow_templates.RESTYLE_PROMPT):
        return False
    feeds = sorted((link["from"], link["input"]) for link in flow["links"] if link["to"] == flow_templates.AI_RENDER_STEP)
    return feeds == sorted([(flow_templates.CAMERA_STEP, "references"), (flow_templates.STYLE_STEP, "prompt")])


def _step_line(run_plan):
    from .flow_templates import AI_RENDER_STEP
    return next((line for line in run_plan["lines"] if line["node"] == AI_RENDER_STEP), None)


def one_line(run_plan):
    """The plan's single paid line for the AI render step, or None (nothing to make, or more than that)."""
    from .flow_templates import AI_RENDER_STEP
    paid = [line for line in run_plan["lines"] if line["action"] == "GENERATE"]
    if len(paid) == 1 and paid[0]["node"] == AI_RENDER_STEP and paid[0]["count"] == 1:
        return paid[0]
    return None


def start(window, style, entry, shown_price, *, plan=None, start_run=None, active_runs=None):
    """Restyle the camera's view: configure, plan (capturing the view), check, start. Returns the run.

    A transaction: a refusal puts the flow's steps and the step's take back as they were, so a click that starts
    nothing changes nothing (as ``flow_ui._retaken`` does for the run dialog). ``plan(tree, window)``,
    ``start_run(tree, selection)`` and ``active_runs(tree name)`` default to the flow editor's own
    (``flow_ui.build_plan`` for the whole flow, ``flow_host``); tests stand them in."""
    import bpy
    from . import flow_templates, flow_tree
    from .flow_graph import check_selection, select
    if window.scene.camera is None:
        raise Refusal(NO_CAMERA)
    if plan is None:
        from .flow_ui import build_plan

        def plan(tree, window):
            return build_plan(tree, window, "ALL", "")
    if active_runs is None:
        from . import flow_host
        active_runs = flow_host.active_runs
    tree = ensure_tree(bpy.data, [entry])
    if active_runs(tree.name):   # A run queued behind a full library has no running record yet: never a second.
        raise Refusal(RUNNING_ALREADY)
    node = tree.nodes.get(flow_templates.AI_RENDER_STEP)
    before, take = flow_tree.tree_flow(tree), getattr(node, "take", None)
    started = False
    try:
        configure(tree, style, entry, camera_aspect(window.scene))
        flow, run_plan = plan(tree, window)
        step = _step_line(run_plan)
        if step is not None and step["action"] == "REUSE":
            if node is None or take is None:
                raise Refusal(NOT_ONE)
            node.take += 1   # Unchanged style, account and scene: the click asks for a new image, a new take.
            flow, run_plan = plan(tree, window)
            step = _step_line(run_plan)
        if step is not None and step["action"] in {"BLOCKED", "RUNNING"}:
            raise (Refusal(RUNNING_ALREADY) if step["action"] == "RUNNING"
                   else Refusal(BLOCKED, reason=step.get("reason") or "?"))
        line = one_line(run_plan)
        if line is None or not _same_price(line["price_each"], shown_price):
            raise Refusal(NOT_ONE)
        selection = select(run_plan, flow)
        check_selection(selection)
        if start_run is None:
            from . import flow_host

            def start_run(tree, selection):
                return flow_host.start_run(tree, selection, owner="ai_render")
        run = start_run(tree, selection)
        started = True
    finally:
        if not started:
            flow_tree.write_flow(tree, before)
            if take is not None:
                node.take = take
    remember(style, entry["connection"], entry["id"])
    return run


def _same_price(planned, shown):
    if planned is None or shown is None:
        return planned is None and shown is None
    return abs(planned - shown) < 0.005


def result(run):
    """``(state, asset id or None, error or None)`` of an AI render run: ``running``, ``done`` or ``failed``."""
    from .flow_templates import AI_RENDER_STEP
    step = run.steps.get(AI_RENDER_STEP) if run is not None else None
    if step is None:
        return "failed", None, None
    if not run.finished:
        return "running", None, None
    if step.get("assets"):
        return "done", step.get("chosen") or step["assets"][0], None
    return "failed", None, step.get("error")
