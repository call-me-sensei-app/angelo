# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Generation Flow is red only where a run failed, in pixels (1.0.5 review of the first-open state).

In a Node Editor of the default window, each case is shown, framed and captured, and every node's pixels are searched
for the alert text's red (calibrated on 1.0.5's first-cut captures: (233, 111, 111), several hundred pixels for a red
step, none elsewhere):

* ``fresh``: the image to reference template with no generation account (English, German and Russian);
* ``models_chosen``: the same with an image account (an in-memory fixture key) and the Reference step's model chosen:
  it waits for the image, neutrally;
* ``add_menu``: new nodes as the Add menu makes them (Generate Image without a prompt, an unlinked Place in Scene, a
  Subflow without a recipe);
* ``stopped``: a step whose last attempt the person stopped: neutral "Stopped";
* ``failed``: the same step whose last attempt failed: red, the detector's own control;
* ``waiting`` (English and German): a step waiting for two others says so on one line ("Waiting for 2 steps"); the
  Place in Scene node of ``fresh`` shows the one-step form ("Waiting for '3D model'").

The template prompt's wrapped lines are measured again here and fit their box. No provider, network or paid request:
the attempts are library records written in memory. Runs on an add-on from before the review's fixes too (its
captures are the "before"). Run through ``run_native_suites.py --gui --include test_blender_flow_setup_states``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup GUI instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-flow-setup-states-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_flow_view
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
prefs = bpy.context.preferences
prefs.view.show_splash = False
prefs.use_preferences_save = False
from angelo_agent import flow_host, flow_nodes, flow_starters, flow_text_field, flow_tree  # noqa: E402

REPORT = OUT / "flow-setup-states.json"
RED_MOST, RED_LEAST = 20, 150
LABEL_IN_BOX = 20 + 2 * 5 + 8 + 6   # NODE_DY, the box inset, UI_TEXT_MARGIN_X and the clip margin (Blender's source).
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": []}
checks, ctx = state["checks"], {}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def editor():
    """The Node Editor the cases are shown in. Angelo may switch the window's workspace after startup (Manual's tabs),
    so when the area picked earlier is gone, the largest area of the window's screen now becomes it."""
    area = next((a for a in window().screen.areas if a.as_pointer() == ctx.get("area")), None)
    if area is None:
        area = max(window().screen.areas, key=lambda a: a.width * a.height)
        area.type = 'NODE_EDITOR'
        area.spaces.active.tree_type = flow_nodes.TREE
        ctx["area"] = area.as_pointer()
    return area


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def pixels(path):
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def red_by_node(path):
    """{node name: alert-red pixels inside its drawn rectangle}."""
    import numpy
    import qa_flow_view
    area = editor()
    region, rects = qa_flow_view.graph_bounds(area, prefs.system.ui_scale)
    px = pixels(path)
    result = {}
    for node, (left, bottom, right, top) in zip(area.spaces.active.node_tree.nodes, rects):
        x0, y0 = max(0, int(region.x + left)), max(0, int(region.y + bottom))
        block = px[y0:max(y0, int(region.y + top)), x0:max(x0, int(region.x + right))]
        r, g, b = block[..., 0], block[..., 1], block[..., 2]
        result[node.name] = int(((r > 180) & (r - g > 90) & (numpy.abs(g - b) < 12)).sum()) if block.size else 0
    return result


def show(tree):
    area = editor()
    space = area.spaces.active
    space.tree_type, space.node_tree = flow_nodes.TREE, tree
    flow_host.tree_changed(tree)
    flow_host.pump()
    with bpy.context.temp_override(window=window(), area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
        bpy.ops.node.select_all(action='DESELECT')
        bpy.ops.node.view_all()


def template(name):
    tree = bpy.data.node_groups.new(name, flow_nodes.TREE)
    flow_tree.flow_id(tree)
    flow_tree.write_flow(tree, flow_starters.instantiate("image_reference_3d"))
    return tree


def image_account():
    """An in-memory image generation key the catalog treats as verified; no provider is ever called."""
    from angelo_agent.api_connections import CheckResult
    from angelo_agent.provider_registry import SPECS
    record = ui.connections().add("GOOGLE", "API_KEY", "Fixture Gemini", group="GENAI")
    ui._memory_keys[record["id"]] = "fixture-key-not-a-real-account"
    ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS[record["mode"]]["models"])
    entry = next(e for e in flow_host.catalog_entries("IMAGE") if e["ready"])
    return entry["connection"], entry["id"]


def attempt(tree, name, status):
    """A library record of an attempt with the step's current input key (memory only)."""
    flow_host.refresh(tree)
    key = flow_host.node_state(tree, name)["key"]
    identity = "qa-%s-%s" % (status, tree.name)
    flow_host.library().assets[identity] = {"id": identity, "kind": "IMAGE", "status": status, "created": time.time(),
                                            "lineage": {"source": "flow", "flow": tree.name, "node": name, "key": key}}
    flow_host.tree_changed(tree)


