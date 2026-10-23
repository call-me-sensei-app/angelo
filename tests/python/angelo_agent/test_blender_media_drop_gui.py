# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's items dragged like any asset, in the running app, with real drags (``studio_drag``,
``ASSET_OT_angelo_drag``, Angelo's drop boxes, ``media_drop``).

A real window and simulated input (``Window.event_simulate``); fixture items (two models, two images; no provider,
account or network). The main window holds a 3D view, Media Studio, an Image Editor, the Outliner and the chat.
Checked, with screenshots: a model dragged from Media Studio's grid lands under the pointer at its GLB's size and one
undo takes it back; an image onto the cube gives it the material, its picture packed; an image on empty space is a
reference image; an image in the Image Editor opens; a model there is refused (nothing added; the red reason in the
screenshot mid-drag); in the Outliner a model goes into the collection under the pointer and an image onto an object
is its material; an image dropped on the chat is attached and on Media Studio is a reference; two items selected with
Cmd/Ctrl+click are dragged together, one undo step; a release without moving still opens the item; and from Media
Studio's own window into the main window's 3D view, undone there. Receipt: ``media-drop-gui.json``.

Run through ``run_native_suites.py --gui --include test_blender_media_drop_gui`` on a build with the native drag.
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
from bpy_extras import view3d_utils
from mathutils import Vector

assert not bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-media-drop-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_LIBRARY_DIR", str(OUT / "library"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_input, inside the app.
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import studio, studio_assets, studio_assets_host  # noqa: E402
from angelo_agent.studio_state import StudioLibrary  # noqa: E402
import qa_input  # noqa: E402

REPORT = OUT / "media-drop-gui.json"
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "screenshots": []}
checks, details = state["checks"], state["details"]


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail
    return bool(value)


def main_window():
    return bpy.context.window_manager.windows[0]


def region_of(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or main_window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def wait(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        yield
    return True


def frames(count):
    for _ in range(count):
        yield


def drag(window, start, end, during=None):
    """A real drag in ``window``'s coordinates, one event per pass, as a person makes it (``qa_input.drag_path``:
    past the drag threshold still on the pressed item, then small moves); ``during`` runs just before the release."""
    events = qa_input.drag_path(start, end, bpy.context.preferences.system.ui_scale)
    for n, event in enumerate(events):
        qa_input.send(window, [event])
        yield
        if during is not None and n == len(events) - 3:
            during()
    yield from frames(4)


def click(window, xy, **keys):
    """A real click: the press and the release a frame apart, as a person's are."""
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(xy[0]), y=int(xy[1]))
    yield
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=int(xy[0]), y=int(xy[1]), **keys)
    yield
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=int(xy[0]), y=int(xy[1]), **keys)
    yield from frames(3)


def drag_across(source, start, target, end):
    """A drag that leaves ``source`` and ends in ``target``: the OS sends a dragging pointer's events to the window
    under it, so the press and first moves go to the first window and the rest, in its coordinates, to the second
    (``qa_input.drag_across_path``)."""
    first, rest = qa_input.drag_across_path(start, end, bpy.context.preferences.system.ui_scale)
    for event in first:
        qa_input.send(source, [event])
        yield
    yield from frames(3)
    for event in rest:
        qa_input.send(target, [event])
        yield
    yield from frames(4)


def undo(window, xy):
    window.event_simulate(type='Z', value='PRESS', ctrl=sys.platform != "darwin", oskey=sys.platform == "darwin",
                          x=int(xy[0]), y=int(xy[1]))
    window.event_simulate(type='Z', value='RELEASE', ctrl=sys.platform != "darwin", oskey=sys.platform == "darwin",
                          x=int(xy[0]), y=int(xy[1]))
    yield from frames(6)


