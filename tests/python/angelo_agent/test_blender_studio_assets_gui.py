# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio items in the running app's Asset Browser, dragged into the scene (``studio_assets_host``).

A real window and simulated input (``Window.event_simulate``); fixture items, no provider, account or network. An
isolated Studio library gets a model (a GLB this test writes, 1 x 1 x 2 m, centred off its origin) whose provider
picture is magenta, and an image that is cyan. Checked, with screenshots: the add-on adds *Angelo Media Studio* to
Preferences and its background writer lists both; an Asset Browser on that library draws the magenta and cyan
previews (found by their pixels, nothing else in the window has those colours); a real drag of the magenta tile into
the 3D view adds the model itself (the asset holds it) under the pointer, standing on the floor plane, at the GLB's
size, and one Ctrl+Z (Cmd+Z) takes the drop back; a drag of the cyan tile
onto the floor gives it the image's material, its picture packed into the file, and a library refresh asked for
mid-drag waits (the dragged asset points into the lists a refresh frees); with the browser set to Link the model
lands linked, as any linked asset, and is the whole model; in English and German, screenshots of Preferences' asset libraries,
the browser's library menu and the browser with its catalogues in that language (Models / Modelle). The writer's first scan starts at once here (``_state["since"]``);
the app waits ``SYNC_DELAY`` after startup.

Run through ``run_native_suites.py --gui --include test_blender_studio_assets_gui``; check
``studio-assets-gui.json`` for ``success`` and ``phase == "complete"``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-assets-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_LIBRARY_DIR", str(OUT / "library"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_input, inside the app.
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False   # Its first click would only close the splash, not start a drag.
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import studio_assets, studio_assets_host  # noqa: E402
from angelo_agent.studio_state import StudioLibrary  # noqa: E402
import qa_input  # noqa: E402

REPORT = OUT / "studio-assets-gui.json"
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "screenshots": []}
checks, details = state["checks"], state["details"]
MAGENTA, CYAN = (1.0, 0.0, 1.0, 1.0), (0.0, 1.0, 1.0, 1.0)
TARGET = Vector((0.6, -0.8, 0.0))


def check(name, value, detail=None):
    """Receipt contract (native_suite_checks): each check is exactly True or False; details are kept apart."""
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail
    return bool(value)


def window():
    return bpy.context.window_manager.windows[0]


def area_of(kind):
    return next((a for a in window().screen.areas if a.type == kind), None)


def region_of(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def solid_png(path, colour, size=(64, 64)):
    image = bpy.data.images.new(path.stem, *size)
    image.pixels.foreach_set(list(colour) * size[0] * size[1])
    image.filepath_raw, image.file_format = str(path), 'PNG'
    image.save()
    bpy.data.images.remove(image)


def fixtures():
    """A Studio library with one model and one image, written as the generators would leave them."""
    scratch = OUT / "fixtures"
    scratch.mkdir(exist_ok=True)
    with bpy.context.temp_override(window=window()):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0.3, 0.2, 1.0))
        sphere = bpy.context.object
        sphere.scale = (1, 1, 2)
        bpy.ops.object.select_all(action='DESELECT')
        sphere.select_set(True)
        bpy.ops.export_scene.gltf(filepath=str(scratch / "model.glb"), export_format='GLB', use_selection=True)
        bpy.data.objects.remove(sphere)
    solid_png(scratch / "preview.png", MAGENTA)
    solid_png(scratch / "image-1.png", CYAN, (96, 64))
    studio = StudioLibrary(studio_assets_host.directories()[0])
    model = studio.add("3D", "A weathered stone column", "Fixture 3D", status="ready")
    (studio.result_directory(model["id"]) / "model.glb").write_bytes((scratch / "model.glb").read_bytes())
    (studio.result_directory(model["id"]) / "preview.png").write_bytes((scratch / "preview.png").read_bytes())
    model.update(files=["model.glb"], thumbnail="preview.png", progress=100)
    studio.update(model)
    image = studio.add("IMAGE", "A cyan poster", "Fixture image", status="ready")
    (studio.result_directory(image["id"]) / "image-1.png").write_bytes((scratch / "image-1.png").read_bytes())
    image.update(files=["image-1.png"], thumbnail="image-1.png", progress=100)
    studio.update(image)
    return model, image


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def drawn(path):
    """Whether a screenshot holds more than one flat colour (a window that has drawn its content)."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = [0.0] * (image.size[0] * image.size[1] * 4)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    return len({round(v, 2) for v in pixels[::97]}) > 4


def find_colour(path, rect, colour):
    """Centre (window pixels) and count of the pixels close to ``colour`` inside ``rect`` of a screenshot."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        scale = width / window().width
        pixels = [0.0] * (width * height * 4)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    x0, y0, w, h = (round(v * scale) for v in rect)
    xs, ys = [], []
    for y in range(max(0, y0), min(height, y0 + h)):
        row = y * width
        for x in range(max(0, x0), min(width, x0 + w)):
            r, g, b = pixels[(row + x) * 4:(row + x) * 4 + 3]
            if all(abs(c - t) < .25 for c, t in zip((r, g, b), colour)):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None, 0
    return (sum(xs) / len(xs) / scale, sum(ys) / len(ys) / scale), len(xs)


