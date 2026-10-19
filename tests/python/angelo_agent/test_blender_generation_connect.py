# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""With no generation account, Media Studio and Generation Flows say what's missing and point to AI Providers.

Fresh accounts folder, no generation connection. Walked and captured: Media
Studio's Create panel (the missing account named once, then one "Connect in AI
Providers", whose click opens AI Providers), AI Enhance's refusal (add a Gemini
API key in AI Providers) with the Studio still taking clicks, the flow buttons'
own operator (its tooltip and its click), a Generation Flow with generator nodes and a Subflow
use (each node names the missing account and offers Connect in AI Providers,
as does the Flow sidebar), a run attempt (a popup naming the steps that need an
account, with the same button), the recipe's own nodes, and the same flow once
an image account is connected (the image step then only asks for a model
choice). The image account is an in-memory fixture key: no provider, network
or paid request is ever called. The add-on is the app's own (nothing is
imported from this checkout), so the same suite shows an older app's behaviour.

Run through ``run_native_suites.py --gui --include test_blender_generation_connect``;
check ``generation-connect.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy
import gpu

assert not bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup GUI instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-generation-connect-qa"))
OUT.mkdir(parents=True, exist_ok=True)
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None  # No coding-agent status checks: generation only.
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
from angelo_agent import flow_host, flow_nodes, flow_tools, flow_tree, flow_ui, preferences_ui, studio  # noqa: E402
from angelo_agent.editor_registry import GENERATIVE_AI  # noqa: E402
from angelo_agent.flow_spec import revision  # noqa: E402
from angelo_agent.view_png import encode_png  # noqa: E402

REPORT = OUT / "generation-connect.json"
STEPS = {"Concept", "Model", "Hero/Sheet", "Hero/Mesh"}
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": [],
         "fixture": "No generation account, then one in-memory fixture image key; no provider, network or paid request"}
ctx, drawn = {}, []
started = time.monotonic()

if hasattr(flow_nodes, "connect_notice"):  # Record what each node, use and the sidebar drew.
    _notice = flow_nodes.connect_notice

    def _recorded(layout, tree, name=None, *args, **kwargs):
        shown = _notice(layout, tree, name, *args, **kwargs)
        drawn.append((tree.name, name, shown))
        del drawn[:-200]
        return shown
    flow_nodes.connect_notice = _recorded


def window():
    return bpy.context.window_manager.windows[0]


def editor():
    return next(a for a in window().screen.areas if a.as_pointer() == ctx["area"])


def main_region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def _grab_studio():
    """After the Studio canvas draws, keep its pixels when asked (a window screenshot can show an older frame)."""
    if ctx.get("grab") is not None and bpy.context.area and bpy.context.area.as_pointer() == ctx.get("area"):
        width, height = bpy.context.region.width, bpy.context.region.height
        pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE')
        pixels.dimensions = width * height * 4
        ctx["grab"] = (bytes(pixels), width, height)


def capture_studio(name):
    """The Studio canvas as drawn now, as the person sees it in that editor."""
    ctx["grab"] = ()
    editor().tag_redraw()
    with bpy.context.temp_override(window=window(), area=editor(), region=main_region(editor())):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    grabbed, ctx["grab"] = ctx["grab"], None
    assert grabbed, "The Studio canvas did not draw"
    (OUT / name).write_bytes(encode_png(*grabbed))
    state["screenshots"].append(str(OUT / name))


def studio_words():
    """The Create panel's labels and button texts as the Studio draws them now."""
    words, draw_label, draw_button = [], studio.label, studio.button

    def label(text, *args, **kwargs):
        words.append(text)
        return draw_label(text, *args, **kwargs)

    def button(state_, rect, text, *args, **kwargs):
        words.append(text)
        return draw_button(state_, rect, text, *args, **kwargs)
    studio.label, studio.button = label, button
    try:
        capture_studio("b1-media-studio-no-account-canvas.png")
    finally:
        studio.label, studio.button = draw_label, draw_button
    return words


