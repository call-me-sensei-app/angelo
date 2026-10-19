# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Director never takes control away: native viewport input reaches Blender.

Real default startup (Director layout and chat), real window events through
``Window.event_simulate``: each probe sends the same events a person would
(middle-mouse drags, wheel, trackpad gestures, numpad, Home, the view pie, an
NDOF button, clicks, G/R/S, A, Tab) with the pointer over the 3D view, then
checks the view or scene state that only Blender's own operator could change.

It also pins the chat's keyboard focus rule (``input_policy``): while the chat
has focus, typed letters stay in the chat even with the pointer over the
viewport, view-navigation keys still reach the viewport, and orbiting or
clicking in the viewport moves focus there.

Stand-in provider CLIs (``fake_provider_cli.py``) answer the start-up sign-in
checks, and the suite connects the stand-in Claude first, because the chat has
no composer until a connection is ready: no real CLI, account, network or
inference. Run through
``run_native_suites.py --gui --include test_blender_director_navigation``;
check ``director-navigation.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-director-navigation-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
bpy.context.preferences.view.smooth_view = 0  # View changes land at once, so each probe reads its own result.
REPORT = OUT / "director-navigation.json"
state = {"phase": "boot", "success": False, "checks": {}, "probes": {}, "screenshots": [],
         "fixture": "Default Director startup, stand-in Claude connected, synthetic window events; no provider, account or inference"}
started = time.monotonic()
pie_draws = [0]


def count_pie(_menu, _context):
    pie_draws[0] += 1


bpy.types.VIEW3D_MT_view_pie.append(count_pie)


def window():
    return bpy.context.window_manager.windows[0]


def view():
    area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return area, region, area.spaces.active.region_3d


def center():
    _, region, _ = view()
    return region.x + region.width // 2, region.y + region.height // 2


def chat_line():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area.spaces.active.history[-1]


def snapshot():
    _, _, r3d = view()
    cube = bpy.data.objects["Cube"]
    return {"rotation": [round(v, 4) for v in r3d.view_rotation], "distance": round(r3d.view_distance, 4),
            "location": [round(v, 3) for v in r3d.view_location], "perspective": r3d.view_perspective,
            "selected": sorted(o.name for o in bpy.context.view_layer.objects if o.select_get()),
            "active": getattr(bpy.context.view_layer.objects.active, "name", None),
            "cube": [round(v, 3) for v in (*cube.location, *cube.rotation_euler, *cube.scale)],
            "mode": bpy.context.mode, "pie": pie_draws[0], "draft": chat_line().body}


def send(kind, value='NOTHING', x=None, y=None, **modifiers):
    cx, cy = center()
    window().event_simulate(type=kind, value=value, x=cx if x is None else x, y=cy if y is None else y, **modifiers)


# A real keyboard event carries the character it types; simulated ones only when given.
# Numpad digits type on macOS too, which is what made them disappear into a focused chat.
TYPED = {"NUMPAD_" + str(digit): str(digit) for digit in range(10)}
TYPED.update(NUMPAD_PERIOD=".", ACCENT_GRAVE="`", G="g", R="r", S="s", A="a")


def press(kind, **modifiers):
    typed = TYPED.get(kind) if not any(modifiers.get(m) for m in ("ctrl", "alt", "oskey")) else None
    send(kind, 'PRESS', **modifiers, **({"unicode": typed} if typed else {}))
    send(kind, 'RELEASE', **modifiers)