def drag(start, end, during=None):
    """A real drag, one event per pass, as a person makes it (``qa_input.drag_path``); ``during`` runs once mid-way,
    while Blender holds the dragged asset."""
    events = qa_input.drag_path(start, end, bpy.context.preferences.system.ui_scale)
    for n, event in enumerate(events):
        qa_input.send(window(), [event])
        yield
        if during is not None and n == len(events) // 2:
            during()


def bounds(objects):
    points = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]
    return (Vector(map(min, *(p.to_tuple() for p in points))), Vector(map(max, *(p.to_tuple() for p in points))))


def wait(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        yield
    return True


def run():
    state["phase"] = "fixtures"
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    model, image = fixtures()
    library, directory = studio_assets_host.directories()
    studio_assets_host._state["since"] = float("-inf")   # The first scan at once (the app waits SYNC_DELAY).
    state["phase"] = "registered"
    ok = yield from wait(lambda: studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries,
                                                            directory) is not None, 20)
    check("library_registered", ok)
    entry = studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries, directory)

    def written():
        manifest = studio_assets.read_manifest(directory)
        return all(manifest.get(r["id"], {}).get("fingerprint") and not manifest[r["id"]].get("failed")
                   for r in (model, image))
    state["phase"] = "writing"
    check("items_written", (yield from wait(written, 120)), studio_assets.read_manifest(directory))

    state["phase"] = "asset_browser"
    view = area_of('VIEW_3D')
    with bpy.context.temp_override(window=window(), area=view, region=region_of(view)):
        bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
        floor = bpy.context.object
        floor.name = "Floor"
        # Operators called from Python push no undo step; a person's own edits do. One step for the fixture scene, so
        # an undo after the drop lands here, as it would for a person.
        bpy.ops.ed.undo_push(message="Fixture scene")
        bpy.ops.screen.area_split(direction='HORIZONTAL', factor=0.42)
    browser = min((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.y)
    browser.ui_type = 'ASSETS'
    yield
    space = browser.spaces.active
    space.params.asset_library_reference = entry.name
    try:
        space.params.display_size = 'LARGE'
    except (AttributeError, TypeError):
        pass
    view = area_of('VIEW_3D')
    yield
    with bpy.context.temp_override(window=window(), area=browser, region=region_of(browser)):
        bpy.ops.asset.library_refresh()
    region = region_of(browser)
    rect = (region.x, region.y, region.width, region.height)
    found = {}

    def tiles():
        shot = capture("asset-browser.png")
        found["model"], found["model_pixels"] = find_colour(shot, rect, MAGENTA)
        found["image"], found["image_pixels"] = find_colour(shot, rect, CYAN)
        return found["model"] and found["image"]
    shown = yield from wait(tiles, 30)
    check("library_selected", space.params.asset_library_reference == entry.name, entry.name)
    check("previews_drawn", shown, {k: v for k, v in found.items() if "pixels" in k})
    if not shown:
        return

    # The writer's own refresh after its first files is never left to race this suite's drags (on a binary without
    # the drag guard that race is the crash refresh_refused_during_drag checks).
    yield from wait(lambda: not studio_assets_host._state["changed"], 15)
    state["phase"] = "drag_model"
    view_region = region_of(view)
    rv3d = view.spaces.active.region_3d
    target = view3d_utils.location_3d_to_region_2d(view_region, rv3d, TARGET)
    point = (view_region.x + target.x, view_region.y + target.y)
    objects = set(bpy.data.objects)
    yield from drag(found["model"], point)
    yield from wait(lambda: set(bpy.data.objects) - objects, 10)
    added = [o for o in bpy.data.objects if o not in objects]
    capture("model-dropped.png")
    check("model_added", len(added) == 1 and added[0].name.startswith("A weathered stone column"),
          [o.name for o in added])
    if added:
        low, high = bounds(added)
        size = tuple(round(v, 3) for v in high - low)
        centre = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, 0))
        check("model_size", size == (1.0, 1.0, 2.0), size)
        check("model_on_floor", abs(low.z) < 1e-3, round(low.z, 4))
        check("model_under_pointer", (centre - TARGET).length < 0.15, [round(v, 3) for v in centre])
        column = added[0]
        check("model_is_the_model", len(column.data.vertices) > 8, len(column.data.vertices))
        # One undo step takes the whole drop back.
        name = column.name
        window().event_simulate(type='Z', value='PRESS', ctrl=True,
                                x=int(point[0]), y=int(point[1]))
        window().event_simulate(type='Z', value='RELEASE', ctrl=True,
                                x=int(point[0]), y=int(point[1]))
        gone = yield from wait(lambda: name not in bpy.data.objects, 10)
        for _ in range(10):
            yield   # Anything the undo left behind would show up here.
        check("one_undo_step_removes_the_drop", gone and name not in bpy.data.objects and "Floor" in bpy.data.objects)
        floor = bpy.data.objects["Floor"]   # An undo reloads the file's data: earlier references are gone.

    state["phase"] = "drag_image"
    spot = view3d_utils.location_3d_to_region_2d(view_region, rv3d, Vector((-2.5, 1.5, 0)))

    def refresh_refused():
        # A refresh mid-drag frees the asset the drag points to (it crashed this suite once): the operator refuses
        # while a drag is in progress (asset_ops.cc), and Angelo's refresh waits for the next tick.
        with bpy.context.temp_override(window=window(), area=browser, region=region_of(browser)):
            refused = not bpy.ops.asset.library_refresh.poll()
        check("refresh_refused_during_drag", refused)   # False on a binary without the guard: never refresh then.
        if refused:
            check("angelo_refresh_waits_during_drag", studio_assets_host.refresh_asset_browsers() is False)
    yield from drag(found["image"], (view_region.x + spot.x, view_region.y + spot.y), during=refresh_refused)
    yield from wait(lambda: floor.active_material is not None, 10)
    capture("image-dropped.png")
    material = floor.active_material
    textures = [n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE'] if material else []
    packed = yield from wait(lambda: bool(textures) and textures[0].packed_file is not None, 10)
    check("dropped_picture_packed", packed, {"material_linked": bool(material and material.library),
          "picture_linked": bool(textures and textures[0].library), "import_method": entry.import_method})
    check("image_material_on_floor", material is not None and material.name.startswith("A cyan poster")
          and textures and Path(bpy.path.abspath(textures[0].filepath)).name == "image-1.png",
          material.name if material else None)
    state["phase"] = "drag_linked"
    # The browser set to Link: the model lands linked, as any linked asset, and is the whole model.
    browser.spaces.active.params.import_method = 'LINK'
    yield
    spot = view3d_utils.location_3d_to_region_2d(view_region, rv3d, Vector((-1.5, -2.0, 0)))
    objects = set(bpy.data.objects)
    yield from drag(found["model"], (view_region.x + spot.x, view_region.y + spot.y))
    yield from wait(lambda: set(bpy.data.objects) - objects, 10)
    added = [o for o in bpy.data.objects if o not in objects and o.type == 'MESH']
    capture("model-linked-dropped.png")
    size = None
    if added:
        bpy.context.view_layer.update()
        low, high = bounds(added)
        size = tuple(round(v, 3) for v in high - low)
    check("linked_drop_is_the_model", len(added) == 1 and len(added[0].data.vertices) > 8 and size == (1.0, 1.0, 2.0),
          [(o.name, bool(o.library), len(o.data.vertices), size) for o in added])
    browser.spaces.active.params.import_method = 'FOLLOW_PREFS'
    state["phase"] = "captures"
    yield from captures(browser, entry, directory)
    state["phase"] = "library_space"
    yield from library_space(directory)
    state["phase"] = "complete"


def library_space(directory):
    """Media Studio's Asset Browser row: it shows the library's size; Turn off asks first (nothing changes until the
    dialog is confirmed), and confirming with "Also delete its copies" (on by default) removes the entry and the
    copies, and nothing else in the folder."""
    from angelo_agent import studio, studio_assets_space as space, studio_library_ops as ops, studio_toggle
    created = studio_toggle.open_studio(window())
    area = created.screen.areas[0]
    yield from wait(lambda: studio.view(area).get("hits"), 10)
    for _ in range(20):   # The size is measured off the main thread; the row shows it on a later frame.
        yield
        if any(a == "library_off" for _r, a, _v in studio.view(area).get("hits", [])):
            break
    measured = space.measure(directory)
    status, actions = studio.library_rows()
    check("studio_shows_the_size", measured > 0 and ops.size_text(measured) in status and actions[0][1] == "library_off",
          [status, ops.size_text(measured)])
    yield from wait(lambda: drawn(capture("studio-library-row.png", created)), 10)
    target = next((r for r, a, _v in studio.view(area).get("hits", []) if a == "library_off"), None)
    check("turn_off_button_drawn", target is not None, studio.view(area).get("geometry"))
    if target is None:
        return
    region = region_of(area)
    x, y = int(region.x + target[0] + target[2] / 2), int(region.y + target[1] + target[3] / 2)
    created.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    created.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    created.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
    for _ in range(15):
        yield
    # Studio's window, behind other applications, can capture blank for a while: a drawn frame (qa_frames).
    yield from wait(lambda: drawn(capture("studio-library-turn-off-dialog.png", created)), 10)
    check("turn_off_waits_for_confirmation", ops.active() and space.measure(directory) == measured)
    # The confirmation is Angelo's card over Studio (studio_library_ops.LibraryCard, card_host): its destructive Turn
    # off has no default action, so Return does nothing; a person clicks its button, and so does this suite.
    from angelo_agent import card_host
    active = card_host._active
    confirm = next((rect for rect, action, _value in (active.plan.hits if active and active.plan else [])
                    if action == "confirm"), None)
    check("turn_off_card_offers_its_confirm", confirm is not None and active.area == area.as_pointer(),
          [active.area == area.as_pointer() if active else None, confirm])
    if confirm is not None:
        if not active.card.free:   # A scene links a library model (the linked drop above): the card leaves "Also
            # delete its copies" unticked; this suite frees them, so it ticks the box first, as a person would.
            box = next((rect for rect, action, _value in active.plan.hits if action == "free"), None)
            if box is not None:
                qa_input.press(created, region.x + box[0] + box[2] / 2, region.y + box[1] + box[3] / 2)
                yield from wait(lambda: active.card.free, 5)
        qa_input.press(created, region.x + confirm[0] + confirm[2] / 2, region.y + confirm[1] + confirm[3] / 2)
    gone = yield from wait(lambda: not ops.active(), 10)
    freed = yield from wait(lambda: space.measure(directory) == 0, 10)
    check("confirmed_turn_off_frees_only_the_copies", gone and freed and not list(directory.rglob("*.blend"))
          and (directory / studio_assets.REGISTRATION).is_file()
          and studio_assets.read_registration(directory)["removed"], [p.name for p in directory.rglob("*")])
    created.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x + 400, y=y + 300)
    for _ in range(20):
        yield
    yield from wait(lambda: drawn(capture("studio-library-off.png", created)), 10)
    after = OUT / "studio-library-off.png"
    changed = changed_fraction(OUT / "studio-library-turn-off-dialog.png", after)
    status, actions = studio.library_rows()
    check("dialog_closes_and_the_row_shows_off", changed > 0.01 and [a for _t, a in actions] == ["library_on"],
          [round(changed, 4), status, actions])


