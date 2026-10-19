# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flows in the actual app: node tree, captures, runs, reuse, approvals and chat cards.

Run with the compiled binary in an isolated factory-startup GUI (not
--background: scene captures, previews and card pixels need a window).
``run_native_suites.py --gui --include test_blender_flows`` isolates it; by
hand, with ``<qa>`` a new disposable folder (Windows: set TEMP, not TMPDIR):

  ANGELO_DISABLE_DEFAULT_CHAT=1 ANGELO_QA_DIR=<qa> \\
    TMPDIR=<qa>/tmp/ HOME=<qa>/home BLENDER_USER_RESOURCES=<qa>/user \\
    /path/to/Angelo --factory-startup --disable-autoexec --no-window-focus \\
    --python tests/python/angelo_agent/test_blender_flows.py

Provider calls go to an in-process fixture transport: no network, keys or paid
requests. Steps run on successive timer ticks so Studio workers, the flow pump
and redraws run between them, as in the application. Inspect flows-qa.json and
the PNGs; a zero exit status alone is not proof.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-flows-qa"))
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
bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")

from angelo_agent import flow_chat, flow_host, flow_nodes, flow_tools, flow_tree, flow_ui, studio
from angelo_agent.flow_approvals import message as approval_message
from angelo_agent.flow_spec import FlowError, revision
from angelo_agent.view_png import encode_png

state = {"phase": "boot", "checks": {}, "success": False,
         "fixture": "In-process fixture providers only; no network, keys or paid requests"}
calls, ctx = [], {}


def png(seed, size=64):
    """A distinct, valid PNG per call, so variants are distinguishable."""
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


def screenshot(name):
    with bpy.context.temp_override(window=window(), screen=window().screen):
        # An unfocused test window may not have redrawn since the last change.
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state.setdefault("captures", []).append(name)


def account(provider, name):
    record = ui.connections().add(provider, "API_KEY", name, group="GENAI")
    ui._memory_keys[record["id"]] = "fixture-key-not-a-real-account"
    ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS[record["mode"]]["models"])
    return record["id"]


def call_for(review, arguments, choices=None):
    return SimpleNamespace(id="fixture-call-" + str(len(calls)), name="angelo_flow_run", precondition=review, approval=True,
                           arguments=arguments, usage_label=flow_tools.usage_label(review),
                           flow_selection=choices or {"excluded": set(), "stops": {}})


def start_run(arguments, choices=None):
    review = flow_tools.precondition(window(), "angelo_flow_run", arguments)
    call = call_for(review, arguments, choices)
    pending = flow_tools.execute(SimpleNamespace(executing_call=call), window(), "angelo_flow_run", arguments)
    return review, call, pending


def current_revision():
    return revision(flow_tree.tree_flow(bpy.data.node_groups["Hero"]))


