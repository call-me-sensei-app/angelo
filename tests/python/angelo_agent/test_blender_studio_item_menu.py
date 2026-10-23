# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's item menu and the Properties editor's drops, in the running app (1.0.5, part A on part E's drag).

A real window and simulated input; fixture items (a model, two images; no provider, account or network), laid out as
``test_blender_media_drop_gui`` lays them out, with the Properties editor kept. Checked, with screenshots:

* a right-click on a grid item opens the item menu for it; with that item part of a Cmd/Ctrl+click selection, for
  the whole selection;
* Open in Image Editor (the menu's own action for images) shows the picture in the Image Editor;
* a model, and an image off a material slot, dragged over the Properties editor are refused: the red reason shows
  mid-drag and nothing is added.

Run through ``run_native_suites.py --gui --include test_blender_studio_item_menu`` on a build with the native drag.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-item-menu-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_LIBRARY_DIR", str(OUT / "library"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_input, inside the app.
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import studio, studio_assets, studio_assets_host, studio_item_menu  # noqa: E402
from angelo_agent.studio_state import StudioLibrary  # noqa: E402
import qa_input  # noqa: E402

REPORT = OUT / "studio-item-menu.json"
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "screenshots": []}
checks, details = state["checks"], state["details"]
menus = []   # The item ids each item-menu draw was for.


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail
    return bool(value)


def main_window():
    return bpy.context.window_manager.windows[0]


