# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A photo dropped on the 3D view in the running app, in Director (1.0.8): ``photo_drop`` end to end.

Real default startup (Director); each drop goes through Blender's own file drop (``WM_OT_drop_import_file``, what
an OS drop runs) with the pointer moved there first, and the card is answered with simulated clicks and keys at its
drawn rectangles. A fixture Meshy account and a stand-in generator (``studio_generation.generate`` writes a cone GLB):
no provider, network or credit.

Covered, each with a screenshot: only Angelo's handler takes an image drop in the 3D view (the stock image drop is set
aside, movies keep it); the card opens beside the drop point with the point marked and the model, account and price
on Make a 3D model; Make a 3D model sends the photo as the one reference with the fixture account, shows progress at
the drop point and places the made model standing at the drop point, selected, one Undo step; Escape changes
nothing; a drop on the cube gives it the photo as its material, packed; with no 3D account the row asks to connect
and bills nothing; a TIFF disables Make a 3D model and Use as a reference only, and Add as a picture stands it at the
drop point; the chat bar's + menu lists Photo to 3D model. Receipt: ``photo-drop-gui.json``.

Run through ``run_native_suites.py --gui --include test_blender_photo_drop_gui``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-photo-drop-qa"))
OUT.mkdir(parents=True, exist_ok=True)
for name, folder in (("ANGELO_STATE_DIR", "state"), ("ANGELO_CONNECTIONS_DIR", "accounts"), ("ANGELO_LIBRARY_DIR", "library")):
    os.environ.setdefault(name, str(OUT / folder))   # run_native_suites sets them before the add-on starts.
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
REPORT = OUT / "photo-drop-gui.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "screenshots": [],
         "fixture": "A fixture Meshy account and a stand-in generator; no provider call, nothing paid"}
checks, details = state["checks"], state["details"]
started = time.monotonic()
phase_started = {}
calls = []   # The stand-in generator's calls: (model, reference names, prompt).
progress_taken = threading.Event()  # Set once the progress screenshot is taken: the stand-in finishes only then.
EMPTY_SPOT, CUBE_SPOT = Vector((3.0, -2.0, 0.0)), Vector((0.0, 0.0, 1.0))


def log(*parts):
    print("ANGELO_PHOTO_DROP_QA", *parts, flush=True)


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail
    return bool(value)


def wait(phase, ready, limit=20.0):
    """True once ``ready()``; raises when ``phase`` has waited ``limit`` seconds."""
    phase_started.setdefault(phase, time.monotonic())
    if ready():
        return True
    if time.monotonic() - phase_started[phase] > limit:
        raise TimeoutError(f"{phase}: timed out after {limit:.0f} s")
    return False


def main_window():
    return bpy.context.window_manager.windows[0]


def view3d():
    area = max((a for a in main_window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=main_window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def spot(world):
    """``world`` in window pixels of the main 3D view."""
    area, region = view3d()
    point = view3d_utils.location_3d_to_region_2d(region, region.data, world)
    return region.x + point.x, region.y + point.y


def drop(path, world):
    """The pointer moved to ``world``, then Blender's own file drop of ``path`` there."""
    window, (area, region) = main_window(), view3d()
    x, y = spot(world)
    if not (region.x <= x < region.x + region.width and region.y <= y < region.y + region.height):
        raise AssertionError(f"the drop point {tuple(world)} is outside the 3D view")
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.wm.drop_import_file('INVOKE_DEFAULT', directory=str(path.parent) + os.sep, files=[{"name": path.name}])
    return x, y


def card():
    from angelo_agent import card_host
    shown = card_host.active_card()
    return shown if shown is not None and shown.kind == "photo_drop" else None


def hit(action):
    """The window-pixel centre of the shown card's control for ``action``, or None (not drawn, or disabled)."""
    from angelo_agent import card_host
    plan = card_host._active.plan if card_host._active else None
    if plan is None:
        return None
    _area, region = view3d()
    rect = next((r for r, a, _v in plan.hits if a == action), None)
    return (region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2) if rect else None


def click(xy):
    window = main_window()
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(xy[0]), y=int(xy[1]))
        window.event_simulate(type='LEFTMOUSE', value=value, x=int(xy[0]), y=int(xy[1]))


def key(kind):
    window = main_window()
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type=kind, value=value, x=window.width // 2, y=window.height // 2)


def fixtures():
    """photo.png (the photo), scan.tif (a format providers don't take) and cone.glb (what the stand-in makes)."""
    folder = OUT / "fx"
    folder.mkdir(exist_ok=True)
    for name, fmt, colour in (("photo.png", 'PNG', (0.9, 0.35, 0.1, 1.0)), ("scan.tif", 'TIFF', (0.1, 0.5, 0.9, 1.0))):
        image = bpy.data.images.new(name, 96, 96)
        image.pixels.foreach_set(list(colour) * 96 * 96)
        image.filepath_raw, image.file_format = str(folder / name), fmt
        image.save()
        bpy.data.images.remove(image)
    before = set(bpy.data.objects)
    bpy.ops.mesh.primitive_cone_add(radius1=0.5, depth=1.0, location=(0.0, 0.0, 0.5))
    cone = next(o for o in bpy.data.objects if o not in before)
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == cone)
    bpy.ops.export_scene.gltf(filepath=str(folder / "cone.glb"), export_format='GLB', use_selection=True)
    bpy.data.objects.remove(cone)
    return folder