def judge(case, tree, language):
    key = "%s_%s" % (case, language)
    shot = capture("flow-setup-%s-%s.png" % (case, language))
    red = red_by_node(shot)
    states = {n.name: {k: v for k, v in (flow_host.node_state(tree, n.name) or {}).items() if k in {"state", "setup", "reason"}}
              for n in tree.nodes}
    state["observed"][key] = {"red": red, "states": states}
    if case == "failed":
        checks["a_failed_run_is_red_" + key] = red.get("Look", 0) >= RED_LEAST
    else:
        checks["nothing_is_red_" + key] = max(red.values(), default=0) <= RED_MOST
    if case == "waiting":   # One line: "Waiting for 2 steps" (the place node in "fresh" shows the one-step form).
        from angelo_agent.flow_setup import waiting_text
        both = states.get("Both", {})
        state["observed"][key]["waiting_line"] = waiting_text(["First", "Second"])
        checks["one_waiting_line_" + key] = (both.get("state") == "WAITING"
                                             and flow_host.node_state(tree, "Both").get("waits") == ["First", "Second"])
    if case == "stopped":
        checks["a_stopped_run_is_stopped_" + key] = states["Look"].get("state") == "STOPPED"
    if case == "fresh":
        lines = flow_text_field.drawn.get((tree.name, "Reference", "prompt"), [])
        node = tree.nodes["Reference"]
        import blf
        blf.size(0, prefs.ui_styles[0].widget.points * prefs.system.ui_scale)
        room = (node.width - LABEL_IN_BOX) * prefs.system.ui_scale
        wide = [line for line in lines if blf.dimensions(0, line)[0] > room + 0.5]
        state["observed"][key]["prompt_lines"] = lines
        checks["the_prompt_wraps_inside_its_box_" + key] = len(lines) > 1 and not wide


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    prefs.view.language = "en_US"
    REPORT.write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False))
    print("ANGELO_FLOW_SETUP_STATES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 240, "flow setup states fixture timed out in " + state["phase"]
        phase = state["phase"]
        if phase == "boot":
            editor()
            prefs.view.use_translate_interface = True
            ctx["fresh"] = template("Fresh template")
            ctx["queue"] = [("fresh", "en_US"), ("fresh", "de_DE"), ("fresh", "ru_RU")]
            state["phase"] = "next"
            return .5
        if phase == "next":
            if not ctx["queue"]:
                if ctx.get("second"):
                    finish()
                    return None
                state["phase"] = "accounts"
                return .2
            case, language = ctx["queue"][0]
            if prefs.view.language != language:
                prefs.view.language = language
                return .4
            show(ctx[case])
            state["phase"] = "look"
            return .6
        if phase == "look":
            case, language = ctx["queue"].pop(0)
            show(ctx[case])
            judge(case, ctx[case], language)
            state["phase"] = "next"
            return .3
        if phase == "accounts":   # English from here on: Blender names a new node in the interface's language.
            if prefs.view.language != "en_US":
                prefs.view.language = "en_US"
                return .4
            connection, model = image_account()
            chosen = template("Models chosen")
            chosen.nodes["Reference"].connection_id, chosen.nodes["Reference"].model = connection, model
            ctx["models_chosen"] = chosen
            added = bpy.data.node_groups.new("Add menu", flow_nodes.TREE)
            for idname, x in ((flow_nodes.ANGELO_FlowGenerateImageNode.bl_idname, 0),
                              (flow_nodes.ANGELO_FlowPlaceNode.bl_idname, 320), (flow_nodes.ANGELO_FlowSubflowNode.bl_idname, 600)):
                node = added.nodes.new(idname)
                node.location = (x, 0)
            added.nodes[0].connection_id, added.nodes[0].model = connection, model
            ctx["add_menu"] = added
            for status in ("stopped", "failed"):
                tree = bpy.data.node_groups.new(status.title(), flow_nodes.TREE)
                look = tree.nodes.new(flow_nodes.ANGELO_FlowGenerateImageNode.bl_idname)
                # Each its own prompt: the same request has the same input key, and the later record would win.
                look.name, look.prompt = "Look", "a brass robot, %s case" % status
                look.connection_id, look.model = connection, model
                attempt(tree, "Look", status)
                ctx[status] = tree
            waiting = bpy.data.node_groups.new("Waiting", flow_nodes.TREE)   # One step waiting for two.
            made = {}
            for name, x, y in (("First", 0, 200), ("Second", 0, -200), ("Both", 380, 0)):
                node = waiting.nodes.new(flow_nodes.ANGELO_FlowGenerateImageNode.bl_idname)
                node.name, node.location, node.prompt = name, (x, y), "a brass robot, " + name.lower()
                node.connection_id, node.model = connection, model
                made[name] = node
            for name in ("First", "Second"):
                waiting.links.new(made[name].outputs["output"], made["Both"].inputs["references"])
            ctx["waiting"] = waiting
            ctx["queue"] = [(case, "en_US") for case in ("models_chosen", "add_menu", "stopped", "failed", "waiting")]
            ctx["queue"].append(("waiting", "de_DE"))
            state["phase"] = "next"
            ctx["second"] = True
            return .4
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
