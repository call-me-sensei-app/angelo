# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select Through in Director, the view people start in, driven only by window events.

Real default startup (Director layout and chat; the viewport header, where Manual mode shows the
toggle, is hidden, so Director's top bar offers it after Blender's menus), real window events through ``Window.event_simulate``. The scene, seen from the
front in an orthographic solid view: cube A (2 m) at the origin hides cube B (1 m) behind it. The
scenario is what a person does: click the viewport, Tab into Edit Mode, 1 for vertices, drag a box
over both cubes (reaches A's front only), Z for the shading pie and pick Select Through beside X-ray,
drag the same box (reaches all of A and B while the view stays solid), Ctrl+Z and Ctrl+Shift+Z
(the selection is undone and redone, the toggle stays), and the pie again to turn it off. Director's
top bar offers the toggle only in Edit Mode and shows the one shared state after each change.

Stand-in provider CLIs (``fake_provider_cli.py``) answer the start-up sign-in checks, as in
``test_blender_director_navigation.py``: no real CLI, account, network or inference. Run through
``run_native_suites.py --gui --include test_blender_select_through_director_gui``; check
``select-through-director.json`` for ``success`` and ``phase == "complete"``. Screenshots: Director
at start, the pie with Select Through, the box with the toggle off and on (the hidden selection
showing faintly through A).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Euler, Vector

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-select-through-director-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
REPORT = OUT / "select-through-director.json"
state = {"phase": "boot", "success": False, "checks": {}, "steps": {}, "screenshots": [],
         "fixture": "Default Director startup, stand-in Claude connected, two cubes (B behind A), synthetic window "
                    "events; no provider, account or inference"}
started = time.monotonic()
pie_draws = [0]


def count_pie(_menu, _context):
    pie_draws[0] += 1


bpy.types.VIEW3D_MT_shading_pie.append(count_pie)     # Z's pie in the default keymap
bpy.types.VIEW3D_MT_shading_ex_pie.append(count_pie)  # ... with "Extra Shading Pie Menu Items" on


def window():
    return bpy.context.window_manager.windows[0]


def view():
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return area, region, area.spaces.active


def send(kind, value='NOTHING', x=None, y=None, **modifiers):
    _, region, _ = view()
    cx, cy = region.x + region.width // 2, region.y + region.height // 2
    window().event_simulate(type=kind, value=value, x=cx if x is None else x, y=cy if y is None else y, **modifiers)


def key(name, **modifiers):
    """A key typed with the pointer over the view's centre (a pie opens there)."""
    def run():
        send('MOUSEMOVE')
        typed = {"Z": "z", "ONE": "1", "TAB": None}.get(name) if not any(modifiers.values()) else None
        send(name, 'PRESS', **modifiers, **({"unicode": typed} if typed else {}))
        send(name, 'RELEASE', **modifiers)
    return run


def click(point_of):
    def run():
        x, y = point_of()
        send('MOUSEMOVE', x=x, y=y)
        send('LEFTMOUSE', 'PRESS', x=x, y=y)
        send('LEFTMOUSE', 'RELEASE', x=x, y=y)
    return run


def empty_point():
    _, region, _ = view()
    return region.x + 40, region.y + 40


def pie_select_through():
    """Pick Select Through in the pie Z opened, by its numpad direction (pies take 1-9 as directions, as a person can):
    the plain pie's fifth item is north-west (7, after the four shading types), the extra-items pie's seventh is
    south-west (1, after X-ray and the shading types); Blender's slot order is W, E, S, N, NW, NE, SW, SE."""
    keyconfig = bpy.context.window_manager.keyconfigs.active
    extra = bool(getattr(getattr(keyconfig, "preferences", None), "use_v3d_shade_ex_pie", False))
    state.setdefault("observed", {})["extra_shading_pie"] = extra
    key('NUMPAD_1' if extra else 'NUMPAD_7')()


def box_drag():
    """A box over both cubes, from the empty view above-left to below-right of them (the default Select Box tool),
    one event per event-loop pass as a person's pointer arrives (``qa_input.drag_path``)."""
    _, region, space = view()
    objects = [bpy.data.objects[name] for name in ("A", "B")]
    points = [location_3d_to_region_2d(region, space.region_3d, obj.matrix_world @ Vector(corner))
              for obj in objects for corner in obj.bound_box]
    margin = 30 * bpy.context.preferences.system.ui_scale
    start = (region.x + min(p.x for p in points) - margin, region.y + max(p.y for p in points) + margin)
    end = (region.x + max(p.x for p in points) + margin, region.y + min(p.y for p in points) - margin)
    events = list(qa_input.drag_path(tuple(map(int, start)), tuple(map(int, end)),
                                     bpy.context.preferences.system.ui_scale))

    def step():
        kind, value, (x, y) = events.pop(0)
        window().event_simulate(type=kind, value=value, x=int(x), y=int(y))
        return .02 if events else None
    bpy.app.timers.register(step, first_interval=.02)


def stock_box_off():
    """The toggle-off baseline: the dragged box is recorded (``dragged_box_off``), then the same box runs through the
    operator in Director's window, which is what the step checks."""
    box_drag()

    def after_drag():
        state.setdefault("observed", {})["dragged_box_off"] = counts()
        operator_box(keep=True)
    bpy.app.timers.register(after_drag, first_interval=1.5)


def operator_box(keep=False):
    """The same box as the drag, through the operator in the same window and view; unless ``keep``, the selection is
    put back afterwards."""
    area, region, space = view()
    objects = [bpy.data.objects[name] for name in ("A", "B")]
    points = [location_3d_to_region_2d(region, space.region_3d, obj.matrix_world @ Vector(corner))
              for obj in objects for corner in obj.bound_box]
    margin = 30 * bpy.context.preferences.system.ui_scale
    rect = (int(min(p.x for p in points) - margin), int(max(p.x for p in points) + margin),
            int(min(p.y for p in points) - margin), int(max(p.y for p in points) + margin))
    before = counts()
    kept = {obj.name: [v.select for v in bmesh.from_edit_mesh(obj.data).verts] for obj in objects}
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.view3d.select_box(xmin=rect[0], xmax=rect[1], ymin=rect[2], ymax=rect[3], mode='SET',
                                  wait_for_input=False)
    found = counts()
    if keep:
        return {"rect": rect, "before": before, "operator": found}
    for obj in objects:   # A script's operator pushes no undo step, so the selection is put back by hand.
        bm = bmesh.from_edit_mesh(obj.data)
        for vert, selected in zip(bm.verts, kept[obj.name]):
            vert.select = selected
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
    return {"rect": rect, "before": before, "operator": found, "restored": counts()}


def counts():
    if bpy.context.mode != 'EDIT_MESH':
        return None
    found = {}
    for name in ("A", "B"):
        bm = bmesh.from_edit_mesh(bpy.data.objects[name].data)
        found[name] = sum(v.select for v in bm.verts)
    return found


def top_bar():
    """(offered, on) as Director's top bar last drew its Select Through button, after a draw now."""
    from angelo_agent import director_select_through
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN', iterations=1)
    return director_select_through.drawn.get(window().as_pointer())


def snapshot():
    _, _, space = view()
    return {"top_bar": top_bar(), "mode": bpy.context.mode, "select_through": space.use_select_through, "xray": space.shading.show_xray,
            "shading": space.shading.type, "pie": pie_draws[0], "verts": counts(),
            "vertex_mode": tuple(bpy.context.tool_settings.mesh_select_mode)}


def scene_two_cubes():
    """The fixture: the startup cube gives way to A (2 m, front) and B (1 m, 4 m behind), both selected, A active."""
    for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
        bpy.data.objects.remove(obj)
    objects = []
    for name, size, location in (("A", 2, (0, 0, 0)), ("B", 1, (0, 4, 0))):
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=size)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        obj.location = location
        bpy.context.scene.collection.objects.link(obj)
        objects.append(obj)
    _, _, space = view()
    rv3d = space.region_3d
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Euler((math.radians(90), 0, 0)).to_quaternion()
    rv3d.view_location = (0, 2, 0)
    rv3d.view_distance = 9
    space.shading.type = 'SOLID'
    space.shading.show_xray = False
    space.use_select_through = False


