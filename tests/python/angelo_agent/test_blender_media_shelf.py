# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Object mode's Asset Shelf of models and materials in the running app (1.0.5, ``media_shelf``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference. A fixture
asset library (one model, one material, written here into a temporary folder and added in the Preferences, as an
Angelo Media Studio library is) stands in for Media Studio's output. In Manual mode's Object mode: stock Blender has
no shelf there (*before*, the shelf class unregistered) and Angelo's is shown by default (*after*), with its
Media Studio and Generation Flows pills; searching the shelf for the fixture model shows its tile (pixels in the
first tile against a search that matches nothing); a real drag of that tile into the 3D view adds the model; a real
drag of the fixture material onto the cube assigns it. With Part E's library and the 1.0.5 binary
(``AssetShelf.enable_catalog``): its tab (the Angelo Media Studio parent catalog, else its Models and Images catalogs,
``media_shelf.tab_catalogs``) is offered, an Images catalog asks for Studio's Image tab, and "Show my assets"
(``media_shelf.show_studio_tab``) opens the shelf on that library and its tab; on the 1.0.4 overlay that step is
recorded as skipped. On the 1.0.5 binary (``AssetShelf.angelo_set_collapsed``) the shelf starts collapsed to its
tab strip (the header's pixels drawn, its rows hidden, the collapse chevron first among the pills), a real click on
the chevron expands it (the rows region's pixels change), and at the end hidden, shown and collapsed again (the
chevron) are each remembered; on the 1.0.4 overlay it starts expanded and only hidden/shown are checked. The empty
state is ``test_blender_shelf_empty``'s (it shows on Angelo Media Studio's own view
only). Hiding the shelf is remembered and showing it again is too.

Run through ``run_native_suites.py --gui --include test_blender_media_shelf``; check ``media-shelf.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_input, inside the app.

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-media-shelf-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "media-shelf.json"
LIBRARY = OUT / "fixture-library"
MODEL, MATERIAL = "QA Pebble", "QA Moss"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": []}
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def region(kind, area=None):
    return next((r for r in (area or view3d()).regions if r.type == kind), None)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
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


def changed(before, after, rect):
    import numpy
    x, y, w, h = (int(v) for v in rect)
    a, b = pixels(before)[y:y + h, x:x + w], pixels(after)[y:y + h, x:x + w]
    return float((numpy.abs(a - b).sum(axis=2) > 24).mean()) if a.size else 0.0


def rect(r):
    return (r.x, r.y, r.width, r.height)


def settled():
    """Director's layout and Manual's Media Studio / Generation Flows tabs are in place (studio_workspaces builds
    those tabs by switching the window through them for a moment after startup)."""
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def write_library():
    """One model and one material marked as assets, written to their own .blend in a folder the Preferences list."""
    LIBRARY.mkdir(parents=True, exist_ok=True)
    mesh = bpy.data.meshes.new(MODEL)
    mesh.from_pydata([(-.3, -.3, 0), (.3, -.3, 0), (0, .3, 0), (0, 0, .5)], [], [(0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)])
    model = bpy.data.objects.new(MODEL, mesh)
    material = bpy.data.materials.new(MATERIAL)
    material.diffuse_color = (0.2, 0.5, 0.2, 1)
    model.asset_mark()
    material.asset_mark()
    bpy.data.libraries.write(str(LIBRARY / "fixture.blend"), {model, mesh, material}, fake_user=True)
    bpy.data.objects.remove(model)
    bpy.data.meshes.remove(mesh)
    bpy.data.materials.remove(material)
    bpy.ops.preferences.asset_library_add(directory=str(LIBRARY))


def press(x, y):
    w = window()
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        w.event_simulate(type=kind, value=value, x=int(x), y=int(y))


def press_fold():
    """A real click on the shelf's collapse chevron, where its pills recorded it."""
    from angelo_agent import asset_shelf_add
    x, y, w, h = dict(asset_shelf_add.places[view3d().as_pointer()][1])[asset_shelf_add.FOLD]
    press(x + w / 2, y + h / 2)


def collapses():
    return "angelo_set_collapsed" in bpy.types.AssetShelf.bl_rna.functions   # No class attribute: bl_rna.


def shelf_override():
    area = view3d()
    return bpy.context.temp_override(window=window(), area=area, region=region('ASSET_SHELF'))


def search(text):
    with shelf_override():
        shelf = bpy.context.asset_shelf
        shelf.search_filter = text
        shelf.show_names = True
    view3d().tag_redraw()


def first_tile():
    """Inside the first tile of the shelf (its top-left one): tiles start at the region's top-left corner."""
    shelf = region('ASSET_SHELF')
    scale = prefs.system.ui_scale
    with shelf_override():
        size = bpy.context.asset_shelf.preview_size
    return shelf.x + (size / 2 + 8) * scale, shelf.y + shelf.height - (size / 2 + 10) * scale


def tile_box():
    """The shelf's first tile, where a search's one match draws (its preview, or a blank card and its name)."""
    shelf = region('ASSET_SHELF')
    with shelf_override():
        size = bpy.context.asset_shelf.preview_size
    return (shelf.x, shelf.y, (size + 24) * prefs.system.ui_scale, shelf.height)


