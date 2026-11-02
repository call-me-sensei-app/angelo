# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The viewport UV checker in Director, the view people start in (doc/angelo/uv_checker.rst).

Real default startup (Director layout and chat; the viewport header is hidden, so Director's top bar offers the
checker after Blender's menus), real window events through ``Window.event_simulate`` wherever a person would use
them. The scene, from the front in a solid view: a monkey and a sphere (Blender's own UVs), a plane whose UVs sit in
UDIM tile 1002 under a second map "Atlas", and a cube with no UV map. The scenario:

* F3, "toggle uv checker", Return turns it on; Director's top-bar button shows it on.
* The + menu's row (``angelo.uv_checker``, as the chat bar runs it) opens the card; clicks on the card choose
  Checker, Density (the legend shows), Stretch, Mark flipped, Grid, 4x repeats, Selected only and back, and the UV
  map "Atlas" by name (the three meshes without it hatched and counted in the card's notice), then Active again (the
  cube with no UV map at all still counted); the card's close button.
* Tab with the sphere: Edit Mode keeps the checker; a UV edit shows at the next draw (live).
* A heavy scene (many dense objects): draw time with the checker off and on, interleaved over the same frames.
* F3 again turns it off. No image, material, node group, texture or material slot changes at any point.

Stand-in provider CLIs (``fake_provider_cli.py``) answer the start-up sign-in checks; no account or inference. Run
through ``run_native_suites.py --gui --include test_blender_uv_checker_director_gui``; check
``uv-checker-director.json`` for ``success`` and ``phase == "complete"``. Screenshots after each visible step.
"""

import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-uv-checker-director-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "uv-checker-director.json"
DATA = ("images", "materials", "node_groups", "textures")
state = {"phase": "boot", "success": False, "checks": {}, "steps": {}, "screenshots": {}, "timings": {},
         "fixture": "Default Director startup, stand-in Claude connected, four meshes (one without UVs, one in UDIM "
                    "tile 1002), synthetic window events; no provider, account or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def view():
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW'), area.spaces.active


def send(kind, value='NOTHING', x=None, y=None, **extra):
    _, region, _ = view()
    window().event_simulate(type=kind, value=value, x=region.x + region.width // 2 if x is None else int(x),
                            y=region.y + region.height // 2 if y is None else int(y), **extra)


def tap(kind, **extra):
    send(kind, 'PRESS', **extra)
    send(kind, 'RELEASE')


def typed(text):
    """Events for ``text`` as a person types it, one key per event-loop pass."""
    keys = {" ": "SPACE"}
    return [(keys.get(char, char.upper()), char) for char in text]


def f3_search(text):
    """F3, the words, Return: the operator search, as a person runs a command by name."""
    def run():
        send('MOUSEMOVE')
        tap('F3')
        queue = typed(text) + [("RET", None)]

        def step():
            kind, char = queue.pop(0)
            tap(kind, **({"unicode": char} if char else {}))
            return .06 if queue else None
        bpy.app.timers.register(step, first_interval=.6)
    return run


def card():
    from angelo_agent import card_host
    shown = card_host.active_card()
    return shown if shown is not None and shown.kind == "uv_checker" else None


def click_card(action, value=None):
    """A click on the shown card's control for (action, value), at its centre in window pixels."""
    def run():
        from angelo_agent import card_host
        plan = card_host._active.plan if card_host._active else None
        rect = next((r for r, a, v in plan.hits if a == action and (value is None or v == value)), None)
        assert rect is not None, f"no {action} {value} on the card"
        _, region, _ = view()
        x, y = region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2
        send('MOUSEMOVE', x=x, y=y)
        send('LEFTMOUSE', 'PRESS', x=x, y=y)
        send('LEFTMOUSE', 'RELEASE', x=x, y=y)
    return run


def open_card():
    """What the chat bar's + menu row runs (``chat_destinations`` Feature ``uv_checker``): its operator, invoked."""
    area, region, _ = view()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.angelo.uv_checker('INVOKE_DEFAULT', show=False)


def top_bar():
    from angelo_agent import uv_checker
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN', iterations=1)
    return uv_checker.drawn.get(window().as_pointer())


def counts():
    return {name: len(getattr(bpy.data, name)) for name in DATA} | {
        "material_slots": sum(len(o.material_slots) for o in bpy.data.objects)}


def snapshot():
    _, _, space = view()
    c = space.uv_checker
    return {"mode": bpy.context.mode, "show": c.show, "pattern": c.pattern, "scale": c.scale, "uv_map": c.uv_map,
            "uv_map_name": c.uv_map_name, "selected_only": c.use_selected_only, "flipped": c.show_flipped,
            "card": card() is not None, "missing": card().missing[0] if card() else None, "top_bar": top_bar(),
            "data": counts()}


def mesh_object(name, build, location, uv_maps=("UVMap",)):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if uv_maps:
        bm.loops.layers.uv.new(uv_maps[0])   # calc_uvs fills an existing UV layer; it makes none.
    build(bm)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def scene_fixture():
    for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
        bpy.data.objects.remove(obj)
    mesh_object("Monkey", lambda bm: bmesh.ops.create_monkey(bm, calc_uvs=True), (-3.3, 0, 0))
    mesh_object("Sphere", lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1,
                                                               calc_uvs=True), (-1.1, 0, 0))
    udim = mesh_object("UdimPlane", lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1,
                                                                    calc_uvs=True), (1.1, 0, 0), uv_maps=("UVMap",))
    udim.rotation_euler = (math.radians(90), 0, 0)
    atlas = udim.data.uv_layers.new(name="Atlas")
    for loop in udim.data.uv_layers["UVMap"].data:
        loop.uv = (loop.uv[0] + 1.0, loop.uv[1])          # Tile 1002.
    for source, target in zip(udim.data.uv_layers["UVMap"].data, atlas.data):
        target.uv = source.uv
    mesh_object("NoUVs", lambda bm: bmesh.ops.create_cube(bm, size=1.6), (3.3, 0, 0), uv_maps=())
    _, _, space = view()
    rv3d = space.region_3d
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Euler((math.radians(80), 0, math.radians(15))).to_quaternion()
    rv3d.view_location = (0, 0, 0)
    rv3d.view_distance = 9
    space.shading.type = 'SOLID'
    state["baseline"] = counts()