def region_of(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=main_window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


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


def frames(count):
    for _ in range(count):
        yield


def wait(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        yield
    return True


def press(window, xy, kind='LEFTMOUSE', **keys):
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(xy[0]), y=int(xy[1]))
    window.event_simulate(type=kind, value='PRESS', x=int(xy[0]), y=int(xy[1]), **keys)
    window.event_simulate(type=kind, value='RELEASE', x=int(xy[0]), y=int(xy[1]), **keys)
    yield from frames(4)


def escape(window):
    window.event_simulate(type='ESC', value='PRESS', x=10, y=10)
    window.event_simulate(type='ESC', value='RELEASE', x=10, y=10)
    yield from frames(3)


def drag(window, start, end, during):
    """A real drag, one event per pass, as a person makes it (``qa_input.drag_path``); ``during`` runs just before
    the release."""
    events = qa_input.drag_path(start, end, bpy.context.preferences.system.ui_scale)
    for n, event in enumerate(events):
        qa_input.send(window, [event])
        yield
        if n == len(events) - 3:
            during()
    yield from frames(4)


def fixtures():
    folder = OUT / "fx"
    folder.mkdir(exist_ok=True)
    bpy.ops.mesh.primitive_cone_add(radius1=0.5, depth=1.0)
    cone = bpy.context.object
    bpy.ops.export_scene.gltf(filepath=str(folder / "cone.glb"), export_format='GLB', use_selection=True)
    bpy.data.objects.remove(cone)
    for name, colour in (("poster", (1.0, 0.0, 1.0, 1.0)), ("banner", (0.0, 1.0, 1.0, 1.0))):
        image = bpy.data.images.new(name, 64, 64)
        image.pixels.foreach_set(list(colour) * 64 * 64)
        image.filepath_raw, image.file_format = str(folder / f"{name}.png"), 'PNG'
        image.save()
        bpy.data.images.remove(image)
    library, made = StudioLibrary(), {}
    for key, kind, source in (("cone", "3D", "cone.glb"), ("poster", "IMAGE", "poster.png"),
                              ("banner", "IMAGE", "banner.png")):
        record = library.add(kind, "A fixture " + key, "Fixture model", status="ready")
        name = "model.glb" if kind == "3D" else "image-1.png"
        (library.result_directory(record["id"]) / name).write_bytes((folder / source).read_bytes())
        record.update(files=[name], thumbnail="" if kind == "3D" else name, progress=100)
        library.update(record)
        made[key] = record["id"]
    return made


def layout(window):
    """3D view | Media Studio over an Image Editor; the Outliner and the Properties editor as they were."""
    from angelo_agent import editor_types
    screen = window.screen
    view = max((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width)
    with bpy.context.temp_override(window=window, area=view, region=region_of(view)):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=0.45)
    right = max((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)
    with bpy.context.temp_override(window=window, area=right, region=region_of(right)):
        bpy.ops.screen.area_split(direction='HORIZONTAL', factor=0.35)
    view = min((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)
    lower, upper = sorted((a for a in screen.areas if a.type == 'VIEW_3D' and a != view), key=lambda a: a.y)
    lower.type = 'IMAGE_EDITOR'
    editor_types.select_console(window, upper, generative=True)
    return upper, lower, next(a for a in screen.areas if a.type == 'PROPERTIES')


def tile(area, identity):
    hit = next((r for r, action, value in studio.view(area).get("hits", []) if action == "asset" and value == identity),
               None)
    region = region_of(area)
    return (region.x + hit[0] + hit[2] / 2, region.y + hit[1] + hit[3] / 2) if hit else None


def red_near(path, xy, area):
    """Alert-red pixels (the drag's "can't drop here" reason) in a band around the pointer, from just left of
    ``area`` (the editor under the pointer) to the window's right edge: Blender draws the reason beside the pointer,
    shifted left when it would leave the window (a drop on the Properties editor at the window's right edge)."""
    px = pixels(path)
    s = bpy.context.preferences.system.ui_scale
    x = max(0, area.x - 200 * s)
    # Above the pointer as much as below: the reason drew ~90 px above it (d-final line). Red-dominant, not a fixed
    # brightness: it measured (174, 87, 87) in the capture (d-round line).
    return count(px, (x, xy[1] - 100 * s, px.shape[1] - x, 300 * s),
                 lambda v: v[0] > 140 and v[0] > 1.6 * v[1] and v[0] > 1.6 * v[2])


def count(px, rect, test):
    x, y, w, h = (int(v) for v in rect)
    block = px[max(0, y):y + h, max(0, x):x + w].reshape(-1, 3)
    return sum(1 for v in block if test(v))


def run():
    window = main_window()
    state["phase"] = "fixtures"
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    ids = fixtures()
    studio.library().reload()   # Studio read its library at start-up (Manual's tab), before these records existed.
    _library, directory = studio_assets_host.directories()
    studio_assets_host._state["since"] = float("-inf")
    written = yield from wait(lambda: all((studio_assets.read_manifest(directory).get(i) or {}).get("fingerprint")
                                          for i in ids.values()), 120)
    check("library_written", written)
    studio_area, image_area, properties = layout(window)
    original = studio_item_menu.ANGELO_MT_studio_item.draw

    def draw(self, context):
        menus.append(list(studio_item_menu.menu_ids))
        return original(self, context)
    studio_item_menu.ANGELO_MT_studio_item.draw = draw
    studio.view(studio_area).update(kind="IMAGE", grid_scroll=0)
    studio_area.tag_redraw()
    yield from wait(lambda: tile(studio_area, ids["poster"]) and tile(studio_area, ids["banner"]), 10)

    state["phase"] = "right_click"
    yield from press(window, tile(studio_area, ids["poster"]), kind='RIGHTMOUSE')
    yield from frames(6)
    capture("item-menu-1-right-click.png")
    check("a_right_click_opens_the_item_menu_for_the_item", menus and menus[-1] == [ids["poster"]], menus)
    yield from escape(window)

    state["phase"] = "selection"
    key = {"oskey": True} if sys.platform == "darwin" else {"ctrl": True}
    yield from press(window, tile(studio_area, ids["poster"]), **key)
    yield from press(window, tile(studio_area, ids["banner"]), **key)
    yield from press(window, tile(studio_area, ids["poster"]), kind='RIGHTMOUSE')
    yield from frames(6)
    capture("item-menu-2-selection.png")
    check("a_right_click_on_a_selected_item_takes_the_selection",
          menus and sorted(menus[-1]) == sorted([ids["poster"], ids["banner"]]), menus)
    yield from escape(window)

    state["phase"] = "open_image"
    with bpy.context.temp_override(window=window, screen=window.screen):
        bpy.ops.angelo.media_open_image(item_ids=ids["poster"])
    region = region_of(image_area)

    def magenta_drawn():   # The editor draws the picture on a later frame than the one it was set on.
        path = capture("item-menu-3-open-in-image-editor.png")
        state["magenta"] = count(pixels(path), (region.x, region.y, region.width, region.height),
                                 lambda v: v[0] > 200 and v[1] < 60 and v[2] > 200)
        return state["magenta"] >= 64 * 64 // 2
    yield from wait(magenta_drawn, 5)
    magenta = state["magenta"]
    shown = image_area.spaces.active.image
    # At least half the 64 px picture's own pixels: it is drawn, at any editor size (it shows at 1:1 or larger).
    check("open_in_image_editor_shows_the_picture", shown is not None and magenta >= 64 * 64 // 2,
          {"image": shown.name if shown else None, "magenta_pixels": magenta})

    state["phase"] = "properties_model"
    studio.view(studio_area).update(kind="3D", grid_scroll=0, selected_ids=[])
    studio_area.tag_redraw()
    yield from wait(lambda: tile(studio_area, ids["cone"]), 10)
    before = len(bpy.data.objects)
    region = region_of(properties)
    end = (region.x + region.width / 2, region.y + region.height / 2)
    shot = {}
    yield from drag(window, tile(studio_area, ids["cone"]), end,
                    lambda: shot.update(path=capture("item-menu-4-properties-refuses-a-model.png")))
    red = red_near(shot["path"], end, properties) if shot else 0
    check("properties_refuses_a_model_with_the_reason", red >= 150 and len(bpy.data.objects) == before,
          {"red_pixels": red, "objects": [before, len(bpy.data.objects)]})

    state["phase"] = "properties_image"
    studio.view(studio_area).update(kind="IMAGE", grid_scroll=0, selected_ids=[])
    studio_area.tag_redraw()
    yield from wait(lambda: tile(studio_area, ids["poster"]), 10)
    materials = len(bpy.data.materials)
    shot = {}
    yield from drag(window, tile(studio_area, ids["poster"]), end,
                    lambda: shot.update(path=capture("item-menu-5-properties-refuses-an-image-off-a-slot.png")))
    red = red_near(shot["path"], end, properties) if shot else 0
    check("properties_refuses_an_image_off_a_slot_with_the_reason", red >= 150 and len(bpy.data.materials) == materials,
          {"red_pixels": red})
    state["phase"] = "complete"


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STUDIO_ITEM_MENU", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick(steps=[]):   # noqa: B006 - the generator lives across timer calls.
    if not steps:
        steps.append(run())
    try:
        next(steps[0])
        if time.monotonic() - started > 300:
            raise AssertionError("timed out in " + state["phase"])
        return 0.05
    except StopIteration:
        finish()
    except Exception as error:   # noqa: BLE001 - every failure is reported.
        finish(error)
    return None


started = time.monotonic()
bpy.app.timers.register(tick, first_interval=1.0)
