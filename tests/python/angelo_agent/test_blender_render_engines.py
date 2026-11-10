# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render engine switcher and AI render in the running app (1.0.8, ``render_engines``, ``ai_render_card``).

Real default startup (Director Mode with the chat) and real input (``Window.event_simulate``). No CLI, account or
inference. Checked against doc/angelo/render_engines.rst: Director's top bar has the engine button, as wide as its
widest label; a click opens Angelo's dropdown (``flow_menu`` ENGINES, its CARD panel and a tile on every row) with
EEVEE, Cycles Preview and Final and AI render, and leaves Flows unpressed; the Cycles Final row switches the scene
to Cycles with the Final preset, on the GPU when this machine has one, and a real render then runs there
(``render_device_session.used_since``); the button names the choice; the Render menu lists Render Engine and AI
Render; the AI render card opens in compose with the remembered style, says no account is connected and keeps
Render off; the real flow path builds this document's AI render flow and captures the camera's view into the
library (a step with no connected account then refuses, so nothing is paid); with a stand-in run (a fake account
and run, no network) Render moves the card to the corner while it runs, then offers Open in Media Studio, whose click
opens Media Studio.

Run through ``run_native_suites.py --gui --include test_blender_render_engines``; check ``render-engines.json`` for
``success`` and ``phase == "complete"``. The screenshots are the GUI proof (Director, the default view).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-render-engines-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "render-engines.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()
FAKE = "00000000-0000-4000-8000-0000000000aa"


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def press(x, y):
    import qa_input
    qa_input.press(window(), x, y)


