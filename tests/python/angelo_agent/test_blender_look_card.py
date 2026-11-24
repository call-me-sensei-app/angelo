# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A lens and film look in the running app: the + menu row, the Camera Look card and the look on the camera view.

Real default startup (Director Mode with the chat) and real input (``Window.event_simulate``); no provider, account
or network. Checked against doc/angelo/camera_look.rst, "The card": the chat bar's + menu opens the card on the
scene's camera; Anamorphic with Match Exposure at ISO 400 (two stops over the light's ISO 100) applies one look node
before the compositing output, the oval bokeh, a film exposure of +2 and the camera view's compositor; the 3D view's
camera view shows it; the card opened again offers Take Off, which puts the bokeh and compositor back; one Undo
brings the look back.

Run through ``run_native_suites.py --gui --include test_blender_look_card``; check ``look-card.json`` for ``success``
and ``phase == "complete"``. The screenshots are the GUI proof (Director, the default view, its camera view).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_hit  # noqa: E402  (window pixels of a surface's points: scale and right to left)
import qa_plus_menu  # noqa: E402  (the + menu's pages, read from the chat's canvas state)
import qa_waits  # noqa: E402  (the runner's --timeout-scale)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-look-card-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "look-card.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def largest_view():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def view3d_pixels(path):
    """The largest 3D view's pixels in a window screenshot, every fourth one (vision: did the view change)."""
    area = largest_view()
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = image.pixels[:]
    finally:
        bpy.data.images.remove(image)
    scale_x, scale_y = width / window().width, height / window().height
    x0, y0 = int(area.x * scale_x), int(area.y * scale_y)
    x1, y1 = int((area.x + area.width) * scale_x), int((area.y + area.height) * scale_y)
    return [tuple(round(pixels[(y * width + x) * 4 + c], 2) for c in range(3))
            for y in range(y0, y1, 4) for x in range(x0, x1, 4)]


def press(x, y):
    import qa_input
    qa_input.press(window(), x, y)


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def at(rect, region=None):
    from qa_input import chat_point   # The chat's points as window pixels (its scale, right to left mirrored).
    return chat_point(region or chat()[1], rect)


def card_hit(action, value=None):
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    for rect, name, found in reversed(active.plan.hits):
        if name == action and (value is None or found == value):
            return qa_hit.point(qa_hit.PIXELS, region, rect)   # A card lays out in region pixels, mirrored right to left.
    raise AssertionError("No %s %s on the card" % (action, value))


def plus_dump(error):
    """What the + menu showed when a step did not come: the steps taken, the hits, the page's rows, the sizes and a
    screenshot (state["plus_dump"]), so a timeout says whether events, geometry or the hit list kept the row away
    (as test_blender_bake_maps_job)."""
    v = view()
    menu = qa_plus_menu.popup(v)
    area, region = chat()
    win = window()
    state["plus_dump"] = {
        "error": str(error), "steps": state.get("plus_steps", [])[-40:], "group": qa_plus_menu.group_of("camera_look"),
        "window": [win.width, win.height], "chat_region": [region.x, region.y, region.width, region.height],
        "chat_area": [area.x, area.y, area.width, area.height], "is_open": qa_plus_menu.is_open(v),
        "page": qa_plus_menu.page(v), "drawn_page": qa_plus_menu.drawn_page(v),
        "chat_hits": [[list(r), a, str(val)] for r, a, val in v.get("hits", [])][:60],
        "popup_hits": [[list(h[0]), h[1], str(h[2])] for h in qa_plus_menu.hits(v)][:80],
        "popup_rect": list(v.get("popup_rect") or []),
        "rows": [{key: (str(value) if key == "value" else value) for key, value in row.items()
                  if key in ("action", "value", "kind", "depth")} for row in (menu.get("rows") or [])][:60],
        "scroll": menu.get("scroll"), "layout": {key: value for key, value in (menu.get("layout") or {}).items()
                                                  if key in ("rect", "max_scroll")}}
    try:
        capture("look-plus-timeout.png")
    except Exception as capture_error:   # The dump is still worth having without the picture.
        state["plus_dump"]["screenshot_error"] = str(capture_error)