def edit_sphere():
    """A click on an empty spot of the view takes the keyboard from the chat's composer (which has it at start-up),
    then the sphere is made the selection and Tab, with the pointer over the view, enters Edit Mode."""
    _, region, _ = view()
    x, y = region.x + 40, region.y + 40
    send('MOUSEMOVE', x=x, y=y)
    send('LEFTMOUSE', 'PRESS', x=x, y=y)
    send('LEFTMOUSE', 'RELEASE', x=x, y=y)

    def select_and_tab():
        sphere = bpy.data.objects["Sphere"]
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj is sphere)
        bpy.context.view_layer.objects.active = sphere
        send('MOUSEMOVE')
        tap('TAB')
    bpy.app.timers.register(select_and_tab, first_interval=.5)


def edit_uvs():
    """A UV edit in the BMesh, as the UV editor's tools make one: scaled up 3x around the origin."""
    sphere = bpy.data.objects["Sphere"]
    bm = bmesh.from_edit_mesh(sphere.data)
    layer = bm.loops.layers.uv.active
    for face in bm.faces:
        for loop in face.loops:
            loop[layer].uv = loop[layer].uv * 3.0
    bmesh.update_edit_mesh(sphere.data)


def heavy_scene():
    """400 dense grids (1,600 quads each) in front of the rest, and the draw time with the checker off and on."""
    for index in range(400):
        mesh_object(f"Dense{index}", lambda bm: bmesh.ops.create_grid(bm, x_segments=40, y_segments=40, size=0.2,
                                                                      calc_uvs=True),
                    ((index % 20 - 9.5) * 0.45, -2, (index // 20 - 9.5) * 0.45)).rotation_euler = (math.radians(90), 0, 0)
    area, region, space = view()
    samples = {False: [], True: []}
    with bpy.context.temp_override(window=window(), area=area, region=region):
        for show in (False, True, False, True):   # Warm-up: shaders and batches for both.
            space.uv_checker.show = show
            bpy.ops.wm.redraw_timer(type='DRAW', iterations=3)
        for _ in range(8):
            for show in (False, True):            # Interleaved, so load changes hit both alike.
                space.uv_checker.show = show
                begun = time.perf_counter()
                bpy.ops.wm.redraw_timer(type='DRAW', iterations=10)
                samples[show].append((time.perf_counter() - begun) * 100)   # ms per draw
    space.uv_checker.show = True
    off, on = statistics.median(samples[False]), statistics.median(samples[True])
    state["timings"] = {"heavy_scene_objects": 400, "heavy_scene_triangles": 400 * 1600 * 2,
                        "region": [region.width, region.height], "off_median_ms": round(off, 2),
                        "on_median_ms": round(on, 2), "off_fps": round(1000 / off, 1), "on_fps": round(1000 / on, 1),
                        "note": "wm.redraw_timer DRAW of the Director view, 8 x 10 draws each, interleaved"}


def same(before, after, *keys):
    return all(before[key] == after[key] for key in keys)


# (name, run, test(before, after), screenshot or None, seconds to wait). Order matters: each starts from the last.
STEPS = (
    ("f3_turns_it_on", f3_search("toggle uv checker"), lambda b, a: a["show"] and a["top_bar"] is True,
     "uv-checker-on.png", 8.0),
    ("plus_menu_opens_the_card", open_card, lambda b, a: a["card"] and a["show"], "uv-checker-card.png", 3.0),
    ("card_checker", click_card("pattern", "CHECKER"), lambda b, a: a["pattern"] == "CHECKER", "uv-checker-checker.png", 3.0),
    ("card_density", click_card("pattern", "TEXEL_DENSITY"), lambda b, a: a["pattern"] == "TEXEL_DENSITY",
     "uv-checker-density.png", 3.0),
    ("card_stretch", click_card("pattern", "STRETCH"), lambda b, a: a["pattern"] == "STRETCH", "uv-checker-stretch.png", 3.0),
    ("card_flipped", click_card("flipped"), lambda b, a: a["flipped"], None, 3.0),
    ("card_grid", click_card("pattern", "COLOR_GRID"), lambda b, a: a["pattern"] == "COLOR_GRID", None, 3.0),
    ("card_repeats_4x", click_card("scale", "4"), lambda b, a: a["scale"] == 4.0, "uv-checker-grid-4x.png", 3.0),
    ("card_selected_only", click_card("objects", "SELECTED"), lambda b, a: a["selected_only"], None, 3.0),
    ("card_all_visible", click_card("objects", "ALL"), lambda b, a: not a["selected_only"], None, 3.0),
    ("card_uv_map_menu", click_card("uv_open"), lambda b, a: a["card"], None, 1.0),
    ("card_named_atlas", click_card("uv_map", "named:Atlas"),
     lambda b, a: (a["uv_map"], a["uv_map_name"]) == ("NAMED", "Atlas") and a["missing"] == 3,   # Monkey, Sphere, NoUVs.
     "uv-checker-named-missing.png", 3.0),
    ("card_uv_map_menu_again", click_card("uv_open"), lambda b, a: a["card"], None, 1.0),
    ("card_active_map", click_card("uv_map", "ACTIVE"), lambda b, a: a["uv_map"] == "ACTIVE" and a["missing"] == 1,
     "uv-checker-no-uv-notice.png", 3.0),   # The cube with no UV map at all, named on the card.
    ("card_closes", click_card("close"), lambda b, a: not a["card"] and a["show"], "uv-checker-udim-and-missing.png", 3.0),
    ("tab_edit_mode_keeps_it", edit_sphere, lambda b, a: a["mode"] == "EDIT_MESH" and a["show"],
     "uv-checker-edit-mode.png", 3.0),
    ("uv_edit_is_live", edit_uvs, None, "uv-checker-edit-mode-after-uv-edit.png", 1.0),
    ("tab_back", lambda: tap('TAB'), lambda b, a: a["mode"] == "OBJECT", None, 3.0),
    ("heavy_scene_timing", heavy_scene, lambda b, a: bool(state["timings"]), "uv-checker-heavy-scene.png", 3.0),
    ("f3_turns_it_off", f3_search("toggle uv checker"), lambda b, a: not a["show"] and a["top_bar"] is False,
     "uv-checker-off.png", 8.0),
)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"][name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        shots, checks = state["screenshots"], state["checks"]
        checks["checker_changes_the_view"] = shots.get("uv-checker-start.png") != shots.get("uv-checker-on.png")
        checks["uv_edit_shows_live"] = (shots.get("uv-checker-edit-mode.png")
                                        != shots.get("uv-checker-edit-mode-after-uv-edit.png"))
        checks["patterns_look_different"] = len({shots.get(name) for name in (
            "uv-checker-on.png", "uv-checker-checker.png", "uv-checker-density.png", "uv-checker-stretch.png")}) == 4
        checks["no_data_changed"] = all(step["after"]["data"] == state["baseline"] for name, step in state["steps"].items()
                                        if name != "heavy_scene_timing" and "after" in step)
        state["phase"] = "complete"
        state["success"] = all(checks.values()) and all(s["passed"] is not False for s in state["steps"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_UV_CHECKER_DIRECTOR", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 420, "UV checker Director fixture timed out in " + state["phase"]
        from angelo_agent import director, ui
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            bpy.ops.angelo.quick_connect(mode="claude_subscription")
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
            checks["viewport_header_hidden"] = not space.show_region_header
            checks["off_by_default"] = not space.uv_checker.show
            checks["top_bar_offers_it_off"] = top_bar() is False
            scene_fixture()
            capture("uv-checker-start.png")
            state["phase"], state["index"], state["pending"] = "step", 0, None
            save()
            return .3
        if state["phase"] == "step":
            if state["pending"]:
                name, test, shot, before, sent, wait = state["pending"]
                after = snapshot()
                passed = None if test is None else bool(test(before, after))
                if passed is False and time.monotonic() - sent < wait:
                    return .1  # Typed keys, a card click or a mode change take a few event-loop passes.
                state["steps"][name] = {"passed": passed, "before": before, "after": after}
                state["pending"] = None
                state["index"] += 1
                if shot:
                    capture(shot)
                save()
            if state["index"] >= len(STEPS):
                finish()
                return None
            name, run, test, shot, wait = STEPS[state["index"]]
            before = snapshot()
            run()
            state["pending"] = (name, test, shot, before, time.monotonic(), wait)
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