def key(name):
    w = window()
    for value in ('PRESS', 'RELEASE'):
        w.event_simulate(type=name, value=value, x=w.width // 2, y=w.height // 2)


def typed(text):
    w = window()
    for char in text:
        w.event_simulate(type='A', value='PRESS', unicode=char, x=w.width // 2, y=w.height // 2)
        w.event_simulate(type='A', value='RELEASE', x=w.width // 2, y=w.height // 2)


def record_top_bar():
    from angelo_agent import chat_toggle
    original = chat_toggle.draw

    def draw(layout, context, *args, **kwargs):
        state["top_bar"] = (context.region.y, context.region.height)
        return original(layout, context, *args, **kwargs)
    chat_toggle.draw = draw


def layout_drawn():
    from angelo_agent import console
    areas = window().screen.areas
    return any(a.type == 'VIEW_3D' for a in areas) and any(console.is_chat(a) for a in areas) and all(
        r.width > 1 for a in areas for r in a.regions if r.type == 'WINDOW')


def engine_point():
    """Inside the engine button: Chat, Studio, Flows and the engine follow each other from Chat's left edge."""
    from angelo_agent import chat_toggle, top_bar_fit
    left, _right, bottom = chat_toggle.anchors[window().as_pointer()]
    fitted = top_bar_fit.widths[window().as_pointer()]
    x = left
    for each in ("chat", "studio", "flows", "engine"):
        _, units, unit = fitted[each]
        if each == "engine":
            return x + 0.5 * units * unit, bottom + state["top_bar"][1] / 2
        x += units * unit


def menu_row_point(value):
    from angelo_agent import flow_menu
    area = next(a for a in window().screen.areas if a.as_pointer() == flow_menu._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    scale = flow_menu._open["scale"]
    index = next(i for i, row in enumerate(flow_menu._open["rows"]) if row.get("value") == value)
    rx, ry, rw, rh = flow_menu._open["layout"]["rows"][index]
    return region.x + (rx + rw / 2) * scale, region.y + (ry + rh / 2) * scale


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


def teal_share(px, rect):
    import numpy
    x, y, w, h = (int(round(v)) for v in rect)
    flat = px[max(0, y):y + h, max(0, x):x + w].reshape(-1, 3)
    return float(numpy.mean(flat[:, 1] > flat[:, 0] + 25)) if len(flat) else 0.0


def card_hit(action):
    """Window pixels of the visible card's control ``action`` (card_host's plan, region pixels)."""
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    for (x, y, w, h), name, _value in active.plan.hits:
        if name == action:
            return region.x + x + w / 2, region.y + y + h / 2
    return None


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_RENDER_ENGINES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def stand_in_run():
    """A fake account and a run that finishes on its second look, so the card's phases run with no network."""
    from angelo_agent import ai_render, flow_host
    run = SimpleNamespace(id="stand-in", finished=False, steps={"AI render": {"assets": [], "error": None}})
    looks = {"count": 0}

    def run_for(run_id):
        looks["count"] += 1
        if looks["count"] > 2:
            run.finished, run.steps["AI render"]["assets"] = True, ["stand-in-asset"]
        return run if run_id == run.id else None
    state["patched"] = (ai_render.start, flow_host.run_for)
    ai_render.start = lambda window, style, entry, price: state.setdefault("started", (style, entry["id"])) and run
    flow_host.run_for = run_for


def restore():
    from angelo_agent import ai_render, flow_host
    if "patched" in state:
        ai_render.start, flow_host.run_for = state.pop("patched")


def tick():
    from angelo_agent import ai_render, card_host, director, flow_menu, render_engines, top_bar_fit
    phase = state["phase"]
    scene = bpy.context.scene
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        if phase == "boot":
            if not state.get("recording"):
                record_top_bar()
                state["recording"] = True
            if not ("top_bar" in state and qa_boot.started() and director.is_director(window()) and layout_drawn()):
                return .3
            seen = state.setdefault("since", {}).setdefault("layout_drawn", time.monotonic())
            if time.monotonic() - seen < 1.0:
                return .2
            scene.render.engine = render_engines.EEVEE
            ai_render.remember("ink wash storybook, dusk light", "", "")
            fitted = top_bar_fit.widths.get(window().as_pointer(), {})
            labels = render_engines.every_label()
            state["measured"]["engine_button"] = fitted.get("engine")
            checks["director_has_the_engine_button_as_wide_as_its_widest_label"] = bool(fitted.get("engine")) and (
                fitted["engine"][1] >= top_bar_fit.label_units(bpy.context, labels)[0] - 0.01
                or fitted["engine"][0] == "")
            capture("render-engines-0-director.png")
            press(*engine_point())
            state["phase"] = "menu"
            return .6
        if phase == "menu":
            if not wait(phase, lambda: flow_menu.is_open(window(), flow_menu.ENGINES) and flow_menu._open.get("layout")):
                return .2
            values = [row["value"] for row in flow_menu._open["rows"] if row["kind"] == "row"]
            # EEVEE and Cycles' presets first, AI render last; every other registered engine between them.
            checks["the_dropdown_lists_eevee_cycles_presets_and_ai_render"] = values[:3] == [
                "engine:BLENDER_EEVEE", "engine:CYCLES:PREVIEW", "engine:CYCLES:FINAL"] and values[-1] == "ai_render" \
                and all(value.startswith("engine:") for value in values[3:-1])
            checks["flows_is_not_pressed_for_the_engines"] = not flow_menu.is_open(window(), flow_menu.FLOWS)
            path = capture("render-engines-1-dropdown.png")
            px = pixels(path)
            area = next(a for a in window().screen.areas if a.as_pointer() == flow_menu._open["area"])
            region = next(r for r in area.regions if r.type == 'WINDOW')
            scale = flow_menu._open["scale"]
            tiles = []
            for row, (rx, ry, rw, rh) in zip(flow_menu._open["rows"], flow_menu._open["layout"]["rows"]):
                if row["kind"] == "row":
                    tiles.append(round(teal_share(px, (region.x + (rx + 8) * scale, region.y + (ry + (rh - 32) / 2)
                                                       * scale, 32 * scale, 32 * scale)), 3))
            state["measured"]["tiles"] = tiles
            checks["every_row_draws_its_glyph_tile"] = bool(tiles) and min(tiles) > 0.2
            press(*menu_row_point("engine:CYCLES:FINAL"))
            state["phase"] = "cycles"
            return .6
        if phase == "cycles":
            if not wait(phase, lambda: scene.render.engine == "CYCLES" and not flow_menu.is_open()):
                return .2
            from angelo_agent import render_device
            shown = render_device.shown(scene) or {}
            state["measured"]["device"] = {"shown": shown, "status": render_device.status(),
                                           "scene_device": scene.cycles.device}
            checks["cycles_final_is_applied"] = render_engines.preset_of(scene) == render_engines.FINAL
            checks["cycles_renders_on_the_gpu_when_one_is_set_up"] = (
                scene.cycles.device == "GPU" and bool(shown.get("gpu"))) or render_device.status().get("state") in {
                "no_gpu", "failed", "unavailable"}
            capture("render-engines-2-cycles-final.png")
            checks["the_button_names_the_choice"] = top_bar_fit.widths[window().as_pointer()]["engine"][0] in {
                render_engines.cycles_label(render_engines.FINAL), ""}
            # A real render on the chosen device: small, so the suite stays quick.
            from angelo_render_device import session
            scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 480, 270, 100
            samples, scene.cycles.samples = scene.cycles.samples, 32
            mark = session.count()
            began = time.monotonic()
            bpy.ops.render.render()
            state["measured"]["render_seconds"] = round(time.monotonic() - began, 2)
            scene.cycles.samples = samples
            used = session.used_since(mark)
            state["measured"]["render_used"] = used
            checks["a_real_render_ran_on_the_gpu"] = bool(used and used[1].get("gpu")) or not shown.get("gpu")
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.call_menu(name="TOPBAR_MT_render")
            state["phase"] = "render_menu"
            return .6
        if phase == "render_menu":
            capture("render-engines-3-render-menu.png")
            from bl_ui.space_topbar import TOPBAR_MT_render
            checks["the_render_menu_has_render_engine_and_ai_render"] = any(
                getattr(f, "__name__", "") == "render_menu" for f in TOPBAR_MT_render._dyn_ui_initialize())
            w = window()   # Away from the menu, then Escape for the submenu and the menu, one pass apart.
            w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=w.width // 4, y=w.height // 2)
            key('ESC')
            state["phase"] = "menu_closing"
            return .4
        if phase == "menu_closing":
            key('ESC')
            state["phase"] = "open_card"
            return .5
        if phase == "open_card":
            bpy.ops.angelo.ai_render()
            state["phase"] = "card"
            return .6
        if phase == "card":
            if not wait(phase, lambda: card_host.is_active("ai_render") and card_host._active.plan is not None):
                return .2
            card = card_host.active_card()
            checks["the_card_opens_in_compose_with_the_remembered_style"] = (
                card.phase == "compose" and card.style.text == "ink wash storybook, dusk light")
            checks["no_account_keeps_render_off_and_says_why"] = card_hit("render") is None and card_hit(
                "connect") is not None
            typed(", film grain")
            state["phase"] = "typed"
            return .4
        if phase == "typed":
            card = card_host.active_card()
            checks["typing_edits_the_style"] = card.style.text.endswith(", film grain")
            capture("render-engines-4-ai-card-compose.png")
            # The real flow path, with an account that is not connected: the flow is built and the camera captured
            # (local and free), then the step refuses because it can run with no account. Nothing is paid.
            from angelo_agent import flow_nodes
            from angelo_agent.flow_spec import FlowError
            entry = {"connection": FAKE, "id": "dola-seedream-5-0-pro-260628", "mode": "seedream_api", "kind": "IMAGE",
                     "name": "Seedream 5.0 Pro", "account": "Stand-in"}
            try:
                ai_render.start(window(), card.style.text, entry, ai_render.price(entry, ai_render.camera_aspect(scene)))
                refused = None
            except FlowError as error:
                refused = str(error)
            tree = ai_render.find_tree(bpy.data.node_groups)
            camera = tree.nodes.get("Camera") if tree else None
            state["measured"]["real_path"] = {"refused": refused, "tree": tree.name if tree else None,
                                              "capture": getattr(camera, "capture_asset", None)}
            from angelo_agent import flow_capture
            captured = bool(camera and camera.capture_asset) or (tree is not None and (tree.name, "Camera")
                                                                  in flow_capture._captures)
            checks["the_real_path_builds_the_flow_and_captures_the_camera"] = bool(
                tree and tree.bl_idname == flow_nodes.TREE and captured)
            checks["an_unconnected_account_is_refused_so_nothing_runs"] = refused is not None
            # The stand-in: a ready account and a run that ends on its own.
            card.choices = [dict(entry, ready=True)]
            card.entry, card.error = card.choices[0], ""
            card._version += 1
            stand_in_run()
            card_host.refresh("ai_render")
            state["phase"] = "render"
            return .5
        if phase == "render":
            point = card_hit("render")
            if not wait(phase, lambda: point is not None):
                return .2
            press(*point)
            state["phase"] = "running"
            return .3
        if phase == "running":
            card = card_host.active_card()
            if not wait(phase, lambda: card is not None and card.phase in {"running", "done"}):
                return .2
            checks["render_moves_the_card_to_the_corner_and_frees_the_view"] = not card.modal
            if card.phase == "running":
                capture("render-engines-5-ai-card-running.png")
            state["phase"] = "done"
            return .4
        if phase == "done":
            card = card_host.active_card()
            if not wait(phase, lambda: card is not None and card.phase == "done"):
                return .2
            checks["the_click_started_with_the_style_typed"] = (state.get("started") or ("",))[0].endswith(
                ", film grain")
            capture("render-engines-6-ai-card-done.png")
            press(*card_hit("open"))
            state["phase"] = "studio"
            return .8
        if phase == "studio":
            from angelo_agent import studio_toggle
            if not wait(phase, lambda: studio_toggle.opened_window(bpy.context.window_manager.windows) is not None
                        or not card_host.is_active("ai_render"), limit=8):
                return .2
            checks["open_in_media_studio_opens_it"] = not card_host.is_active("ai_render")
            restore()
            capture("render-engines-7-media-studio.png")
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        restore()
        finish(error)
        return None
    return .2


import qa_input  # noqa: E402  (synthetic clicks; after_clicks)
# Never a phase while a click it sent is still on its way (qa_input.after_clicks).
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