def changed_fraction(first, second):
    """The share of pixels that differ between two screenshots of the same window."""
    images = [bpy.data.images.load(str(path), check_existing=False) for path in (first, second)]
    try:
        if tuple(images[0].size) != tuple(images[1].size):
            return 1.0
        import numpy
        pixels = []
        for image in images:
            values = numpy.empty(image.size[0] * image.size[1] * 4, dtype=numpy.float32)
            image.pixels.foreach_get(values)
            pixels.append(values.reshape(-1, 4)[:, :3])
        return float((numpy.abs(pixels[0] - pixels[1]).max(axis=1) > 0.02).mean())
    finally:
        for image in images:
            bpy.data.images.remove(image)


def captures(browser, entry, directory):
    """Screenshots for review in English, German and Arabic: Preferences' asset libraries, the browser's library menu,
    the browser on the library with its catalogues in the interface language, and the browser on All Libraries
    (Blender's own text beside the library: All Libraries, the online-access card)."""
    from angelo_agent.ui_text import iface_
    prefs = bpy.context.preferences
    for language in ("en_US", "de_DE", "ar_EG"):
        prefs.view.language = language
        for _ in range(5):
            yield
        models = studio_assets._clean(iface_("Models"))
        cats = directory / studio_assets.CATS_FILE
        renamed = yield from wait(lambda: f"/{models}:" in cats.read_text(encoding="utf-8"), 30)
        check(f"catalogues_in_{language}", renamed, cats.read_text(encoding="utf-8").splitlines()[-2:])
        with bpy.context.temp_override(window=window(), area=browser, region=region_of(browser)):
            bpy.ops.asset.library_refresh()
        for _ in range(10):
            yield
        capture(f"asset-browser-{language}.png")
        params = browser.spaces.active.params
        params.asset_library_reference = 'ALL'
        for _ in range(10):
            yield
        capture(f"asset-browser-all-libraries-{language}.png")
        params.asset_library_reference = entry.name
        yield
        with bpy.context.temp_override(window=window(), area=browser, region=region_of(browser)):
            bpy.ops.wm.context_menu_enum(data_path="space_data.params.asset_library_reference")
        for _ in range(5):
            yield
        capture(f"library-menu-{language}.png")
        window().event_simulate(type='ESC', value='PRESS')
        window().event_simulate(type='ESC', value='RELEASE')
        yield
        before = set(bpy.context.window_manager.windows)
        with bpy.context.temp_override(window=window()):
            bpy.ops.screen.userpref_show(section='ASSETS')   # 5.2: Asset Libraries is its own section.
        for _ in range(30):   # A new window draws its first frame a few event loops after it opens.
            yield
        opened = [w for w in bpy.context.window_manager.windows if w not in before]
        if opened:
            name = f"preferences-asset-libraries-{language}.png"
            # A new window's first frames can be blank: capture until it has drawn more than its background.
            yield from wait(lambda: drawn(capture(name, opened[0])), 10)
            with bpy.context.temp_override(window=opened[0]):
                bpy.ops.wm.window_close()
        check(f"preferences_captured_{language}", bool(opened))
        yield
    prefs.view.language = "en_US"


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
    REPORT.write_text(json.dumps(state, indent=2))
    print("ANGELO_STUDIO_ASSETS_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=2)
