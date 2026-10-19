# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""BILLED one-shot live Generation Flow check (in-app driver).

Builds one flow through the agent tool, then runs it ONCE with real providers:
two reference images (front, side) and a shared negative-prompt Text node feed
a third image, which feeds a Tripo 3D model that is placed in the scene. Every
generation goes through the normal path (FlowRun -> StudioJobs); nothing is
retried. Keys load only through the app's saved-key path; none are read,
printed or written. Refuses unless ANGELO_QA_LIVE_GENAI=1,
--authorize-billed-generation and explicit profile/state/library directories
are given, and unless this checkout has the Gemini JPEG fix. A run is billed
three Nano Banana 2 images at 1K and one Tripo 3.1 textured model, at the
prices in flow_prices.py. From the repository root, with the GUI app::

    ANGELO_QA_LIVE_GENAI=1 ANGELO_CONNECTIONS_DIR=<profile>/connections \
    ANGELO_STATE_DIR=<tmp>/state ANGELO_LIBRARY_DIR=<profile>/library ANGELO_DISABLE_DEFAULT_CHAT=1 \
    BLENDER_USER_RESOURCES=<tmp>/user TMPDIR=<tmp>/tmp/ \
    <Angelo> --factory-startup --disable-autoexec --no-window-focus \
        --python tests/python/angelo_agent/live_flow_smoke.py -- --authorize-billed-generation \
        --gemini-connection UUID --tripo-connection UUID --out <tmp>/out [--blend <path>.blend]

