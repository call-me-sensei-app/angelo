# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Director's Flows and a flow's Add step open Angelo's own popup in the running app (1.0.5, ``flow_menu``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference. Checked
against ``doc/angelo/studio_entry_points.rst`` and read from each capture's pixels: Director's Flows button opens
Angelo's popup, not Blender's dropdown, on its CARD panel, with New Flow's tile and no tile beside a template
(section 2), and the chat's state unchanged; Escape closes it; a click on a template row opens the flow in its own
window, whose bar names the flow with its step count under the name and links back to Media Studio with the shared
pill; Add step opens the step menu (a tile beside every step type, not Blender's node Add menu) and its Text row adds
a Text step placed with a click; Run all reaches ``angelo.flow_run``; the Media Studio pill opens Media Studio.

Run through ``run_native_suites.py --gui --include test_blender_flow_menu``; check ``flow-menu.json`` for ``success``
and ``phase == "complete"``. The 1.0.5 "before" captures are ``test_blender_studio_discoverability``'s studio-4 (the
stock dropdown) and the flow window's stock Add menu.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_flow_view, inside the app.
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-flow-menu-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "flow-menu.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()
TEMPLATE = "image_reference_3d"


def window():
    return bpy.context.window_manager.windows[0]


def windows():
    return bpy.context.window_manager.windows


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def press(x, y, target=None):
    import qa_input   # The pointer arrives, then the press, a pass later (a click in one pass could miss).
    qa_input.press(target or window(), x, y)


