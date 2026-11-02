# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An installed render engine add-on in the running app (1.0.8, the installed-engines branch).

Real default startup (Director with the chat) and real input. No CLI, account, network or inference. A small render
engine that fills its image with one colour (``installed_engine_fixture``, defined in this test folder, so outside
the shipped scripts exactly like a person's add-on such as V-Ray or BlendLuxCore) is registered once Angelo has
started. Checked against doc/angelo/render_engines.rst, "Installed engines": the engine dropdown lists it after
Blender's own engines with its add-on's name; its row makes it the scene's engine; F12 renders through it (the
result holds its colour); a render job's admission names it as extension code needing approval
(``render_extensions.inspect``); ``angelo_scene_settings`` lists it in ``installed_engines``; Auto asks before a
scene program that sets it, while one that sets Cycles still runs without a card; and after the add-on is
unregistered the dropdown no longer offers it.

Run through ``run_native_suites.py --gui --include test_blender_installed_engines``; check
``installed-engines.json`` for ``success`` and ``phase == "complete"``.
"""

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
import qa_boot  # noqa: E402
import installed_engine_fixture as fixture  # noqa: E402
product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-installed-engines-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "installed-engines.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()


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
    from angelo_agent import chat_toggle, top_bar_fit
    left, _right, bottom = chat_toggle.anchors[window().as_pointer()]
    fitted = top_bar_fit.widths[window().as_pointer()]
    x = left
    for each in ("chat", "studio", "flows", "engine"):
        _, units, unit = fitted[each]
        if each == "engine":
            return x + 0.3 * units * unit, bottom + state["top_bar"][1] / 2
        x += units * unit


def menu_row_point(value):
    from angelo_agent import flow_menu
    area = next(a for a in window().screen.areas if a.as_pointer() == flow_menu._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    scale = flow_menu._open["scale"]
    index = next(i for i, row in enumerate(flow_menu._open["rows"]) if row.get("value") == value)
    rx, ry, rw, rh = flow_menu._open["layout"]["rows"][index]
    return region.x + (rx + rw / 2) * scale, region.y + (ry + rh / 2) * scale


def program(engine):
    scene = bpy.context.scene
    from angelo_agent import scene_settings
    return SimpleNamespace(name="angelo_scene_program", arguments={
        "mode": "APPLY", "read_set": [], "write_set": [{"handle": scene_settings.scene_handle(scene), "revision": "r"}],
        "operations": [{"kind": "property_set", "target": scene_settings.scene_handle(scene),
                        "parameters": {"values": {"render.engine": engine}}}]})


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_INSTALLED_ENGINES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import director, flow_menu, render_engines
    phase = state["phase"]
    scene = bpy.context.scene
    try:
        assert time.monotonic() - started < 180, "Suite timed out"
        if phase == "boot":
            if not state.get("recording"):
                record_top_bar()
                state["recording"] = True
            if not ("top_bar" in state and qa_boot.started() and director.is_director(window()) and layout_drawn()):
                return .3
            seen = state.setdefault("since", {}).setdefault("layout_drawn", time.monotonic())
            if time.monotonic() - seen < 1.0:
                return .2
            fixture.register()   # As enabling a person's add-on does, after start-up.
            scene.render.engine = render_engines.EEVEE
            state["phase"] = "open"
            return .5
        if phase == "open":
            press(*engine_point())
            state["phase"] = "menu"
            return .6
        if phase == "menu":
            if not wait(phase, lambda: flow_menu.is_open(window(), flow_menu.ENGINES) and flow_menu._open.get("layout")):
                return .2
            rows = [row for row in flow_menu._open["rows"] if row["kind"] == "row"]
            values = [row["value"] for row in rows]
            state["measured"]["rows"] = values
            row = next((row for row in rows if row["value"] == "engine:" + fixture.IDNAME), None)
            checks["the_dropdown_lists_the_installed_engine_after_blenders"] = bool(row) and values.index(
                "engine:" + fixture.IDNAME) > values.index("engine:BLENDER_WORKBENCH") and values[-1] == "ai_render"
            checks["it_is_named_as_its_add_on_names_it"] = bool(row) and row["label"] == fixture.LABEL
            capture("installed-engines-1-dropdown.png")
            press(*menu_row_point("engine:" + fixture.IDNAME))
            state["phase"] = "chosen"
            return .6
        if phase == "chosen":
            if not wait(phase, lambda: scene.render.engine == fixture.IDNAME and not flow_menu.is_open()):
                return .2
            capture("installed-engines-2-chosen.png")
            scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 64, 48, 100
            # Saved through the Standard view, so the file holds the engine's colour sRGB-encoded (AgX would tint it).
            scene.view_settings.view_transform, scene.view_settings.look = 'Standard', 'None'
            scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
            bpy.ops.render.render()
            image = bpy.data.images.get("Render Result")
            pixel = None
            if image is not None:
                path = OUT / "installed-render.png"
                image.save_render(str(path))
                loaded = bpy.data.images.load(str(path))
                pixel = [round(v, 2) for v in loaded.pixels[:3]]
                bpy.data.images.remove(loaded)
            state["measured"]["pixel"] = pixel
            srgb = [12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055 for c in fixture.COLOUR]
            state["measured"]["expected"] = [round(c, 2) for c in srgb]
            checks["f12_renders_through_the_installed_engine"] = pixel is not None and all(
                abs(a - b) < 0.03 for a, b in zip(pixel, srgb))
            from angelo_agent import render_extensions, scene_settings
            report = render_extensions.inspect(scene, render_extensions.RENDER)
            state["measured"]["admission"] = {"engine": report.engine, "label": report.engine_label}
            checks["a_render_job_names_it_as_extension_code"] = bool(report.engine_label) and fixture.LABEL in (
                report.engine_label or "")
            observed = scene_settings.observe(window())
            checks["the_settings_read_lists_it"] = [e["id"] for e in observed.get("installed_engines", [])] == [
                fixture.IDNAME]
            from angelo_agent import ui
            controller = ui.get_controller()
            checks["auto_asks_before_a_program_sets_it"] = not controller._auto_bounded(program(fixture.IDNAME),
                                                                                        "AUTO_BOUNDED")
            checks["auto_still_runs_a_program_that_sets_cycles"] = controller._auto_bounded(program("CYCLES"),
                                                                                           "AUTO_BOUNDED")
            fixture.unregister()   # The person disables the add-on.
            scene.render.engine = render_engines.EEVEE
            press(*engine_point())
            state["phase"] = "gone"
            return .6
        if phase == "gone":
            if not wait(phase, lambda: flow_menu.is_open(window(), flow_menu.ENGINES) and flow_menu._open.get("layout")):
                return .2
            values = [row["value"] for row in flow_menu._open["rows"] if row["kind"] == "row"]
            checks["a_disabled_add_on_is_no_longer_offered"] = "engine:" + fixture.IDNAME not in values
            capture("installed-engines-3-after-disable.png")
            flow_menu.close("suite")
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


import qa_input  # noqa: E402  (synthetic clicks; after_clicks)
# Never a phase while a click it sent is still on its way (qa_input.after_clicks).
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