def build_scene():
    """Bright blocks on a floor, a camera on them, and the largest 3D view looking through it in Material Preview
    (the viewport compositor runs in the camera view)."""
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type in {'MESH', 'CAMERA'}:
            bpy.data.objects.remove(obj)
    bpy.ops.mesh.primitive_plane_add(size=20)
    for n, x in enumerate((-2.0, 0.0, 2.0)):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, 0.0, 0.5 + n * 0.3))
        material = bpy.data.materials.new("Glow %d" % n)
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Emission Color"].default_value = (1.0, 0.8, 0.5, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 4.0
        bpy.context.active_object.data.materials.append(material)
    camera = bpy.data.objects.new("Shot Camera", bpy.data.cameras.new("Shot Camera"))
    scene.collection.objects.link(camera)
    camera.location, camera.rotation_euler = (0.0, -8.0, 2.0), (1.38, 0.0, 0.0)
    camera.data.dof.aperture_fstop = 4.0
    scene.camera = camera
    area = largest_view()
    space = area.spaces.active
    space.region_3d.view_perspective = 'CAMERA'
    space.shading.type = 'MATERIAL'
    return camera


def look_nodes_count():
    group = bpy.context.scene.compositing_node_group
    from angelo_agent import look_nodes
    return 0 if group is None else sum(1 for n in group.nodes if n.name == look_nodes.NODE_NAME)


def look_state():
    from angelo_agent import look_system
    return json.loads(bpy.context.scene.get(look_system.STATE, "{}"))


def finish(error=None):
    if error:
        trace = "".join(traceback.format_exception(error))
        state["error"] = trace.replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_LOOK_CARD", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit * qa_waits.timeout_scale():
        raise AssertionError("Timed out in " + phase)
    return False


def card():
    from angelo_agent import card_host
    return card_host.active_card()