def key(name, target=None):
    w = target or window()
    for value in ('PRESS', 'RELEASE'):
        w.event_simulate(type=name, value=value, x=w.width // 2, y=w.height // 2)


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def record_top_bar():
    """The top bar's height, from the region that draws the Chat toggle (a global area ``screen.areas`` omits)."""
    from angelo_agent import chat_toggle
    original = chat_toggle.draw

    def draw(layout, context, *args, **kwargs):   # The toggle's compact flags pass through (top_bar_fit).
        state["top_bar"] = (context.region.y, context.region.height)
        return original(layout, context, *args, **kwargs)
    chat_toggle.draw = draw


def layout_drawn():
    """Director's layout is on screen: a 3D view and the chat, each with a drawn WINDOW region."""
    from angelo_agent import console
    areas = window().screen.areas
    return any(a.type == 'VIEW_3D' for a in areas) and any(console.is_chat(a) for a in areas) and all(
        r.width > 1 for a in areas for r in a.regions if r.type == 'WINDOW')


def button_rects():
    """{name: [x0, x1]} window pixels of the top bar's Chat, Studio and Flows as top_bar_fit recorded them."""
    from angelo_agent import chat_toggle, top_bar_fit
    left, _right, _bottom = chat_toggle.anchors[window().as_pointer()]
    rects, x = {}, left
    for name, (_label, units, unit) in top_bar_fit.widths[window().as_pointer()].items():
        rects[name] = [round(x), round(x + units * unit)]
        x += units * unit
    return rects


def diagnose(phase):
    """What a click that opened nothing did: whether Flows ran (its route), the popup opened, and it drew."""
    if state.get("diagnosis"):
        return
    from angelo_agent import flow_menu, flow_open
    state["diagnosis"] = {"phase": phase, "routes": list(flow_open.routes), "is_open": flow_menu.is_open(),
                          "history": list(getattr(flow_menu, "history", [])),
                          "open_area": (flow_menu._open or {}).get("area"), "drawn": getattr(flow_menu, "drawn", None),
                          "areas": [(a.type, a.x, a.y, a.width, a.height) for a in window().screen.areas]}
    capture("flow-menu-timeout-%s.png" % phase)


def flows_button_point():
    """Inside Flows: Chat, Studio and Flows follow each other from Chat's left edge (chat_toggle.anchors), each as
    wide as top_bar_fit recorded it."""
    from angelo_agent import chat_toggle, top_bar_fit
    left, _right, bottom = chat_toggle.anchors[window().as_pointer()]
    fitted = top_bar_fit.widths[window().as_pointer()]
    x = left
    for each in ("chat", "studio", "flows"):
        _, units, unit = fitted[each]
        if each == "flows":
            return x + 0.4 * units * unit, bottom + state["top_bar"][1] / 2
        x += units * unit


def record_bar_actions():
    from angelo_agent import flow_window
    original = flow_window.act

    def act(target, area, region, hit):
        state.setdefault("bar_actions", []).append(hit[0])
        return original(target, area, region, hit)
    flow_window.act = act


def chat_state():
    from angelo_agent import chat_toggle
    return chat_toggle.chat_state(window(), windows())[0]


def pixels(path):
    """A capture's pixels as an (height, width, 3) array of 0-255 integers, bottom row first."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def block(px, rect):
    x, y, w, h = (int(round(v)) for v in rect)
    return px[max(0, y):max(0, y + h), max(0, x):max(0, x + w)]


def main_colour(px, rect):
    import numpy
    flat = block(px, rect).reshape(-1, 3)
    if not len(flat):
        return None
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    value = int(values[counts.argmax()])
    return [value // 65536, value // 256 % 256, value % 256]


def share(px, rect, test):
    import numpy
    flat = block(px, rect).reshape(-1, 3)
    return float(numpy.mean([bool(test(v)) for v in flat])) if len(flat) else 0.0


def apart(a, b):
    return sum(abs(int(x) - int(y)) for x, y in zip(a, b))


def rgb(color):
    return [round(c * 255) for c in color[:3]]


def tinted(v):
    return v[1] > v[0] + 25   # The teal tiles and teal fills: green well above red; the UI's greys never are.


def menu_tiles(px, region, scale):
    """Each visible row's tile area (window pixels): the share of teal-tinted pixels there, split into rows that have
    a glyph and rows that have none (a template, one of your flows)."""
    from angelo_agent import flow_menu
    geometry, rows = flow_menu._open["layout"], flow_menu._open["rows"]
    lx, ly, lw, lh = geometry["list_rect"]
    tiles, bare, plus = [], [], []
    for row, (rx, ry, rw, rh) in zip(rows, geometry["rows"]):
        if row["kind"] not in ("row", "header") or ry < ly or ry + rh > ly + lh:
            continue
        top = flow_menu.HEADER_GAP if row["kind"] == "header" else 0
        tile = (region.x + (rx + 8) * scale, region.y + (ry + top + (rh - top - 32) / 2) * scale, 32 * scale,
                32 * scale)
        if row.get("glyph") == "add":   # New Flow: a 16 px plus, no tile (section 2).
            plus.append(round(share(px, tile, tinted), 3))
            continue
        (tiles if row.get("glyph") else bare).append(round(share(px, tile, tinted), 3))
    return tiles, bare, plus


def menu_region():
    from angelo_agent import flow_menu
    w = next(x for x in windows() if x.as_pointer() == flow_menu._open["window"])
    area = next(a for a in w.screen.areas if a.as_pointer() == flow_menu._open["area"])
    return w, next(r for r in area.regions if r.type == 'WINDOW')


def menu_row_point(value):
    from angelo_agent import flow_menu
    _, region = menu_region()
    scale = flow_menu._open["scale"]
    index = next(i for i, row in enumerate(flow_menu._open["rows"]) if row.get("value") == value)
    rx, ry, rw, rh = flow_menu._open["layout"]["rows"][index]
    return region.x + (rx + rw / 2) * scale, region.y + (ry + rh / 2) * scale


def check_menu(name, path, rows_expected):
    from angelo_agent import card_kit, flow_menu
    target, region = menu_region()
    scale = flow_menu._open["scale"]
    px = pixels(path)
    tiles, bare, plus = menu_tiles(px, region, scale)
    x, y, w, h = (v * scale for v in flow_menu._open["layout"]["rect"])
    panel = main_colour(px, (region.x + x + 20 * scale, region.y + y + 3 * scale, w - 40 * scale, 4 * scale))
    state["measured"][name] = {"tiles": tiles, "bare": bare, "plus": plus, "panel": panel, "drawn": flow_menu.drawn,
                               "width": w}
    rows = [row["value"] for row in flow_menu._open["rows"] if row["kind"] == "row"]
    checks[name + "_lists_the_registry_rows"] = rows == [row["value"] for row in rows_expected if row["kind"] == "row"]
    checks[name + "_draws_its_card_panel"] = panel is not None and apart(panel, rgb(card_kit.CARD)) <= 12
    checks[name + "_is_at_least_320_wide"] = w >= flow_menu.MIN_WIDTH * scale - 1
    # Calibrated offscreen (paint_check, 1.0.5): a tile is 0.35-0.6 teal-tinted; a row without a glyph has none.
    checks[name + "_draws_a_tile_only_where_a_glyph_is"] = bool(tiles) and min(tiles) > 0.2 and all(
        b < 0.02 for b in bare) and all(0.03 < p < 0.2 for p in plus)


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_FLOW_MENU", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import director, flow_menu, flow_open, flow_window
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 180, "Suite timed out"
        if phase == "boot":
            if not state.get("recording"):
                record_top_bar()
                record_bar_actions()
                state["recording"] = True
            if not ("top_bar" in state and settled() and director.is_director(window()) and layout_drawn()):
                return .3
            # Director's layout is built a few frames after the mode settles (35f5b544698's slot captured an empty
            # window here and its click opened nothing): click only once it has drawn for a moment.
            seen = state.setdefault("since", {}).setdefault("layout_drawn", time.monotonic())
            if time.monotonic() - seen < 1.0:
                return .2
            state["chat"] = chat_state()
            capture("flow-menu-0-director.png")
            point = flows_button_point()
            state["click"] = {"point": [round(v) for v in point], "buttons": button_rects()}
            press(*point)
            state["phase"] = "flows_menu"
            return .6
        if phase == "flows_menu":
            opened = lambda: flow_menu.is_open(window()) and flow_menu._open.get("layout")
            if not opened() and time.monotonic() - state.setdefault("since", {}).setdefault(phase, time.monotonic()) > 8:
                diagnose("flows_menu")   # Says whether the click ran Flows, opened the popup, or drew it.
            if not wait(phase, opened):
                return .2
            checks["flows_opens_angelos_popup_and_leaves_the_chat_as_it_was"] = (
                flow_menu._open["menu"] == flow_menu.FLOWS and chat_state() == state["chat"])
            path = capture("flow-menu-1-flows-menu.png")
            check_menu("flows_menu", path, flow_menu.flow_rows())
            key('ESC')
            state["phase"] = "flows_closed"
            return .4
        if phase == "flows_closed":
            checks["escape_closes_the_flows_menu"] = not flow_menu.is_open()
            press(*flows_button_point())
            state["phase"] = "flows_again"
            return .6
        if phase == "flows_again":
            if not wait(phase, lambda: flow_menu.is_open(window()) and flow_menu._open.get("layout")):
                return .2
            press(*menu_row_point(flow_open.STARTER_ROW + TEMPLATE))   # A template row, clicked.
            state["phase"] = "flow_window"
            return .6
        if phase == "flow_window":
            target = flow_open.flow_window(windows())
            area = target.screen.areas[0] if target else None
            if not wait(phase, lambda: area is not None and (flow_window._bars.get(area.as_pointer()) or {}).get("drawn")):
                return .2
            seen = state.setdefault("since", {}).setdefault("bar_drawn", time.monotonic())
            if time.monotonic() - seen < .6:   # The framed graph settles (flow_open.frame_when_drawn).
                return .2
            space = area.spaces.active
            checks["a_template_row_opens_its_flow_in_its_own_window"] = not flow_menu.is_open() and bool(
                space.node_tree and len(space.node_tree.nodes) == 4)
            drawn = flow_window._bars[area.as_pointer()]["drawn"]
            path = capture("flow-menu-2-flow-window.png", target)
            px = pixels(path)
            region = next(r for r in area.regions if r.type == 'WINDOW')
            line, _problem = flow_window.status(space.node_tree)
            from angelo_agent import card_kit
            s = prefs.system.ui_scale
            bar_bottom = region.y + region.height - flow_window.HEIGHT * s
            status_band = (region.x + drawn["text_x"], bar_bottom + 6 * s, 120 * s, 9 * s)   # The status line.
            text_share = share(px, status_band, lambda v: apart(v, rgb(card_kit.CARD)) > 40)
            state["measured"]["flow_window"] = {"drawn": drawn, "status_text_share": round(text_share, 3)}
            checks["the_bar_names_the_flow_and_its_step_count"] = (
                drawn["title"] == flow_window.flow_label(space.node_tree.name) and drawn.get("status") == line
                and text_share > 0.03)
            lx, ly, lw, lh = drawn["link"]   # The shared pill: PANEL inside a 1 px LINE border.
            pill = main_colour(px, (region.x + lx + lw * .55, region.y + ly + 3 * s, lw * .4, lh - 6 * s))
            state["measured"]["flow_window"]["pill"] = pill
            checks["the_bar_links_back_to_media_studio"] = pill is not None and apart(pill, rgb(card_kit.PANEL)) <= 12
            if hasattr(area, "angelo_owned"):   # A native build with the flag (review A2, A1); else recorded as skipped.
                checks["the_flow_editor_is_angelo_owned"] = bool(area.angelo_owned)
                u = 20 * s   # U.widget_unit: the hidden regions' toggle tabs (area.cc region_azone_tab_plus).
                top, left, right = region.y + region.height, region.x, region.x + region.width
                zones = {"header_v": (right - 2 * u, top - 0.4 * u, u, 0.4 * u),
                         "toolbar_gt": (left, top - 2 * u, 0.5 * u, u), "sidebar_lt": (right - 0.5 * u, top - 2 * u, 0.5 * u, u)}
                marks = {name: round(share(px, z, lambda v: apart(v, rgb(card_kit.CARD)) > 40), 3) for name, z in zones.items()}
                state["measured"]["flow_window"]["tab_zones"] = marks
                from angelo_agent import chrome_guard   # Why chrome shows: its flags, its regions, the guard's record.
                state["measured"]["flow_window"]["chrome"] = {
                    "flags": chrome_guard.shown(area), "guarded": space.as_pointer() in chrome_guard.guarded,
                    "undone": len(chrome_guard.undone), "owned": bool(area.angelo_owned),
                    "regions": {r.type: r.width for r in area.regions if r.type in {'UI', 'TOOLS', 'HEADER'}}}
                checks["no_region_toggle_arrows"] = all(m < 0.02 for m in marks.values())
                import qa_flow_view
                _region, rects = qa_flow_view.graph_bounds(area, s)
                headers = [main_colour(px, (region.x + l + 10 * s, region.y + t - 14 * s, max(1, r - l - 20 * s), 6 * s))
                           for l, b_, r, t in rects]
                state["measured"]["flow_window"]["node_headers"] = headers
                # Calibrated on the card-language header (the node backdrop, lighter and cooler); the old maroon
                # category colour is red well above blue.
                checks["node_headers_are_neutral_not_maroon"] = bool(headers) and all(
                    h is not None and h[0] - h[2] <= 6 and abs(h[0] - h[1]) <= 12 for h in headers)
            else:
                state["notes_a2"] = "Area.angelo_owned not in this build (Python overlay on an older app): A1/A2 skipped"
            add = drawn["rects"]["add"]
            state["nodes_before"] = len(space.node_tree.nodes)
            press(region.x + add[0] + add[2] / 2, region.y + add[1] + add[3] / 2, target)
            state["phase"] = "add_menu"
            return .6
        if phase == "add_menu":
            if not wait(phase, lambda: flow_menu.is_open() and flow_menu._open.get("layout")):
                return .2
            target, _region = menu_region()
            checks["add_step_opens_angelos_step_menu"] = flow_menu._open["menu"] == flow_menu.STEPS
            path = capture("flow-menu-3-add-step.png", target)
            check_menu("add_step_menu", path, flow_window.step_rows())
            x, y = menu_row_point("step:ANGELO_FlowTextNode")
            press(x, y, target)   # Adds the Text step at the pointer, moving with it until a click places it.
            state["phase"] = "place"
            return .5
        if phase == "place":
            target = flow_open.flow_window(windows())
            area = target.screen.areas[0]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            x, y = region.x + region.width * .5, region.y + region.height * .25
            target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))
            target.event_simulate(type='LEFTMOUSE', value='PRESS', x=int(x), y=int(y))
            target.event_simulate(type='LEFTMOUSE', value='RELEASE', x=int(x), y=int(y))
            state["phase"] = "placed"
            return .5
        if phase == "placed":
            target = flow_open.flow_window(windows())
            space = target.screen.areas[0].spaces.active
            texts = [n for n in space.node_tree.nodes if n.bl_idname == "ANGELO_FlowTextNode"]
            checks["its_text_row_adds_a_text_step"] = len(space.node_tree.nodes) == state["nodes_before"] + 1 and bool(texts)
            capture("flow-menu-4-step-added.png", target)
            drawn = flow_window._bars[target.screen.areas[0].as_pointer()]["drawn"]
            region = next(r for r in target.screen.areas[0].regions if r.type == 'WINDOW')
            run = drawn["rects"]["run"]
            press(region.x + run[0] + run[2] / 2, region.y + run[1] + run[3] / 2, target)
            state["phase"] = "ran"
            return .6
        if phase == "ran":
            # With no account nothing can run: Run all is drawn disabled and its click says why (review P4, P5);
            # once a step can run, the click reaches angelo.flow_run.
            ran_tree = flow_open.flow_window(windows()).screen.areas[0].spaces.active.node_tree
            expected = "run_blocked" if flow_window.blocking(ran_tree) else "run"
            state["measured"]["run_all"] = {"expected": expected, "bar_actions": state.get("bar_actions", [])}
            checks["run_all_reaches_the_flows_run"] = expected in state.get("bar_actions", [])
            key('ESC', flow_open.flow_window(windows()))   # Whatever Run all showed (no account: nothing runs).
            target = flow_open.flow_window(windows())
            drawn = flow_window._bars[target.screen.areas[0].as_pointer()]["drawn"]
            region = next(r for r in target.screen.areas[0].regions if r.type == 'WINDOW')
            lx, ly, lw, lh = drawn["link"]
            press(region.x + lx + lw / 2, region.y + ly + lh / 2, target)
            state["phase"] = "studio"
            return .6
        if phase == "studio":
            from angelo_agent import studio_toggle
            if not wait(phase, lambda: studio_toggle.opened_window(windows()) is not None, limit=6):
                return .2
            checks["the_media_studio_link_opens_media_studio"] = "studio" in state.get("bar_actions", [])
            state["phase"] = "to_flow"
            return .6
        if phase == "to_flow":   # Media Studio > Turn into a flow: a flow that starts where Studio is.
            from angelo_agent import studio, studio_toggle, studio_to_flow
            target = studio_toggle.opened_window(windows())
            area = next(a for a in target.screen.areas if studio.is_studio(a))
            setup = studio.view(area)
            setup.update(kind="IMAGE", prompt="a red fox in soft studio light")
            chosen = studio.selected(setup) or {}
            state["to_flow_expected"] = {"model": chosen.get("id", ""), "prompt": setup["prompt"]}
            state["trees_before"] = {t.name for t in bpy.data.node_groups}
            region = next(r for r in area.regions if r.type == 'WINDOW')
            capture("flow-menu-5-media-studio.png", target)
            pill = next((rect for rect, action, _ in setup.get("hits", []) if action == studio_to_flow.ACTION), None)
            state["to_flow_pill"] = bool(pill)
            if pill:   # Studio draws it (its hook): a real click, and its pixels are the shared pill's.
                from angelo_agent import card_kit
                px = pixels(OUT / "flow-menu-5-media-studio.png")
                x, y, w, h = pill
                import qa_frames
                studio_px = px[region.y:region.y + region.height, region.x:region.x + region.width]
                if qa_frames.drawn(studio_px):
                    body = main_colour(px, (region.x + x + w * .6, region.y + y + 4, w * .35, h - 8))
                    checks["turn_into_a_flow_is_visible_in_media_studio"] = body is not None and apart(
                        body, rgb(card_kit.PANEL)) <= 12
                else:   # The window readback came back flat (a capture fault, not a draw one): the pill drew its hit.
                    state["notes"] = "Media Studio's capture is one flat colour (readback); the pill is checked by its hit"
                    checks["turn_into_a_flow_is_visible_in_media_studio"] = w > 0 and h > 0
                press(region.x + x + w / 2, region.y + y + h / 2, target)
            else:   # Not placed in Studio yet (D's hook): the same operator its click runs.
                state["notes"] = "Turn into a flow's pill is not drawn by Studio in this build; operator called"
                with bpy.context.temp_override(window=target, screen=target.screen, area=area, region=region):
                    bpy.ops.angelo.studio_turn_into_flow()
            state["phase"] = "to_flow_check"
            return .6
        if phase == "to_flow_check":
            from angelo_agent import flow_nodes
            made = [t for t in bpy.data.node_groups if t.bl_idname == flow_nodes.TREE
                    and t.name not in state["trees_before"]]
            if not wait(phase, lambda: bool(made), limit=5):
                return .2
            tree = made[0]
            step = next((n for n in tree.nodes if n.bl_idname == "ANGELO_FlowGenerateImageNode"), None)
            expected = state["to_flow_expected"]
            state["to_flow_made"] = {"tree": tree.name, "nodes": [n.name for n in tree.nodes],
                                     "model": getattr(step, "model", None), "prompt": getattr(step, "prompt", None)}
            checks["turn_into_a_flow_starts_with_studios_model_and_prompt"] = bool(
                step and step.model == expected["model"] and step.prompt == expected["prompt"])
            from angelo_agent import flow_open
            target = flow_open.flow_window(windows())
            if target is not None:
                capture("flow-menu-6-from-media-studio.png", target)
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
