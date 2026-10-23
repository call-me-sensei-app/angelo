# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assets dragged into a node editor land where they are dropped, by real drags (1.0.5 drop targets).

The window's 3D view becomes a Node Editor and its Properties editor an Asset Browser (Current File, thumbnails) with
three images and one 3D model marked as assets (image assets need the experimental Extended Asset Browser,
turned on here; the Media Studio cases below need nothing non-default). Each case is a real drag
(``Window.event_simulate``: press on the asset's tile, move into the node editor, release) of the asset under the
pointer, or of a selection made with Ctrl+click first:

* ``flow_one``: one image into a Generation Flow becomes one Image step at the drop point; one Undo removes it;
* ``flow_many``: two selected images become two Image steps;
* ``flow_model`` (English and German): over the flow, a 3D model's drag shows why it can't be dropped, in the alert
  colour near the pointer (pixels); dropping it adds nothing;
* ``shader_one``: one image into a material's node tree is still Blender's own drop (one Image Texture node);
* ``shader_many``: two selected images become two Image Texture nodes, and one Undo removes both;
* Media Studio's own library (its background writer lists two images and a model): two Studio images into the flow
  become Image steps of their library ids (never imported again); the Studio model shows why it can't be dropped; a
  Studio image into the material becomes an Image Texture node of its picture.

Each drop is captured while the pointer is over the node editor (``node-drop-<case>.png``) and after. No account,
provider or network. Run through ``run_native_suites.py --gui --include test_blender_node_drop_targets``; check
``node-drop-targets.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup GUI instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-node-drop-targets-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_LIBRARY_DIR", str(OUT / "library"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_input, inside the app.
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
prefs = bpy.context.preferences
prefs.view.show_splash = False
prefs.use_preferences_save = False
# Blender 5.2's Asset Browser lists Image data-blocks only with the experimental Extended Asset Browser, so the
# Current File image cases need it; Media Studio's images are material assets and need nothing non-default.
prefs.experimental.use_extended_asset_browser = True
from angelo_agent import flow_nodes, studio_assets, studio_assets_host  # noqa: E402
from angelo_agent.studio_state import StudioLibrary  # noqa: E402
import qa_input  # noqa: E402

REPORT = OUT / "node-drop-targets.json"
IMAGES = {"Drop blue": (0, 0, 1, 1), "Drop green": (0, 1, 0, 1), "Drop red": (1, 0, 0, 1)}
MODEL = "Chair"
FLOW = "Drop targets"
STUDIO_IMAGES = {"A cyan poster": (0, 1, 1, 1), "A magenta poster": (1, 0, 1, 1)}
STUDIO_MODEL = "A stone column"
RED_LEAST = 100        # The drop's reason in the alert colour is hundreds of pixels; none without it.
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": []}
checks, ctx = state["checks"], {}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def area(name):
    return next(a for a in window().screen.areas if a.as_pointer() == ctx[name])


def region(name, kind='WINDOW'):
    return next(r for r in area(name).regions if r.type == kind)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def red_near(path, x, y):
    """Alert-red pixels in a band beside the pointer, where the drop's reason is drawn."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    px = (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)
    pixel = width / window().width   # Screenshot pixels per window coordinate.
    x, y, scale = x * pixel, y * pixel, prefs.system.ui_scale * pixel
    block = px[max(0, int(y - 80 * scale)):int(y + 120 * scale), max(0, int(x - 60 * scale)):int(x + 700 * scale)]
    r, g, b = block[..., 0], block[..., 1], block[..., 2]
    # The reason's text (theme alert colour, measured (175, 87, 87) at its core): red clearly dominant, which the
    # neutral editor background and its grid never are.
    return int(((r > 120) & (r - g > 50) & (numpy.abs(g - b) < 20)).sum())


def move(x, y, **modifiers):
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y), **modifiers)


def click(x, y, **modifiers):
    move(x, y, **modifiers)
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type='LEFTMOUSE', value=value, x=int(x), y=int(y), **modifiers)


def browser_asset():
    with bpy.context.temp_override(window=window(), area=area("browser"), region=region("browser")):
        asset = bpy.context.asset
        return asset.name if asset else None


def drop_point():
    r = region("nodes")
    return r.x + r.width * 0.45, r.y + r.height * 0.55


def node_space(x, y):
    r = region("nodes")
    vx, vy = r.view2d.region_to_view(x - r.x, y - r.y)
    return vx / prefs.system.ui_scale, vy / prefs.system.ui_scale


def show_flow():
    space = area("nodes").spaces.active
    space.tree_type = flow_nodes.TREE
    space.node_tree = bpy.data.node_groups[FLOW]   # By name: an undo replaces every data-block.


def show_material():
    space = area("nodes").spaces.active
    space.tree_type = 'ShaderNodeTree'
    space.shader_type = 'OBJECT'
    bpy.context.view_layer.objects.active = bpy.data.objects["Cube"]


def tree():
    return area("nodes").spaces.active.edit_tree


def undo(placed_nodes):
    """Undo the drop, only when it added something: otherwise the step undone would be the fixtures' own (a build
    without the drop, the "before" captures, keeps going through every case)."""
    if placed_nodes:
        with bpy.context.temp_override(window=window(), area=area("nodes"), region=region("nodes")):
            bpy.ops.ed.undo()


def undo_push(message):
    """Operators and data changes from Python push no undo step; a person's own edits do. One step here, so an undo
    after the drop lands on it, as it would for a person."""
    with bpy.context.temp_override(window=window(), area=area("nodes"), region=region("nodes")):
        bpy.ops.ed.undo_push(message=message)


def deselect():
    r = region("browser")
    click(r.x + r.width - 10, r.y + 10)


def drag(name, rows, shot):
    """Generator: a selection of several is made with Ctrl+click on each; then the last one (selected, so the
    selection stays) is dragged into the node editor and dropped."""
    if len(rows) > 1:
        for row in rows:
            x, y = ctx["rows"][row]
            click(x, y, ctrl=True)
            yield .5
    x, y = ctx["rows"][rows[-1]]
    # As a hand does (qa_input.drag_path): the drag starts once the pointer passes Blender's drag threshold over the
    # pressed tile; a first jump out of it is a plain click (measured: no drag starts). It lingers 1 px off the drop
    # point and back, so the drop target draws its highlight for the capture before the release.
    end_x, end_y = drop_point()
    events = qa_input.drag_path((x, y), (end_x, end_y), bpy.context.preferences.system.ui_scale, release=False,
                                linger=True)
    for n, event in enumerate(events):
        qa_input.send(window(), [event])
        yield .4 if n >= len(events) - 2 else .1
    path = capture(shot)
    state["observed"].setdefault(name, {})["red_near_pointer"] = red_near(path, end_x, end_y)
    qa_input.send(window(), [qa_input.release_at((end_x, end_y))])
    yield .8
    # One more pointer move lets the window finish the drop before the "after" capture (measured: without it the
    # capture still shows the drag).
    move(end_x + 3, end_y)
    yield .4
    area("nodes").tag_redraw()
    yield .3
    capture(shot.replace(".png", "-after.png"))


def find_colour(path, colour):
    """Centre (window coordinates) of the browser's pixels close to ``colour`` in a screenshot, or None."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    scale = width / window().width
    r = region("browser")
    px = values.reshape(height, width, 4)[..., :3]
    x0, y0 = int(r.x * scale), int(r.y * scale)
    block = px[y0:y0 + int(r.height * scale), x0:x0 + int(r.width * scale)]
    near = (numpy.abs(block - numpy.array(colour[:3], dtype=numpy.float32)) < .25).all(axis=2)
    ys, xs = numpy.nonzero(near)
    if len(xs) < 40:
        return None
    return r.x + xs.mean() / scale, r.y + ys.mean() / scale