def studio_click(action):
    """A real click (window events) on the Studio canvas's drawn control."""
    area = editor()
    region = main_region(area)
    rect = next((r for r, a, _ in studio.view(area)["hits"] if a == action), None)
    if rect is None:
        state["observed"].setdefault("missing_controls", []).append(action)
        return
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def build(flow, **edit):
    tree = bpy.data.node_groups.get(flow)
    arguments = dict(edit, flow=flow, expected_revision=revision(flow_tree.tree_flow(tree)) if tree else None)
    flow_tools.precondition(window(), "angelo_flow_build", arguments)
    return flow_tools.execute(None, window(), "angelo_flow_build", arguments)


def show(tree_name):
    area = editor()
    space = area.spaces.active
    space.tree_type, space.node_tree, space.show_region_ui = flow_nodes.TREE, bpy.data.node_groups[tree_name], True
    flow_host.tree_changed(space.node_tree)
    flow_host.pump()
    try:
        next(r for r in area.regions if r.type == 'UI').active_panel_category = "Flow"
    except (AttributeError, TypeError):
        pass
    with bpy.context.temp_override(window=window(), area=area, region=main_region(area)):
        bpy.ops.node.select_all(action='DESELECT')
        bpy.ops.node.view_all()


def needs(tree_name):
    return dict((flow_host._cache.get(tree_name) or {}).get("needs") or {})


