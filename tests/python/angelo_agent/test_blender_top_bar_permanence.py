# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Studio and Flows are permanent top-bar buttons (1.0.5, maintainer: "part of the permanent fixture").

Real default startup, simulated input, no account (``test_blender_chat_start_states`` covers a signed-in chat). For
Director and three of Manual's workspace tabs, at UI scale 1 and 2, in English, German and Russian: both buttons are
drawn (``top_bar_fit.widths``, an icon at least when the bar lacks room, ``top_bar_fit.compact``) and visible, read
from the window's pixels inside each button; for each mode and tab at both scales in English a real click on Studio
opens Media Studio's window and a click on Flows opens the Generation Flows rows. Captures:
``permanence-<mode>-<tab>-<scale>-<language>.png``. Then in each mode the Window menu offers Media Studio and
Generation Flows: its draw includes them, the pixels under it change (``window-menu-<mode>.png``), and each entry's
operator opens the right window (Media Studio's, and a template's flow window). Then an Asset Browser in place of
the timeline: its two header pills are visible and open both, and at three widths (the full bottom row, half, a
quarter) in English and German at scale 1 and 2 nothing of Blender's header lies under a pill: each width is captured
with and without the pills (``asset-browser-width-<width>-<scale>-<language>[-no-pills].png``), and inside every
pill's rectangle the capture without them is the header's own background (coherence AB1).

Run through ``run_native_suites.py --gui --include test_blender_top_bar_permanence``; check
``top-bar-permanence.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))   # qa_input

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-top-bar-permanence-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "top-bar-permanence.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "cases": {}}
checks = state["checks"]
started = time.monotonic()
LANGUAGES = ("en_US", "de_DE", "ru_RU", "ar_EG")   # Arabic: right to left, but Blender never mirrors the top bar.
SCALES = (1.0, 2.0)
VISIBLE = 0.05   # Share of a button's middle that differs from the bar: an icon and a label draw well above it.


def window():
    return bpy.context.window_manager.windows[0]


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def press(x, y):
    import qa_input   # The pointer arrives, then the press, a pass later (a click in one pass could miss).
    qa_input.press(window(), x, y)


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def record_top_bar():
    """Keep the top bar's right-hand region as it draws the Chat toggle (the top bar is a global area)."""
    from angelo_agent import chat_toggle
    original = chat_toggle.draw

    def draw(layout, context, *args, **kwargs):
        state["top_bar"] = (context.region.x, context.region.y, context.region.width, context.region.height)
        return original(layout, context, *args, **kwargs)
    chat_toggle.draw = draw


def record_flows_menu():
    """Count the Generation Flows menus' draws: each draws only while open. Director's Flows (the button, a pill)
    opens Angelo's popup (``flow_menu``); the Window menu's submenu is ``ANGELO_MT_flows``."""
    from angelo_agent import flow_menu, flow_open
    menu = flow_open.ANGELO_MT_flows
    original = menu.draw

    def draw(self, context):
        state["flows_menu_draws"] = state.get("flows_menu_draws", 0) + 1
        return original(self, context)
    menu.draw = draw
    original_paint = flow_menu.paint

    def paint(*args, **kwargs):
        state["flows_menu_draws"] = state.get("flows_menu_draws", 0) + 1
        return original_paint(*args, **kwargs)
    flow_menu.paint = paint


def flows_shown(since_draws):
    """The Generation Flows rows are showing: Manual's Generation Flows tab (its start page or a flow), the chat's +
    menu, or Director's Flows popup (``flow_menu``) or submenu."""
    from angelo_agent import chat_view, console, studio_workspaces
    if studio_workspaces.active(window(), "flows"):
        return True
    rows = [arg for area in window().screen.areas if console.is_chat(area)
            for _, _, arg in chat_view._views.get(area.as_pointer(), {}).get("popup_hits", [])
            if isinstance(arg, str) and arg.startswith("flows:")]
    return bool(rows) or state.get("flows_menu_draws", 0) > since_draws


def close_flows():
    from angelo_agent import chat_view, console, flow_menu, studio_workspaces
    flow_menu.close()   # Director's popup (its modal handler ends at the next event).
    if studio_workspaces.active(window(), "flows"):   # Back from Manual's tab (lands a tick later).
        back = bpy.data.workspaces.get(studio_workspaces.came_from(window()) or "")
        if back is not None:
            window().workspace = back
        return
    for area in window().screen.areas:
        if console.is_chat(area):
            chat_view._views.get(area.as_pointer(), {}).pop("popover", None)
            area.tag_redraw()
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type='ESC', value=value, x=window().width // 2, y=window().height // 2)