def find_rows(key, colours):
    """Generator: each listed asset's tile. The pictures' tiles are found by their colour; the grid they sit on gives
    the other tiles' centres, each named by clicking it (a click makes that asset the active one)."""
    found = {}
    for _ in range(40):   # Previews are made in the background.
        shot = capture("node-drop-%s-browser.png" % key)
        found = {name: find_colour(shot, colour) for name, colour in colours.items()}
        if all(found.values()):
            break
        yield .5
    found = {name: centre for name, centre in found.items() if centre}
    r = region("browser")
    xs = sorted({round(c[0]) for c in found.values()})
    ys = sorted({round(c[1]) for c in found.values()}, reverse=True)
    steps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 20] + [a - b for a, b in zip(ys, ys[1:]) if a - b > 20]
    pitch = min(steps) if steps else 120 * prefs.system.ui_scale
    origin = next(iter(found.values()), (r.x + pitch / 2, r.y + r.height - pitch / 2))
    tiles = dict(found)
    known = list(found.values())
    for row in range(-3, 4):
        for column in range(-3, 4):
            x, y = origin[0] + column * pitch, origin[1] - row * pitch
            if not (r.x + 8 < x < r.x + r.width - 8 and r.y + 8 < y < r.y + r.height - 8):
                continue
            if any(abs(x - kx) < pitch / 3 and abs(y - ky) < pitch / 3 for kx, ky in known):
                continue
            click(x, y)
            yield .45   # Longer than a double click, so every probe is a single click.
            name = browser_asset()
            if name and name not in tiles:
                tiles[name] = (x, y)
            known.append((x, y))
    ctx["rows"] = tiles
    state["observed"][key] = {name: (round(x), round(y)) for name, (x, y) in tiles.items()}
    click(r.x + r.width - 10, r.y + 10)   # Empty space: nothing selected.
    yield .5