def stand_in(glb):
    """``studio_generation.generate`` without a provider: progress, then the cone as model.glb."""
    def generate(binding, model, prompt, options, references, directory, emit, cancel, *args, **kwargs):
        calls.append((model, [r["name"] for r in references], prompt, binding.service))
        emit({"status": "generating", "progress": 40})
        progress_taken.wait(60)   # Held until the suite has its progress screenshot (a bound, never a pace).
        (Path(directory) / "model.glb").write_bytes(Path(glb).read_bytes())
        return {"status": "ready", "progress": 100, "files": ["model.glb"], "thumbnail": ""}
    return generate


def finish(error=None):
    progress_taken.set()
    if error is not None:
        state.update(phase="failed", error=str(error), traceback=traceback.format_exc())
        log(state["traceback"])
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    log(json.dumps({"phase": state["phase"], "success": state["success"],
                    "failed": [k for k, v in checks.items() if not v]}))
    bpy.ops.wm.quit_blender()


def added_objects():
    return [o for o in bpy.context.scene.objects if o.name not in state["objects_before"]]


def tick():
    from angelo_agent import card_host, chat_destinations, director, photo_drop_choices as choices, studio, studio_generation, ui
    from angelo_agent.api_connections import CheckResult
    from angelo_agent.provider_registry import SPECS
    phase = state["phase"]
    try:
        if phase == "boot":
            import addon_utils
            if not addon_utils.check("angelo_agent")[1]:
                addon_utils.enable("angelo_agent", default_set=False, persistent=False)
            state["phase"] = "settle"
            return .5
        if phase == "settle":
            if not wait(phase, lambda: qa_boot.started() and any(a.type == 'VIEW_3D' for a in main_window().screen.areas),
                        limit=60):
                state["waiting_on"] = qa_boot.waiting_on()
                return .3
            check("runs_in_director", director.is_director(main_window()))
            check("only_angelo_takes_image_drops_in_the_3d_view",
                  getattr(bpy.types, "VIEW3D_FH_empty_image", None) is None
                  and getattr(bpy.types, "ANGELO_FH_photo_drop", None) is not None
                  and getattr(bpy.types, "ANGELO_FH_movie_empty", None) is not None)
            feature = chat_destinations.feature("photo_to_3d")
            check("plus_menu_lists_photo_to_3d", feature is not None and feature.operator == "angelo.photo_drop")
            state["folder"] = str(fixtures())
            if "Cube" not in bpy.data.objects:
                bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
            cube = bpy.data.objects["Cube"]
            cube.data.materials.clear()
            record = ui.connections().add("MESHY", "API_KEY", "QA Meshy", group="GENAI")
            ui._memory_keys[record["id"]] = "qa-fixture-key-not-a-real-account"
            ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True,
                                                                     SPECS[record["mode"]]["models"])
            state["account"] = record["id"]
            studio_generation.generate = stand_in(Path(state["folder"]) / "cone.glb")
            area, region = view3d()
            with bpy.context.temp_override(window=main_window(), area=area, region=region):
                bpy.ops.view3d.view_all(center=False)
            state["objects_before"] = sorted(o.name for o in bpy.context.scene.objects)
            state["phase"] = "drop_make"
            return .5
        folder = Path(state.get("folder", OUT))
        if phase == "drop_make":
            state["drop_xy"] = drop(folder / "photo.png", EMPTY_SPOT)
            state["phase"] = "card_make"
            return .4
        if phase == "card_make":
            if not wait(phase, lambda: card() is not None and hit(choices.MAKE_3D) is not None):
                return .2
            shown = card()
            rows = {r.action: r for r in choices.rows(shown.facts)}
            check("card_offers_all_four_choices", all(rows[a].enabled for a in
                                                      (choices.MAKE_3D, choices.REFERENCE, choices.PICTURE)))
            check("make_row_names_the_model_account_and_price",
                  shown.facts.model and shown.facts.model["connection"] == state["account"]
                  and rows[choices.MAKE_3D].price is not None, shown.facts.model)
            _area, region = view3d()
            x, y = state["drop_xy"]
            px, py = shown.point
            check("card_stands_at_the_drop_point", abs(region.x + px - x) < 2 and abs(region.y + py - y) < 2)
            plan_card = card_host._active.plan.card
            check("card_never_covers_the_drop_point",
                  not (plan_card[0] <= px <= plan_card[0] + plan_card[2] and plan_card[1] <= py <= plan_card[1] + plan_card[3]))
            capture("photo-drop-1-card.png")
            click(hit(choices.MAKE_3D))
            state["phase"] = "making"
            return .3
        if phase == "making":
            if not wait(phase, lambda: card() is None and studio._jobs.active, limit=10):
                return .2
            capture("photo-drop-2-making-at-the-drop-point.png")
            progress_taken.set()
            state["phase"] = "placed"
            return .3
        if phase == "placed":
            if not wait(phase, lambda: bool(added_objects()), limit=30):
                return .3
            check("one_generation_with_the_photo_as_its_reference",
                  len(calls) == 1 and calls[0][0] == "meshy-7" and calls[0][1] == ["photo.png"], calls)
            added = added_objects()
            bpy.context.view_layer.update()
            meshes = [o for o in added if o.type == 'MESH']
            corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
            centre = sum(corners, Vector()) / max(1, len(corners))
            low = min(c.z for c in corners) if corners else 99
            check("model_stands_at_the_drop_point",
                  meshes and (Vector((centre.x, centre.y)) - EMPTY_SPOT.xy).length < 0.25 and abs(low - EMPTY_SPOT.z) < 0.05,
                  {"centre": tuple(centre), "base": low})
            check("placed_model_is_selected", all(o.select_get() for o in meshes))
            capture("photo-drop-3-model-placed.png")
            area, region = view3d()
            with bpy.context.temp_override(window=main_window(), area=area, region=region):
                bpy.ops.ed.undo()
            check("one_undo_removes_the_placed_model", not added_objects())
            with bpy.context.temp_override(window=main_window(), area=area, region=region):
                bpy.ops.ed.redo()
            state["objects_before"] = sorted(o.name for o in bpy.context.scene.objects)
            state["phase"] = "drop_cancel"
            return .3
        if phase == "drop_cancel":
            drop(folder / "photo.png", EMPTY_SPOT + Vector((-1.5, 0, 0)))
            state["phase"] = "cancel"
            return .4
        if phase == "cancel":
            if not wait(phase, lambda: card() is not None and hit("cancel") is not None):
                return .2
            key('ESC')
            state["phase"] = "cancelled"
            return .4
        if phase == "cancelled":
            if not wait(phase, lambda: card() is None, limit=5):
                return .2
            check("escape_changes_nothing", not added_objects() and len(calls) == 1)
            drop(folder / "photo.png", CUBE_SPOT)
            state["phase"] = "material"
            return .4
        if phase == "material":
            if not wait(phase, lambda: card() is not None and hit(choices.MATERIAL) is not None):
                return .2
            check("drop_on_the_cube_targets_it", card().facts.target == "Cube", card().facts.target)
            capture("photo-drop-4-on-the-cube.png")
            click(hit(choices.MATERIAL))
            state["phase"] = "material_done"
            return .4
        if phase == "material_done":
            if not wait(phase, lambda: card() is None, limit=5):
                return .2
            material = bpy.data.objects["Cube"].active_material
            images = [n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE'] if material else []
            check("cube_gets_the_photo_as_its_material_packed",
                  images and images[0].packed_file is not None, material.name if material else None)
            area, region = view3d()
            area.spaces.active.shading.type = 'MATERIAL'
            capture("photo-drop-5-material.png")
            area.spaces.active.shading.type = 'SOLID'
            for identity in list(ui._memory_keys):
                if identity == state["account"]:
                    ui.connections().remove(identity)
                    ui._memory_keys.pop(identity, None)
                    ui._api_connections.results.pop(identity, None)
            drop(folder / "photo.png", EMPTY_SPOT)
            state["phase"] = "no_keys"
            return .4
        if phase == "no_keys":
            if not wait(phase, lambda: card() is not None and hit(choices.CONNECT) is not None):
                return .2
            check("no_account_asks_to_connect", card().facts.model is None and hit(choices.MAKE_3D) is None)
            capture("photo-drop-6-no-keys.png")
            key('ESC')
            state["phase"] = "drop_tiff"
            return .4
        if phase == "drop_tiff":
            if not wait(phase, lambda: card() is None, limit=5):
                return .2
            drop(folder / "scan.tif", EMPTY_SPOT + Vector((0, 2.0, 0)))
            state["phase"] = "tiff"
            return .4
        if phase == "tiff":
            if not wait(phase, lambda: card() is not None and hit(choices.PICTURE) is not None):
                return .2
            check("tiff_disables_model_and_reference_only",
                  hit(choices.MAKE_3D) is None and hit(choices.CONNECT) is None and hit(choices.REFERENCE) is None
                  and hit(choices.PICTURE) is not None)
            capture("photo-drop-7-unsupported-format.png")
            before = set(bpy.data.objects)
            state["before_picture"] = [o.name for o in before]
            click(hit(choices.PICTURE))
            state["phase"] = "picture"
            return .4
        if phase == "picture":
            if not wait(phase, lambda: card() is None, limit=5):
                return .2
            new = [o for o in bpy.data.objects if o.name not in state["before_picture"]]
            picture = new[0] if len(new) == 1 else None
            target = EMPTY_SPOT + Vector((0, 2.0, 0))
            check("add_as_a_picture_stands_it_at_the_drop_point",
                  picture is not None and picture.empty_display_type == 'IMAGE'
                  and (picture.location - target).length < 0.05, [o.name for o in new])
            capture("photo-drop-8-picture.png")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
