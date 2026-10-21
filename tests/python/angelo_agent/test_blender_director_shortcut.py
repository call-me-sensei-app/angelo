# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Director / Manual shortcut works over every editor, and Blender's Ctrl+Alt+D still does Blender's work.

Real default startup and real keys (``Window.event_simulate``). No CLI, account, network or inference.

Evidence: 1.0 to 1.0.2 bound the toggle to Ctrl+Option+D, which Blender's "User Interface" keymap (almost every
region) and Outliner take first, so it worked only over the 3D view (``shortcuts.py``). The contract:
Ctrl+Option+M everywhere, Ctrl+Option+D kept over the 3D view. Covered: in Manual mode Ctrl+Option+M
(``shortcuts.DIRECTOR``, read from the keymap as bound) typed with the pointer over each Blender editor's main
region, header and visible sidebar, the top bar, the status bar and the chat switches to Director; typed in Director
it switches back; a driver on the active object survives every press. The Director / Manual menu shows it (a
screenshot) and no keymap binds the shown toggle to anything else. Ctrl+Option+D over the 3D view switches both
ways. Blender's own Ctrl+Alt+D elsewhere: over the driven Location X field it removes that driver; in the Outliner
it stays Blender's (no Angelo operator runs); neither changes the mode. Last, after a keyconfig preset reload (which
drops C-registered items) the shortcut still switches.

Run through ``run_native_suites.py --gui --include test_blender_director_shortcut``; check
``director-shortcut.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-director-shortcut-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "director-shortcut.json"
bpy.context.preferences.use_preferences_save = False
# Every Blender editor an area can show (Area.ui_type); one Blender does not offer is recorded, never failed.
EDITORS = ("VIEW_3D", "IMAGE_EDITOR", "UV", "ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree",
           "TextureNodeTree", "SEQUENCE_EDITOR", "CLIP_EDITOR", "DOPESHEET", "TIMELINE", "FCURVES", "DRIVERS",
           "NLA_EDITOR", "TEXT_EDITOR", "CONSOLE", "INFO", "OUTLINER", "PROPERTIES", "FILES", "ASSETS",
           "SPREADSHEET", "PREFERENCES")
REGIONS = ("WINDOW", "HEADER", "UI")
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "pressed": [], "skipped": []}
started = time.monotonic()
run = {}


def window():
    return bpy.context.window_manager.windows[0]


def director_mode():
    from angelo_agent import director
    return director.is_director(window())


def settled():
    from angelo_agent import director, ui
    return director._timer is None and ui._layout_timer is None


def chord():
    """The toggle's chord as bound now: what the person would type."""
    from angelo_agent import shortcuts
    bound = shortcuts.bound(shortcuts.DIRECTOR[0])
    assert bound, "The Director toggle has no keyboard shortcut bound"
    return {key: bound[key] for key in ("ctrl", "alt", "shift", "oskey")}, bound["type"]


def press(x, y, key=None, **modifiers):
    if key is None:
        modifiers, key = chord()
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type=key, value='PRESS', x=x, y=y, **modifiers)
    window().event_simulate(type=key, value='RELEASE', x=x, y=y, **modifiers)


def centre(region):
    return region.x + region.width // 2, region.y + region.height // 2


def work_area():
    """Manual mode's largest area that is not the chat: the one each editor is shown in."""
    from angelo_agent import console
    areas = [a for a in window().screen.areas if not console.is_chat(a)]
    return max(areas, key=lambda a: a.width * a.height)


def probe():
    return bpy.data.objects["ShortcutProbe"]


def drivers():
    data = probe().animation_data
    return [(d.data_path, d.array_index) for d in data.drivers] if data else []


def add_driver():
    curve = probe().driver_add("location", 0)
    curve.driver.expression = "0.25"


def places():
    """Every place the shortcut is typed in Manual mode: each editor, the top and status bars, the chat."""
    return list(EDITORS) + ["top bar", "status bar", "chat"]


