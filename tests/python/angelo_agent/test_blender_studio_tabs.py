# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual's Media Studio and Generation Flows tabs (1.0.5, ``studio_workspaces``).

Real default startup and simulated input; no CLI, account, network or inference. The file gets both tabs once, in
the order Director · Media Studio · Generation Flows · the rest. In Manual a real click on the top bar's Studio shows
the Media Studio tab, filled by Media Studio with no stock header (read from the capture's pixels: its top band is
the Studio canvas, not Blender's grey header), and a second click goes back where it came from; Flows does the same
with the Generation Flows tab, whose start page draws the Generation Flows rows. In English, German and Arabic at UI
scale 1 and 2 the strip is captured with each tab active. A tab the person deleted is not added back after the file
is saved and opened again. Captures: ``tabs-*.png``.

Run through ``run_native_suites.py --gui --include test_blender_studio_tabs``; check ``studio-tabs.json`` for
``success`` and ``phase == "complete"``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-tabs-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "studio-tabs.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "strips": {}}
checks = state["checks"]
started = time.monotonic()
GREY_HEADER = (47, 47, 47)   # Blender's editor header in these captures (calibrated, studio-2 before 1.0.5's fix).


def window():
    return bpy.context.window_manager.windows[0]


class QA_OT_hold_tool(bpy.types.Operator):
    """A stand-in for a Blender tool in progress: a modal operator that passes every event on until told to end."""
    bl_idname = "qa.hold_tool"
    bl_label = "QA hold tool"
    done = False

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return {'FINISHED'} if QA_OT_hold_tool.done else {'PASS_THROUGH'}


bpy.utils.register_class(QA_OT_hold_tool)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def press(x, y):
    import qa_input   # The pointer arrives, then the press, a pass later (a click in one pass could miss).
    qa_input.press(window(), x, y)


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def top_bar_point(name):
    """Inside a top-bar button: Chat, Studio and Flows follow Chat's left edge (chat_toggle.anchors), each as wide as
    top_bar_fit recorded it."""
    from angelo_agent import chat_toggle, top_bar_fit
    left, _, bottom = chat_toggle.anchors[window().as_pointer()]
    fitted = top_bar_fit.widths[window().as_pointer()]
    x = left
    for each in ("chat", "studio", "flows"):
        _, units, unit = fitted[each]
        if each == name:
            return x + 0.4 * units * unit, bottom + unit * 0.6
        x += units * unit


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


def main_colour(block):
    import numpy
    flat = block.reshape(-1, 3)
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    value = int(values[counts.argmax()])
    return numpy.array([value // 65536, value // 256 % 256, value % 256])


def tab_area_looks(path):
    """The tab's area as drawn: content (share away from its main colour) and whether its top band is the stock
    header's grey."""
    import numpy
    area = window().screen.areas[0]
    px = pixels(path)[area.y:area.y + area.height, area.x:area.x + area.width]
    main = main_colour(px)
    content = float((numpy.abs(px.reshape(-1, 3) - main).sum(axis=1) > 24).mean())
    band = main_colour(px[-max(4, int(6 * prefs.system.ui_scale)):, int(px.shape[1] * .3):int(px.shape[1] * .6)])
    grey = int(numpy.abs(band - numpy.array(GREY_HEADER)).sum()) <= 12
    return {"content": round(content, 4), "stock_header": grey, "areas": len(window().screen.areas)}


def chrome(area):
    """Blender's chrome in a tab's editor: the space's header/sidebar/toolbar flags still on, and the regions drawn
    wider than a hidden region's sliver."""
    from angelo_agent import chrome_guard
    return {"flags": chrome_guard.shown(area),
            "regions": {r.type: r.width for r in area.regions if r.type in {'HEADER', 'TOOLS', 'UI'} and r.width > 2}}


def strip_order():
    from angelo_agent import director_layout, studio_workspaces
    shown = studio_workspaces.strip()   # As the tabs show them; bpy.data.workspaces is always alphabetical.
    assert shown is not None, "Needs the 1.0.5 binary (WorkSpace.angelo_tab_order)"
    names = [w.name for w in shown]
    director = next((w.name for w in bpy.data.workspaces if director_layout.is_director_workspace(w)), None)
    return names, director


def finish(error=None):
    prefs.view.language, prefs.view.ui_scale = "en_US", 1.0
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STUDIO_TABS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=25):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import director, flow_window, studio_workspaces as tabs
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 300, "Suite timed out"
        if phase == "boot":
            if not wait(phase, lambda: settled() and director.is_director(window()) and not tabs._building
                        and tabs.tab("studio") is not None and tabs.tab("flows") is not None):
                return .3
            names, director_name = strip_order()
            state["strip"] = names
            first = [n for n in names if n in (director_name, tabs.TABS["studio"], tabs.TABS["flows"])]
            checks["the_file_gets_both_tabs_in_order"] = first == [director_name, tabs.TABS["studio"], tabs.TABS["flows"]]
            # The editor-type menu's entry is Media Studio with its spark frame (1.0.5 vocabulary; built-in icon
            # ANGELO_MEDIA_STUDIO), never the legacy "Generative AI" with the stock image icon.
            area = window().screen.areas[0]
            icons = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items
            entry = {"name": bpy.types.UILayout.enum_item_name(area, "ui_type", "GENERATIVE_AI"),
                     "icon": bpy.types.UILayout.enum_item_icon(area, "ui_type", "GENERATIVE_AI"),
                     "glyph": icons["ANGELO_MEDIA_STUDIO"].value if "ANGELO_MEDIA_STUDIO" in icons else None}
            state["editor_type_entry"] = entry
            checks["the_editor_type_entry_is_media_studio_with_its_glyph"] = (
                entry["name"] in ("Media Studio", bpy.app.translations.pgettext_iface("Media Studio"))
                and entry["glyph"] is not None and entry["icon"] == entry["glyph"])
            # The build switches the window through the tabs it works on (Blender's operators act on the active
            # workspace): one event-loop pass per step, so a first launch sees it for a moment, never a second or two.
            state["build"] = dict(tabs.last_build)
            # Built under the start-up cover before anything shows (studio_workspaces.startup_build_pending), or not at
            # all when the file already had them: either way the window never shows the build for long.
            checks["building_the_tabs_shows_them_under_half_a_second"] = (
                not state["build"] or 0 < state["build"].get("seconds", 9) < 0.5)
            checks["no_duplicate_tabs"] = not any(n.startswith(tabs.TABS["studio"] + ".") or
                                                  n.startswith(tabs.TABS["flows"] + ".") for n in names)
            director.request_mode(window(), "MANUAL", persist=False)
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .3
            state["home"] = window().workspace.name
            capture("tabs-0-manual-before.png")
            press(*top_bar_point("studio"))
            state["phase"] = "studio_tab"
            return .8
        if phase == "studio_tab":
            if not wait(phase, lambda: tabs.active(window(), "studio")):
                return .2
            looks = state["studio_tab"] = tab_area_looks(capture("tabs-1-media-studio-tab.png"))
            checks["studio_switches_to_a_full_area_media_studio_tab"] = (
                looks["areas"] == 1 and looks["content"] > 0.015 and not looks["stock_header"])
            state["studio_chrome"] = chrome(window().screen.areas[0])
            checks["the_media_studio_tab_has_no_stock_sidebar_or_toolbar"] = not state["studio_chrome"]["flags"]
            press(*top_bar_point("studio"))   # Pressed: back where it came from.
            state["phase"] = "studio_back"
            return .8
        if phase == "studio_back":
            if not wait(phase, lambda: window().workspace.name == state["home"]):
                return .2
            checks["a_second_click_goes_back"] = window().workspace.name == state["home"]
            press(*top_bar_point("flows"))
            state["phase"] = "flows_tab"
            return .8
        if phase == "flows_tab":
            if not wait(phase, lambda: tabs.active(window(), "flows")):
                return .2
            area = window().screen.areas[0]
            if not wait("flows_rows", lambda: bool((flow_window._bars.get(area.as_pointer(), {}).get("drawn") or {})
                                                   .get("start_rows")), limit=6):
                return .2
            looks = state["flows_tab"] = tab_area_looks(capture("tabs-2-generation-flows-tab.png"))
            drawn = flow_window._bars[area.as_pointer()]["drawn"]
            state["flows_drawn"] = drawn
            checks["flows_switches_to_a_full_area_flows_tab_with_its_start_page"] = (
                looks["areas"] == 1 and not looks["stock_header"] and looks["content"] > 0.015
                and {"flows:new", "flows:template:image_to_3d"} <= set(drawn["start_rows"]))
            # Coherence W2 (1.0.5): no node toolbar down the left, no Active Tool sidebar on the right.
            state["flows_chrome"] = chrome(area)
            checks["the_generation_flows_tab_has_no_stock_sidebar_or_toolbar"] = not state["flows_chrome"]["flags"]
            names, director_name = strip_order()
            state["strip_after_visits"] = names
            checks["the_pair_stays_together_after_director"] = tabs.in_order()
            w = window()   # The person presses N and T over the flow canvas: no stock sidebar or toolbar comes back.
            x, y = area.x + area.width // 2, area.y + area.height // 2
            w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
            for key in ('N', 'T'):
                w.event_simulate(type=key, value='PRESS', x=x, y=y)
                w.event_simulate(type=key, value='RELEASE', x=x, y=y)
            state["phase"] = "flows_keys"
            return .6
        if phase == "flows_keys":
            from angelo_agent import chrome_guard
            area = window().screen.areas[0]
            keys_at = state.setdefault("since", {}).setdefault("flows_keys_at", time.monotonic())
            if chrome(area)["flags"] and time.monotonic() - keys_at < 2:   # Undone within a moment, by events.
                return .1
            state["flows_chrome_after_keys"] = chrome(area)
            state["chrome_undone"] = list(chrome_guard.undone)
            checks["n_and_t_leave_no_stock_region_in_an_angelo_tab"] = not state["flows_chrome_after_keys"]["flags"]
            checks["no_timer_keeps_the_chrome_hidden"] = not hasattr(tabs, "keep_chrome_hidden") and not any(
                getattr(chrome_guard, name, None) and bpy.app.timers.is_registered(getattr(chrome_guard, name))
                for name in ("keep_chrome_hidden", "poll"))
            press(*top_bar_point("flows"))
            state["phase"] = "flows_back"
            return .8
        if phase == "flows_back":
            if not wait(phase, lambda: window().workspace.name == state["home"]):
                return .2
            checks["flows_goes_back_too"] = True
            state["strip_todo"] = [(scale, language, kind) for scale in (1.0, 2.0)
                                   for language in ("en_US", "de_DE", "ar_EG") for kind in ("studio", "flows")]
            state["phase"] = "strip"
            return .3
        if phase == "strip":   # The strip with each tab active, in English, German and Arabic at UI scale 1 and 2.
            if not state["strip_todo"]:
                prefs.view.ui_scale, prefs.view.language = 1.0, "en_US"
                window().workspace = bpy.data.workspaces[state["home"]]
                state["phase"] = "banner_modal"
                return .5
            scale, language, kind = state["strip_todo"][0]
            if prefs.view.ui_scale != scale or prefs.view.language != language or not tabs.active(window(), kind):
                prefs.view.ui_scale, prefs.view.language = scale, language
                window().workspace = tabs.tab(kind)
                return .5
            capture("tabs-strip-%s-%g-%s.png" % (kind, scale, language))
            state["strip_todo"].pop(0)
            return .1
        if phase == "banner_modal":   # 1.0.5 blocker: a Studio area first drawn while a modal runs never raises.
            window().workspace = tabs.tab("studio")
            state["phase"] = "banner_open"
            return .6
        if phase == "banner_open":
            # A running modal tool is an interaction (as the startup splash, setup card and welcome are; the welcome
            # needs a 3D view this tab has none of, so a stand-in tool). With it running, the shown Media Studio
            # console is emptied, so Blender runs its banner on the next draw; the banner prepares the area and logs
            # no traceback (the runner fails on one), as at startup.
            from angelo_agent import input_policy
            with bpy.context.temp_override(window=window()):
                bpy.ops.qa.hold_tool('INVOKE_DEFAULT')
            area = window().screen.areas[0]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window(), area=area, region=region):
                bpy.ops.console.clear(scrollback=True, history=True)
            state["banner_modal"] = {"interaction": input_policy.interaction_active(window()),
                                     "cleared": not area.spaces.active.scrollback}
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)   # The banner runs in this draw.
            state["phase"] = "banner_drawn"
            return .4
        if phase == "banner_drawn":
            area = window().screen.areas[0]
            info = state["banner_modal"]
            info["prepared"] = bool(area.spaces.active.scrollback)
            checks["a_studio_area_drawn_under_a_modal_is_prepared_without_a_traceback"] = (
                info["interaction"] and info["cleared"] and info["prepared"])
            QA_OT_hold_tool.done = True   # The stand-in tool ends on its next event.
            window().workspace = bpy.data.workspaces[state["home"]]
            state["phase"] = "delete"
            return .5
        if phase == "delete":   # The person deletes the Media Studio tab; the file keeps it deleted.
            if not state.get("delete_from_home") and not wait(phase, lambda: window().workspace.name == state["home"]):
                return .2
            state["delete_from_home"] = True
            target = tabs.tab("studio")
            if window().workspace != target:   # Delete Workspace acts on the shown workspace, as a person's does.
                window().workspace = target
                return .3
            with bpy.context.temp_override(window=window()):
                bpy.ops.workspace.delete()   # Applied on the next event-loop pass (Blender's notifier).
            state["phase"] = "deleted"
            return .3
        if phase == "deleted":   # Saved once the deletion has applied: an earlier save kept the tab in the file.
            if not wait(phase, lambda: tabs.tab("studio") is None, limit=10):
                return .2
            path = OUT / "tabs-deleted.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
            state["saved"] = str(path)
            state["phase"] = "reopened"
            bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=True)
            return 1.0
        if phase == "reopened":
            if not wait(phase, lambda: settled() and not tabs._building, limit=20):
                return .3
            since = state.setdefault("since", {}).setdefault("reopened_grace", time.monotonic())
            if time.monotonic() - since < 3:   # Give ensure() its chance to (wrongly) add it back.
                return .3
            checks["a_deleted_tab_is_not_added_back"] = tabs.tab("studio") is None and tabs.tab("flows") is not None
            state["strip_after_reopen"] = strip_order()[0]
            # + Add Workspace has an Angelo section (C: workspace_edit.cc draws ANGELO_MT_workspace_add): it draws.
            menu = tabs.ANGELO_MT_workspace_add
            original = menu.draw

            def draw(self, context):
                state["angelo_section_draws"] = state.get("angelo_section_draws", 0) + 1
                return original(self, context)
            menu.draw = draw
            state["add_menu_before"] = str(capture("tabs-3-before-add-workspace.png"))
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.call_menu(name="WORKSPACE_MT_add")
            state["phase"] = "add_menu"
            return .8
        if phase == "add_menu":
            capture("tabs-4-add-workspace-angelo-section.png")
            checks["add_workspace_has_the_angelo_section"] = state.get("angelo_section_draws", 0) > 0
            for value in ('PRESS', 'RELEASE'):
                window().event_simulate(type='ESC', value=value, x=window().width // 2, y=window().height // 2)
            with bpy.context.temp_override(window=window()):   # Its Media Studio item: the deleted tab, back once.
                bpy.ops.angelo.workspace_tab_add(kind="studio")
            state["phase"] = "added_back"
            return 1.0
        if phase == "added_back":
            if not wait(phase, lambda: not tabs._building and tabs.tab("studio") is not None, limit=10):
                return .3
            names = strip_order()[0]
            checks["the_angelo_section_adds_the_tab_back_once"] = (
                names.count(tabs.TABS["studio"]) == 1 and not any(n.startswith(tabs.TABS["studio"] + ".") for n in names))
            state["phase"], state["type_scales"] = "type_menu", [1.0, 2.0]
            return .3
        if phase == "type_menu":   # The editor-type menu, open, at UI scale 1 and 2: Media Studio with its glyph.
            if not state["type_scales"]:
                prefs.view.ui_scale = 1.0
                state["phase"] = "complete"
                finish()
                return None
            scale = state["type_scales"][0]
            if window().workspace.name != state["home"]:   # A tab with a header (an Angelo tab hides its own).
                window().workspace = bpy.data.workspaces[state["home"]]
                return .5
            if prefs.view.ui_scale != scale:
                prefs.view.ui_scale = scale
                return .8
            # Any editor's header starts with its editor-type button (the window may show an Angelo tab, no 3D view).
            area = max((a for a in window().screen.areas if any(r.type == 'HEADER' and r.height > 1 for r in a.regions)),
                       key=lambda a: a.width * a.height)
            header = next(r for r in area.regions if r.type == 'HEADER' and r.height > 1)
            unit = round(18 * scale) + 2 * bpy.context.preferences.system.pixel_size
            press(header.x + int(4 * scale) + unit * 0.8, header.y + header.height // 2)   # Its editor-type button.
            state["phase"] = "type_menu_open"
            return .8
        if phase == "type_menu_open":
            scale = state["type_scales"].pop(0)
            capture("tabs-5-editor-type-menu-x%d.png" % scale)
            for value in ('PRESS', 'RELEASE'):
                window().event_simulate(type='ESC', value=value, x=window().width // 2, y=window().height // 2)
            state["phase"] = "type_menu"
            return .5
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5, persistent=True)