def render_ops(ops, name, width=640, height=520):
    """Draw card operations offscreen with the chat renderer's primitives and save the pixels."""
    import gpu
    from mathutils import Matrix
    from angelo_agent import chat_view
    offscreen = gpu.types.GPUOffScreen(width, height)
    hits = []
    with offscreen.bind():
        framebuffer = gpu.state.active_framebuffer_get()
        framebuffer.clear(color=(0.094, 0.094, 0.094, 1.0))
        with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
            gpu.matrix.load_identity()
            gpu.matrix.load_projection_matrix(Matrix.Translation((-1, -1, 0)) @ Matrix.Diagonal((2 / width, 2 / height, 1, 1)))
            gpu.state.blend_set('ALPHA')
            flow_chat.draw(ops, hits, chat_view.elide, chat_view.shape, chat_view.text, studio.thumbnail)
            gpu.state.blend_set('NONE')
        pixels = framebuffer.read_color(0, 0, width, height, 4, 0, 'FLOAT')
    offscreen.free()
    pixels.dimensions = width * height * 4
    image = bpy.data.images.new("Angelo Flow Card QA", width, height, alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(pixels)
        image.filepath_raw, image.file_format = str(OUT / name), 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    state.setdefault("captures", []).append(name)
    distinct = len({tuple(round(v, 2) for v in pixels[i:i + 3]) for i in range(0, len(pixels), 4 * 97)})
    return hits, distinct


def wrap(value, width, size):
    from angelo_agent import chat_view
    return chat_view.wrap(value, width, size, chat_view._font)


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
    (OUT / "flows-qa.json").write_text(json.dumps(state, indent=2))
    print("ANGELO_FLOWS_GUI", json.dumps({k: state[k] for k in ("phase", "success", "checks")}), flush=True)
    if state.get("traceback"):
        print(state["traceback"], flush=True)
    bpy.ops.wm.quit_blender()


def wait(pending, label, limit=90):
    """Poll a pending run; True when it has a result (stored in ctx[label])."""
    ctx.setdefault(label + "_started", time.monotonic())
    result = pending.poll()
    if result is not None:
        ctx[label] = result
        return True
    if time.monotonic() - ctx[label + "_started"] > limit:
        raise AssertionError(label + " did not finish within " + str(limit) + " s")
    return False


def step():
    phase = state["phase"]
    if phase == "boot":
        studio._jobs.transport = fixture_transport
        ctx["gemini"], ctx["tripo"] = account("GOOGLE", "Fixture Gemini"), account("TRIPO", "Fixture Tripo")
        glb_path = OUT / "fixture-cube.glb"
        with bpy.context.temp_override(window=window()):
            bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', use_selection=False)
        ctx["glb"] = glb_path.read_bytes()
        sketch = OUT / "sketch.png"
        sketch.write_bytes(png(7, 96))
        ctx["sketch"] = flow_host.library().import_image(str(sketch))["id"]
        result = flow_tools.execute(None, window(), "angelo_flow_build", {"flow": "Hero", "expected_revision": None, "nodes": [
            {"id": "Sketch", "type": "IMAGE", "fields": {"source": {"kind": "ASSET", "id": ctx["sketch"]}}},
            {"id": "Capture", "type": "SCENE_CAPTURE", "fields": {"framing": "VIEWPORT", "shading": "CLAY", "resolution": 512}},
            {"id": "Style", "type": "TEXT", "fields": {"text": "toon style, flat colors"}},
            {"id": "Sheet", "type": "GENERATE_IMAGE", "fields": {"connection_id": ctx["gemini"], "model": "gemini-3.1-flash-image",
                                                                 "prompt": "Character sheet of a fox knight", "variants": 2}},
            {"id": "Turnaround", "type": "GENERATE_IMAGE", "fields": {"connection_id": ctx["gemini"], "model": "gemini-3.1-flash-image",
                                                                      "prompt": "Front, side and back turnaround", "options": {"resolution": "1K"}}},
            {"id": "Mesh", "type": "GENERATE_3D", "fields": {"connection_id": ctx["tripo"], "model": "P2-20260801"}},
            {"id": "Place", "type": "PLACE_IN_SCENE"}], "links": [
            {"from": "Sketch", "to": "Sheet", "input": "references"}, {"from": "Capture", "to": "Sheet", "input": "references"},
            {"from": "Style", "to": "Sheet", "input": "prompt"}, {"from": "Sheet", "to": "Turnaround", "input": "references"},
            {"from": "Turnaround", "to": "Mesh", "input": "reference"}, {"from": "Mesh", "to": "Place", "input": "model"}]})
        tree = bpy.data.node_groups["Hero"]
        check("build_creates_node_tree", tree.bl_idname == flow_nodes.TREE and len(tree.nodes) == 7 and len(tree.links) == 6)
        check("build_revision_round_trips", result["revision"] == current_revision())
        check("build_generates_nothing", not calls and result["generated"] == 0)
        area = max((a for a in window().screen.areas if a.type != 'VIEW_3D'), key=lambda a: a.width * a.height)
        area.type = 'NODE_EDITOR'
        area.spaces.active.tree_type = flow_nodes.TREE
        area.spaces.active.node_tree = tree
        ctx["editor"] = area
        state["phase"] = "first_run"
        return 0.5
    if phase == "first_run":
        arguments = {"flow": "Hero", "expected_revision": current_revision(), "scope": "ALL"}
        review = flow_tools.precondition(window(), "angelo_flow_run", arguments)
        tree = bpy.data.node_groups["Hero"]
        capture = tree.nodes["Capture"].capture_asset
        check("capture_becomes_library_image", capture and flow_host.library().assets[capture]["kind"] == "IMAGE")
        again = flow_tools.precondition(window(), "angelo_flow_run", arguments)
        check("unchanged_scene_reuses_capture", again == review and tree.nodes["Capture"].capture_asset == capture)
        text = approval_message("angelo_flow_run", dict(arguments, _review=review), flow_tools.usage_label(review))
        check("card_lists_first_step_and_defers_rest", "☑ Sheet ×2" in text["body"] and "after review" in text["body"]
              and "Runs 2 paid generations now" in text["body"])
        check("card_shows_estimate_with_date", "≈ $0.20" in text["body"] and "as of" in text["body"])
        call = call_for(review, arguments)
        ops, height = flow_chat.approval_layout(call, 16, 500, 600, wrap, 20)
        hits, distinct = render_ops(ops, "flows-approval-card.png", 640, 520)
        card = ops[0]
        check("approval_hits_inside_card", all(card[1] <= x and x + w <= card[1] + card[3] and card[2] <= y and y + h <= card[2] + card[4]
                                               for (x, y, w, h), _, _ in hits))
        check("approval_card_has_toggles_and_run_button", {"flow_toggle", "flow_stop", "approve", "decline", "flow_details"} <= {h[1] for h in hits})
        check("approval_card_draws_pixels", distinct > 8)
        flow_chat.dispatch(None, SimpleNamespace(pending={call.id: call}), "flow_stop", (call.id, "Sheet"))
        unstopped = review.selection(call.flow_selection)
        check("toggling_review_stop_puts_all_steps_on_the_card", unstopped["count"] == 4 and not unstopped["later"])
        flow_chat.dispatch(None, SimpleNamespace(pending={call.id: call}), "flow_toggle", (call.id, "Turnaround"))
        check("unticking_drops_dependents", review.selection(call.flow_selection)["count"] == 2)
        ctx["run1"] = start_run(arguments)
        live = flow_host.run_for(ctx["run1"][2].run_id)
        check("live_flow_summary_is_running", not live.finished and live.result()["status"] == "RUNNING")
        message = {"role": "activity", "tool": "angelo_flow_run", "result": {"run_id": live.id}}
        ops, _ = flow_chat.run_layout(message, 16, 500, 600, wrap, 20)
        hits, distinct = render_ops(ops, "flows-running-card.png", 640, 520)
        check("live_flow_card_says_running_with_stop", any(op[0] == "text" and "· Running" in op[1] for op in ops)
              and any(hit[1] == "flow_stop_run" for hit in hits) and distinct > 8)
        state["phase"] = "wait_first_run"
        return 0.3
    if phase == "wait_first_run":
        if not wait(ctx["run1"][2], "result1"):
            return 0.3
        result = ctx["result1"]
        posts = [url for url, post in calls if post]
        check("first_card_runs_only_sheet_variants", len(posts) == 2 and all(u.endswith("/interactions") for u in posts))
        check("run_stops_for_review_with_both_variants", result.get("review_stop", {}).get("node") == "Sheet"
              and len(result["review_stop"]["pick_from"]) == 2 and result["review_stop"]["remaining_steps"] == ["Turnaround", "Mesh"])
        assets = [flow_host.library().assets[i] for i in result["review_stop"]["pick_from"]]
        check("outputs_record_lineage", all(a["lineage"]["node"] == "Sheet" and a["lineage"]["key"] and
                                           set(a["lineage"]["reference_ids"]) == {ctx["sketch"], bpy.data.node_groups["Hero"].nodes["Capture"].capture_asset}
                                           for a in assets))
        tree = bpy.data.node_groups["Hero"]
        folder = next((f for f in flow_host.library().folders.values() if f["name"] == "Flow · Hero"), None)
        check("outputs_filed_in_flow_folder", folder is not None and all(a["folder"] == folder["id"] for a in assets))
        check("node_records_last_key_and_choice", tree.nodes["Sheet"].last_key == assets[0]["lineage"]["key"]
              and tree.nodes["Sheet"].chosen == assets[0]["id"])
        message = {"role": "activity", "tool": "angelo_flow_run", "result": dict(result, run_id=ctx["run1"][2].run_id)}
        flow_host.pump()
        ops, _ = flow_chat.run_layout(message, 16, 500, 600, wrap, 20)
        hits, distinct = render_ops(ops, "flows-run-card.png", 640, 520)
        check("run_card_offers_choice_and_attach", sum(1 for h in hits if h[1] == "flow_choose") == 2
              and sum(1 for h in hits if h[1] == "flow_attach") == 2 and distinct > 8)
        ctx["second"] = assets[1]["id"]
        chosen = flow_tools.execute(None, window(), "angelo_flow_choose", {"flow": "Hero", "expected_revision": current_revision(),
                                                                        "node": "Sheet", "asset_id": ctx["second"]})
        check("choose_sets_second_variant", tree.nodes["Sheet"].chosen == ctx["second"] and chosen["chosen"] == ctx["second"])
        try:
            flow_tools.precondition(window(), "angelo_flow_choose", {"flow": "Hero", "expected_revision": "000000000000",
                                                                    "node": "Sheet", "asset_id": ctx["second"]})
            check("stale_revision_refused", False)
        except FlowError:
            check("stale_revision_refused", True)
        ctx["run2"] = start_run({"flow": "Hero", "expected_revision": current_revision(), "scope": "DOWNSTREAM", "node": "Sheet"})
        lines = {l["node"]: l["action"] for l in ctx["run2"][0].plan["lines"]}
        check("downstream_reuses_sheet", lines == {"Sheet": "REUSE", "Turnaround": "GENERATE", "Mesh": "GENERATE"})
        state["phase"] = "wait_second_run"
        return 0.3
    if phase == "wait_second_run":
        if not wait(ctx["run2"][2], "result2", limit=120):
            return 0.5
        result = ctx["result2"]
        steps = {s["node"]: s for s in result["steps"]}
        check("second_run_done", result["status"] == "DONE" and set(steps) == {"Turnaround", "Mesh"})
        turnaround = flow_host.library().assets[steps["Turnaround"]["outputs"][0]]
        check("chosen_variant_feeds_next_step", turnaround["lineage"]["reference_ids"] == [ctx["second"]])
        mesh = flow_host.library().assets[steps["Mesh"]["outputs"][0]]
        check("mesh_saved_as_glb", mesh["kind"] == "3D" and mesh["status"] == "ready" and mesh["files"] == ["model.glb"])
        try:
            flow_tools.precondition(window(), "angelo_flow_run", {"flow": "Hero", "expected_revision": current_revision(), "scope": "ALL"})
            check("unchanged_flow_has_nothing_to_pay_for", False)
        except FlowError as error:
            check("unchanged_flow_has_nothing_to_pay_for", "Nothing in this run needs generating" in str(error))
        before = len(bpy.context.scene.objects)
        flow_host.pump()
        with bpy.context.temp_override(window=window()):
            placed = bpy.ops.angelo.flow_place(tree="Hero", node="Place")
        check("place_imports_into_scene", placed == {'FINISHED'} and len(bpy.context.scene.objects) > before)
        for obj in bpy.context.selected_objects:
            obj.location.x += 2.5  # Move the placed model so the next capture differs.
        # The placed model changes what Scene Capture sees, so the flow is now
        # legitimately out of date from the capture onward.
        capture_before = bpy.data.node_groups["Hero"].nodes["Capture"].capture_asset
        review = flow_tools.precondition(window(), "angelo_flow_run", {"flow": "Hero", "expected_revision": current_revision(), "scope": "ALL"})
        check("scene_change_refreshes_capture_and_flow", bpy.data.node_groups["Hero"].nodes["Capture"].capture_asset != capture_before
              and any(l["node"] == "Sheet" and l["action"] == "GENERATE" for l in review.plan["lines"]))
        posts_before = sum(1 for _, post in calls if post)
        flow_tools.execute(None, window(), "angelo_flow_build", {"flow": "Hero", "expected_revision": current_revision(),
                                                                 "nodes": [{"id": "Style", "fields": {"text": "realistic armor"}}]})
        inspected = flow_tools.execute(None, window(), "angelo_flow_inspect", {"flow": "Hero"})
        states = {n["id"]: n["state"] for n in inspected["nodes"]}
        check("edit_marks_step_stale_and_waits_after", states["Sheet"] == "STALE" and states["Turnaround"] == "WAITING")
        arguments = {"flow": "Hero", "expected_revision": current_revision(), "scope": "ALL"}
        review = flow_tools.precondition(window(), "angelo_flow_run", arguments)
        tree = bpy.data.node_groups["Hero"]
        tree.nodes["Style"].text = "changed after approval"
        run = flow_host.start_run(tree, review.selection(), owner="qa")
        check("edit_after_approval_generates_nothing", run.steps["Sheet"]["state"] == "FAILED"
              and "changed after approval" in run.steps["Sheet"]["error"] and sum(1 for _, p in calls if p) == posts_before)
        flow_ui._use_image(tree, "Turnaround", "references", ctx["sketch"])
        flow = flow_tree.tree_flow(tree)
        check("pick_image_adds_wired_image_node", sum(1 for l in flow["links"] if l["to"] == "Turnaround") == 2)
        bad = tree.links.new(tree.nodes["Style"].outputs["output"], tree.nodes["Sheet"].inputs["references"])
        flow_tree.mark_invalid_links(tree)
        check("wrong_kind_link_is_red_and_ignored", not bad.is_valid and len(flow_tree.tree_flow(tree)["links"]) == len(flow["links"]))
        tree.links.remove(bad)
        flow_host.pump()
        region = next(r for r in ctx["editor"].regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window(), area=ctx["editor"], region=region):
            bpy.ops.node.view_all()
        state["phase"] = "screenshot"
        return 1.0
    if phase == "screenshot":
        # Captures are done: give the flow the big 3D View area for a readable screenshot.
        area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
        area.type = 'NODE_EDITOR'
        area.spaces.active.tree_type = flow_nodes.TREE
        area.spaces.active.node_tree = bpy.data.node_groups["Hero"]
        ctx["big_editor"] = area
        state["phase"] = "frame_all"
        return 0.5
    if phase == "frame_all":
        area = ctx["big_editor"]
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window(), area=area, region=region):
            bpy.ops.node.select_all(action='DESELECT')
            bpy.ops.node.view_all()
        state["phase"] = "final_screenshot"
        return 1.0
    if phase == "final_screenshot":
        flow_host.pump()
        screenshot("flows-node-editor.png")
        check("no_unexpected_network", all(url.startswith("https://") and (".invalid/" in url or "googleapis" in url or "tripo3d" in url)
                                           for url, _ in calls))
        finish()
        return None
    return 0.3


bpy.app.timers.register(tick, first_interval=1.0)