def regions_of(name):
    """Shows ``name`` and returns ``{region type: (x, y)}`` for where to type, or ``{}`` when Blender lacks it."""
    if name in {"top bar", "status bar"}:   # Global areas, outside Screen.areas: by window position.
        return {"WINDOW": (window().width // 2, window().height - 8 if name == "top bar" else 6)}
    if name == "chat":
        from angelo_agent import console
        area = next(a for a in window().screen.areas if console.is_chat(a))
        return {"WINDOW": centre(next(r for r in area.regions if r.type == 'WINDOW'))}
    area = work_area()
    try:
        area.ui_type = name
    except TypeError:
        return {}
    if name == "PROPERTIES":
        area.spaces.active.context = 'OBJECT'
    return {r.type: centre(r) for r in area.regions if r.type in REGIONS and r.width > 1 and r.height > 1}


def director_points():
    """Where Director is typed back to Manual, in turn: its 3D view, that view's header, the chat."""
    from angelo_agent import console
    view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    chat = next(a for a in window().screen.areas if console.is_chat(a))
    found = [("Director 3D view", next(r for r in view.regions if r.type == 'WINDOW')),
             ("Director chat", next(r for r in chat.regions if r.type == 'WINDOW'))]
    header = next((r for r in view.regions if r.type == 'HEADER' and r.height > 1), None)
    if header:
        found.insert(1, ("Director 3D view header", header))
    return found


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_DIRECTOR_SHORTCUT", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def waited(condition, limit=15.0):
    """None while waiting; True once ``condition`` holds; False once ``limit`` seconds passed without it."""
    if condition():
        run.pop("since", None)
        return True
    run.setdefault("since", time.monotonic())
    if time.monotonic() - run["since"] > limit:
        run.pop("since", None)
        return False
    return None


def hovered(area, region):
    with bpy.context.temp_override(window=window(), area=area, region=region):
        found = bpy.context.property
    return found if found else None


def field_points(region):
    """Pointer positions over the Properties editor's upper half, top down: where the Location X field is looked for."""
    xs = [region.x + int(region.width * f) for f in (0.55, 0.7, 0.45, 0.8)]
    top, bottom = region.y + region.height - 10, region.y + region.height // 3
    return [(x, y) for y in range(top, bottom, -6) for x in xs]


def tick():
    try:
        assert time.monotonic() - started < 540, "Fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        if phase == "boot":
            if not settled() or not director_mode():
                return .2
            from angelo_agent import director
            checks["starts_in_director"] = True
            director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .3
        if phase == "manual":
            done = waited(lambda: settled() and not director_mode())
            if done is None:
                return .2
            checks["reaches_manual"] = done
            probe_object = bpy.data.objects.new("ShortcutProbe", None)
            bpy.context.scene.collection.objects.link(probe_object)
            bpy.context.view_layer.objects.active = probe_object
            probe_object.select_set(True)
            add_driver()
            run["places"] = places()
            run["director_turn"] = 0
            state["phase"] = "next"
            return .2
        if phase == "next":
            if not run.get("regions"):
                if not run["places"]:
                    state["phase"] = "old_chord"
                    return .2
                run["place"] = run["places"].pop(0)
                found = regions_of(run["place"])
                if not found:
                    state["skipped"].append(run["place"])
                    return .1
                run["regions"] = sorted(found)
                return .3   # Let the editor draw before typing over it.
            region_type = run["regions"].pop(0)
            point = regions_of(run["place"]).get(region_type)
            if point is None:
                return .1
            run["label"] = f"{run['place']}/{region_type}"
            press(*point)
            state["pressed"].append(run["label"])
            state["phase"] = "to_director"
            return .2
        if phase == "to_director":
            done = waited(lambda: settled() and director_mode())
            if done is None:
                return .2
            checks[f"switches_to_director_from {run['label']}"] = done
            checks[f"keeps_the_driver_at {run['label']}"] = drivers() == [("location", 0)]
            if run["label"] == "OUTLINER/WINDOW":
                capture("director-from-outliner.png")
            points = director_points()
            run["back"], region = points[run["director_turn"] % len(points)]
            run["director_turn"] += 1
            press(*centre(region))
            state["phase"] = "to_manual"
            return .2
        if phase == "to_manual":
            done = waited(lambda: settled() and not director_mode())
            if done is None:
                return .2
            checks[f"switches_back_from {run['back']}"] = done
            state["phase"] = "next"
            return .2
        if phase == "old_chord":
            from angelo_agent import shortcuts
            shown = []
            for keyconfig in (bpy.context.window_manager.keyconfigs.user, bpy.context.window_manager.keyconfigs.addon):
                shown = shown or [(i.type, i.ctrl, i.alt, i.shift, i.oskey) for km in keyconfig.keymaps
                                  for i in km.keymap_items if i.idname == shortcuts.DIRECTOR.idname and i.active]
            state["toggle_bindings"] = shown
            checks["the_shown_toggle_is_bound_to_ctrl_option_m_only"] = shown == [('M', 1, 1, 0, 0)]
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.call_menu(name="ANGELO_MT_workspace_mode")
            state["phase"] = "menu_shown"
            return .5
        if phase == "menu_shown":
            capture("mode-menu-shows-the-shortcut.png")
            press(window().width // 2, window().height // 2, key='ESC')
            run["view"] = regions_of("VIEW_3D")["WINDOW"]
            state["phase"] = "old_chord_press"
            return .4
        if phase == "old_chord_press":
            press(*run["view"], key='D', ctrl=True, alt=True)
            state["phase"] = "old_chord_director"
            return .2
        if phase == "old_chord_director":
            done = waited(lambda: settled() and director_mode())
            if done is None:
                return .2
            checks["ctrl_option_d_over_the_3d_view_switches_to_director"] = done
            view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
            press(*centre(next(r for r in view.regions if r.type == 'WINDOW')), key='D', ctrl=True, alt=True)
            state["phase"] = "old_chord_manual"
            return .2
        if phase == "old_chord_manual":
            done = waited(lambda: settled() and not director_mode())
            if done is None:
                return .2
            checks["ctrl_option_d_over_directors_3d_view_switches_back"] = done
            checks["ctrl_option_d_keeps_the_driver"] = drivers() == [("location", 0)]
            regions_of("PROPERTIES")
            area = work_area()
            region = next(r for r in area.regions if r.type == 'WINDOW')
            run["scan"] = field_points(region)
            state["phase"] = "field_scan"
            return .3
        if phase == "field_scan":
            area = work_area()
            region = next(r for r in area.regions if r.type == 'WINDOW')
            if run.get("probe_point"):
                found = hovered(area, region)
                if found and found[0] == probe() and found[1] == "location" and found[2] == 0:
                    run["field"] = run["probe_point"]
                    state["field_point"] = run["field"]
                    press(*run["field"])   # The Director shortcut over the driven field: Director, driver kept.
                    state["phase"] = "field_director"
                    return .2
            if not run["scan"]:
                checks["finds_the_driven_location_x_field"] = False
                state["phase"] = "outliner"
                return .2
            run["probe_point"] = run["scan"].pop(0)
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=run["probe_point"][0], y=run["probe_point"][1])
            return .05
        if phase == "field_director":
            done = waited(lambda: settled() and director_mode())
            if done is None:
                return .2
            checks["director_shortcut_over_a_driven_field_switches"] = done
            checks["director_shortcut_over_a_driven_field_keeps_the_driver"] = drivers() == [("location", 0)]
            from angelo_agent import director
            director.request_mode(window(), "MANUAL")
            state["phase"] = "field_manual"
            return .3
        if phase == "field_manual":
            done = waited(lambda: settled() and not director_mode())
            if done is None:
                return .2
            regions_of("PROPERTIES")
            run["field_ready"] = time.monotonic()
            state["phase"] = "field_remove"
            return .5
        if phase == "field_remove":
            area = work_area()
            region = next(r for r in area.regions if r.type == 'WINDOW')
            if "field_pressed" not in run:
                window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=run["field"][0], y=run["field"][1])
                run["field_pressed"] = False
                return .1
            if not run["field_pressed"]:
                found = hovered(area, region)
                checks["the_driven_field_is_under_the_pointer_again"] = bool(
                    found and found[0] == probe() and found[1] == "location")
                capture("driven-field-before-ctrl-alt-d.png")
                press(*run["field"], key='D', ctrl=True, alt=True)
                run["field_pressed"] = True
                return .2
            done = waited(lambda: drivers() == [], limit=3)
            if done is None:
                return .2
            checks["ctrl_alt_d_over_a_driven_field_removes_its_driver"] = done
            checks["ctrl_alt_d_over_a_driven_field_switches_nothing"] = settled() and not director_mode()
            state["phase"] = "outliner"
            return .2
        if phase == "outliner":
            if drivers() != [("location", 0)]:
                for path, index in drivers():
                    probe().driver_remove(path, index)
                add_driver()
            regions_of("OUTLINER")
            # The Outliner's own Ctrl+Alt+D (Delete Drivers for Selected) polls only in the Data API view, and stock
            # Blender's region-wide "User Interface" keymap (Remove Driver) takes the chord first even there: the key
            # must stay Blender's, whichever Blender operator that is. The Data API view is where both could act.
            work_area().spaces.active.display_mode = 'DATA_API'
            run["ops_before"] = [op.as_pointer() for op in bpy.context.window_manager.operators]
            state["phase"] = "outliner_press"
            return .4
        if phase == "outliner_press":
            area = work_area()
            press(*centre(next(r for r in area.regions if r.type == 'WINDOW')), key='D', ctrl=True, alt=True)
            state["phase"] = "outliner_wait"
            return .2
        if phase == "outliner_wait":
            if waited(lambda: False, limit=1.5) is None:
                return .2
            new = [op.bl_idname for op in bpy.context.window_manager.operators if op.as_pointer() not in run["ops_before"]]
            state["outliner_operators_run"] = new
            checks["ctrl_alt_d_in_the_outliner_runs_no_angelo_operator"] = not [n for n in new if n.startswith("ANGELO_OT")]
            checks["ctrl_alt_d_in_the_outliner_switches_nothing"] = settled() and not director_mode()
            checks["ctrl_alt_d_in_the_outliner_keeps_unselected_drivers"] = drivers() == [("location", 0)]
            # A keyconfig preset reload drops C-registered items; the add-on keyconfig's must survive it.
            preset = bpy.utils.preset_find("Blender", "keyconfig")
            bpy.ops.preferences.keyconfig_activate(filepath=preset)
            state["phase"] = "reloaded"
            return .5
        if phase == "reloaded":
            press(*regions_of("PROPERTIES")["WINDOW"])
            state["phase"] = "reloaded_wait"
            return .2
        if phase == "reloaded_wait":
            done = waited(lambda: settled() and director_mode())
            if done is None:
                return .2
            checks["switches_after_a_keyconfig_preset_reload"] = done
            capture("director-after-preset-reload.png")
            finish()
            return None
        raise AssertionError("Unknown phase " + phase)
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=1.0)