def tick():
    from angelo_agent import card_host, director
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 300 * qa_waits.timeout_scale(), "Suite timed out"
        if phase == "boot":
            if not (qa_boot.started() and director.is_director(window())):
                return .3
            camera = build_scene()
            state["camera"] = camera.name
            state["bokeh_before"] = (camera.data.dof.aperture_blades, camera.data.dof.aperture_ratio)
            rect = next((r for r, a, v in view().get("hits", []) if a == "attach"), None)
            if rect is not None:
                press(*at(rect))
            state["phase"] = "plus"
            return 1.0
        if phase == "plus":
            try:
                opened = wait(phase, lambda: qa_plus_menu.is_open(view()), limit=6)
                # The Render group's tile opens its page, then the row is clicked, as a person does.
                kind, rect = qa_plus_menu.step(view(), "popup_add_open", "camera_look") if opened else (None, None)
                if opened:
                    state.setdefault("plus_steps", []).append(
                        [round(time.monotonic() - started, 2), kind, list(rect) if rect else None])
                # The row's 6 s run from the first step taken: a menu not drawn yet ("wait" before any step) is the
                # first draw, which on a GPU-less host waits for the camera view's shaders (13 s on the Linux VM).
                started_row = "plus_row" in state.get("since", {})
                if opened and (kind in ("open", "back", "scroll", "scroll_up") or (kind == "wait" and started_row)):
                    wait("plus_row", lambda: False, limit=6)   # Raises once the row has not come in 6 s.
            except AssertionError as error:
                plus_dump(error)
                raise
            if not opened:
                return .2   # The menu opens on a later draw (wait raises once it has not in 6 s).
            if opened:
                if kind in ("open", "back", "scroll", "scroll_up", "wait"):
                    if kind in ("scroll", "scroll_up"):
                        wheel = 'WHEELUPMOUSE' if kind == "scroll_up" else 'WHEELDOWNMOUSE'
                        for value in ('PRESS', 'RELEASE'):
                            window().event_simulate(type=wheel, value=value, x=at(rect)[0], y=at(rect)[1])
                    elif rect is not None:
                        press(*at(rect))
                    return .4
                state["before_shot"] = str(capture("look-0-plus-menu.png"))
                checks["the_plus_menu_row_opens_the_card"] = kind == "click"
                if kind == "click":
                    press(*at(rect))
            state["phase"] = "card"
            return .8
        if phase == "card":
            if not wait(phase, lambda: card_host.is_active("camera_look") and card_host._active.plan is not None):
                return .2
            checks["the_card_opens_on_the_scenes_camera"] = card().camera == state["camera"] and \
                card().phase == "compose" and abs(card().reference_f_stop - 4.0) < 1e-6
            press(*card_hit("preset_open"))
            state["phase"] = "pick"
            return .5
        if phase == "pick":
            press(*card_hit("preset", "ANAMORPHIC"))
            state["phase"] = "match"
            return .5
        if phase == "match":
            press(*card_hit("match"))
            state["phase"] = "iso"
            return .5
        if phase == "iso":
            press(*card_hit("iso", "400"))
            state["phase"] = "apply"
            return .5
        if phase == "apply":
            checks["the_card_shows_two_stops"] = abs(card().stops() - 2.0) < 1e-6
            capture("look-1-card.png")
            press(*card_hit("apply"))
            state["phase"] = "applied"
            return .8
        if phase == "applied":
            if not wait(phase, lambda: card().phase in ("done", "failed")):
                return .2
            camera = bpy.data.objects[state["camera"]]
            stages = look_state().get("stages", {})
            checks["apply_succeeds"] = card().phase == "done" and card().error == ""
            checks["one_look_node_before_the_output"] = look_nodes_count() == 1
            checks["the_oval_bokeh_of_anamorphic"] = camera.data.dof.aperture_ratio == 2.0
            checks["film_exposure_two_stops"] = abs(stages.get("film", {}).get("exposure", 0) - 2.0) < 1e-6
            checks["the_camera_view_runs_the_compositor"] = largest_view().spaces.active.shading.use_compositor == \
                'CAMERA'
            state["measured"]["receipt"] = card().receipt
            state["phase"] = "look"
            return 2.0   # The viewport compositor's first frames.
        if phase == "look":
            after = capture("look-2-applied.png")
            before, now = view3d_pixels(Path(state["before_shot"])), view3d_pixels(after)
            changed = sum(a != b for a, b in zip(before, now)) / max(1, len(now))
            state["measured"]["view_pixels_changed"] = round(changed, 4)
            checks["the_camera_view_shows_the_look"] = changed > 0.01
            press(*card_hit("close"))
            state["phase"] = "reopen"
            return .6
        if phase == "reopen":
            area = largest_view()
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window(), area=area, region=region):
                bpy.ops.angelo.camera_look('INVOKE_DEFAULT')   # F3 > Camera Look, as a person runs it.
            state["phase"] = "take_off"
            return .6
        if phase == "take_off":
            if not wait(phase, lambda: card_host.is_active("camera_look") and card_host._active.plan is not None):
                return .2
            checks["the_card_reopens_on_the_applied_look"] = card().preset == "ANAMORPHIC"
            press(*card_hit("take_off"))
            state["phase"] = "taken_off"
            return .8
        if phase == "taken_off":
            if not wait(phase, lambda: card().phase in ("done", "failed")):
                return .2
            camera = bpy.data.objects[state["camera"]]
            checks["take_off_puts_everything_back"] = look_nodes_count() == 0 and not look_state() and \
                (camera.data.dof.aperture_blades, camera.data.dof.aperture_ratio) == state["bokeh_before"]
            capture("look-3-taken-off.png")
            press(*card_hit("close"))
            state["phase"] = "undo"
            return .6
        if phase == "undo":
            area = largest_view()
            x, y = area.x + area.width // 2, area.y + area.height // 2
            for value in ('PRESS', 'RELEASE'):
                window().event_simulate(type='Z', value=value, ctrl=True, x=x, y=y)
            state["phase"] = "undone"
            return 1.0
        if phase == "undone":
            camera = bpy.data.objects[state["camera"]]
            checks["one_undo_brings_the_look_back"] = look_nodes_count() == 1 and \
                look_state().get("preset") == "ANAMORPHIC" and camera.data.dof.aperture_ratio == 2.0
            capture("look-4-undone.png")
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


import qa_input  # noqa: E402  (synthetic clicks; after_clicks)
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
