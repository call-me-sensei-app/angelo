# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio is discoverable in the running app (1.0.5, ``studio_toggle``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference.
Covered, each with a screenshot: with no account connected, the chat bar's + opens Angelo's menu (Media Studio,
Generation Flows, Base meshes) and typing into the waiting field types nothing; in Director the top bar has Studio beside Chat, and a real click on it opens Media
Studio in its own window (the Generative AI editor) and leaves Director's 3D view + chat split as it was, with the
button pressed; a second click closes that window; Flows beside it opens its menu (captured), and its template item
creates a wired flow shown in its own window (New Flow reuses that window); the Angelo menu offers Media Studio, and
its item opens and closes it in Manual mode too; the welcome shows the Media Studio slide, fitting its 3D view in English, German and French,
and a real click on its Try it closes the welcome and opens Media Studio. The flow window frames the template's whole
graph; Chat, Studio, Flows and the mode menu fit their labels in all of Angelo's languages (widest three captured). The empty chat's generation suggestions are
``test_blender_chat_start_states``'s.

Run through ``run_native_suites.py --gui --include test_blender_studio_discoverability``; check
``studio-discoverability.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import sys

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_flow_view, inside the app.
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-discoverability-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "studio-discoverability.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "welcome": {}}
checks = state["checks"]
started = time.monotonic()
LANGUAGES = ("en_US", "de_DE", "fr_FR")


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


TOGGLES = []   # (mode, main window?) of each Media Studio toggle the suite's clicks ran (close_diagnosis).


def watch_toggles():
    from angelo_agent import studio_toggle
    cls = studio_toggle.ANGELO_OT_toggle_studio
    original = cls.execute
    if getattr(original, "_watched", False):
        return

    def execute(self, context):
        TOGGLES.append((self.mode, context.window == window()))
        return original(self, context)
    execute._watched = True
    cls.execute = execute


def press_at(where, target=None):
    """A click at a point read again at the press (``where()``): the top bar's buttons move as it redraws."""
    import qa_input
    qa_input.press(target or window(), where)


def press(x, y, target=None):
    import qa_input   # The pointer arrives, then the press, a pass later (a click in one pass could miss).
    qa_input.press(target or window(), x, y)


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def studio_window():
    from angelo_agent import studio_toggle
    return studio_toggle.opened_window(windows())


def layout_signature():
    return sorted((a.type, a.width, a.height) for a in window().screen.areas)


def top_bar_point(name):
    """Inside a top-bar button: Chat, Studio and Flows follow each other from Chat's left edge (chat_toggle.anchors),
    each as wide as top_bar_fit recorded it; 40% into the button is on it whatever Blender's small gap between them."""
    from angelo_agent import chat_toggle, top_bar_fit
    left, right, bottom = chat_toggle.anchors[window().as_pointer()]
    fitted = top_bar_fit.widths[window().as_pointer()]
    x = left
    for each in ("chat", "studio", "flows"):
        _, units, unit = fitted[each]
        if each == name:
            return x + 0.4 * units * unit, bottom + state["top_bar"][1] / 2
        x += units * unit


def studio_button_point():
    return top_bar_point("studio")


def record_top_bar():
    """Keep the top bar's right-hand region as it draws the Chat toggle. The top bar is a global area, which
    ``screen.areas`` never lists, so its height comes from the region that draws the button."""
    from angelo_agent import chat_toggle
    original = chat_toggle.draw

    def draw(layout, context, *args, **kwargs):   # The compact flags too (top_bar_fit).
        state["top_bar"] = (context.region.y, context.region.height)
        return original(layout, context, *args, **kwargs)
    chat_toggle.draw = draw


def chat_view_state():
    from angelo_agent import chat_view, console
    area = next((a for a in window().screen.areas if console.is_chat(a)), None)
    return chat_view._views.get(area.as_pointer(), {}) if area else {}


def chat_point(action):
    """Window coordinates of a chat control's centre (its hit rectangle, region pixels)."""
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, _ in chat_view_state().get("hits", []) if a == action)
    return region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2


def flows_button_point():
    return top_bar_point("flows")


TEXT_MARGINS = 1.5   # UI units beside a header label: its icon and Blender's text margins (interface_widgets.cc).


