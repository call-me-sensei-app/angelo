# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Flow recipes and Subflow nodes in the actual app, with fixture providers.

A recipe flow (sketch + style → sheet with two variants → 3D model) is used
twice in another flow. The suite checks the Subflow sockets that follow the
recipe's interface, the agent tools' view of it, a run of one use with a
review stop, per-use choices and keys (the shared recipe is never written),
free reuse by an identical use, copying a saved recipe into the file, and
socket and state updates after the recipe changes. Run like
test_blender_flows.py (isolated factory-startup GUI) or through
run_native_suites.py. Inspect flow-recipes-qa.json and the PNGs.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup GUI instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-flow-recipes-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui
from angelo_agent.api_connections import CheckResult
from angelo_agent.provider_registry import SPECS

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.smooth_view = 0  # Framing must be complete when the screenshot is taken.
bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")

from angelo_agent import flow_host, flow_nodes, flow_recipe_library, flow_tools, flow_tree, studio
from angelo_agent.flow_approvals import message as approval_message
from angelo_agent.flow_spec import revision
from angelo_agent.view_png import encode_png

IMAGE_MODEL, MESH_MODEL = "gemini-3.1-flash-image", "P2-20260801"
state = {"phase": "boot", "checks": {}, "success": False,
         "fixture": "In-process fixture providers only; no network, keys or paid requests"}
calls, ctx = [], {}


def png(seed, size=64):
    r, g, b = (seed * 53) % 256, (seed * 97) % 256, (seed * 151) % 256
    return encode_png(bytes([r, g, b, 255]) * (size * size), size, size)


def fixture_transport(url, body=None, headers=None, limit=None, timeout=None, service=False):
    calls.append((url, body is not None))
    if url.endswith("/interactions"):
        import base64
        data = base64.b64encode(png(len(calls))).decode()
        return json.dumps({"steps": [{"type": "model_output", "content": [{"type": "image", "data": data}]}]}).encode()
    if url.endswith("/files"):
        return b'{"code":0,"data":{"file_token":"fixture-token"}}'
    if "/generation/" in url:
        return b'{"code":0,"data":{"task_id":"fixture-task"}}'
    if url.endswith("/tasks/fixture-task"):
        return json.dumps({"code": 0, "data": {"status": "success", "progress": 100, "output": {
            "model_url": "https://fixture.invalid/model.glb", "rendered_image_url": "https://fixture.invalid/preview.png"}}}).encode()
    if url.endswith("/model.glb"):
        return ctx["glb"]
    if url.endswith("/preview.png"):
        return png(99)
    raise AssertionError("Unexpected fixture URL " + url)


def window():
    return bpy.context.window_manager.windows[0]


def check(name, value):
    state["checks"][name] = bool(value)


def posts():
    return sum(1 for _, post in calls if post)


def screenshot(name):
    with bpy.context.temp_override(window=window(), screen=window().screen):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state.setdefault("captures", []).append(name)


def account(provider, name):
    record = ui.connections().add(provider, "API_KEY", name, group="GENAI")
    ui._memory_keys[record["id"]] = "fixture-key-not-a-real-account"
    ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS[record["mode"]]["models"])
    return record["id"]


def build(flow, **edit):
    tree = bpy.data.node_groups.get(flow)
    expected = revision(flow_tree.tree_flow(tree)) if tree else None
    arguments = dict(edit, flow=flow, expected_revision=expected)
    flow_tools.precondition(window(), "angelo_flow_build", arguments)
    return flow_tools.execute(None, window(), "angelo_flow_build", arguments)


def run_arguments(scope="NODE", node="Hero"):
    tree = bpy.data.node_groups["Characters"]
    return {"flow": "Characters", "expected_revision": revision(flow_tree.tree_flow(tree)), "scope": scope, "node": node}


def start_run(arguments, choices=None):
    review = flow_tools.precondition(window(), "angelo_flow_run", arguments)
    call = SimpleNamespace(id="fixture-call-" + str(len(calls)), name="angelo_flow_run", precondition=review, approval=True,
                           arguments=arguments, usage_label=flow_tools.usage_label(review),
                           flow_selection=choices or {"excluded": set(), "stops": {}})
    pending = flow_tools.execute(SimpleNamespace(executing_call=call), window(), "angelo_flow_run", arguments)
    return review, pending