def studio_fixtures():
    """Media Studio items as the generators leave them: two images and one model (a GLB of the default cube)."""
    studio = StudioLibrary(studio_assets_host.directories()[0])
    made = {}
    for name, colour in STUDIO_IMAGES.items():
        path = OUT / (name.replace(" ", "-") + ".png")
        picture = bpy.data.images.new(path.stem, 48, 48)
        picture.pixels.foreach_set(list(colour) * 48 * 48)
        picture.filepath_raw, picture.file_format = str(path), 'PNG'
        picture.save()
        bpy.data.images.remove(picture)
        record = studio.add("IMAGE", name, "Fixture image", status="ready")
        (studio.result_directory(record["id"]) / "image-1.png").write_bytes(path.read_bytes())
        record.update(files=["image-1.png"], thumbnail="image-1.png", progress=100)
        studio.update(record)
        made[name] = record["id"]
    glb = OUT / "model.glb"
    cube = bpy.data.objects["Cube"]
    with bpy.context.temp_override(window=window(), selected_objects=[cube], active_object=cube):
        bpy.ops.export_scene.gltf(filepath=str(glb), export_format='GLB', use_selection=True)
    record = studio.add("3D", STUDIO_MODEL, "Fixture 3D", status="ready")
    (studio.result_directory(record["id"]) / "model.glb").write_bytes(glb.read_bytes())
    record.update(files=["model.glb"], progress=100)
    studio.update(record)
    made[STUDIO_MODEL] = record["id"]
    return made


def placed(name, idname, before):
    nodes = [n for n in tree().nodes if n.bl_idname == idname and n.name not in before]
    x, y = node_space(*drop_point())
    state["observed"].setdefault(name, {}).update(
        nodes=[(n.name, tuple(round(v) for v in n.location)) for n in nodes], drop_point=(round(x), round(y)))
    return nodes, (x, y)


def near(node, point, reach=320):
    return abs(node.location.x - point[0]) <= reach and abs(node.location.y - point[1]) <= reach


