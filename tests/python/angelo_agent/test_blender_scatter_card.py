# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scatter with a biome in the running app: the + menu row, the Scatter card, its Library models and the result.

Real default startup (Director Mode with the chat) and real input (``Window.event_simulate``); no provider or
account, and nothing leaves this computer: the Library is a mirror served here (``qa_library_mirror``). Checked
against doc/angelo/scatter.rst, "The card": the chat bar's + menu opens the card on the active mesh; the Meadow
biome lists its three layers and Scatter stays off until each has objects; Use Selection fills Stones with the
person's own pebble; Find in Library lists the mirror's model for Grass and for Flowers, and Add imports it through
the Library's import into a collection hidden in the viewport and renders; Scatter makes one new object whose
Geometry Nodes group is Angelo's, placing instances for every layer, the surface unchanged; the 3D view shows them;
one Undo takes the system away and keeps the imports.

Run through ``run_native_suites.py --gui --include test_blender_scatter_card``; check ``scatter-card.json`` for
``success`` and ``phase == "complete"``. The screenshots are the GUI proof (Director, the default view).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_hit  # noqa: E402  (window pixels of a surface's points: scale and right to left)
import qa_library_mirror  # noqa: E402  (the Library, served from this process)
import qa_plus_menu  # noqa: E402  (the + menu's pages, read from the chat's canvas state)
import qa_waits  # noqa: E402  (the runner's --timeout-scale)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-scatter-card-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "scatter-card.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()

mirror = qa_library_mirror.Mirror()
mirror.model("qa:grass", "Grass Clump", ["grass", "clump"], size=(0.4, 0.4, 0.5))
mirror.model("qa:flower", "Wild Flower", ["flower", "meadow"], size=(0.2, 0.2, 0.4))
mirror.model("qa:shed", "Garden Shed", ["shed", "building"], size=(3.0, 2.0, 2.5))   # Never offered: other words.
mirror.start()


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def view3d_pixels(path):
    """The largest 3D view's pixels in a window screenshot, as a flat list of RGB tuples (vision: did it change)."""
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
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


def build_scene():
    """A 24 m terrain with a gentle rise, and a pebble of the person's own for the Stones layer."""
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type == 'MESH':
            bpy.data.objects.remove(obj)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=48, y_subdivisions=48, size=24)
    terrain = bpy.context.active_object
    terrain.name = "Terrain"
    for vertex in terrain.data.vertices:
        vertex.co.z = 1.2 * math.exp(-(vertex.co.x ** 2 + vertex.co.y ** 2) / 60.0)
    bpy.ops.mesh.primitive_ico_sphere_add(radius=0.25, location=(30, 0, 0))
    pebble = bpy.context.active_object
    pebble.name = "Pebble"
    bpy.ops.object.select_all(action='DESELECT')
    terrain.select_set(True)
    bpy.context.view_layer.objects.active = terrain
    return terrain


def surface_digest(obj):
    return (len(obj.data.vertices), round(sum(v.co.z for v in obj.data.vertices), 6), len(obj.modifiers))