def wait(pending, label, limit=90):
    ctx.setdefault(label + "_started", time.monotonic())
    result = pending.poll()
    if result is not None:
        ctx[label] = result
        return True
    if time.monotonic() - ctx[label + "_started"] > limit:
        raise AssertionError(label + " did not finish within " + str(limit) + " s")
    return False


def states(flow="Characters"):
    flow_host.pump()
    return flow_host.refresh(bpy.data.node_groups[flow])["states"]


def tick():
    try:
        return step()
    except Exception as error:
        state.update(phase="failed", error=str(error), traceback=traceback.format_exc())
        finish()
        return None


def finish():
    if state["phase"] != "failed":
        state["success"] = bool(state["checks"]) and all(state["checks"].values())
        state["phase"] = "complete"
    state["fixture_calls"] = [url for url, _ in calls]
    (OUT / "flow-recipes-qa.json").write_text(json.dumps(state, indent=2))
    print("ANGELO_FLOW_RECIPES_GUI", json.dumps({k: state[k] for k in ("phase", "success", "checks")}), flush=True)
    if state.get("traceback"):
        print(state["traceback"], flush=True)
    bpy.ops.wm.quit_blender()


def frame(area):
    flow_host.pump()
    sidebar = next(r for r in area.regions if r.type == 'UI')
    try:
        sidebar.active_panel_category = "Flow"
    except (AttributeError, TypeError):
        pass  # Older builds can't switch sidebar tabs from Python; the screenshot shows another tab.
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.node.select_all(action='DESELECT')
        bpy.ops.node.view_all()