BLANK = 0.015   # Share of a window's pixels away from its main colour: 0.0 blank, ~0.04 Media Studio, ~0.16 a flow.


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


def content_share(path):
    """How much of a captured window is drawn: the share of pixels clearly away from its most common colour."""
    import numpy
    flat = pixels(path).reshape(-1, 3)
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    main = values[counts.argmax()]
    return float((numpy.abs(flat - numpy.array([main // 65536, main // 256 % 256, main % 256])).sum(axis=1) > 24).mean())


def drawn(name, path):
    """Record and return whether a window's capture shows content (a blank window is one colour)."""
    share = content_share(path)
    state.setdefault("content_share", {})[name] = round(share, 4)
    return share > BLANK


def no_stock_header(name, path):
    """Whether a Media Studio window's top band is the Studio's own canvas colour, not Blender's grey editor header
    (calibrated: header (47, 47, 47), canvas (11, 6, 18), 106 apart)."""
    import numpy
    px = pixels(path)   # Bottom row first: the window's top is the last rows.
    height, width, _ = px.shape

    def main(block):
        flat = block.reshape(-1, 3)
        keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
        values, counts = numpy.unique(keys, return_counts=True)
        value = int(values[counts.argmax()])
        return numpy.array([value // 65536, value // 256 % 256, value % 256])
    band = main(px[height - max(4, int(6 * prefs.system.ui_scale)):, int(width * .3):int(width * .95)])
    canvas = main(px[int(height * .4):int(height * .7), int(width * .4):int(width * .9)])
    state.setdefault("studio_top_band", {})[name] = {"top": band.tolist(), "canvas": canvas.tolist()}
    return int(numpy.abs(band - canvas).sum()) <= 24


def changed_share(before, after, box):
    """The share of pixels inside ``box`` (x0, y0, x1, y1, window pixels, y up) that differ between two captures."""
    import numpy
    a, b = pixels(before), pixels(after)
    x0, y0, x1, y1 = (int(v) for v in box)
    a, b = a[max(0, y0):y1, max(0, x0):x1], b[max(0, y0):y1, max(0, x0):x1]
    return float((numpy.abs(a - b).sum(axis=2) > 24).mean()) if a.size else 0.0


def redraw_top_bar():
    """Lay out and draw the top bar now, in the language set on an earlier tick."""
    from angelo_agent import chat_toggle
    chat_toggle.redraw_top_bars()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)


def clipped_top_bar_labels(expected=("chat", "studio", "flows", "mode")):
    """Top-bar buttons whose label, as drawn now, would not fit the width top_bar_fit gave them, or that were drawn
    without one; [] when every label fits. The label's width is measured here at Blender's widget font."""
    import blf
    from angelo_agent import top_bar_fit
    fitted = top_bar_fit.widths.get(window().as_pointer(), {})
    blf.size(0, prefs.ui_styles[0].widget.points * prefs.system.ui_scale)
    clipped = [name for name in expected if name not in fitted]
    for name, (label, units, unit) in fitted.items():
        if blf.dimensions(0, label)[0] + TEXT_MARGINS * unit > units * unit:
            clipped.append(name)
    return clipped


def graph_framed(area):
    import qa_flow_view
    from angelo_agent import chat_view, flow_window
    return qa_flow_view.graph_framed(area, prefs.system.ui_scale, state.setdefault("flow_graph", {}),
                                     flow_window.bar_height(chat_view.ui_scale()))


def main_colour(block):
    import numpy
    flat = block.reshape(-1, 3)
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    value = int(values[counts.argmax()])
    return numpy.array([value // 65536, value // 256 % 256, value % 256])


def flow_window_checks(target, path):
    """What a person sees in a template's flow window, read from its capture and from what was drawn: Angelo's bar
    (its colour across the top, its title and buttons), no stock sidebar, no red before any action, and every node
    text field whole."""
    import numpy
    from angelo_agent import card_kit, flow_text_field, flow_window
    from angelo_agent.ui_text import iface_
    area = target.screen.areas[0]
    tree = area.spaces.active.node_tree
    px = pixels(path)
    height, width, _ = px.shape
    scale = prefs.system.ui_scale
    bar = main_colour(px[height - int(10 * scale):, int(width * .3):int(width * .6)])
    expected = numpy.array([round(c * 255) for c in card_kit.CARD[:3]])
    right = main_colour(px[int(height * .3):int(height * .7), int(width * .9):int(width * .99)])
    left = main_colour(px[int(height * .3):int(height * .7), int(width * .005):int(width * .03)])
    bounds = state.get("flow_graph", {}).get("bounds") or [0, 0, width, height]
    x0, y0, x1, y1 = (max(0, int(v)) for v in bounds)
    nodes = px[y0:y1, x0:x1]
    r, g, b = nodes[..., 0], nodes[..., 1], nodes[..., 2]
    # Calibrated on the 1.0.5 capture with "Can't run" (about 800 such pixels): an error red has blue near its green;
    # an orange socket or link (blue far under green) is not one.
    red = int(((r >= 160) & (g <= 110) & (b >= 70) & (abs(g - b) <= 25)).sum())
    drawn_bar = flow_window._bars.get(area.as_pointer(), {}).get("drawn") or {}
    fields = {key: lines for key, lines in flow_text_field.drawn.items() if key[0] == tree.name}
    whole = all("".join(lines).replace(" ", "") == getattr(tree.nodes[key[1]], key[2]).replace(" ", "").replace("\n", "")
                for key, lines in fields.items())
    state["flow_window"] = {"bar": bar.tolist(), "expected_bar": expected.tolist(), "right": right.tolist(),
                            "left": left.tolist(), "red": red, "bar_drawn": drawn_bar,
                            "fields": {"/".join(k[1:]): v for k, v in fields.items()}}
    checks["the_flow_window_shows_angelos_bar_not_the_stock_header"] = (
        int(numpy.abs(bar - expected).sum()) <= 24 and drawn_bar.get("labels") == [iface_(flow_window.ADD),
                                                                                    iface_(flow_window.RUN)]
        and drawn_bar.get("title") == tree.name)
    checks["the_flow_window_has_no_stock_sidebar"] = int(numpy.abs(right - left).sum()) <= 24
    checks["a_fresh_template_shows_no_red"] = red < 40
    checks["every_node_text_field_is_drawn_whole"] = bool(fields) and whole
    checks["a_long_prompt_wraps_instead_of_being_cut"] = any(len(lines) > 1 for lines in fields.values())


def click_welcome(action, value=None):
    from angelo_agent import canvas_rtl, intro
    area = next(a for a in window().screen.areas if a.as_pointer() == intro._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action and (value is None or v == value))
    press(region.x + canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width), region.y + rect[1] + rect[3] / 2)


def welcome_fits():
    from angelo_agent import intro
    plan = intro._open.get("plan")
    area = next((a for a in window().screen.areas if a.as_pointer() == intro._open.get("area")), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    if not plan or not region:
        return None
    return {"height": round(plan["height"]), "view": region.height, "scale": round(plan["scale"], 3),
            "card": any((action, value) == ("slide_try", "studio") for _, action, value in plan["hits"]),
            "fits": plan["height"] <= region.height - 32 * prefs.system.ui_scale}


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STUDIO_DISCOVERABILITY", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import director, intro, studio_toggle
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        if phase == "boot":
            if not state.get("recording"):
                record_top_bar()
                watch_toggles()
                state["recording"] = True
            if not ("top_bar" in state and settled() and director.is_director(window())
                    and any(a == "attach" for _, a, _ in chat_view_state().get("hits", []))):
                return .3
            state["layout"] = layout_signature()
            # No account is connected here: the chat bar is still Angelo's navigation (1.0.5). Its + opens the menu.
            press(*chat_point("attach"))
            state["phase"] = "plus"
            return .5
        if phase == "plus":
            if not wait(phase, lambda: (chat_view_state().get("popover") or {}).get("kind") == "add"):
                return .2
            rows = chat_view_state()["popover"]["rows"]
            top = [r["value"] for r in rows if r["kind"] in {"row", "group"} and not r.get("depth")]
            checks["before_a_connection_plus_offers_studio_flows_workflows_and_base_meshes"] = top[:5] == [
                "attach", "media_studio", "flows", "workflows", "base_mesh"]
            capture("studio-0-plus-before-a-connection.png")
            w = window()
            x, y = int(chat_point("attach")[0]), int(chat_point("attach")[1]) + 60
            for kind, text in (('ESC', None), ('A', "a")):   # Close the menu, then type into the waiting field.
                # Blender takes a typed character only on a press; a release (or ESC) carries none.
                w.event_simulate(type=kind, value='PRESS', x=x, y=y, **({"unicode": text} if text else {}))
                w.event_simulate(type=kind, value='RELEASE', x=x, y=y)
            state["phase"] = "typed"
            return .4
        if phase == "typed":
            from angelo_agent import console
            area = next(a for a in window().screen.areas if console.is_chat(a))
            checks["the_field_waits_for_a_connection"] = (chat_view_state().get("composer_hidden") is True
                                                         and area.spaces.active.history[-1].body == "")
            capture("studio-1-director-top-bar.png")
            checks["the_angelo_menu_offers_media_studio"] = studio_toggle.menu_item in getattr(
                bpy.types.TOPBAR_MT_blender.draw, "_draw_funcs", [])
            press_at(studio_button_point)
            state["phase"] = "opened"
            return .5
        if phase == "opened":
            if not wait(phase, lambda: studio_window() is not None):
                return .2
            studio = studio_window()
            checks["a_click_on_studio_opens_media_studio_in_its_own_window"] = (
                studio is not None and studio != window() and studio_toggle.is_studio(studio.screen.areas[0]))
            checks["directors_layout_is_unchanged"] = layout_signature() == state["layout"]
            from angelo_agent import director, ui   # What it was instead, and whether a layout change was under way.
            state["layout_at_open"] = {"layout": layout_signature(), "workspace": window().workspace.name,
                                       "layout_timer": ui._layout_timer is not None,
                                       "director_timer": director._timer is not None}
            state["phase"] = "opened_look"
            return .6
        if phase == "opened_look":
            # A new window captured before it has presented reads back flat (Metal), though it draws on screen: looked
            # at as the welcome's window is, after 0.6 s and again for up to 3 s more until it shows content (d-final
            # on d8e801755eb captured it at once and read 0.0).
            studio = studio_window()
            seen = state.setdefault("since", {}).setdefault("top_bar_window", time.monotonic())
            path = capture("studio-2-media-studio-window.png", studio)
            if not drawn("top_bar", path) and time.monotonic() - seen < 3.0:
                return .3
            checks["media_studio_from_the_top_bar_draws_its_content"] = drawn("top_bar", path)
            checks["media_studio_from_the_top_bar_has_no_stock_header"] = no_stock_header("top_bar", path)
            capture("studio-3-director-button-pressed.png")
            press_at(studio_button_point)
            state["phase"] = "closed"
            return .5
        if phase == "closed":
            import qa_input
            from angelo_agent import layout_after
            state["close_diagnosis"] = {"sent": list(qa_input.sent), "deferred": list(layout_after.deferred),
                                        "busy": layout_after.busy(), "windows": len(windows()),
                                        "studio_open": studio_window() is not None, "toggles": list(TOGGLES)}
            if not wait(phase, lambda: studio_window() is None and len(windows()) == 1):
                return .2
            checks["a_second_click_closes_it"] = studio_window() is None and len(windows()) == 1
            state["flows_before"] = str(capture("studio-4a-before-the-flows-menu.png"))
            press(*flows_button_point())   # Flows beside Studio: Angelo's Flows popup (flow_menu) under it.
            state["phase"] = "flows_menu"
            return .6
        if phase == "flows_menu":
            from angelo_agent import flow_menu

            def flows_rows():
                return [row["value"] for row in (flow_menu._open or {}).get("rows", []) if row["kind"] == "row"]
            if not wait(phase, lambda: flow_menu.is_open(window()) and flow_menu._open.get("layout") and flows_rows(),
                        limit=6):
                return .2
            after = capture("studio-4-director-flows-menu.png")
            # The popup opens under the button over the editor below it: its rows are the registry's, and the pixels
            # under it change against the capture before the click.
            area = next(a for a in window().screen.areas if a.as_pointer() == flow_menu._open["area"])
            region = next(r for r in area.regions if r.type == 'WINDOW')
            x, y, w_, h_ = flow_menu._open["layout"]["rect"]
            scale = flow_menu._open["scale"]
            box = (region.x + x * scale, region.y + y * scale, region.x + (x + w_) * scale, region.y + (y + h_) * scale)
            state["flows_menu_changed"] = round(changed_share(state["flows_before"], after, box), 4)
            state["flows_rows"] = flows_rows()
            # Calibrated offscreen (flow_menu.paint over the chat's ground, 1.0.5): the popup's CARD panel is close to
            # the chat's colour, so its border, shadow, tile and text change 9.3% of its rectangle; nothing does without it.
            checks["the_flows_button_opens_angelos_flows_popup"] = (
                {"flows:new", "flows:template:image_to_3d", "flows:template:image_reference_3d"} <= set(flows_rows())
                and state["flows_menu_changed"] > 0.04)
            w = window()
            w.event_simulate(type='ESC', value='PRESS', x=w.width // 2, y=w.height // 2)
            w.event_simulate(type='ESC', value='RELEASE', x=w.width // 2, y=w.height // 2)
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.flow_create(template="image_reference_3d")   # The menu's template item.
            state["phase"] = "flow_window"
            return .5
        if phase == "flow_window":
            from angelo_agent import flow_open
            if not wait(phase, lambda: flow_open.flow_window(windows()) is not None):
                return .2
            target = flow_open.flow_window(windows())
            # The template's whole graph is framed once its window and nodes are drawn (flow_open.frame_when_drawn).
            since = state.setdefault("since", {}).setdefault("flow_framed", time.monotonic())
            if not graph_framed(target.screen.areas[0]) and time.monotonic() - since < 6:
                return .2
            checks["the_flow_window_frames_the_whole_graph"] = graph_framed(target.screen.areas[0])
            tree = target.screen.areas[0].spaces.active.node_tree
            checks["director_reaches_a_flow_template_in_its_own_window"] = bool(
                tree and {"Image", "Reference", "3D model", "Place in scene"} <= {n.name for n in tree.nodes}
                and len(tree.links) == 3)
            checks["directors_layout_is_unchanged_by_the_flow"] = layout_signature() == state["layout"]
            path = capture("studio-5-flow-window.png", target)
            checks["the_flow_window_draws_its_content"] = drawn("flow", path)
            flow_window_checks(target, path)
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.flow_create()   # New Flow: shown in the same flow window.
            checks["a_new_flow_reuses_the_flow_window"] = (flow_open.flow_window(windows()) == target
                                                          and len(windows()) == 2)
            with bpy.context.temp_override(window=target, area=target.screen.areas[0]):
                bpy.ops.wm.window_close()
            director.request_mode(window(), "MANUAL", persist=False)
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .3
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.toggle_studio()   # Angelo menu > Media Studio: in Manual, its tab (studio_workspaces).
            state["phase"] = "manual_opened"
            return .7
        if phase == "manual_opened":
            from angelo_agent import studio_workspaces
            checks["the_menu_item_opens_it_in_manual_mode"] = (studio_window() is not None
                                                               or studio_workspaces.active(window(), "studio"))
            capture("studio-6-manual-menu-opened.png")
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.toggle_studio()   # Pressed: back where it came from (or the window closes).
            state["phase"] = "manual_closed"
            return .7
        if phase == "manual_closed":
            from angelo_agent import studio_workspaces
            checks["and_closes_it"] = studio_window() is None and not studio_workspaces.active(window(), "studio")
            director.request_mode(window(), "DIRECTOR", persist=False)
            state["phase"] = "welcome"
            state["languages"] = list(LANGUAGES[1:]) + [LANGUAGES[0]]
            return .5
        if phase == "welcome":
            if not wait(phase, lambda: settled() and director.is_director(window())):
                return .3
            language = state["languages"][0]
            prefs.view.language = language
            prefs.view.use_translate_interface = True
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
            state["phase"] = "welcome_open"
            return 1.0
        if phase == "welcome_open":
            if not wait(phase, lambda: intro.is_open() and "plan" in intro._open):
                return .2
            from angelo_agent import welcome_slides
            if intro._open["plan"].get("slide") != 1:   # The slideshow's Media Studio slide (1.0.5).
                intro.show_slide(1)
                intro._open["slide_changed"] = 0.0
                return .3
            language = state["languages"].pop(0)
            fit = welcome_fits()
            state["welcome"][language] = fit
            capture("studio-7-welcome-%s.png" % language)
            checks["the_welcome_shows_the_studio_card_and_fits_" + language] = bool(fit and fit["card"] and fit["fits"])
            from angelo_agent.ui_text import iface_
            plan = intro._open["plan"]
            box, s = plan["slides"], plan["scale"]
            within = [op for op in plan["ops"] if op[0] == "text" and box[1] <= op[3] <= box[1] + box[3]]
            title = [op[1] for op in within if op[4] == 17 * s]
            lines = [op[1] for op in within if op[4] == 13.5 * s]
            state.setdefault("studio_card", {})[language] = {"title": title, "lines": lines}
            tiles = [op[2] for op in intro._open["plan"]["ops"] if op[0] == "tile"]
            checks["every_demo_tile_title_is_whole_" + language] = bool(tiles) and not any(
                line.endswith("\u2026") for lines in tiles for line in lines)
            # Drawn in this language and whole: every line of its title and text, none cut with "…".
            checks["the_studio_card_is_translated_and_whole_" + language] = bool(
                title and lines and "".join(title).replace(" ", "") == iface_(welcome_slides.ALL_SLIDES[1][1]).replace(" ", "")
                and not any(line.endswith("\u2026") for line in title + lines))
            if state["languages"]:
                click_welcome("close")
                state["phase"] = "welcome_next"
                return .5
            click_welcome("slide_try", "studio")   # English, last: the Media Studio slide's Try it.
            state["phase"] = "welcome_studio"
            return .5
        if phase == "welcome_next":
            if not wait(phase, lambda: not intro.is_open()):
                return .2
            state["phase"] = "welcome"
            return .2
        if phase == "welcome_studio":
            if not wait(phase, lambda: not intro.is_open() and studio_window() is not None):
                return .2
            # The welcome opens the window from a timer a moment after the click: look at it once it has been on
            # screen as long as the top bar's (0.6 s), then for up to 3 s more until it shows content.
            seen = state.setdefault("since", {}).setdefault("welcome_window", time.monotonic())
            waited = time.monotonic() - seen
            if waited < .6:
                return .2
            studio = studio_window()
            area = studio.screen.areas[0]
            state["welcome_window"] = {"ui_type": area.ui_type, "regions": [(r.type, r.width, r.height)
                                                                           for r in area.regions]}
            path = capture("studio-8-from-the-welcome.png", studio)
            if not drawn("welcome", path) and waited < 3.6:
                return .3
            checks["the_welcome_card_opens_media_studio"] = studio_window() is not None and not intro.is_open()
            checks["media_studio_from_the_welcome_draws_its_content"] = drawn("welcome", path)
            checks["media_studio_from_the_welcome_has_no_stock_header"] = no_stock_header("welcome", path)
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.toggle_studio()
            from angelo_agent import setup_card
            identifiers = [i.identifier for i in prefs.view.bl_rna.properties["language"].enum_items]
            state["label_languages"] = [setup_card.resolve(code, identifiers) for code, _ in setup_card.LANGUAGES]
            state["clipped"], state["label_units"], state["label_mode"], state["rooms"] = {}, {}, "director", {}
            state["phase"] = "labels"
            return .3
        if phase == "labels":
            # Every top-bar label fits in each of Angelo's languages (a German or Russian label is longer). A language
            # takes effect on the next event-loop pass, so it is set on one tick and measured on the next, and a
            # measurement only counts when the drawn Studio and Flows labels are that language's.
            from angelo_agent import flow_open, setup_card, top_bar_fit
            from angelo_agent.ui_text import iface_
            language = state.pop("measuring", None)
            if language:
                redraw_top_bar()
                fitted = top_bar_fit.widths.get(window().as_pointer(), {})
                room = top_bar_fit.rooms.get(window().as_pointer(), {})
                tight = room.get("level", 0) >= top_bar_fit.STUDIO_ICONS   # No room: icon-only (top_bar_fit.compact).
                state["rooms"].setdefault(state["label_mode"], {})[language] = room
                current = {"studio": "" if tight else studio_toggle.label(),
                           "flows": "" if tight else iface_(flow_open.TOP_BAR_LABEL)}
                stale = [name for name, text in current.items() if fitted.get(name, ("",))[0] != text]
                state["clipped"][language] = clipped_top_bar_labels() + [name + " (stale)" for name in stale]
                state["label_units"][language] = sum(units for _, units, _ in fitted.values())
            if state["label_languages"]:
                language = state["label_languages"].pop(0)
                if language:
                    top_bar_fit.widths.pop(window().as_pointer(), None)   # Re-recorded by the next draw.
                    prefs.view.language = state["measuring"] = language
                return .3
            mode = state["label_mode"]
            state.setdefault("clipped_by_mode", {})[mode] = dict(state["clipped"])
            checks["every_%s_top_bar_label_fits_in_every_language" % mode] = (
                len(state["clipped"]) == len(setup_card.LANGUAGES) and not any(state["clipped"].values()))
            if mode == "manual":
                checks["manual_shows_labelled_studio_and_flows_where_there_is_room"] = any(
                    room.get("level", 0) < top_bar_fit.STUDIO_ICONS for room in state["rooms"]["manual"].values())
                prefs.view.language = "en_US"
                state["phase"] = "scale_two"
                return .3
            state["longest"] = sorted(state["label_units"], key=state["label_units"].get)[-3:]
            state["phase"] = "label_shots"
            return .1
        if phase == "label_shots":   # The three widest top bars, for the eye: each set, then captured a tick later.
            if state.get("shooting"):
                redraw_top_bar()
                capture("studio-9-top-bar-%s.png" % state.pop("shooting"))
            if state["longest"]:
                prefs.view.language = state["shooting"] = state["longest"].pop()
                return .3
            prefs.view.language = "en_US"
            director.request_mode(window(), "MANUAL", persist=False)   # The same labels in Manual's top bar.
            from angelo_agent import setup_card
            identifiers = [i.identifier for i in prefs.view.bl_rna.properties["language"].enum_items]
            state["label_languages"] = [setup_card.resolve(code, identifiers) for code, _ in setup_card.LANGUAGES]
            state["clipped"], state["label_units"], state["label_mode"] = {}, {}, "manual"
            state["phase"] = "labels_manual"
            return .5
        if phase == "labels_manual":
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .3
            state["phase"] = "labels"
            return .1
        if phase == "scale_two":   # Manual's top bar (workspace tabs, Chat, the mode menu) at UI scale 2.
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .3
            if prefs.view.ui_scale != 2.0:
                from angelo_agent import top_bar_fit
                top_bar_fit.widths.pop(window().as_pointer(), None)   # Director's buttons are not drawn in Manual.
                prefs.view.ui_scale = 2.0
                return .5
            redraw_top_bar()
            from angelo_agent import top_bar_fit
            state["clipped_at_scale_two"] = clipped_top_bar_labels()
            room = state["room_at_scale_two"] = top_bar_fit.rooms.get(window().as_pointer(), {})
            checks["the_top_bar_labels_fit_at_ui_scale_two"] = not state["clipped_at_scale_two"]
            # Angelo's buttons leave the menus and tabs their room: everything fits, or Studio and Flows went
            # icon-only and Chat dropped its shortcut (measured: top_bar_fit.compact).
            fitted = top_bar_fit.widths.get(window().as_pointer(), {})
            checks["at_ui_scale_two_angelos_buttons_leave_the_tabs_room"] = bool(room) and (
                room["left"] + room["right"] <= room["width"] or room["level"] == top_bar_fit.CHAT_ICON)
            # Permanent: whatever the level, both buttons are drawn (an icon at least).
            checks["at_ui_scale_two_studio_and_flows_are_still_there"] = "studio" in fitted and "flows" in fitted
            capture("studio-10-manual-top-bar-ui-scale-2.png")
            prefs.view.ui_scale = 1.0
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