def script():
    from angelo_agent.editor_types import set_editor
    # The 3D view becomes the node editor, the Properties editor an Asset Browser listing the Current File's assets.
    screen = window().screen
    nodes = max((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    browser = next(a for a in screen.areas if a.type == 'PROPERTIES')
    ctx["nodes"], ctx["browser"] = nodes.as_pointer(), browser.as_pointer()
    nodes.type = 'NODE_EDITOR'
    set_editor(window(), browser, ui_type='ASSETS')
    yield .6
    for name, colour in IMAGES.items():
        path = OUT / (name.replace(" ", "-") + ".png")
        made = bpy.data.images.new(name, 32, 32)
        made.pixels.foreach_set(list(colour) * 32 * 32)
        made.filepath_raw, made.file_format = str(path), 'PNG'
        made.save()
        made.asset_mark()
    chair = bpy.data.objects.new(MODEL, bpy.data.meshes["Cube"])
    chair.asset_mark()
    bpy.data.node_groups.new(FLOW, flow_nodes.TREE)
    space = area("browser").spaces.active
    space.show_region_toolbar = False
    space.params.asset_library_reference = 'LOCAL'
    space.params.display_type = 'THUMBNAIL'
    with bpy.context.temp_override(window=window(), area=area("browser"), region=region("browser")):
        bpy.ops.asset.library_refresh()
    show_flow()
    yield 1.0
    yield from find_rows("rows", IMAGES)
    checks["every_asset_row_was_found"] = set(ctx["rows"]) >= set(IMAGES) | {MODEL}
    r = region("browser")

    state["phase"] = "flow_one"
    undo_push("Drop fixtures")
    yield .3
    before = {n.name for n in tree().nodes}
    yield from drag("flow_one", ["Drop red"], "node-drop-flow-one.png")
    steps, point = placed("flow_one", flow_nodes.ANGELO_FlowImageNode.bl_idname, before)
    checks["flow_one_image_is_one_image_step"] = len(steps) == 1 and bool(steps[0].asset_id)
    checks["flow_one_lands_at_the_drop_point"] = len(steps) == 1 and near(steps[0], point)
    undo(steps)
    yield .6
    checks["flow_one_one_undo_removes_it"] = {n.name for n in tree().nodes} == before

    state["phase"] = "flow_many"
    show_flow()
    deselect()
    yield .5
    before = {n.name for n in tree().nodes}
    yield from drag("flow_many", ["Drop green", "Drop blue"], "node-drop-flow-many.png")
    steps, point = placed("flow_many", flow_nodes.ANGELO_FlowImageNode.bl_idname, before)
    checks["flow_many_two_images_are_two_image_steps"] = len(steps) == 2 and all(s.asset_id for s in steps)
    checks["flow_many_land_at_the_drop_point"] = len(steps) == 2 and near(steps[0], point)
    undo(steps)
    yield .6
    checks["flow_many_one_undo_removes_both"] = {n.name for n in tree().nodes} == before

    prefs.view.use_translate_interface = True
    for language in ("en_US", "de_DE"):
        state["phase"] = "flow_model_" + language
        prefs.view.language = language
        deselect()
        yield .6
        before = {n.name for n in tree().nodes}
        yield from drag("flow_model_" + language, [MODEL], "node-drop-flow-model-%s.png" % language)
        red = state["observed"]["flow_model_" + language]["red_near_pointer"]
        checks["flow_model_says_why_in_the_alert_colour_" + language] = red >= RED_LEAST
        checks["flow_model_adds_nothing_" + language] = {n.name for n in tree().nodes} == before
    prefs.view.language = "en_US"
    yield .5

    state["phase"] = "shader_one"
    show_material()
    yield .6
    deselect()
    yield .5
    before = {n.name for n in tree().nodes}
    yield from drag("shader_one", ["Drop red"], "node-drop-shader-one.png")
    textures, _ = placed("shader_one", "ShaderNodeTexImage", before)
    checks["shader_one_image_is_one_image_texture"] = (
        len(textures) == 1 and textures[0].image is not None and textures[0].image.name == "Drop red")

    state["phase"] = "shader_many"
    undo_push("Before shader_many")
    deselect()
    yield .5
    before = {n.name for n in tree().nodes}
    yield from drag("shader_many", ["Drop green", "Drop blue"], "node-drop-shader-many.png")
    yield .6   # The stack animation ends, and with it the drop's undo step.
    textures, point = placed("shader_many", "ShaderNodeTexImage", before)
    checks["shader_many_two_images_are_two_image_textures"] = (
        sorted(t.image.name for t in textures if t.image) == ["Drop blue", "Drop green"])
    checks["shader_many_land_at_the_drop_point"] = bool(textures) and near(textures[0], point)
    undo(textures)
    yield .6
    checks["shader_many_one_undo_removes_both"] = {n.name for n in tree().nodes} == before

    # Media Studio's own library, written by its background writer as in the app.
    state["phase"] = "studio_library"
    made = studio_fixtures()
    directory = studio_assets_host.directories()[1]
    studio_assets_host._state["since"] = float("-inf")   # The writer's first scan at once (the app waits a little).
    written = False
    for _ in range(240):
        manifest = studio_assets.read_manifest(directory)
        written = all(manifest.get(i, {}).get("fingerprint") and not manifest[i].get("failed") for i in made.values())
        if written:
            break
        yield .5
    checks["studio_items_written"] = written
    entry = studio_assets.find_library(prefs.filepaths.asset_libraries, directory)
    area("browser").spaces.active.params.asset_library_reference = entry.name
    for _ in range(30):   # The writer's own refresh after its files is never left to race the drags.
        yield .5
        if not studio_assets_host._state["changed"]:
            break
    with bpy.context.temp_override(window=window(), area=area("browser"), region=region("browser")):
        bpy.ops.asset.library_refresh()
    yield 1.5
    yield from find_rows("studio_rows", STUDIO_IMAGES)
    checks["every_studio_row_was_found"] = set(ctx["rows"]) >= set(STUDIO_IMAGES) | {STUDIO_MODEL}

    state["phase"] = "studio_flow"
    show_flow()
    yield .6
    before = {n.name for n in tree().nodes}
    yield from drag("studio_flow", list(STUDIO_IMAGES), "node-drop-studio-flow.png")
    steps, point = placed("studio_flow", flow_nodes.ANGELO_FlowImageNode.bl_idname, before)
    checks["studio_images_are_image_steps_of_their_library_ids"] = (
        sorted(s.asset_id for s in steps) == sorted(made[n] for n in STUDIO_IMAGES))
    checks["studio_images_land_at_the_drop_point"] = bool(steps) and near(steps[0], point)
    deselect()
    yield .5
    before = {n.name for n in tree().nodes}
    yield from drag("studio_model", [STUDIO_MODEL], "node-drop-studio-model.png")
    red = state["observed"]["studio_model"]["red_near_pointer"]
    checks["studio_model_says_why_in_the_alert_colour"] = red >= RED_LEAST
    checks["studio_model_adds_nothing"] = {n.name for n in tree().nodes} == before

    state["phase"] = "studio_shader"
    show_material()
    yield .6
    deselect()
    yield .5
    before = {n.name for n in tree().nodes}
    yield from drag("studio_shader", ["A cyan poster"], "node-drop-studio-shader.png")
    textures, point = placed("studio_shader", "ShaderNodeTexImage", before)
    checks["a_studio_image_is_an_image_texture_of_its_picture"] = (
        len(textures) == 1 and textures[0].image is not None and textures[0].image.size[0] == 48)
    checks["a_studio_image_lands_at_the_drop_point"] = len(textures) == 1 and near(textures[0], point)


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    prefs.view.language = "en_US"
    REPORT.write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False))
    print("ANGELO_NODE_DROP_TARGETS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


steps = script()


def tick():
    try:
        assert time.monotonic() - started < 420, "node drop targets fixture timed out in " + state["phase"]
        return next(steps)
    except StopIteration:
        finish()
    except Exception as error:
        finish(error)
    return None


bpy.app.timers.register(tick, first_interval=1.0)