def notices(tree_name):
    """The latest drawn result per node name in a tree (None: the sidebar)."""
    return {name: shown for tree, name, shown in drawn if tree == tree_name}


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_GENERATION_CONNECT", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 240, "Generation-connect fixture timed out in " + state["phase"]
        checks, seen, phase = state["checks"], state["observed"], state["phase"]
        if phase == "boot":  # Flows first: the Studio's refusal must not be what the status bar still shows.
            area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
            ctx["area"] = area.as_pointer()
            area.type = 'NODE_EDITOR'
            area.spaces.active.tree_type = flow_nodes.TREE
            build("Turnaround", nodes=[
                {"id": "Style", "type": "TEXT", "fields": {"text": "clean toon style"}},
                {"id": "Sheet", "type": "GENERATE_IMAGE", "fields": {"prompt": "Character sheet"}},
                {"id": "Mesh", "type": "GENERATE_3D", "fields": {}}], links=[
                {"from": "Style", "to": "Sheet", "input": "prompt"}, {"from": "Sheet", "to": "Mesh", "input": "reference"}],
                recipe={"inputs": ["Style"], "output": "Mesh"})
            build("Characters", nodes=[
                {"id": "Brief", "type": "TEXT", "fields": {"text": "a friendly robot"}},
                {"id": "Concept", "type": "GENERATE_IMAGE", "fields": {"prompt": "Concept art"}},
                {"id": "Model", "type": "GENERATE_3D", "fields": {}},
                {"id": "Place", "type": "PLACE_IN_SCENE"},
                {"id": "Hero", "type": "SUBFLOW", "fields": {"recipe": "Turnaround"}}], links=[
                {"from": "Brief", "to": "Concept", "input": "prompt"}, {"from": "Concept", "to": "Model", "input": "reference"},
                {"from": "Model", "to": "Place", "input": "model"}])
            show("Characters")
            state["phase"] = "flow_settle"
            return .6
        if phase == "flow_settle":
            show("Characters")  # A tick after showing the tree, so node sizes are known.
            state["phase"] = "flow_shot"
            return .6
        if phase == "flow_shot":
            flow_host.pump()
            capture("b4-flow-no-account.png")
            seen.update(flow_needs=needs("Characters"), flow_notices=notices("Characters"))
            shown = seen["flow_notices"]
            checks["flow_needs_name_every_generator"] = seen["flow_needs"] == {step: "connect" for step in STEPS}
            checks["nodes_use_and_sidebar_point_to_ai_providers"] = all(shown.get(n) for n in ("Concept", "Model", "Hero", None))
            area = editor()
            region = main_region(area)
            x, y = region.x + region.width // 2, region.y + region.height // 2
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
            state["pointer"] = (x, y)
            with bpy.context.temp_override(window=window(), area=area, region=region):
                seen["run_attempt"] = sorted(bpy.ops.angelo.flow_run('INVOKE_DEFAULT', tree="Characters", scope="ALL"))
            state["phase"] = "run_shot"
            return .6
        if phase == "run_shot":
            capture("b5-flow-run-attempt.png")
            shown = getattr(flow_ui, "connect_notices", [])
            seen["run_notice"] = shown[-1] if shown else None
            checks["run_attempt_names_the_steps_and_offers_ai_providers"] = (
                seen["run_attempt"] == ["CANCELLED"] and bool(shown) and len(shown[-1]) == len(STEPS)
                and {"Concept", "Model"} <= set(shown[-1]))
            checks["run_attempt_starts_nothing"] = not flow_host.active_runs() and not flow_host.jobs().active
            x, y = state["pointer"]
            window().event_simulate(type='ESC', value='PRESS', x=x, y=y)
            window().event_simulate(type='ESC', value='RELEASE', x=x, y=y)
            show("Turnaround")
            state["phase"] = "recipe_shot"
            return .8
        if phase == "recipe_shot":
            flow_host.pump()
            capture("b6-recipe-no-account.png")
            shown = notices("Turnaround")
            seen["recipe_notices"] = shown
            checks["recipe_steps_point_to_ai_providers"] = bool(shown.get("Sheet")) and bool(shown.get("Mesh"))
            from angelo_agent.api_connections import CheckResult
            from angelo_agent.provider_registry import SPECS
            record = ui.connections().add("GOOGLE", "API_KEY", "Fixture Gemini", group="GENAI")
            ctx["fixture"] = record["id"]
            ui._memory_keys[record["id"]] = "fixture-key-not-a-real-account"
            ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS[record["mode"]]["models"])
            drawn.clear()
            show("Characters")
            state["phase"] = "image_account_shot"
            return .8
        if phase == "image_account_shot":
            flow_host.pump()
            capture("b7-flow-image-account-connected.png")
            seen.update(after_needs=needs("Characters"), after_notices=notices("Characters"))
            checks["a_connected_image_account_leaves_only_a_choice"] = (
                seen["after_needs"].get("Concept") == "choose" and seen["after_needs"].get("Model") == "connect"
                and seen["after_notices"].get("Concept") is False and seen["after_notices"].get("Model") is True)
            # Back to no generation account for Media Studio, in the same area.
            ui.connections().remove(ctx["fixture"])
            ui._memory_keys.pop(ctx["fixture"], None)
            ui._api_connections.results.pop(ctx["fixture"], None)
            area = editor()
            with bpy.context.temp_override(window=window(), area=area, region=main_region(area)):
                bpy.ops.angelo.editor_type(ui_type=GENERATIVE_AI)
            state["phase"] = "studio"
            return .5
        if phase == "studio":
            view = studio.view(editor())
            view["collapsed"] = False
            editor().tag_redraw()
            state["phase"] = "studio_shot"
            return .5
        if phase == "studio_shot":
            view = studio.view(editor())
            capture("b1-media-studio-no-account.png")
            words = studio_words()
            seen["create_panel_words"] = words
            checks["create_panel_says_connect_in_ai_providers_once"] = (
                words.count("Connect in AI Providers") == 1 and "No OpenAI account yet" in words)
            model = studio.selected(view)
            seen["studio_model"] = {"name": (model or {}).get("name"), "account": (model or {}).get("account"),
                                    "ready": bool((model or {}).get("ready"))}
            checks["studio_offers_connect_in_ai_providers"] = any(a == "settings" for _, a, _ in view["hits"])
            checks["studio_model_not_ready_without_an_account"] = not seen["studio_model"]["ready"]
            view["prompt"] = "A small brass robot"
            try:
                studio.enhancement_binding()
                seen["enhance_refusal"] = None
            except ValueError as error:
                seen["enhance_refusal"] = str(error)
            checks["ai_enhance_asks_for_a_gemini_key_in_ai_providers"] = seen["enhance_refusal"] == "Add a Gemini API key in AI Providers"
            editor().tag_redraw()  # AI Enhance is clickable once the prompt has text.
            state["phase"] = "enhance_click"
            return .5
        if phase == "enhance_click":
            studio_click("enhance")
            state["phase"] = "enhance_shot"
            return .6
        if phase == "enhance_shot":
            view = studio.view(editor())
            seen["studio_at_shot"] = {"error": studio.last_error, "notice": view.get("notice"),
                                      "hits": sorted({a for _, a, _ in view["hits"]})}
            capture("b2-ai-enhance-no-key.png")  # The whole window: no traceback, no stale error.
            capture_studio("b2-ai-enhance-no-key-canvas.png")  # The Studio as drawn after the click.
            seen["enhance_notice"] = studio.view(editor()).get("notice")
            checks["studio_says_why_in_its_own_notice"] = seen["enhance_notice"] == "Add a Gemini API key in AI Providers"
            # Generate needs a connected model: without one it is drawn disabled, not clickable.
            checks["generate_is_not_offered_without_an_account"] = not any(
                a == "generate" for _, a, _ in studio.view(editor())["hits"])
            seen["collapsed_before"] = studio.view(editor())["collapsed"]
            studio_click("collapse")  # The Studio still takes clicks after AI Enhance refused.
            state["phase"] = "studio_alive"
            return .5
        if phase == "studio_alive":
            checks["studio_still_takes_clicks_after_the_refusal"] = studio.view(editor())["collapsed"] != seen["collapsed_before"]
            if studio.view(editor())["collapsed"]:
                studio.view(editor())["collapsed"] = False
                editor().tag_redraw()
            state["phase"] = "studio_settings"
            return .5
        if phase == "studio_settings":
            studio_click("settings")
            state["phase"] = "studio_prefs"
            return .8
        if phase == "studio_prefs":
            prefs = next((w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)
            checks["studio_button_opens_ai_providers"] = prefs is not None and preferences_ui.is_selected()
            if prefs is not None:
                capture("b3-studio-connect-opens-ai-providers.png", prefs)
                with bpy.context.temp_override(window=prefs):
                    bpy.ops.wm.window_close()
            state["phase"] = "flow_connect"
            return .6
        if phase == "flow_connect":
            # The flow buttons' own operator: its tooltip says what the click does, and the click opens AI Providers.
            seen["flow_connect_tooltip"] = bpy.ops.angelo.flow_connect.get_rna_type().description
            with bpy.context.temp_override(window=window()):
                seen["flow_connect_result"] = sorted(bpy.ops.angelo.flow_connect('INVOKE_DEFAULT'))
            state["phase"] = "flow_connect_prefs"
            return .8
        if phase == "flow_connect_prefs":
            prefs = next((w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)
            checks["flow_button_tooltip_says_what_it_does"] = (
                "AI Providers" in seen["flow_connect_tooltip"] and "generation account" in seen["flow_connect_tooltip"])
            checks["flow_button_opens_ai_providers"] = (
                seen["flow_connect_result"] == ["FINISHED"] and prefs is not None and preferences_ui.is_selected())
            finish()
            return None
        return .3
    except Exception as error:  # noqa: BLE001  (the receipt records it)
        finish(error)
        return None


bpy.types.SpaceConsole.draw_handler_add(_grab_studio, (), 'WINDOW', 'POST_PIXEL')
bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