def buttons():
    """``{name: (x0, y0, x1, y1)}`` window pixels of Chat, Studio and Flows: they follow each other from Chat's left
    edge (chat_toggle.anchors), each as wide as top_bar_fit recorded it."""
    from angelo_agent import chat_toggle, top_bar_fit
    left, _, bottom = chat_toggle.anchors[window().as_pointer()]
    fitted = top_bar_fit.widths.get(window().as_pointer(), {})
    height = state["top_bar"][3]
    rects, x = {}, left
    for name in ("chat", "studio", "flows"):
        if name not in fitted:
            continue
        _, units, unit = fitted[name]
        rects[name] = (x, bottom, x + units * unit, bottom + height)
        x += units * unit
    return rects


def glyph_contrast(px, rect):
    """The contrast ratio (WCAG) between a top-bar button's glyph and the button's own fill: the icon sits in the
    button's left square; its fill is that square's most common colour, the glyph the pixel furthest from it."""
    import numpy
    x0, y0, x1, y1 = rect
    side = y1 - y0
    block = px[int(y0 + .2 * side):int(y1 - .2 * side), int(x0 + 2):int(x0 + side)]
    if not block.size:
        return None
    flat = block.reshape(-1, 3)
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    main = int(values[counts.argmax()])
    fill = numpy.array([main // 65536, main // 256 % 256, main % 256])

    def luminance(rgb):
        c = numpy.asarray(rgb, dtype=float) / 255.0
        c = numpy.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        return float(c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722)
    lum = numpy.asarray([luminance(c) for c in flat[::7]])
    base = luminance(fill)
    far = lum[numpy.abs(lum - base).argmax()] if lum.size else base
    hi, lo = max(far, base), min(far, base)
    return round((hi + 0.05) / (lo + 0.05), 2)


def _runs(band, background):
    """The runs of columns in ``band`` that differ clearly from the bar's colour (specks dropped)."""
    import numpy
    ink = (numpy.abs(band - background).sum(axis=2) > 60).any(axis=0)
    runs, start = [], None
    for i, on in enumerate(list(ink) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None
    return [r for r in runs if r[1] - r[0] >= 2]


def mode_gap(px, background):
    """Pixels between the mode button's icon and its label (1.0.5 review SD1), in the mode slot that follows Flows
    (top_bar_fit widths); when that slot shows no icon and label, found in the pixels instead (``_mode_gap_found``).
    None when neither finds them."""
    from angelo_agent import chat_toggle, top_bar_fit
    fitted = top_bar_fit.widths.get(window().as_pointer(), {})
    if "mode" not in fitted:
        return None
    left, _, bottom = chat_toggle.anchors[window().as_pointer()]
    x = left + sum(fitted[n][1] * fitted[n][2] for n in ("chat", "studio", "flows") if n in fitted)
    _, units, unit = fitted["mode"]
    height = state["top_bar"][3]
    rows = slice(int(bottom + .3 * height), int(bottom + .7 * height))
    runs = _runs(px[rows, int(x):int(x + units * unit)], background)
    if len(runs) >= 2:
        return runs[1][0] - runs[0][1]
    return _mode_gap_found(px[rows, max(0, int(left - unit)):], background, unit)


def _mode_gap_found(band, background, unit):
    """The gap found in the pixels from Chat's left edge: Chat, Studio and Flows draw as three wide runs (their
    pills, labelled), and the mode button's icon and label are the runs after the third. The recorded widths missed
    the slot in Manual at scale 1 (None in every language on the stage, d-merged), whose capture shows the wrench
    4 px from "Manual". The icon can draw as pieces (the wrench: 5 px, 2 px apart, 7 px): every run starting within
    its 16 px is the icon."""
    runs = _runs(band, background)
    pills = [i for i, r in enumerate(runs) if r[1] - r[0] > unit]
    after = runs[pills[2] + 1:] if len(pills) >= 3 else []
    icon_end = after[0][0] + 16 * prefs.system.ui_scale if after else 0
    icon = [r for r in after if r[0] < icon_end]
    label = [r for r in after if r[0] >= icon_end]
    if not label:
        state.setdefault("mode_gap_runs", []).append(runs[:10])   # What was there, for the report.
        return None
    return label[0][0] - max(r[1] for r in icon)


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


def visible(px, rect, background):
    """Share of the middle of ``rect`` (window pixels) that differs clearly from the bar's own colour."""
    import numpy
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    block = px[int(y0 + .2 * h):int(y1 - .2 * h), int(x0 + .2 * w):int(x1 - .2 * w)]
    if not block.size:
        return 0.0
    return float((numpy.abs(block - background).sum(axis=2) > 40).mean())


def bar_colour(px):
    import numpy
    x, y, w, h = state["top_bar"]
    flat = px[int(y + h * .3):int(y + h * .7), int(x):int(x + w)].reshape(-1, 3)
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    value = int(values[counts.argmax()])
    return numpy.array([value // 65536, value // 256 % 256, value % 256])


def ab_area():
    return next((a for a in window().screen.areas if a.as_pointer() == state.get("ab_area")), None)


def ab_region(area, kind):
    return next(r for r in area.regions if r.type == kind)


def ab_rects(area):
    """The Asset Browser header's two pills in window pixels (x0, y0, x1, y1). The list's empty and no-results states
    are test_blender_asset_browser_states'."""
    from angelo_agent import asset_browser_entry as entry
    region = ab_region(area, 'HEADER')
    return {action: (region.x + x, region.y + y, region.x + x + w, region.y + y + h)
            for (x, y, w, h), action in entry._hits.get((area.as_pointer(), 'HEADER'), ())}


def menu_moment():
    """What could close a menu just opened: the window's workspace and screen, the layout holds, the windows."""
    from angelo_agent import director, ui
    w = window()
    return {"t": round(time.monotonic(), 3), "workspace": w.workspace.name, "screen": w.screen.as_pointer(),
            "layout_timer": ui._layout_timer is not None, "director_timer": director._timer is not None,
            "windows": len(bpy.context.window_manager.windows)}


def header_clear(area, key):
    """The Asset Browser header with and without its pills: inside every pill's rectangle, the capture without the
    pills must be the header's own background (nothing of Blender's under a pill, coherence AB1); the pills sit
    inside the header region and apart from each other."""
    import numpy
    from angelo_agent import asset_browser_entry as entry
    region = ab_region(area, 'HEADER')
    area.tag_redraw()
    with_pills = pixels(capture("asset-browser-width-%s.png" % key))
    rects = ab_rects(area)
    labels = entry._drawn.get(area.as_pointer(), {}).get("labels") if rects else None
    handle = next((h for h, kind in entry._handles if kind == 'HEADER'), None)
    bpy.types.SpaceFileBrowser.draw_handler_remove(handle, 'HEADER')
    try:
        area.tag_redraw()
        without = pixels(capture("asset-browser-width-%s-no-pills.png" % key))
    finally:
        entry._handles[:] = [(h, kind) for h, kind in entry._handles if kind != 'HEADER'] + [
            (bpy.types.SpaceFileBrowser.draw_handler_add(entry._draw, ('HEADER',), 'HEADER', 'POST_PIXEL'), 'HEADER')]
        area.tag_redraw()
    background = numpy_main(without, region)
    under = {}
    for action, (x0, y0, x1, y1) in rects.items():
        block = without[int(y0) + 1:int(y1) - 1, int(x0) + 1:int(x1) - 1]
        under[action] = round(float((numpy.abs(block - background).sum(axis=2) > 40).mean()) if block.size else 1.0, 3)
    inside = all(region.x <= x0 and x1 <= region.x + region.width and region.y <= y0 and y1 <= region.y + region.height
                 for x0, y0, x1, y1 in rects.values())
    ordered = sorted(rects.values())
    apart = all(a[2] <= b[0] for a, b in zip(ordered, ordered[1:]))
    shown = {action: round(visible(with_pills, rect, background), 3) for action, rect in rects.items()}
    return {"width": region.width, "labels": labels, "under": under, "shown": shown, "inside": inside, "apart": apart,
            "clear": all(value <= 0.02 for value in under.values()) and inside and apart
            and all(value > VISIBLE for value in shown.values())}


def numpy_main(px, region):
    import numpy
    flat = px[region.y:region.y + region.height, region.x:region.x + region.width].reshape(-1, 3)
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    value = int(values[counts.argmax()])
    return numpy.array([value // 65536, value // 256 % 256, value % 256])


def centre(rect):
    x0, y0, x1, y1 = rect
    return (x0 + x1) / 2, (y0 + y1) / 2


def studio_shown():
    """Media Studio is showing: its own window (Director), or Manual's Media Studio tab."""
    from angelo_agent import studio_toggle, studio_workspaces
    return (studio_toggle.opened_window(bpy.context.window_manager.windows) is not None
            or studio_workspaces.active(window(), "studio"))


def close_studio():
    """Whether Media Studio is showing; it is closed again (its window) or left (its tab) either way."""
    from angelo_agent import studio_toggle, studio_workspaces
    opened = studio_shown()
    if studio_toggle.opened_window(bpy.context.window_manager.windows) is not None:
        with bpy.context.temp_override(window=window()):
            bpy.ops.angelo.toggle_studio()
    elif studio_workspaces.active(window(), "studio"):
        back = bpy.data.workspaces.get(studio_workspaces.came_from(window()) or "")
        if back is not None:
            window().workspace = back
    return opened


def cases():
    """(mode, workspace name or None, scale, language, look) for every combination: Manual on three of its ordinary
    tabs (Angelo's own tabs are test_blender_studio_tabs'), and Angelo Light in English, German and Arabic at both
    scales (the review asks for the light look in every script direction)."""
    from angelo_agent import director_layout, studio_workspaces
    manual = [ws.name for ws in bpy.data.workspaces
              if not director_layout.is_director_workspace(ws) and studio_workspaces.tab_kind(ws) is None][:3]
    places = [("director", None)] + [("manual", name) for name in manual]
    dark = [(mode, tab, scale, language, "dark") for mode, tab in places for scale in SCALES for language in LANGUAGES]
    light = [(mode, tab, scale, language, "light") for mode, tab in places[:2] for scale in SCALES
             for language in ("en_US", "de_DE", "ar_EG")]
    return dark + light + [(places[0][0], places[0][1], 1.0, "en_US", "dark")]   # Back to the dark look.


def apply_look(look):
    from angelo_agent import setup_card
    path = setup_card.look_preset_path(bpy, look)
    if path:
        bpy.ops.script.execute_preset(filepath=path, menu_idname="USERPREF_MT_interface_theme_presets")
    state["look"] = look


def finish(error=None):
    prefs.view.language, prefs.view.ui_scale = "en_US", 1.0
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_TOP_BAR_PERMANENCE", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        state["since"].pop(phase, None)   # A later visit of the same case waits afresh (it timed out at once).
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def place(mode, tab):
    """Ask for ``mode`` (and Manual's ``tab``); True once the window is there and settled."""
    from angelo_agent import director
    w = window()
    if not settled():   # Director refuses a mode change while the chat dock moves ("Wait for the chat dock ...").
        return False
    if mode == "director" and not director.is_director(w):
        director.request_mode(w, "DIRECTOR", persist=False)
        return False
    if mode == "manual" and director.is_director(w):
        director.request_mode(w, "MANUAL", persist=False)
        return False
    if tab and w.workspace.name != tab:
        w.workspace = bpy.data.workspaces[tab]
        return False
    return settled()


def tick():
    from angelo_agent import director, studio_toggle, top_bar_fit
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 280, "Suite timed out"
        if phase == "boot":
            if not state.get("recording"):
                record_top_bar()
                record_flows_menu()
                state["recording"] = True
            if not ("top_bar" in state and settled() and director.is_director(window())):
                return .3
            prefs.view.use_translate_interface = True
            state["todo"] = cases()
            state["phase"] = "case"
            return .1
        if phase == "case":
            if not state["todo"]:
                checks["studio_and_flows_present_and_visible_everywhere"] = bool(state["cases"]) and all(
                    case["present"] and case["visible"] for case in state["cases"].values())
                checks["chat_studio_flows_mode_in_order_in_every_language"] = all(
                    case["order"] for case in state["cases"].values())
                checks["flows_draws_its_catalogue_string"] = all(case["catalogue"] for case in state["cases"].values())
                checks["every_glyph_reads_on_its_button_in_light_and_dark"] = bool(state["cases"]) and all(
                    case["glyph_contrast_ok"] for case in state["cases"].values() if "glyph_contrast_ok" in case)
                checks["the_mode_icon_sits_beside_its_label_everywhere"] = all(
                    case["mode_gap_ok"] for case in state["cases"].values() if "mode_gap_ok" in case)
                checks["the_chat_shortcut_keeps_its_order_in_every_language"] = all(
                    case["chat_keys"]["in_order"] for case in state["cases"].values())
                checks["arabic_shows_the_chat_shortcut_somewhere"] = any(
                    key.endswith("-ar_EG") and case["chat_keys"]["shown"] for key, case in state["cases"].items())
                checks["arabic_flows_is_translated"] = any(
                    key.endswith("-ar_EG") and case["labels"].get("flows") not in ("", "Flows")
                    for key, case in state["cases"].items())
                checks["studio_and_flows_present_in_angelo_light"] = any(
                    key.endswith("-light") for key in state["cases"]) and all(
                    case["present"] and case["visible"] for key, case in state["cases"].items() if key.endswith("-light"))
                checks["studio_and_flows_open_on_a_click_everywhere"] = bool(state.get("clicks")) and all(
                    click["studio"] and click["flows"] for click in state["clicks"].values())
                prefs.view.ui_scale, prefs.view.language = 1.0, "en_US"
                state["menu_modes"] = ["director", "manual"]
                state["phase"] = "window_menu"
                return .4
            mode, tab, scale, language, look = state["todo"][0]
            if not wait("case %s" % (state["todo"][0],), lambda: place(mode, tab), limit=15):
                return .3
            if state.get("look", "dark") != look:
                apply_look(look)
                return .4
            if prefs.view.ui_scale != scale or prefs.view.language != language:
                top_bar_fit.widths.pop(window().as_pointer(), None)   # Re-recorded by the next draw.
                prefs.view.ui_scale, prefs.view.language = scale, language
                return .4
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            from angelo_agent import flow_open
            from angelo_agent.ui_text import iface_
            key = "%s-%s-%g-%s%s" % (mode, (tab or "").replace(" ", "_"), scale, language,
                                     "-light" if look == "light" else "")
            drawn = top_bar_fit.widths.get(window().as_pointer(), {}).get("flows")
            drawn_since = state.setdefault("drawn_since", {}).setdefault(key, time.monotonic())
            # This language's bar has drawn once its Flows width is recorded again (cleared at the switch) with this
            # language's label, or none at the icon-only level. An empty record is no draw yet: counting it as one
            # captured the last language's bar (6ceaef365ff: the ar case showed ru, the ru case de).
            if drawn is None or drawn[0] not in (iface_(flow_open.TOP_BAR_LABEL), ""):
                # The top bar is a global area (outside screen.areas and redraw_timer): the language change's own
                # window notifier redraws it on a later pass.
                assert time.monotonic() - drawn_since < 10, "the %s bar never drew" % language
                return .2
            # Drawn is not yet shown: a capture right after the first draw read back the frame before it (rerun on
            # 9d7186ae723: the ar case's label was recorded in Arabic while its capture showed the ru bar). Half a
            # second more, so the drawn bar is the one on screen.
            shown_since = state.setdefault("shown_since", {}).setdefault(key, time.monotonic())
            if time.monotonic() - shown_since < .5:
                return .2
            path = capture("permanence-%s.png" % key)
            fitted = top_bar_fit.widths.get(window().as_pointer(), {})
            rects = buttons()
            px = pixels(path)
            background = bar_colour(px)
            shares = {name: round(visible(px, rect, background), 3) for name, rect in rects.items()}
            from angelo_agent import flow_open
            from angelo_agent.ui_text import iface_
            room = top_bar_fit.rooms.get(window().as_pointer(), {})
            flows_label = fitted.get("flows", ("",))[0]
            from angelo_agent import shortcuts
            keys, chat_label = shortcuts.text(shortcuts.CHAT.idname), fitted.get("chat", ("",))[0]
            gap = mode_gap(px, background)
            contrast = {name: glyph_contrast(px, rect) for name, rect in rects.items()}
            state["cases"][key] = {
                # Each glyph against its own button's fill, at least 3:1 in Light and Dark (1.0.5 review).
                "glyph_contrast": contrast,
                "glyph_contrast_ok": all(v is not None and v >= 3.0 for v in contrast.values()),
                # The mode button's icon beside its label (≤ 10 px × the interface scale: Blender's own header-menu
                # spacing, coherence SD1 ruling 2026-10-23), not floated apart.
                "mode_gap": gap, "mode_gap_ok": gap is not None and gap <= 10 * prefs.system.ui_scale,
                # The shortcut as Blender prints it ("⌃ ⌥ N"), one left-to-right run; in Arabic before the label.
                "chat_keys": {"keys": keys, "label": chat_label, "shown": bool(keys) and keys in chat_label,
                              "in_order": not keys or keys not in chat_label or (
                                  chat_label.startswith(keys) if language == "ar_EG" else chat_label.endswith(keys))},
                "present": "studio" in fitted and "flows" in fitted, "labels": {n: fitted[n][0] for n in fitted},
                # Drawn left to right Chat, Studio, Flows, then the mode menu, in every language (never mirrored).
                "order": list(fitted)[:4] == ["chat", "studio", "flows", "mode"],
                # Flows draws this language's catalogue string (an icon only when the bar went compact).
                "catalogue": flows_label in ("", iface_(flow_open.TOP_BAR_LABEL)),
                "visible": shares.get("studio", 0) > VISIBLE and shares.get("flows", 0) > VISIBLE, "shares": shares,
                "level": top_bar_fit.rooms.get(window().as_pointer(), {}).get("level")}
            state["todo"].pop(0)
            if language == "en_US":   # A real click on each, in every place and at both scales.
                state["click"] = {"key": key, "rects": rects}
                state["phase"] = "click_studio"
            return .1
        if phase == "click_studio":
            x0, y0, x1, y1 = state["click"]["rects"]["studio"]
            press((x0 + x1) / 2, (y0 + y1) / 2)
            state["phase"] = "studio_opened"
            return .6
        if phase == "studio_opened":
            if not studio_shown() and not wait("studio %s" % state["click"]["key"], lambda: False, limit=4):
                return .2
            # Closed (window) or left (tab) again: the next click starts from the same place.
            state.setdefault("clicks", {})[state["click"]["key"]] = {"studio": close_studio()}
            state["phase"] = "flows_click"
            return .6
        if phase == "flows_click":
            x0, y0, x1, y1 = state["click"]["rects"]["flows"]
            state["flows_draws_before"] = state.get("flows_menu_draws", 0)
            press((x0 + x1) / 2, (y0 + y1) / 2)
            state["phase"] = "flows_opened"
            return .6
        if phase == "flows_opened":
            from angelo_agent import flow_open
            before = state["flows_draws_before"]
            since = state.setdefault("since", {}).setdefault("flows " + state["click"]["key"], time.monotonic())
            if not flows_shown(before) and time.monotonic() - since < 4:
                return .2
            click = state["clicks"][state["click"]["key"]]
            click["flows"] = flows_shown(before)
            click["route"] = flow_open.routes[-1] if flow_open.routes else None   # What the operator did.
            from angelo_agent import flow_menu
            click["menu_history"] = list(getattr(flow_menu, "history", []))[-4:]   # Why a popup did not stay open.
            capture("permanence-flows-%s.png" % state["click"]["key"])
            close_flows()
            state["phase"] = "case"
            return .3
        if phase == "window_menu":   # Window > Media Studio and Generation Flows, in each mode.
            if not state["menu_modes"]:
                checks["window_menu_offers_media_studio_and_flows_in_both_modes"] = bool(state.get("menus")) and all(
                    menu["drawn"] and menu["visible"] and menu["studio"] and menu["flow"]
                    for menu in state["menus"].values())
                state["ab_todo"] = [(1.0, "en_US"), (1.0, "de_DE"), (2.0, "en_US"), (2.0, "de_DE")]
                state["phase"] = "ab_setup"
                return .3
            mode = state["menu_modes"][0]
            if not wait("menu " + mode, lambda: place(mode, None), limit=15):
                return .3
            state["menu_before"] = str(capture("window-menu-%s-before.png" % mode))
            state["menu_drawn_before"] = studio_toggle.menus_drawn.get("TOPBAR_MT_window", 0)
            state["menu_diag"] = {"call": menu_moment()}
            # A click on the top bar's own Window menu, as a person opens it. wm.call_menu's popup at the pointer drew
            # and then closed within 0.8 s in Manual, with nothing in the window changed (rerun on 9d7186ae723), which
            # is not the menu a person gets.
            import qa_input
            w = window()
            qa_input.press(w, int(w.width * 0.07), w.height - 12)
            state["phase"] = "window_menu_early"
            return qa_input.DELAY + .15
        if phase == "window_menu_early":   # Two looks: a menu that opens and is closed again shows only early.
            mode = state["menu_modes"][0]
            state["menu_early"] = str(capture("window-menu-%s-early.png" % mode))
            state["menu_diag"]["early"] = menu_moment()
            state["phase"] = "window_menu_open"
            return .65
        if phase == "window_menu_open":
            import numpy
            mode = state["menu_modes"].pop(0)
            after = capture("window-menu-%s.png" % mode)
            state["menu_diag"]["late"] = menu_moment()
            a = pixels(state["menu_before"])
            height, width, _ = a.shape
            box = (slice(int(height * .25), int(height * .97)), slice(0, int(width * .35)))   # Under the Window menu.
            early = float((numpy.abs(a[box] - pixels(state.pop("menu_early"))[box]).sum(axis=2) > 24).mean())
            changed = float((numpy.abs(a[box] - pixels(after)[box]).sum(axis=2) > 24).mean())
            state["menu_diag"]["early_changed"] = round(early, 4)
            print("   window_menu_diag", mode, json.dumps(state["menu_diag"]))
            for value in ('PRESS', 'RELEASE'):
                window().event_simulate(type='ESC', value=value, x=window().width // 2, y=window().height // 2)
            # Each entry's own operator, as its click runs it: Media Studio (its window, or Manual's tab), then a
            # template's flow (its window, or Manual's Generation Flows tab). A workspace switch lands a tick later.
            with bpy.context.temp_override(window=window()):
                if hasattr(bpy.types, "ANGELO_OT_open_media_studio"):
                    bpy.ops.angelo.open_media_studio(kind='AUTO')
                else:
                    bpy.ops.angelo.toggle_studio(mode='OPEN')
            state["menu_result"] = {"mode": mode, "drawn": studio_toggle.menus_drawn.get("TOPBAR_MT_window", 0)
                                    > state["menu_drawn_before"], "visible": changed > 0.02, "changed": round(changed, 4),
                                    "diag": state.pop("menu_diag")}
            state["phase"] = "window_menu_studio"
            return .7
        if phase == "window_menu_studio":
            state["menu_result"]["studio"] = close_studio()
            state["phase"] = "window_menu_flow"
            return .7
        if phase == "window_menu_flow":
            if not state.get("flow_requested"):
                state["flow_requested"] = True
                with bpy.context.temp_override(window=window()):
                    bpy.ops.angelo.flow_create(template="image_to_3d")
                return .8
            from angelo_agent import flow_open, studio_workspaces
            state.pop("flow_requested")
            target = flow_open.flow_window(bpy.context.window_manager.windows)
            on_tab = (studio_workspaces.active(window(), "flows") and window().screen.areas[0].type == 'NODE_EDITOR'
                      and window().screen.areas[0].spaces.active.node_tree is not None)
            if target is not None:
                with bpy.context.temp_override(window=target, area=target.screen.areas[0]):
                    bpy.ops.wm.window_close()
            if studio_workspaces.active(window(), "flows"):
                back = bpy.data.workspaces.get(studio_workspaces.came_from(window()) or "")
                if back is not None:
                    window().workspace = back
            result = state.pop("menu_result")
            mode = result.pop("mode")
            result["flow"] = target is not None or on_tab
            state.setdefault("menus", {})[mode] = result
            state["phase"] = "window_menu"
            return .5
        if phase == "ab_setup":   # An Asset Browser in Manual's default workspace, in place of its timeline.
            if not wait("ab_setup", lambda: place("manual", "Layout"), limit=15):   # Never an Angelo tab.
                return .3
            from angelo_agent.editor_types import set_editor
            area = min(window().screen.areas, key=lambda a: a.height)   # The timeline, at the bottom.
            state["ab_area"], state["ab_was"] = area.as_pointer(), area.ui_type
            set_editor(window(), area, ui_type='ASSETS')
            state["phase"] = "ab_case"
            return .6
        if phase == "abw_width":   # Coherence AB1: the same browser at three widths; each half as wide as the last.
            if not state["abw_widths"]:
                ok = bool(state.get("abw")) and all(case["clear"] for case in state["abw"].values())
                checks["asset_browser_pills_never_cover_blenders_header"] = ok
                wide = [case for key, case in state.get("abw", {}).items() if key.startswith("wide-1")]
                checks["asset_browser_full_width_shows_the_pills"] = bool(wide) and all(
                    case["labels"] is not None for case in wide)
                for pointer in state.get("abw_extra", []):   # The halves split off for the sweep.
                    extra = next((a for a in window().screen.areas if a.as_pointer() == pointer), None)
                    if extra is not None:
                        with bpy.context.temp_override(window=window(), area=extra):
                            bpy.ops.screen.area_close()
                area = ab_area()
                if area is not None:
                    from angelo_agent.editor_types import set_editor
                    set_editor(window(), area, ui_type=state["ab_was"])
                prefs.view.ui_scale, prefs.view.language = 1.0, "en_US"
                state["phase"] = "complete"
                finish()
                return None
            if state["abw_widths"][0] != "wide" and not state.get("abw_split_done"):
                before = {a.as_pointer() for a in window().screen.areas}
                with bpy.context.temp_override(window=window(), area=ab_area()):
                    bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
                state.setdefault("abw_extra", []).extend(
                    a.as_pointer() for a in window().screen.areas if a.as_pointer() not in before)
                state["abw_split_done"] = True
                return .6
            state["abw_todo"] = [(1.0, "en_US"), (1.0, "de_DE"), (2.0, "en_US"), (2.0, "de_DE")]
            state["phase"] = "abw_case"
            return .3
        if phase == "abw_case":
            if not state["abw_todo"]:
                state["abw_widths"].pop(0)
                state["abw_split_done"] = False
                state["phase"] = "abw_width"
                return .3
            scale, language = state["abw_todo"][0]
            if prefs.view.ui_scale != scale or prefs.view.language != language:
                prefs.view.ui_scale, prefs.view.language = scale, language
                return .5
            state["abw_todo"].pop(0)
            state.setdefault("abw", {})["%s-%g-%s" % (state["abw_widths"][0], scale, language)] = header_clear(
                ab_area(), "%s-%g-%s" % (state["abw_widths"][0], scale, language))
            if state["abw_widths"][0] == "narrow" and "library_menu" not in state:   # SD2: the route at any width.
                from angelo_agent import asset_browser_entry as entry
                entry.library_menu_drawn.clear()
                area = ab_area()
                header = ab_region(area, 'HEADER')
                with bpy.context.temp_override(window=window(), area=area, region=header):
                    bpy.ops.wm.call_menu(name="ASSETBROWSER_MT_library")
                state["phase"] = "abw_menu"
                return .5
            return .3
        if phase == "abw_menu":
            from angelo_agent import asset_browser_entry as entry
            capture("asset-browser-narrow-library-menu.png")
            drawn = [list(e) for e in entry.library_menu_drawn]
            state["library_menu"] = {"drawn": drawn, "pills": state["abw"].get(
                [k for k in state["abw"] if k.startswith("narrow")][-1], {}).get("labels")}
            checks["a_narrow_asset_browser_offers_both_in_its_library_menu"] = any(
                set(e) >= {"studio", "flows"} for e in drawn)
            w = window()
            w.event_simulate(type='ESC', value='PRESS')
            w.event_simulate(type='ESC', value='RELEASE')
            state["phase"] = "abw_case"
            return .4
        if phase == "ab_case":
            if not state["ab_todo"]:
                with_room = [case for case in state.get("ab", {}).values() if case.get("room", True)]
                checks["asset_browser_offers_media_studio_and_flows"] = bool(with_room) and all(
                    case["visible"] and case["studio"] and case["flows"] for case in with_room)
                state["abw_widths"] = ["wide", "medium", "narrow"]
                state["abw_todo"] = []
                state["phase"] = "abw_width"
                return .3
            scale, language = state["ab_todo"][0]
            if prefs.view.ui_scale != scale or prefs.view.language != language:
                prefs.view.ui_scale, prefs.view.language = scale, language
                return .5
            from angelo_agent import asset_browser_entry as entry
            area = ab_area()
            area.tag_redraw()
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            key = "%g-%s" % (scale, language)
            path = capture("asset-browser-%s.png" % key)
            px = pixels(path)
            rects = ab_rects(area)
            background = numpy_main(px, ab_region(area, 'HEADER'))
            shares = {action: round(visible(px, rect, background), 3) for action, rect in rects.items()}
            room = entry.header_labels(ab_region(area, 'HEADER').width, scale, area.spaces.active) is not None
            state.setdefault("ab", {})[key] = {"shares": shares, "drawn": entry._drawn.get(area.as_pointer(), {}),
                                               "visible": len(shares) == 2 and all(v > VISIBLE for v in shares.values()),
                                               "studio": False, "flows": False, "room": room}
            state["ab_key"], state["ab_rects"] = key, rects
            if not room:   # No room for the pills in this header (by design, AB1): nothing to click; the next case.
                state["ab_todo"].pop(0)
                return .3
            press(*centre(rects["studio"]))
            state["phase"] = "ab_studio"
            return .7
        if phase == "ab_studio":
            case = state["ab"][state["ab_key"]]
            case["studio"] = close_studio()
            state["flows_draws_before"] = state.get("flows_menu_draws", 0)
            press(*centre(state["ab_rects"]["flows"]))
            state["phase"] = "ab_flows"
            return .7
        if phase == "ab_flows":
            case = state["ab"][state["ab_key"]]
            before = state["flows_draws_before"]
            since = state.setdefault("since", {}).setdefault("ab_flows " + state["ab_key"], time.monotonic())
            if not flows_shown(before) and time.monotonic() - since < 4:
                return .2
            case["flows"] = flows_shown(before)
            close_flows()
            state["ab_todo"].pop(0)
            state["phase"] = "ab_case"
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