def select_both():
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj.name in {"A", "B"})
    bpy.context.view_layer.objects.active = bpy.data.objects["A"]


# (name, events, success test on (before, after), screenshot or None). Order matters: each starts from the last.
STEPS = (
    ("click_viewport_takes_focus", click(empty_point), lambda b, a: a["mode"] == "OBJECT", None),
    ("select_both_cubes", select_both, None, None),   # Fixture: the click above deselected everything.
    ("tab_enters_edit_mode", key('TAB'), lambda b, a: a["mode"] == "EDIT_MESH" and a["top_bar"] == (True, False),
     "director-top-bar-offers-select-through.png"),
    ("one_vertex_mode", key('ONE'), lambda b, a: a["vertex_mode"] == (True, False, False), None),
    ("alt_a_deselects", key('A', alt=True), lambda b, a: a["verts"] == {"A": 0, "B": 0}, None),
    # Toggle off is Blender's unchanged select-buffer path, which simulated drags do not drive (Mac 1, three runs:
    # dragged 0, the operator in the same window 4); the baseline is that operator, the dragged box is recorded.
    ("box_off_reaches_front_only", stock_box_off,
     lambda b, a: a["verts"]["B"] == 0 and 0 < a["verts"]["A"] < 8, "director-select-through-off.png"),
    ("alt_a_deselects_again", key('A', alt=True), lambda b, a: a["verts"] == {"A": 0, "B": 0}, None),
    ("z_opens_shading_pie", key('Z'), lambda b, a: a["pie"] > b["pie"], "director-shading-pie.png"),
    ("pie_turns_select_through_on", pie_select_through,
     lambda b, a: a["select_through"] and not a["xray"] and a["shading"] == 'SOLID' and a["top_bar"] == (True, True),
     None),
    ("box_on_reaches_hidden_cube", box_drag,
     lambda b, a: a["verts"] == {"A": 8, "B": 8} and not a["xray"], "director-select-through-on.png"),
    ("ctrl_z_undoes_the_selection", key('Z', ctrl=True),
     lambda b, a: a["verts"] == {"A": 0, "B": 0} and a["select_through"], None),
    ("ctrl_shift_z_redoes_it", key('Z', ctrl=True, shift=True),
     lambda b, a: a["verts"] == {"A": 8, "B": 8} and a["select_through"], None),
    ("z_opens_shading_pie_again", key('Z'), lambda b, a: a["pie"] > b["pie"], None),
    ("pie_turns_select_through_off", pie_select_through,
     lambda b, a: not a["select_through"] and a["top_bar"] == (True, False), None),
    ("tab_returns_to_object_mode", key('TAB'), lambda b, a: a["mode"] == "OBJECT" and a["top_bar"][0] is False, None),
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
        state["success"] = all(state["checks"].values()) and all(s["passed"] is not False for s in state["steps"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_SELECT_THROUGH_DIRECTOR", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 180, "Select Through Director fixture timed out in " + state["phase"]
        from angelo_agent import director, ui
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            bpy.ops.angelo.quick_connect(mode="claude_subscription")   # The composer's field, as people see it.
            state["phase"] = "connect"
            return .2
        if state["phase"] == "connect":
            if not ui.connection_available():
                return .2
            send('MOUSEMOVE')
            state["phase"], state["settled"] = "settle", time.monotonic()
            return .2
        if state["phase"] == "settle":
            if time.monotonic() - state["settled"] < 1.5:
                return .2
            _, _, space = view()
            checks = state["checks"]
            checks["director_is_default"] = director.preferred_mode() == "DIRECTOR"
            # The header button is out of reach here, so the pie is how a person in Director gets the toggle.
            checks["viewport_header_hidden"] = not space.show_region_header
            checks["select_through_off_by_default"] = not space.use_select_through
            checks["top_bar_offers_nothing_in_object_mode"] = top_bar() == (False, False)
            capture("director-select-through-start.png")
            scene_two_cubes()
            state["phase"], state["index"], state["pending"] = "step", 0, None
            save()
            return .3
        if state["phase"] == "step":
            if state["pending"]:
                name, test, shot, before, sent = state["pending"]
                after = snapshot()
                passed = None if test is None else bool(test(before, after))
                if passed is False and time.monotonic() - sent < 3.0:
                    return .1  # A drag sent one event per pass, or a pie, takes a few event-loop passes.
                state["steps"][name] = {"passed": passed, "before": before, "after": after}
                state["pending"] = None
                state["index"] += 1
                if shot:
                    capture(shot)
                save()
            if state["index"] >= len(STEPS):
                finish()
                return None
            name, run, test, shot = STEPS[state["index"]]
            before = snapshot()
            run()
            state["pending"] = (name, test, shot, before, time.monotonic())
            return .2
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