Inspect <out>/live-flow.json (checks, step assets, the approval card text) and
the screenshots; a zero exit status alone is not proof.
"""

import json
import os
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
STARTED = time.monotonic()


def arg(name):
    return ARGS[ARGS.index(name) + 1] if name in ARGS and ARGS.index(name) + 1 < len(ARGS) else ""


OUT = Path(arg("--out") or Path(tempfile.gettempdir(), "angelo-live-flow"))
OUT.mkdir(parents=True, exist_ok=True)
BLEND = Path(arg("--blend")) if arg("--blend") else None
report = {"script": "live_flow_smoke.py", "checks": {}, "steps": {}, "errors": [], "complete": False,
          "binary": bpy.app.binary_path, "library": os.environ.get("ANGELO_LIBRARY_DIR", "")}
run = {"phase": "keys", "pending": None}


def log(text):
    print("[live-flow %7.1fs] %s" % (time.monotonic() - STARTED, text), flush=True)


def check(name, value):
    report["checks"][name] = bool(value)
    log(("PASS " if value else "FAIL ") + name)


def refusal():
    if os.environ.get("ANGELO_QA_LIVE_GENAI") != "1" or "--authorize-billed-generation" not in ARGS:
        return "ANGELO_QA_LIVE_GENAI=1 and --authorize-billed-generation are required"
    if bpy.app.background:
        return "Saved keys load only in the GUI app"
    for name in ("ANGELO_CONNECTIONS_DIR", "ANGELO_STATE_DIR", "ANGELO_LIBRARY_DIR"):
        if not os.environ.get(name):
            return name + " must be explicit"
    try:
        uuid.UUID(arg("--gemini-connection")), uuid.UUID(arg("--tripo-connection"))
    except ValueError:
        return "--gemini-connection and --tripo-connection must be connection UUIDs"
    return None


REASON = refusal()
if not REASON:
    import addon_utils
    from angelo_agent import ui
    ui._authentication.start = lambda *args, **kwargs: None
    ui._authentication.pump = lambda: []
    ui.refresh_connected_models = lambda *args, **kwargs: None
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    from angelo_agent import flow_chat, flow_host, flow_nodes, flow_tools, flow_tree, saved_keys_ui
    from angelo_agent.flow_approvals import message as approval_message
    from angelo_agent.flow_spec import revision
    from angelo_agent.saved_keys import FAILURES
    from angelo_agent.studio_generation import checked_glb, validated_image
    import angelo_agent.studio_generation as _generation
    if not Path(_generation.__file__).resolve().is_relative_to(ROOT.resolve()):
        REASON = "angelo_agent was loaded from " + _generation.__file__ + ", not this checkout"
    elif '"image/jpeg"' not in Path(_generation.__file__).read_text():
        REASON = "This checkout lacks the Gemini JPEG fix; Gemini image requests would fail"
CONNECTIONS = {} if REASON else {"gemini": arg("--gemini-connection"), "tripo": arg("--tripo-connection")}


def window():
    return bpy.context.window_manager.windows[0]


def keys_ready():
    """True when both keys loaded and verified, False on failure, None while waiting."""
    for name, identity in CONNECTIONS.items():
        record = ui.connections().records.get(identity)
        if record is None:
            raise RuntimeError(name + " connection is missing from the profile")
        if not saved_keys_ui.available():
            raise RuntimeError("Keychain unavailable: " + saved_keys_ui.unavailable_reason())
        kind, outcome = saved_keys_ui._vault.state.get(identity, (None, None))
        if kind == "load" and (outcome in FAILURES or outcome in {"absent", "rejected"}):
            raise RuntimeError(f"{name} saved key did not load ({outcome}): {saved_keys_ui.missing_key_status(identity)}")
        if not ui._memory_keys.get(identity):
            return None
        result = ui._api_connections.results.get(identity)
        if result is None or result.state == "checking":
            return None
        if result.state != "verified":
            raise RuntimeError(f"{name} connection check: {result.message}")
    return True


def pick(kind, connection, preferred):
    ready = [e for e in flow_host.catalog_entries(kind) if e["connection"] == connection and e["ready"]]
    for model in preferred:
        if any(e["id"] == model for e in ready):
            return model
    raise RuntimeError(f"No ready {kind} model on the connection; available: {[e['id'] for e in ready]}")


def screenshot(name):
    with bpy.context.temp_override(window=window(), screen=window().screen):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    report.setdefault("captures", []).append(str(OUT / name))


NEGATIVE = "Avoid: text, letters, watermark, logo, blurry, extra limbs, cropped body, busy background."


def step():
    phase = run["phase"]
    if phase == "keys":
        ready = keys_ready()
        if ready is None:
            if time.monotonic() - STARTED > 90:
                raise RuntimeError("Keys did not load and verify within 90 s")
            return 0.5
        run["image_model"] = pick("IMAGE", CONNECTIONS["gemini"], ["gemini-3.1-flash-image"])
        run["mesh_model"] = pick("3D", CONNECTIONS["tripo"], ["v3.1-20260211", "P2-20260801"])
        report["models"] = {"image": run["image_model"], "mesh": run["mesh_model"]}
        image = lambda prompt: {"connection_id": CONNECTIONS["gemini"], "model": run["image_model"], "prompt": prompt,
                                "options": {"aspect": "1:1", "resolution": "1K"}}
        result = flow_tools.execute(None, window(), "angelo_flow_build", {"flow": "Live Robot", "expected_revision": None, "nodes": [
            {"id": "Negative", "type": "TEXT", "label": "Shared negative prompt", "fields": {"text": NEGATIVE}},
            {"id": "Front", "type": "GENERATE_IMAGE", "fields": image(
                "Front view of a small teal toy robot with round white eyes and stubby legs, full body, centered, plain white background, soft studio light.")},
            {"id": "Side", "type": "GENERATE_IMAGE", "fields": image(
                "Side view of a small teal toy robot with round white eyes and stubby legs, full body, centered, plain white background, soft studio light.")},
            {"id": "Hero", "type": "GENERATE_IMAGE", "fields": image(
                "Three-quarter view of exactly the toy robot shown in the reference images: same shape, colors and proportions. "
                "Full body, centered, plain white background, soft studio light, suitable for 3D reconstruction.")},
            {"id": "Model", "type": "GENERATE_3D", "fields": {"connection_id": CONNECTIONS["tripo"], "model": run["mesh_model"],
                                                               "options": {"texture": True}}},
            {"id": "Place", "type": "PLACE_IN_SCENE"}], "links": [
            {"from": "Negative", "to": "Front", "input": "prompt"}, {"from": "Negative", "to": "Side", "input": "prompt"},
            {"from": "Negative", "to": "Hero", "input": "prompt"},
            {"from": "Front", "to": "Hero", "input": "references"}, {"from": "Side", "to": "Hero", "input": "references"},
            {"from": "Hero", "to": "Model", "input": "reference"}, {"from": "Model", "to": "Place", "input": "model"}]})
        check("flow_built", result["created"] and len(bpy.data.node_groups["Live Robot"].nodes) == 6)
        area = max((a for a in window().screen.areas if a.type != 'VIEW_3D'), key=lambda a: a.width * a.height)
        area.type = 'NODE_EDITOR'
        area.spaces.active.tree_type = flow_nodes.TREE
        area.spaces.active.node_tree = bpy.data.node_groups["Live Robot"]
        run["editor"] = area
        run["phase"] = "plan"
        return 0.3
    if phase == "plan":
        tree = bpy.data.node_groups["Live Robot"]
        arguments = {"flow": "Live Robot", "expected_revision": revision(flow_tree.tree_flow(tree)), "scope": "ALL"}
        review = flow_tools.precondition(window(), "angelo_flow_run", arguments)
        selection = review.selection()
        text = approval_message("angelo_flow_run", dict(arguments, _review=review), flow_tools.usage_label(review))
        report["approval_card"] = text
        log("APPROVAL CARD\n" + text["title"] + "\n" + text["body"])
        check("one_card_lists_all_four_generations", selection["count"] == 4 and not selection["later"])
        call = SimpleNamespace(id="live-flow-run", name="angelo_flow_run", precondition=review, approval=True,
                               arguments=arguments, usage_label=flow_tools.usage_label(review),
                               flow_selection={"excluded": set(), "stops": {}})
        # The operator authorized this billed run (--authorize-billed-generation).
        run["pending"] = flow_tools.execute(SimpleNamespace(executing_call=call), window(), "angelo_flow_run", arguments)
        run["started"] = time.monotonic()
        run["phase"] = "watch"
        return 1.0
    if phase == "watch":
        live = flow_host.run_for(run["pending"].run_id)
        states = {n: s["state"] for n, s in live.steps.items()}
        if states != run.get("last_states"):
            run["last_states"] = states
            log("steps " + json.dumps(states))
        result = run["pending"].poll()
        if result is None:
            if time.monotonic() - run["started"] > 1800:
                raise RuntimeError("The run did not finish within 30 minutes; stopping local monitoring (no retry)")
            return 2.0
        report["run_result"] = result
        log("run finished: " + result["status"] + f" in {time.monotonic() - run['started']:.0f} s")
        run["phase"] = "verify"
        return 0.5
    if phase == "verify":
        result, library = report["run_result"], flow_host.library()
        steps = {s["node"]: s for s in result["steps"]}
        check("run_done_with_four_steps", result["status"] == "DONE" and set(steps) == {"Front", "Side", "Hero", "Model"})
        check("no_automatic_retries", result["automatic_retries"] == 0 and result["generations_started"] == 4)
        assets = {name: library.assets[steps[name]["outputs"][0]] for name in steps if steps[name]["outputs"]}
        for name, asset in assets.items():
            report["steps"][name] = {k: asset.get(k) for k in ("id", "kind", "model", "status", "files", "thumbnail", "folder", "lineage")}
            report["steps"][name]["prompt"] = asset.get("prompt")
        for name in ("Front", "Side", "Hero"):
            data = library.checked_file(assets[name], assets[name]["files"][0]).read_bytes()
            validated_image(data)
            report["steps"][name]["bytes"] = len(data)
        check("images_decodable", all(report["steps"][n].get("bytes") for n in ("Front", "Side", "Hero")))
        check("negative_text_in_every_image_prompt", all(NEGATIVE in assets[n]["prompt"] for n in ("Front", "Side", "Hero")))
        check("hero_reads_both_references", assets["Hero"]["lineage"]["reference_ids"] == [assets["Front"]["id"], assets["Side"]["id"]])
        check("model_reads_hero", assets["Model"]["lineage"]["reference_ids"] == [assets["Hero"]["id"]])
        checked_glb(library.checked_file(assets["Model"], "model.glb").read_bytes())
        check("model_is_valid_glb", assets["Model"]["files"] == ["model.glb"])
        folder = next((f for f in library.folders.values() if f["name"] == "Flow · Live Robot"), None)
        check("all_outputs_in_flow_folder", folder and all(a["folder"] == folder["id"] for a in assets.values()))
        listed = {a["id"] for a in library.visible("IMAGE", folder["id"]) + library.visible("3D", folder["id"])}
        check("library_lists_all_outputs", {a["id"] for a in assets.values()} <= listed)
        report["library_folder"] = folder
        run["phase"] = "place"
        return 0.5
    if phase == "place":
        before = {o.name for o in bpy.context.scene.objects}
        flow_host.pump()
        with bpy.context.temp_override(window=window()):
            placed = bpy.ops.angelo.flow_place(tree="Live Robot", node="Place")
        added = [o.name for o in bpy.context.scene.objects if o.name not in before]
        report["placed_objects"] = added
        check("model_placed_in_scene", placed == {'FINISHED'} and any(bpy.data.objects[n].type == 'MESH' for n in added))
        cube = bpy.data.objects.get("Cube")
        if cube:
            cube.hide_viewport = True  # So the placed model is visible in the screenshot.
        view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in view.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window(), area=view, region=region):
            bpy.ops.view3d.view_selected()
        flow_host.pump()
        run["phase"] = "previews"
        return 1.0
    if phase == "previews":
        flow_host.pump()
        outputs = [report["steps"][n]["id"] for n in ("Front", "Side", "Hero", "Model")]
        check("flow_has_previews_for_all_outputs", all(flow_host.preview_icon(i) for i in outputs))
        editor = run["editor"]
        region = next(r for r in editor.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window(), area=editor, region=region):
            bpy.ops.node.select_all(action='DESELECT')
            bpy.ops.node.view_all()
        run["phase"] = "screens"
        return 1.5
    if phase == "screens":
        screenshot("live-flow-scene.png")
        message = {"role": "activity", "tool": "angelo_flow_run", "result": report["run_result"]}
        ops, _ = flow_chat.run_layout(message, 16, 700, 680, lambda v, w, s: [(v, 0)], 20)
        report["run_card_thumbnails"] = sum(1 for op in ops if op[0] == "thumb")
        check("run_card_shows_every_output", report["run_card_thumbnails"] == 4)
        view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
        view.type = 'NODE_EDITOR'
        view.spaces.active.tree_type = flow_nodes.TREE
        view.spaces.active.node_tree = bpy.data.node_groups["Live Robot"]
        run["big"] = view
        run["phase"] = "big_editor"
        return 0.5
    if phase == "big_editor":
        region = next(r for r in run["big"].regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window(), area=run["big"], region=region):
            bpy.ops.node.view_all()
        run["phase"] = "final"
        return 1.5
    if phase == "final":
        flow_host.pump()
        screenshot("live-flow-node-editor.png")
        run["big"].type = 'VIEW_3D'
        if BLEND:
            BLEND.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(BLEND), copy=True)
            report["blend"] = str(BLEND)
        return finish()
    return 0.5


def finish():
    report["complete"] = True
    report["success"] = bool(report["checks"]) and all(report["checks"].values()) and not report["errors"]
    report["elapsed_s"] = round(time.monotonic() - STARTED, 1)
    text = json.dumps(report, indent=2, default=str)
    secrets = [ui._memory_keys.get(i, "") for i in CONNECTIONS.values()] if not REASON else []
    report["key_material_absent"] = not any(s and s in text for s in secrets)
    text = json.dumps(report, indent=2, default=str)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    (OUT / "live-flow.json").write_text(text)
    log("DONE success=%s report=%s" % (report["success"], OUT / "live-flow.json"))
    bpy.ops.wm.quit_blender()
    return None


def tick():
    try:
        return step()
    except Exception:
        report["errors"].append({"phase": run["phase"], "traceback": traceback.format_exc()[-3000:]})
        log("driver error in %s; no new submission\n%s" % (run["phase"], traceback.format_exc()[-1500:]))
        if run.get("pending") is not None and run["phase"] == "watch":
            run["pending"].cancel()
        return finish()


if REASON:
    report["refused"] = REASON
    log("REFUSED: " + REASON)
    (OUT / "live-flow.json").write_text(json.dumps(report, indent=2))
    bpy.ops.wm.quit_blender()
else:
    bpy.context.preferences.view.show_splash = False
    bpy.app.timers.register(tick, first_interval=1.0)