def step():
    phase = state["phase"]
    if phase == "boot":
        studio._jobs.transport = fixture_transport
        gemini, tripo = account("GOOGLE", "Fixture Gemini"), account("TRIPO", "Fixture Tripo")
        glb_path = OUT / "fixture-cube.glb"
        with bpy.context.temp_override(window=window()):
            bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', use_selection=False)
        ctx["glb"] = glb_path.read_bytes()
        for name, seed in (("hero", 7), ("villain", 11)):
            path = OUT / (name + ".png")
            path.write_bytes(png(seed, 96))
            ctx[name] = flow_host.library().import_image(str(path))["id"]
        build("Turnaround", nodes=[
            {"id": "Sketch", "type": "IMAGE", "fields": {"source": None}},
            {"id": "Style", "type": "TEXT", "fields": {"text": "clean toon style"}},
            {"id": "Sheet", "type": "GENERATE_IMAGE", "fields": {"connection_id": gemini, "model": IMAGE_MODEL,
                                                                 "prompt": "Character sheet", "variants": 2}},
            {"id": "Mesh", "type": "GENERATE_3D", "fields": {"connection_id": tripo, "model": MESH_MODEL}}], links=[
            {"from": "Sketch", "to": "Sheet", "input": "references"}, {"from": "Style", "to": "Sheet", "input": "prompt"},
            {"from": "Sheet", "to": "Mesh", "input": "reference"}], recipe={"inputs": ["Sketch", "Style"], "output": "Mesh"})
        recipe = bpy.data.node_groups["Turnaround"]
        check("recipe_interface_saved_on_tree", recipe.angelo_recipe_output == "Mesh" and recipe.nodes["Sketch"].exposed
              and recipe.nodes["Style"].exposed)
        result = build("Characters", nodes=[
            {"id": "Hero sketch", "type": "IMAGE", "fields": {"source": {"kind": "ASSET", "id": ctx["hero"]}}},
            {"id": "Villain sketch", "type": "IMAGE", "fields": {"source": {"kind": "ASSET", "id": ctx["villain"]}}},
            {"id": "Hero", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}},
            {"id": "Villain", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}},
            {"id": "Place", "type": "PLACE_IN_SCENE"}], links=[
            {"from": "Hero sketch", "to": "Hero", "input": "Sketch"}, {"from": "Villain sketch", "to": "Villain", "input": "Sketch"},
            {"from": "Hero", "to": "Place", "input": "model"}])
        tree, hero = bpy.data.node_groups["Characters"], bpy.data.node_groups["Characters"].nodes["Hero"]
        check("subflow_sockets_follow_interface", [(s.identifier, s.bl_idname) for s in hero.inputs]
              == [("Sketch", flow_nodes.SOCKET_IDS["IMAGE"]), ("Style", flow_nodes.SOCKET_IDS["TEXT"])]
              and [s.bl_idname for s in hero.outputs] == [flow_nodes.SOCKET_IDS["MODEL"]] and len(tree.links) == 3)
        check("build_lists_inner_steps", {n["id"] for n in result["nodes"]} >= {"Hero/Sheet", "Hero/Mesh", "Villain/Sheet"}
              and not calls)
        inspected = flow_tools.execute(None, window(), "angelo_flow_inspect", {"flow": "Characters"})
        subflow = next(n for n in inspected["nodes"] if n["id"] == "Hero")
        check("inspect_shows_subflow_steps_and_outer_links", subflow["type"] == "SUBFLOW"
              and subflow["fields"] == {"recipe": "Turnaround"} and {s["id"] for s in subflow["steps"]} >= {"Hero/Sheet", "Hero/Mesh"}
              and "Hero sketch -> Hero.Sketch" in inspected["links"])
        listing = flow_tools.execute(None, window(), "angelo_flow_inspect", {})
        check("inspect_lists_recipe_interface", any(f["flow"] == "Turnaround" and f.get("recipe") ==
                                                    {"inputs": {"Sketch": "IMAGE", "Style": "TEXT"}, "output": "MODEL"}
                                                    for f in listing["flows"]))
        area = max((a for a in window().screen.areas if a.type != 'VIEW_3D'), key=lambda a: a.width * a.height)
        area.type = 'NODE_EDITOR'
        area.spaces.active.tree_type = flow_nodes.TREE
        area.spaces.active.node_tree = bpy.data.node_groups["Characters"]
        arguments = run_arguments()
        review = flow_tools.precondition(window(), "angelo_flow_run", arguments)
        text = approval_message("angelo_flow_run", dict(arguments, _review=review), flow_tools.usage_label(review))
        check("card_names_inner_steps_and_defers_after_review", "Hero › Sheet ×2" in text["body"] and "after review" in text["body"])
        ctx["run1"] = start_run(arguments)
        state["phase"] = "wait_first"
        return 0.3
    if phase == "wait_first":
        if not wait(ctx["run1"][1], "first"):
            return 0.3
        result, tree = ctx["first"], bpy.data.node_groups["Characters"]
        stop = result.get("review_stop", {})
        check("run_stops_at_inner_review", stop.get("node") == "Hero/Sheet" and len(stop.get("pick_from", [])) == 2
              and stop.get("remaining_steps") == ["Hero/Mesh"] and posts() == 2)
        assets = [flow_host.library().assets[i] for i in stop["pick_from"]]
        check("lineage_names_inner_step", all(a["lineage"]["node"] == "Hero/Sheet" and a["lineage"]["flow"] == "Characters"
                                              and a["lineage"]["reference_ids"] == [ctx["hero"]] for a in assets))
        hero, recipe = tree.nodes["Hero"], bpy.data.node_groups["Turnaround"]
        check("per_use_state_on_subflow_not_recipe", hero.inner("last_key").get("Sheet") == assets[0]["lineage"]["key"]
              and hero.inner("chosen").get("Sheet") == assets[0]["id"] and not recipe.nodes["Sheet"].last_key
              and not recipe.nodes["Sheet"].chosen)
        ctx["choice"] = assets[1]["id"]
        chosen = flow_tools.execute(None, window(), "angelo_flow_choose", {
            "flow": "Characters", "expected_revision": run_arguments()["expected_revision"], "node": "Hero/Sheet",
            "asset_id": ctx["choice"]})
        current = states()
        check("choose_inner_step_on_this_use_only", chosen["chosen"] == ctx["choice"] and current["Hero/Sheet"]["chosen"] == ctx["choice"]
              and not tree.nodes["Villain"].inner("chosen") and current["Villain/Sheet"]["state"] == "MISSING")
        ctx["run2"] = start_run(run_arguments("DOWNSTREAM", "Hero"))
        lines = {l["node"]: l["action"] for l in ctx["run2"][0].plan["lines"]}
        check("downstream_reuses_sheet_and_makes_model", lines == {"Hero/Sheet": "REUSE", "Hero/Mesh": "GENERATE"})
        state["phase"] = "wait_second"
        return 0.3
    if phase == "wait_second":
        if not wait(ctx["run2"][1], "downstream", limit=120):
            return 0.5
        mesh = next((s for s in ctx["downstream"]["steps"] if s["node"] == "Hero/Mesh"), {})
        model = flow_host.library().assets[mesh["outputs"][0]] if mesh.get("outputs") else {}
        check("model_built_from_chosen_variant", ctx["downstream"]["status"] == "DONE" and model.get("kind") == "3D"
              and model.get("status") == "ready" and model["lineage"]["reference_ids"] == [ctx["choice"]])
        current = states()
        check("subflow_output_feeds_place", current["Place"]["state"] == "READY" and current["Place"]["value"] == model.get("id"))
        summary = flow_host.subflow_summary(bpy.data.node_groups["Characters"], "Hero")
        check("subflow_summary_ready_with_output", summary["state"] == "READY" and summary["output_step"] == "Hero/Mesh")
        before = posts()
        build("Characters", nodes=[{"id": "Hero again", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}}],
              links=[{"from": "Hero sketch", "to": "Hero again", "input": "Sketch"}])
        current = states()
        check("identical_use_reuses_outputs_for_free", current["Hero again/Sheet"]["state"] == "READY" and posts() == before)
        # Copy-in from the recipe library: a saved recipe this file doesn't have yet.
        flow_recipe_library.save("Library Turnaround", flow_tree.tree_flow(bpy.data.node_groups["Turnaround"]))
        result = build("Elsewhere", nodes=[{"id": "Use", "type": "SUBFLOW", "fields": {"recipe": "Library Turnaround"}}])
        copied = bpy.data.node_groups.get("Library Turnaround")
        check("saved_recipe_copied_into_file", result.get("recipes_copied_into_file") == ["Library Turnaround"]
              and copied is not None and copied.angelo_recipe_output == "Mesh"
              and not any(n.get("chosen") for n in flow_recipe_library.load_all()["Library Turnaround"]["nodes"]))
        # Recipe edits reach every use: the prompt makes Hero stale; un-exposing Style removes its socket.
        recipe = bpy.data.node_groups["Turnaround"]
        recipe.nodes["Sheet"].prompt = "Character sheet, three-quarter view"
        recipe.nodes["Style"].exposed = False
        current = states()
        hero = bpy.data.node_groups["Characters"].nodes["Hero"]
        check("recipe_edit_makes_uses_stale", current["Hero/Sheet"]["state"] == "STALE")
        check("recipe_interface_change_resyncs_sockets", [s.identifier for s in hero.inputs] == ["Sketch"]
              and len(hero.outputs) == 1)
        recipe.nodes["Style"].exposed = True
        # The runs are done: give the flow the big 3D View area for readable screenshots.
        area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
        area.type = 'NODE_EDITOR'
        space = area.spaces.active
        space.tree_type, space.node_tree, space.show_region_ui = flow_nodes.TREE, bpy.data.node_groups["Characters"], True
        ctx["editor"] = area
        state["phase"] = "frame_flow"
        return 0.5
    if phase in {"frame_flow", "frame_recipe"}:
        frame(ctx["editor"])  # A tick after showing the tree, so node sizes are known.
        state["phase"] = {"frame_flow": "settle_flow", "frame_recipe": "settle_recipe"}[phase]
        return 0.5
    if phase in {"settle_flow", "settle_recipe"}:
        with bpy.context.temp_override(window=window(), screen=window().screen):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        state["phase"] = {"settle_flow": "screenshot_flow", "settle_recipe": "screenshot_recipe"}[phase]
        return 0.5
    if phase == "screenshot_flow":
        flow_host.pump()
        screenshot("flow-recipes-uses.png")
        with bpy.context.temp_override(window=window(), area=ctx["editor"]):
            opened = bpy.ops.angelo.flow_open_recipe(recipe="Turnaround")
        check("open_recipe_shows_it", opened == {'FINISHED'} and ctx["editor"].spaces.active.node_tree.name == "Turnaround")
        state["phase"] = "frame_recipe"
        return 0.5
    if phase == "screenshot_recipe":
        flow_host.pump()
        screenshot("flow-recipes-recipe.png")
        check("no_unexpected_network", all(".invalid/" in url or "googleapis" in url or "tripo3d" in url for url, _ in calls))
        finish()
        return None
    return 0.3


bpy.app.timers.register(tick, first_interval=1.0)