def drag(button, dx, dy, **modifiers):
    cx, cy = center()
    send('MOUSEMOVE')
    send(button, 'PRESS', **modifiers)
    for step in range(1, 5):
        send('MOUSEMOVE', x=cx + dx * step // 4, y=cy + dy * step // 4, **modifiers)
    send(button, 'RELEASE', x=cx + dx, y=cy + dy, **modifiers)


def gesture(kind, dx, dy, **modifiers):
    """Trackpad events carry their delta as xy minus the previous pointer position."""
    cx, cy = center()
    send('MOUSEMOVE')
    send(kind, x=cx + dx, y=cy + dy, **modifiers)
    send('MOUSEMOVE')


def key(name, **modifiers):
    def run():
        send('MOUSEMOVE')
        press(name, **modifiers)
    return run


def click_at(point_of):
    def run():
        x, y = point_of()
        send('MOUSEMOVE', x=x, y=y)
        send('LEFTMOUSE', 'PRESS', x=x, y=y)
        send('LEFTMOUSE', 'RELEASE', x=x, y=y)
    return run


def cube_point():
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    _, region, r3d = view()
    point = location_3d_to_region_2d(region, r3d, bpy.data.objects["Cube"].location)
    return int(region.x + point.x), int(region.y + point.y)


def empty_point():
    _, region, _ = view()
    return region.x + 40, region.y + 40


def transform(letter):
    """Start away from the pivot (the framed cube is at the center) and sweep a quarter turn outward."""
    def run():
        cx, cy = center()
        send('MOUSEMOVE', x=cx + 80, y=cy + 20)
        press(letter)
        for x, y in ((cx + 70, cy + 60), (cx + 45, cy + 90), (cx + 20, cy + 110)):
            send('MOUSEMOVE', x=x, y=y)
        send('LEFTMOUSE', 'PRESS', x=cx + 20, y=cy + 110)
        send('LEFTMOUSE', 'RELEASE', x=cx + 20, y=cy + 110)
    return run


def changed(*fields):
    return lambda before, after: any(before[field] != after[field] for field in fields)


def cube_changed(start, end):
    return lambda before, after: before["cube"][start:end] != after["cube"][start:end]


# (name, events, success test on (before, after)). Order matters: each starts from the previous state.
PROBES = (
    # The chat has keyboard focus after startup, like a text field.
    ("focused_chat_numpad_view_follows_pointer", key('NUMPAD_1'),
     lambda b, a: a["rotation"] != b["rotation"] and a["draft"] == b["draft"] == ""),
    ("focused_chat_keeps_typed_letters_safe", key('G'),
     lambda b, a: a["draft"] == "g" and a["cube"] == b["cube"]),
    ("middle_mouse_orbit", lambda: drag('MIDDLEMOUSE', 120, 40), changed("rotation")),
    ("orbit_moved_keyboard_focus_to_viewport", key('A'),
     lambda b, a: len(a["selected"]) == 3 and a["draft"] == b["draft"] == ""),
    ("shift_middle_mouse_pan", lambda: drag('MIDDLEMOUSE', 80, 30, shift=True), changed("location")),
    ("ctrl_middle_mouse_zoom", lambda: drag('MIDDLEMOUSE', 0, 80, ctrl=True), changed("distance")),
    ("wheel_zoom_in", lambda: (send('MOUSEMOVE'), send('WHEELUPMOUSE', 'PRESS')), lambda b, a: a["distance"] < b["distance"]),
    ("wheel_zoom_out", lambda: (send('MOUSEMOVE'), send('WHEELDOWNMOUSE', 'PRESS')), lambda b, a: a["distance"] > b["distance"]),
    ("trackpad_pan_orbits", lambda: gesture('TRACKPADPAN', 40, 15), changed("rotation")),
    ("trackpad_shift_pan_moves", lambda: gesture('TRACKPADPAN', 40, 15, shift=True), changed("location")),
    ("trackpad_pinch_zooms", lambda: gesture('TRACKPADZOOM', 0, 30), changed("distance")),
    ("trackpad_rotate_rolls", lambda: gesture('MOUSEROTATE', 30, 0), changed("rotation")),
    ("numpad_3_right", key('NUMPAD_3'), changed("rotation")),
    ("numpad_7_top", key('NUMPAD_7'), changed("rotation")),
    ("numpad_5_orthographic", key('NUMPAD_5'), changed("perspective")),
    ("numpad_5_perspective", key('NUMPAD_5'), changed("perspective")),
    ("home_frames_all", key('HOME'), changed("distance", "location")),
    ("click_selects_cube", click_at(cube_point), lambda b, a: a["active"] == "Cube" and a["selected"] == ["Cube"]),
    ("numpad_period_frames_selected", key('NUMPAD_PERIOD'), changed("distance", "location")),
    ("view_pie_opens", key('ACCENT_GRAVE'), lambda b, a: a["pie"] > b["pie"]),
    ("view_pie_closes", key('ESC'), None),
    ("ndof_button_front_view", key('NDOF_BUTTON_FRONT'), changed("rotation")),
    ("g_grabs_cube", transform('G'), cube_changed(0, 3)),
    ("r_rotates_cube", transform('R'), cube_changed(3, 6)),
    ("s_scales_cube", transform('S'), cube_changed(6, 9)),
    ("ctrl_z_undoes_scale", key('Z', ctrl=True), cube_changed(6, 9)),
    ("click_empty_deselects", click_at(empty_point), lambda b, a: a["selected"] == []),
    ("a_selects_all", key('A'), lambda b, a: len(a["selected"]) == 3),
    ("tab_enters_edit_mode", key('TAB'), lambda b, a: a["mode"] == "EDIT_MESH"),
    ("tab_returns_to_object_mode", key('TAB'), lambda b, a: a["mode"] == "OBJECT"),
    ("numpad_0_camera_view", key('NUMPAD_0'), lambda b, a: a["perspective"] == "CAMERA"),
)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state["phase"] = "complete"
        state["success"] = all(state["checks"].values()) and all(p["passed"] is not False for p in state["probes"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_DIRECTOR_NAVIGATION", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 180, "Director navigation fixture timed out in " + state["phase"]
        from angelo_agent import director, console, ui
        if state["phase"] == "boot":
            if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
                return .2
            # The composer exists only once a connection is ready; connect the stand-in Claude
            # the way the welcome's "Connect Claude" does, so the focused-chat rule below has a field to protect.
            bpy.ops.angelo.quick_connect(mode="claude_subscription")
            state["phase"] = "connect"
            return .2
        if state["phase"] == "connect":
            if not ui.connection_available():
                return .2
            send('MOUSEMOVE')  # Any event lets the new composer take the keyboard focus it asked for.
            state["phase"], state["settled"] = "settle", time.monotonic()
            return .2
        if state["phase"] == "settle":
            if time.monotonic() - state["settled"] < 1.5:
                return .2
            area, _, _ = view()
            space = area.spaces.active
            checks = state["checks"]
            checks["director_is_default"] = director.preferred_mode() == "DIRECTOR"
            checks["director_adds_no_input_handler"] = not hasattr(bpy.types, "ANGELO_OT_director_input") and not any(
                "director" in str(getattr(op, "bl_idname", "")).lower() for op in window().modal_operators)
            checks["manual_chrome_hidden"] = not any((space.show_region_header, space.show_region_toolbar, space.show_region_ui,
                                                      space.show_gizmo, window().screen.show_statusbar))
            checks["selection_overlays_visible_without_grid_clutter"] = (space.overlay.show_overlays and space.overlay.show_outline_selected
                                                                        and not space.overlay.show_floor and not space.overlay.show_text)
            checks["chat_docked_beside_viewport"] = sum(console.is_chat(a) for a in window().screen.areas) == 1
            capture("director-navigation-start.png")
            state["phase"], state["index"], state["pending"] = "probe", 0, None
            save()
            return .2
        if state["phase"] == "probe":
            if state["pending"]:
                name, test, before, sent = state["pending"]
                after = snapshot()
                passed = None if test is None else bool(test(before, after))
                if passed is False and time.monotonic() - sent < 1.5:
                    return .1  # Modal navigation can take a few event-loop passes.
                state["probes"][name] = {"passed": passed, "before": before, "after": after}
                state["pending"] = None
                state["index"] += 1
                if name == "focused_chat_keeps_typed_letters_safe":
                    chat_line().body = ""  # Leave an empty draft for the rest of the probes.
                if name == "click_selects_cube":
                    capture("director-cube-selected.png")
                if name == "s_scales_cube":
                    capture("director-after-transforms.png")
                save()
            if state["index"] >= len(PROBES):
                capture("director-navigation-end.png")
                finish()
                return None
            name, run, test = PROBES[state["index"]]
            before = snapshot()
            run()
            state["pending"] = (name, test, before, time.monotonic())
            return .15
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