def finish(error=None):
    if error:
        trace = "".join(traceback.format_exception(error))
        state["error"] = trace.replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_SCATTER_CARD", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
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
            terrain = build_scene()
            state["surface"] = surface_digest(terrain)
            state["objects_before"] = len(bpy.data.objects)
            rect = next((r for r, a, v in view().get("hits", []) if a == "attach"), None)
            if rect is not None:
                press(*at(rect))
            state["phase"] = "plus"
            return .6
        if phase == "plus":
            if wait(phase, lambda: qa_plus_menu.is_open(view()), limit=6):
                kind, rect = qa_plus_menu.step(view(), "popup_add_open", "scatter")
                if kind in ("open", "back", "scroll", "scroll_up", "wait"):
                    wait("plus_row", lambda: False, limit=6)   # Raises once the row has not come in 6 s.
                    if kind in ("scroll", "scroll_up"):
                        wheel = 'WHEELUPMOUSE' if kind == "scroll_up" else 'WHEELDOWNMOUSE'
                        for value in ('PRESS', 'RELEASE'):
                            window().event_simulate(type=wheel, value=value, x=at(rect)[0], y=at(rect)[1])
                    elif rect is not None:
                        press(*at(rect))
                    return .4
                capture("scatter-0-plus-menu.png")
                checks["the_plus_menu_row_opens_the_card"] = kind == "click"
                if kind == "click":
                    press(*at(rect))
            state["phase"] = "card"
            return .8
        if phase == "card":
            if not wait(phase, lambda: card_host.is_active("scatter") and card_host._active.plan is not None):
                return .2
            checks["the_card_opens_on_the_active_mesh_as_a_biome"] = card().surface == "Terrain" and \
                card().mode == "biome" and card().phase == "compose"
            press(*card_hit("biome_open"))
            state["phase"] = "pick_biome"
            return .5
        if phase == "pick_biome":
            press(*card_hit("biome", "meadow"))
            state["phase"] = "biome"
            return .5
        if phase == "biome":
            layers = [layer["name"] for layer in card().layers()]
            checks["meadow_lists_its_three_layers"] = card().biome == "meadow" and layers == ["Stones", "Grass",
                                                                                           "Flowers"]
            enabled = [found for _rect, action, found in card_host._active.plan.hits if action == "scatter"]
            checks["scatter_waits_until_every_layer_has_objects"] = not enabled and not card().ready()
            capture("scatter-1-meadow.png")
            bpy.ops.object.select_all(action='DESELECT')
            bpy.data.objects["Pebble"].select_set(True)   # The person picks their own pebble in the view.
            press(*card_hit("use_selection", "Stones"))
            state["phase"] = "find_grass"
            return .5
        if phase in ("find_grass", "find_flowers"):
            layer = "Grass" if phase == "find_grass" else "Flowers"
            if phase == "find_grass":
                checks["use_selection_fills_stones"] = card().assets.get("Stones") == ["Pebble"]
            press(*card_hit("find", layer))
            state["phase"] = "found_" + layer
            return .3
        if phase.startswith("found_"):
            layer = phase[len("found_"):]
            if not wait(phase, lambda: card().found and card().found[0] == layer and card().found[1] is not None):
                return .2
            offered = [item["id"] for item in card().found[1]]
            wanted = "qa:grass" if layer == "Grass" else "qa:flower"
            checks["find_offers_the_layers_model_%s" % layer.lower()] = offered == [wanted]
            capture("scatter-2-found-%s.png" % layer.lower())
            press(*card_hit("add", wanted))
            state["phase"] = "added_" + layer
            return .3
        if phase.startswith("added_"):
            layer = phase[len("added_"):]
            if not wait(phase, lambda: card().fetching is None and card().assets.get(layer), limit=60):
                return .2
            names = card().assets[layer]
            collection = bpy.data.objects[names[0]].users_collection[0]
            layer_collection = next(lc for lc in _walk(bpy.context.view_layer.layer_collection)
                                    if lc.collection == collection)
            checks["add_imports_hidden_for_%s" % layer.lower()] = collection.hide_render and \
                layer_collection.hide_viewport and collection.name.startswith("Library")
            state["phase"] = "find_flowers" if layer == "Grass" else "ready"
            return .3
        if phase == "ready":
            checks["scatter_is_offered_once_every_layer_has_objects"] = card().ready()
            state["before_shot"] = str(capture("scatter-3-ready.png"))
            press(*card_hit("scatter"))
            state["phase"] = "done"
            return .5
        if phase == "done":
            if not wait(phase, lambda: card().phase in ("done", "failed")):
                return .2
            receipt = card().receipt or {}
            state["measured"]["receipt"] = receipt
            checks["scatter_succeeds"] = card().phase == "done"
            counts = {layer["name"]: layer["instances"] for layer in receipt.get("layers", ())}
            checks["every_layer_placed_instances"] = set(counts) == {"Stones", "Grass", "Flowers"} and \
                all(counts.values())
            system = bpy.data.objects.get(receipt.get("object", ""))
            modifier = system.modifiers[0] if system is not None and system.modifiers else None
            checks["one_new_geometry_nodes_object"] = modifier is not None and modifier.type == 'NODES' and \
                modifier.node_group.name.startswith("Angelo Scatter") and \
                len(bpy.data.objects) == state["objects_before"] + 3   # The system and the two imports.
            checks["the_surface_is_unchanged"] = surface_digest(bpy.data.objects["Terrain"]) == state["surface"]
            state["system"] = receipt.get("object")
            state["phase"] = "look"
            return .5
        if phase == "look":
            after = capture("scatter-4-done.png")
            before = view3d_pixels(Path(state["before_shot"]))
            now = view3d_pixels(after)
            changed = sum(a != b for a, b in zip(before, now)) / max(1, len(now))
            state["measured"]["view_pixels_changed"] = round(changed, 4)
            checks["the_view_shows_the_scatter"] = changed > 0.01
            press(*card_hit("close"))
            state["phase"] = "undo"
            return .5
        if phase == "undo":
            area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
            x, y = area.x + area.width // 2, area.y + area.height // 2
            for value in ('PRESS', 'RELEASE'):
                window().event_simulate(type='Z', value=value, ctrl=True, x=x, y=y)
            state["phase"] = "undone"
            return 1.0
        if phase == "undone":
            names = {obj.name for obj in bpy.data.objects}
            checks["one_undo_removes_the_system_and_keeps_the_imports"] = state["system"] not in names and \
                "Grass Clump" in names and "Wild Flower" in names
            capture("scatter-5-undone.png")
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


def _walk(layer_collection):
    yield layer_collection
    for child in layer_collection.children:
        yield from _walk(child)


import qa_input  # noqa: E402  (synthetic clicks; after_clicks)
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