def drag(start, end):
    """A real drag, one event per event-loop pass (``tick`` sends them): Blender starts a drag only after it has
    seen the press, then moves past its threshold still on the tile, so the path is ``qa_input.drag_path``'s (a
    first move that left the tile in one event made the 3D view box-select instead: the a1c3a0dc6cf slot)."""
    import qa_input
    state["events"] = qa_input.drag_path(start, end, prefs.system.ui_scale)


def cube_point():
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    view = region('WINDOW')
    cube = next(o for o in bpy.context.scene.objects if o.type == 'MESH' and o.name != MODEL)
    point = location_3d_to_region_2d(view, view3d().spaces.active.region_3d, cube.matrix_world.translation)
    state["cube"] = cube.name
    return view.x + point.x, view.y + point.y


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_MEDIA_SHELF", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def settles(phase, condition, limit=20):
    """True once ``condition`` holds, False after ``limit`` seconds (recorded, the suite goes on), None meanwhile."""
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    return False if time.monotonic() - since > limit else None


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import asset_shelf_add, director, media_shelf
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 300, "Suite timed out"
        if state.get("events"):   # A drag in progress: one event per pass.
            kind, value, (x, y) = state["events"].pop(0)
            window().event_simulate(type=kind, value=value, x=int(x), y=int(y))
            return .05
        if phase == "boot":   # Director hides every shelf (director_layout.configure): Manual shows it.
            if not settled():
                return .3
            media_shelf._path = lambda: OUT / media_shelf.FILE   # This run's choice, never a profile's.
            bpy.utils.unregister_class(media_shelf.ANGELO_AST_object_assets)   # Stock Blender, for before.
            if director.is_director(window()):
                director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .2
            checks["object_mode"] = bpy.context.mode == 'OBJECT'
            state["before"] = str(capture("media-shelf-0-before-stock-object-mode.png"))
            checks["stock_blender_has_no_object_mode_shelf"] = not media_shelf._shown(view3d())
            bpy.utils.register_class(media_shelf.ANGELO_AST_object_assets)
            view3d().tag_redraw()
            state["phase"] = "shown"
            return .5
        if phase == "shown":   # 1.0.5: collapsed to its tab strip by default; the 1.0.4 overlay: expanded.
            first = "collapsed" if collapses() else "expanded"
            if not wait(phase, lambda: media_shelf.state(view3d()) == first
                        and asset_shelf_add.places.get(view3d().as_pointer()) is not None):
                return .2
            after = capture("media-shelf-1-after-%s-by-default.png" % first)
            state["first_state"] = str(after)
            header = region('ASSET_SHELF_HEADER')
            checks["shown_by_default_in_object_mode"] = True
            checks["its_tab_strip_is_drawn"] = changed(state["before"], after, rect(header)) > 0.05
            checks["media_studio_opens_the_3d_tab_here"] = asset_shelf_add.places[view3d().as_pointer()][3] == "3d"
            if collapses():
                actions = [a for a, _r in asset_shelf_add.places[view3d().as_pointer()][1]]
                checks["starts_collapsed_with_only_its_tab_strip"] = not media_shelf._shown(view3d())
                checks["the_collapse_chevron_comes_first"] = actions[:1] == [asset_shelf_add.FOLD]
                press_fold()
                state["phase"] = "expanded"
                return .5
            state["phase"] = "library"
            return .1
        if phase == "expanded":   # The chevron's real click: the rows show.
            if not wait(phase, lambda: media_shelf.state(view3d()) == "expanded"):
                view3d().tag_redraw()
                return .2
            path = capture("media-shelf-1b-expanded-by-its-chevron.png")
            rows = region('ASSET_SHELF')
            checks["its_chevron_expands_it_to_its_rows"] = changed(state["first_state"], path, rect(rows)) > 0.05
            state["phase"] = "library"
            return .3
        if phase == "library":
            write_library()
            search("zzzz no asset matches this")
            state["phase"] = "empty"
            return .6
        if phase == "empty":
            state["empty"] = str(capture("media-shelf-2-search-nothing.png"))
            search(MODEL)
            state["phase"] = "tile"
            return 1.0
        if phase == "tile":   # The library lists asynchronously: wait for the tile to draw.
            path = capture("media-shelf-3-fixture-model-tile.png")
            share = changed(state["empty"], path, tile_box())
            if not wait(phase, lambda: share > 0.02, limit=30):
                return .5
            state["tile_share"] = round(share, 4)
            checks["a_fixture_asset_shows_in_the_shelf"] = True
            view = region('WINDOW')
            state["drop"] = (view.x + view.width * .3, view.y + view.height * .6)
            state["objects_before"] = len(bpy.context.scene.objects)
            drag(first_tile(), state["drop"])
            state["phase"] = "dropped"
            return .8
        if phase == "dropped":   # A drag that never lands is recorded, and the suite's later checks still run.
            landed = settles(phase, lambda: any(o.name.startswith(MODEL) for o in bpy.context.scene.objects))
            if landed is None:
                return .3
            capture("media-shelf-4-model-dragged-into-the-scene.png")
            checks["dragging_a_model_adds_it_to_the_scene"] = landed and len(
                bpy.context.scene.objects) == state["objects_before"] + 1
            search(MATERIAL)
            state["phase"] = "material_tile"
            return 1.0
        if phase == "material_tile":
            path = capture("media-shelf-5-fixture-material-tile.png")
            if not wait(phase, lambda: changed(state["empty"], path, tile_box()) > 0.02, limit=30):
                return .5
            drag(first_tile(), cube_point())
            state["phase"] = "assigned"
            return .8
        if phase == "assigned":
            cube = bpy.context.scene.objects[state["cube"]]
            assigned = settles(phase, lambda: cube.active_material is not None
                               and cube.active_material.name.startswith(MATERIAL))
            if assigned is None:
                return .3
            capture("media-shelf-6-material-dropped-on-the-cube.png")
            checks["dropping_a_material_on_an_object_assigns_it"] = assigned
            search("")
            try:   # Part E's library: its first sync writes the catalogues (~20 s after startup); ask for it now.
                from angelo_agent import studio_assets_host
                studio_assets_host._state["since"] = float("-inf")
                state["library"] = "syncing"
            except ImportError:
                state["library"] = "absent (no Part E in this build)"
            state["phase"] = "tabs"
            return .5
        if phase == "tabs":   # Angelo Media Studio's Models and Images, offered as tabs once the library has them.
            hooked = "enable_catalog" in bpy.types.AssetShelf.bl_rna.functions
            catalogs = media_shelf.tab_catalogs()
            view3d().tag_redraw()   # The offer is tried from a draw (media_shelf._sample).
            if state["library"].startswith("absent") or not hooked:
                state["tabs"] = {"skipped": state["library"] if hooked else "no AssetShelf.enable_catalog in this binary "
                                 "(the 1.0.4 overlay); the 1.0.5 build runs this check"}
            else:
                def offered():
                    with shelf_override():
                        shelf = bpy.context.asset_shelf
                        return bool(catalogs) and all(shelf.is_catalog_enabled(p) for p in catalogs.values())
                if not wait(phase, offered, limit=60):
                    return .5
                capture("media-shelf-7-studio-tabs.png")
                state["tabs"] = {"catalogs": catalogs}
                checks["angelo_media_studio_is_offered_as_a_tab"] = True
                images = media_shelf.studio_catalogs().get("image")
                if images:   # The Images catalog (a child of the parent tab, or a tab itself) asks for Image.
                    with shelf_override():
                        bpy.context.asset_shelf.active_catalog_path = images
                    checks["an_images_catalog_asks_for_studios_image_tab"] = \
                        media_shelf.shelf_kind(window(), view3d()) == "image"
                state["show_assets"] = media_shelf.show_studio_tab(window())
                checks["show_my_assets_is_accepted"] = state["show_assets"] is True
                state["phase"] = "show_assets"
                return .8
            view3d().spaces.active.show_region_asset_shelf = False   # The person hides it (View > Asset Shelf).
            state["phase"] = "hidden"
            return media_shelf.SETTLE + .5
        if phase == "show_assets":
            name, parent = media_shelf.library_name(), media_shelf.studio_tab_path()
            def on_library():
                with shelf_override():
                    shelf = bpy.context.asset_shelf
                    return shelf.asset_library_reference == name and shelf.active_catalog_path == (parent or "")
            if not wait(phase, on_library, limit=10):
                return .2
            capture("media-shelf-8-show-my-assets.png")
            checks["show_my_assets_opens_the_library_on_its_tab"] = True
            with shelf_override():
                bpy.context.asset_shelf.asset_library_reference = 'ALL'
            view3d().spaces.active.show_region_asset_shelf = False   # The person hides it (View > Asset Shelf).
            state["phase"] = "hidden"
            return media_shelf.SETTLE + .5
        if phase == "hidden":
            view3d().tag_redraw()
            if not wait(phase, lambda: media_shelf.remembered() == "hidden", limit=10):
                return .3
            capture("media-shelf-7-hidden-and-remembered.png")
            checks["hiding_it_is_remembered"] = True
            view3d().spaces.active.show_region_asset_shelf = True
            state["phase"] = "reshown"
            return .5
        if phase == "reshown":
            view3d().tag_redraw()
            if not wait(phase, lambda: media_shelf.remembered() == "expanded", limit=10):
                return .3
            checks["showing_it_again_is_remembered"] = True
            if not collapses():
                state["phase"] = "complete"
                finish()
                return None
            press_fold()   # Collapsed again by the person: kept for the next file.
            state["phase"] = "recollapsed"
            return media_shelf.SETTLE + .5
        if phase == "recollapsed":
            view3d().tag_redraw()
            if not wait(phase, lambda: media_shelf.state(view3d()) == "collapsed"
                        and media_shelf.remembered() == "collapsed", limit=10):
                return .3
            capture("media-shelf-9-collapsed-again-and-remembered.png")
            checks["collapsing_it_again_is_remembered"] = True
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:   # noqa: BLE001 (the suite reports any failure in its receipt)
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