def find_colour(path, rect, colour):
    """Centre (window pixels) of the pixels close to ``colour`` inside ``rect`` of a screenshot, or None."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    scale = width / main_window().width
    grid = values.reshape(height, width, 4)[:, :, :3]
    x0, y0, w, h = (round(v * scale) for v in rect)
    part = grid[max(0, y0):y0 + h, max(0, x0):x0 + w]
    ys, xs = numpy.nonzero((numpy.abs(part - numpy.array(colour[:3])) < .25).all(axis=2))
    if not len(xs):
        return None
    return (x0 + xs.mean()) / scale, (y0 + ys.mean()) / scale


def mesh_size(objects):
    bpy.context.view_layer.update()
    points = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]
    low = Vector(map(min, *(p.to_tuple() for p in points)))
    high = Vector(map(max, *(p.to_tuple() for p in points)))
    return low, high


def fixtures():
    folder = OUT / "fx"
    folder.mkdir(exist_ok=True)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0.3, 0.2, 1.0))
    sphere = bpy.context.object
    sphere.scale = (1, 1, 2)
    bpy.ops.export_scene.gltf(filepath=str(folder / "column.glb"), export_format='GLB', use_selection=True)
    bpy.data.objects.remove(sphere)
    bpy.ops.mesh.primitive_cone_add(radius1=0.5, depth=1.0, location=(0.0, 0.0, 0.5))
    cone = bpy.context.object
    bpy.ops.export_scene.gltf(filepath=str(folder / "cone.glb"), export_format='GLB', use_selection=True)
    bpy.data.objects.remove(cone)
    for name, colour in (("poster", (1.0, 0.0, 1.0, 1.0)), ("banner", (0.0, 1.0, 1.0, 1.0))):
        image = bpy.data.images.new(name, 64, 64)
        image.pixels.foreach_set(list(colour) * 64 * 64)
        image.filepath_raw, image.file_format = str(folder / f"{name}.png"), 'PNG'
        image.save()
        bpy.data.images.remove(image)
    library = StudioLibrary()
    made = {}
    for key, kind, source, prompt in (("column", "3D", "column.glb", "A stone column"),
                                      ("cone", "3D", "cone.glb", "A clay cone"),
                                      ("poster", "IMAGE", "poster.png", "A magenta poster"),
                                      ("banner", "IMAGE", "banner.png", "A cyan banner")):
        record = library.add(kind, prompt, "Fixture model", status="ready")
        name = "model.glb" if kind == "3D" else "image-1.png"
        (library.result_directory(record["id"]) / name).write_bytes((folder / source).read_bytes())
        record.update(files=[name], thumbnail="" if kind == "3D" else name, progress=100)
        library.update(record)
        made[key] = record
    return made


def layout(window):
    """3D view | Media Studio over an Image Editor, the Outliner, the chat in Properties' place."""
    from angelo_agent import editor_types
    screen = window.screen
    view = max((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width)
    with bpy.context.temp_override(window=window, area=view, region=region_of(view)):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=0.45)
    view = min((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)
    right = max((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)
    with bpy.context.temp_override(window=window, area=right, region=region_of(right)):
        bpy.ops.screen.area_split(direction='HORIZONTAL', factor=0.35)
    lower, upper = sorted((a for a in screen.areas if a.type == 'VIEW_3D' and a != view), key=lambda a: a.y)
    lower.type = 'IMAGE_EDITOR'
    editor_types.select_console(window, upper, generative=True)
    chat = next(a for a in screen.areas if a.type == 'PROPERTIES')
    editor_types.select_console(window, chat, coding=True)
    return view, upper, lower, next(a for a in screen.areas if a.type == 'OUTLINER'), chat


def tile(area, identity):
    hit = next((r for r, action, value in studio.view(area).get("hits", []) if action == "asset" and value == identity),
               None)
    if hit is None:
        return None
    region = region_of(area)
    return region.x + hit[0] + hit[2] / 2, region.y + hit[1] + hit[3] / 2


def show_kind(area, kind):
    studio.view(area).update(kind=kind, grid_scroll=0)
    area.tag_redraw()


def point_in_view(view, location):
    region, rv3d = region_of(view), view.spaces.active.region_3d
    xy = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(location))
    return region.x + xy.x, region.y + xy.y


def trace():
    """Record in the receipt every drop that reaches ``angelo.media_drop`` and every grid press and release (what a
    failed check needs to say where a drag stopped)."""
    from angelo_agent import media_drop, studio_drag
    log = state.setdefault("trace", [])
    run_drop = media_drop._Drop.run   # Both drop operators (data and interface-only) share it.

    def traced_run(self, context, event):
        result = run_drop(self, context, event)
        log.append(["drop", self.target, self.items.count("\n"), self.object, sorted(result)])
        return result
    media_drop._Drop.run = traced_run
    press, released = studio_drag.press, studio_drag.released

    def traced_press(state_, identity, rect, area, event, x, y):
        log.append(["press", identity[:8], bool(event.shift), bool(event.oskey), bool(event.ctrl)])
        return press(state_, identity, rect, area, event, x, y)

    def traced_released(state_):
        result = released(state_)
        if result:
            log.append(["release", result[0], result[1][:8], list(state_.get("selected_ids") or [])])
        return result
    studio_drag.press, studio_drag.released = traced_press, traced_released
    from angelo_agent import studio_asset_ops
    invoke = studio_asset_ops.ANGELO_OT_studio_asset.invoke

    def traced_invoke(self, context, event):
        result = invoke(self, context, event)
        log.append(["popup", self.asset_id[:8], sorted(result)])
        return result
    studio_asset_ops.ANGELO_OT_studio_asset.invoke = traced_invoke


def run():
    trace()
    window = main_window()
    state["phase"] = "fixtures"
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    made = fixtures()
    ids = {k: r["id"] for k, r in made.items()}
    library_dir, directory = studio_assets_host.directories()
    studio_assets_host._state["since"] = float("-inf")
    written = yield from wait(lambda: all(
        (studio_assets.read_manifest(directory).get(i) or {}).get("fingerprint") and not
        (studio_assets.read_manifest(directory).get(i) or {}).get("failed") for i in ids.values()), 120)
    check("library_written", written, studio_assets.read_manifest(directory))
    studio.library().reload()
    bpy.ops.mesh.primitive_cube_add(location=(-2.0, 1.5, 0.5))
    bpy.context.object.name = "Cube"
    bpy.ops.object.select_all(action='DESELECT')
    view, studio_area, image_area, outliner, chat = layout(window)
    yield from frames(20)
    rv3d = view.spaces.active.region_3d
    rv3d.view_location, rv3d.view_distance = (0.0, 0.0, 0.0), 14.0
    yield from frames(10)

    state["phase"] = "model_to_view"
    show_kind(studio_area, "3D")
    yield from wait(lambda: tile(studio_area, ids["column"]), 10)
    target = point_in_view(view, (1.0, -1.0, 0.0))
    before = set(bpy.data.objects)
    yield from drag(window, tile(studio_area, ids["column"]), target)
    yield from wait(lambda: set(bpy.data.objects) - before, 10)
    added = [o for o in bpy.data.objects if o not in before]
    capture("model-dropped.png")
    low, high = mesh_size(added) if added else (Vector(), Vector())
    check("model_lands_under_the_pointer", len(added) == 1 and abs(low.z) < 1e-3
          and (Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, 0)) - Vector((1.0, -1.0, 0.0))).length < 0.2
          and tuple(round(v, 3) for v in high - low) == (1.0, 1.0, 2.0), [o.name for o in added])
    name = added[0].name if added else ""
    yield from undo(window, target)
    check("one_undo_takes_the_drop_back", name and name not in bpy.data.objects)

    state["phase"] = "image_to_cube"
    show_kind(studio_area, "IMAGE")
    yield from wait(lambda: tile(studio_area, ids["poster"]), 10)
    yield from drag(window, tile(studio_area, ids["poster"]), point_in_view(view, (-2.0, 1.5, 0.5)))
    cube = bpy.data.objects["Cube"]
    yield from wait(lambda: cube.active_material is not None, 10)
    texture = next((n.image for n in cube.active_material.node_tree.nodes if n.type == 'TEX_IMAGE'), None) \
        if cube.active_material else None
    check("image_on_object_is_its_material", texture is not None and texture.packed_file is not None,
          cube.active_material.name if cube.active_material else None)
    capture("image-on-cube.png")

    state["phase"] = "image_to_empty_space"
    before = set(bpy.data.objects)
    vr = region_of(view)   # Empty floor in the view's lower left: nothing under the pointer.
    empty = (vr.x + vr.width * 0.15, vr.y + vr.height * 0.2)
    yield from drag(window, tile(studio_area, ids["banner"]), empty)
    yield from wait(lambda: set(bpy.data.objects) - before, 10)
    added = [o for o in bpy.data.objects if o not in before]
    check("image_on_empty_space_is_a_reference", len(added) == 1 and added[0].empty_display_type == 'IMAGE'
          and added[0].data and added[0].data.packed_file is not None, [o.name for o in added])

    # On an Empty (no material to take), as on empty space: a reference image.
    bpy.ops.object.empty_add(location=(-1.0, 2.5, 0.5))
    marker = bpy.context.object
    bpy.ops.object.select_all(action='DESELECT')
    yield from frames(4)
    before = set(bpy.data.objects)
    yield from drag(window, tile(studio_area, ids["poster"]), point_in_view(view, (-1.0, 2.5, 0.5)))
    yield from wait(lambda: set(bpy.data.objects) - before, 10)
    added = [o for o in bpy.data.objects if o not in before]
    check("image_on_an_empty_is_a_reference", len(added) == 1 and added[0].empty_display_type == 'IMAGE'
          and not marker.material_slots, [o.name for o in added])

    state["phase"] = "image_editor"
    image_region = region_of(image_area)
    centre = (image_region.x + image_region.width / 2, image_region.y + image_region.height / 2)
    yield from drag(window, tile(studio_area, ids["poster"]), centre)
    shown = image_area.spaces.active.image
    check("image_opens_in_the_image_editor", shown is not None and shown.packed_file is not None,
          shown.name if shown else None)
    show_kind(studio_area, "3D")
    yield from wait(lambda: tile(studio_area, ids["column"]), 10)
    before = (set(bpy.data.objects), set(bpy.data.images))
    yield from drag(window, tile(studio_area, ids["column"]), centre,
                    during=lambda: capture("model-refused-on-image-editor.png"))
    check("model_refused_on_the_image_editor", set(bpy.data.objects) == before[0]
          and image_area.spaces.active.image == shown)

    state["phase"] = "outliner"
    rows = region_of(outliner)
    unit = 20 * bpy.context.preferences.system.ui_scale
    row = lambda index: (rows.x + rows.width / 3, rows.y + rows.height - (index + 0.5) * unit)  # noqa: E731
    before = set(bpy.data.objects)
    yield from drag(window, tile(studio_area, ids["cone"]), row(1))   # Row 1: "Collection".
    yield from wait(lambda: set(bpy.data.objects) - before, 10)
    added = [o for o in bpy.data.objects if o not in before]
    check("model_into_the_collection_under_the_pointer", len(added) == 1
          and bpy.data.collections.get("Collection") in added[0].users_collection, [o.name for o in added])
    capture("model-in-outliner.png")
    # An image onto an object's row is its material. Rows: Scene Collection, Collection, then its objects by name.
    show_kind(studio_area, "IMAGE")
    yield from wait(lambda: tile(studio_area, ids["banner"]), 10)
    outliner.spaces.active.filter_text = "Cube"   # Rows: Scene Collection, Collection, Cube.
    yield from frames(6)
    cube = bpy.data.objects["Cube"]
    before_material = cube.active_material
    # Over the object's name, as Blender's own Outliner drops need (outliner_dropzone_element): the row's third
    # level starts three units in, after the expand arrow and the icon.
    on_name = (rows.x + 4.2 * unit, row(2)[1])
    yield from drag(window, tile(studio_area, ids["banner"]), on_name, during=lambda: capture("outliner-image-drag.png"))
    yield from wait(lambda: cube.active_material is not before_material, 10)
    check("image_onto_an_outliner_object_is_its_material", cube.active_material is not None
          and cube.active_material.name.startswith("A cyan banner"),
          cube.active_material.name if cube.active_material else None)
    outliner.spaces.active.filter_text = ""
    show_kind(studio_area, "3D")
    yield from wait(lambda: tile(studio_area, ids["column"]), 10)

    state["phase"] = "multi_select"
    # The last drag selected the banner (as the Asset Browser selects what it drags), in the Image tab: hidden here,
    # so never dragged with these. Cmd/Ctrl+click selects the cone and the column.
    for key in ("cone", "column"):
        yield from click(window, tile(studio_area, ids[key]), oskey=sys.platform == "darwin",
                         ctrl=sys.platform != "darwin")
    yield from frames(3)
    selected = list(studio.view(studio_area).get("selected_ids") or [])
    before = set(bpy.data.objects)
    target = point_in_view(view, (0.0, -3.0, 0.0))
    yield from drag(window, tile(studio_area, ids["column"]), target)
    yield from wait(lambda: len(set(bpy.data.objects) - before) >= 2, 10)
    added = [o for o in bpy.data.objects if o not in before]
    check("two_selected_items_land_together", len(added) == 2 and set(selected) >= {ids["cone"], ids["column"]},
          [selected, [o.name for o in added]])
    capture("two-dropped.png")
    names = [o.name for o in added]
    yield from undo(window, target)
    check("one_undo_for_the_whole_drop", names and not any(n in bpy.data.objects for n in names))

    state["phase"] = "chat_and_studio"
    from angelo_agent import ui
    controller = ui.get_controller()
    show_kind(studio_area, "IMAGE")
    yield from wait(lambda: tile(studio_area, ids["banner"]), 10)
    attached = len(controller.attachments)
    chat_region = region_of(chat)
    yield from drag(window, tile(studio_area, ids["banner"]), (chat_region.x + chat_region.width / 2, chat_region.y + 40))
    check("image_attached_to_the_chat", len(controller.attachments) == attached + 1, len(controller.attachments))
    references = len(studio.view(studio_area)["references"])
    studio.view(studio_area)["collapsed"] = False
    yield from frames(4)
    studio_region = region_of(studio_area)
    yield from drag(window, tile(studio_area, ids["poster"]),
                    (studio_region.x + 120, studio_region.y + studio_region.height - 200))
    check("image_on_media_studio_is_a_reference", len(studio.view(studio_area)["references"]) == references + 1)

    # A drag released back on the grid it came from (a click that moved a little) adds nothing.
    references = len(studio.view(studio_area)["references"])
    grid = studio.view(studio_area)["geometry"]["assets"]
    yield from drag(window, tile(studio_area, ids["poster"]),
                    (studio_region.x + grid[0] + grid[2] - 40, studio_region.y + grid[1] + 60))
    check("a_drop_back_on_the_grid_adds_nothing", len(studio.view(studio_area)["references"]) == references)

    state["phase"] = "click_still_opens"
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=10, y=10)
    yield from frames(4)
    opened_before = sum(1 for t in state["trace"] if t[0] == "popup")
    yield from click(window, tile(studio_area, ids["poster"]))
    yield from frames(8)
    popups = [t for t in state["trace"] if t[0] == "popup"][opened_before:]
    capture("click-opens.png")
    check("a_click_opens_the_item", len(popups) == 1 and popups[0][1] == ids["poster"][:8]
          and popups[0][2] == ["RUNNING_MODAL"], popups)
    window.event_simulate(type='ESC', value='PRESS')
    window.event_simulate(type='ESC', value='RELEASE')
    yield from frames(4)

    state["phase"] = "from_its_own_window"
    from angelo_agent import studio_toggle
    own = studio_toggle.open_studio(window)
    own_area = own.screen.areas[0]
    show_kind(own_area, "3D")
    yield from frames(20)
    studio.library().reload()
    yield from wait(lambda: tile(own_area, ids["column"]), 15)
    start = tile(own_area, ids["column"])
    goal = point_in_view(view, (-1.0, -2.0, 0.0))
    before = set(bpy.data.objects)
    yield from drag_across(own, start, window, goal)
    yield from wait(lambda: set(bpy.data.objects) - before, 10)
    added = [o for o in bpy.data.objects if o not in before]
    check("drop_from_media_studios_own_window", len(added) == 1, [o.name for o in added])
    capture("from-own-window.png")
    if added:
        name = added[0].name
        yield from undo(window, goal)
        check("undone_in_the_main_window", name not in bpy.data.objects)
    state["phase"] = "from_the_asset_browser"
    # The same drop box takes Blender's own Asset Browser drag: the poster's preview is its magenta picture.
    studio_area.ui_type = 'ASSETS'
    yield from frames(10)
    entry = studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries, directory)
    params = studio_area.spaces.active.params
    params.asset_library_reference = entry.name
    yield from wait(lambda: studio_area.spaces.active.params.asset_library_reference == entry.name, 5)
    with bpy.context.temp_override(window=window, area=studio_area, region=region_of(studio_area)):
        bpy.ops.file.select_all(action='DESELECT')
    yield from frames(6)
    image_area.spaces.active.image = None
    browser = region_of(studio_area)
    found = {}

    def preview():
        found["xy"] = find_colour(capture("asset-browser.png"), (browser.x, browser.y, browser.width, browser.height),
                                  (1.0, 0.0, 1.0))
        return found["xy"]
    shown = yield from wait(preview, 30)
    if shown:
        yield from drag(window, found["xy"], centre, during=lambda: capture("asset-browser-drag.png"))
        opened = image_area.spaces.active.image
        check("asset_browser_image_opens_in_the_image_editor", opened is not None and opened.packed_file is not None,
              opened.name if opened else None)
    else:
        check("asset_browser_image_opens_in_the_image_editor", False, "no magenta preview in the Asset Browser")

    state["phase"] = "complete"


def tick(steps=[]):   # noqa: B006 - the generator lives across timer calls.
    try:
        if not steps:
            steps.append(run())
        next(steps[0])
        return .1
    except StopIteration:
        pass
    except Exception:
        state["error"] = traceback.format_exc()
    state["success"] = state["phase"] == "complete" and all(checks.values())
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_MEDIA_DROP_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=2)
