# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo Media Studio's empty Asset Shelf in the running app (1.0.5, ``shelf_empty``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference. Needs the 1.0.5
binary (``AssetShelf.angelo_shown_count``, ``enable_catalog``) and Part E's library; on another binary the suite fails
saying so. In Manual mode's Object mode, on a fresh profile (Media Studio has made nothing):

- an empty Current File shelf shows no Angelo state (the scope rule: only Angelo Media Studio's own view says it);
- "Show my assets" (``media_shelf.show_studio_tab``), from that Current File shelf, lands on the Angelo Media Studio
  library and its tab, which the shelf now lists among its tabs (``AssetShelf.is_catalog_enabled``, Angelo's RNA);
- there the state shows, in English and German at UI scale 1 and 2, each captured with a narrow 3D view beside the
  wide one (split for the capture): the teal button by pixels in its recorded box in both, the message dropped in the
  narrow one and the button inside it;
- a real click on the button opens Media Studio on its 3D tab;
- a model written into the library's folder under its Models catalog, as Media Studio's converter writes one, reaches
  the shelf through the library's own refresh (``studio_assets_host.refresh_asset_browsers``) and the state goes; when
  that refresh misses the shelf the check fails and a manual Refresh still takes the listed capture; the shelf with
  that one item is captured in English and German at UI scale 1 and 2.

Run through ``run_native_suites.py --gui --include test_blender_shelf_empty``; check ``shelf-empty.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-shelf-empty-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "shelf-empty.json"
TEAL = (43, 196, 191)   # card_kit.TEAL in 0-255: the button's fill.
VARIANTS = (("en_US", 1.0), ("de_DE", 1.0), ("en_US", 2.0), ("de_DE", 2.0))
NARROW = 430   # The narrow view's width in interface units: the button fits, the message does not.
MODEL = "QA Studio Pebble"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "variants": {}, "variant": 0}
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def windows():
    return bpy.context.window_manager.windows


def views():
    return [a for a in window().screen.areas if a.type == 'VIEW_3D']


def view3d():
    return max(views(), key=lambda a: a.width * a.height)


def region(kind, area=None):
    return next((r for r in (area or view3d()).regions if r.type == kind), None)


def shelf_override(area=None):
    area = area or view3d()
    return bpy.context.temp_override(window=window(), area=area, region=region('ASSET_SHELF', area))


def shelf(area=None):
    if region('ASSET_SHELF', area) is None:
        return None
    with shelf_override(area):
        return getattr(bpy.context, "asset_shelf", None)


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def press(x, y):
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=int(x), y=int(y))


def pixels(path):
    """A capture's pixels as an (height, width, 3) array of 0-255 integers, bottom row first (window pixels)."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def teal_share(px, rect):
    import numpy
    x, y, w, h = (int(round(v)) for v in rect)
    block = px[max(0, y):y + h, max(0, x):x + w].reshape(-1, 3)
    return round(float((numpy.abs(block - numpy.array(TEAL)).sum(axis=1) <= 40).mean()), 3) if block.size else 0.0


def changed(before, after, rect):
    import numpy
    x, y, w, h = (int(v) for v in rect)
    a, b = pixels(before)[y:y + h, x:x + w], pixels(after)[y:y + h, x:x + w]
    return float((numpy.abs(a - b).sum(axis=2) > 24).mean()) if a.size else 0.0


def settled():
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def to_studio(area):
    """``area``'s shelf on Angelo Media Studio, as "Show my assets" leaves the main view (a split view's own shelf)."""
    from angelo_agent import media_shelf
    parent = media_shelf.studio_tab_path()
    with shelf_override(area):
        current = bpy.context.asset_shelf
        current.asset_library_reference = media_shelf.library_name()
        if parent and not current.is_catalog_enabled(parent):
            current.enable_catalog(parent)
        current.active_catalog_path = parent or ""
    area.tag_redraw()


def studio_window():
    from angelo_agent import studio
    return next((w for w in windows() if w.screen and any(studio.is_studio(a) for a in w.screen.areas)), None)


def studio_kind(target):
    from angelo_agent import studio
    area = next((a for a in target.screen.areas if studio.is_studio(a)), None) if target and target.screen else None
    return studio.view(area).get("kind") if area else None


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_SHELF_EMPTY", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(key, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(key, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + key)
    return False


def write_studio_item():
    """One model under the library's Models catalog, in its folder, as Media Studio's converter writes an item."""
    from angelo_agent import studio_assets, studio_state
    folder = studio_assets.asset_directory(studio_state.library_directory())
    path = folder / "qa-shelf-empty-fixture.blend"   # Not a name the library owns (studio_assets.owned_blend).
    mesh = bpy.data.meshes.new(MODEL)
    mesh.from_pydata([(-.3, -.3, 0), (.3, -.3, 0), (0, .3, 0), (0, 0, .5)], [], [(0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)])
    model = bpy.data.objects.new(MODEL, mesh)
    model.asset_mark()
    model.asset_data.catalog_id = studio_assets.CATALOGS["3D"][0]
    bpy.data.libraries.write(str(path), {model, mesh}, fake_user=True)
    bpy.data.objects.remove(model)
    bpy.data.meshes.remove(mesh)
    state["fixture"] = str(path)


def listed():
    from angelo_agent import shelf_empty
    current = shelf()
    return (view3d().as_pointer() not in shelf_empty.places and current is not None
            and current.angelo_list_ready and current.angelo_shown_count > 0)


def tick():
    from angelo_agent import director, media_shelf, shelf_empty
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 300, "Suite timed out"
        if phase == "boot":
            if not settled():
                return .3
            rna = bpy.types.AssetShelf.bl_rna   # RNA members are no class attributes: read them from bl_rna.
            assert "angelo_shown_count" in rna.properties and "enable_catalog" in rna.functions, \
                "Needs the 1.0.5 binary (AssetShelf.angelo_shown_count, enable_catalog)"
            media_shelf._path = lambda: OUT / media_shelf.FILE   # This run's choice, never a profile's.
            if director.is_director(window()):
                director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            state["manual_wait"] = {"director": director.is_director(window()), "waiting_on": qa_boot.waiting_on(),
                                    "workspace": window().workspace.name}   # In the report if this times out.
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .2
            view3d().spaces.active.show_region_asset_shelf = True
            state["phase"] = "library"
            return .5
        if phase == "library":   # Part E registers its library at startup; its catalogues come with its first sync.
            if not wait(phase, lambda: media_shelf.library_name() is not None and shelf() is not None, limit=30):
                return .3
            state["library"] = media_shelf.library_name()
            with shelf_override():
                bpy.context.asset_shelf.asset_library_reference = 'LOCAL'
                bpy.context.asset_shelf.active_catalog_path = ""
            view3d().tag_redraw()
            state["phase"] = "current_file"
            return .5
        if phase == "current_file":   # Nothing in this file is an asset: Blender's own empty shelf, no Angelo state.
            def empty_listed():
                view3d().tag_redraw()
                current = shelf()
                return current.angelo_list_ready and current.angelo_shown_count == 0
            if not wait(phase, empty_listed, limit=15):
                return .2
            state.setdefault("current_file_frames", 0)
            state["current_file_frames"] += 1
            if state["current_file_frames"] < 3:   # A few more draws: the state would have drawn by now.
                view3d().tag_redraw()
                return .2
            capture("shelf-empty-0-current-file-no-angelo-state.png")
            checks["an_empty_current_file_shelf_shows_no_angelo_state"] = \
                view3d().as_pointer() not in shelf_empty.places
            state["show_assets"] = media_shelf.show_studio_tab(window())
            checks["show_my_assets_is_accepted"] = state["show_assets"] is True
            state["phase"] = "show_assets"
            return .5
        if phase == "show_assets":
            parent = media_shelf.studio_tab_path()
            def landed():
                current = shelf()
                return (current.asset_library_reference == state["library"]
                        and current.active_catalog_path == (parent or ""))
            if not wait(phase, landed, limit=10):
                return .2
            state["landed"] = {"library": state["library"], "tab": parent or "(All)"}
            checks["show_my_assets_lands_on_the_angelo_media_studio_library_and_tab"] = True
            if parent:
                checks["the_angelo_media_studio_tab_is_one_of_the_shelfs_tabs"] = shelf().is_catalog_enabled(parent)
            state["phase"] = "variant"
            return .3
        if phase == "variant":
            language, scale = VARIANTS[state["variant"]]
            prefs.view.language, prefs.view.use_translate_interface = language, True
            prefs.view.ui_scale = scale
            shelf_empty.places.clear()
            shelf_empty.texts.clear()
            state["phase"] = "split"
            return 1.0
        if phase == "split":   # A narrow 3D view beside the wide one, for this capture only.
            language, scale = VARIANTS[state["variant"]]
            base = view3d()
            state["views_before"] = len(views())
            factor = min(0.45, NARROW * scale / base.width)
            with bpy.context.temp_override(window=window(), screen=window().screen, area=base,
                                           region=region('WINDOW', base)):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=factor)
            state["phase"] = "dress"
            return .5
        if phase == "dress":
            key = "dress%d" % state["variant"]
            if not wait(key, lambda: len(views()) > state["views_before"] and all(shelf(a) for a in views()), limit=10):
                for area in views():
                    area.spaces.active.show_region_asset_shelf = True
                    area.tag_redraw()
                return .2
            for area in views():
                to_studio(area)
            state["phase"] = "shot"
            return .5
        if phase == "shot":
            language, scale = VARIANTS[state["variant"]]
            tag = "%s_x%d" % (language[:2], int(scale))
            narrow, wide = min(views(), key=lambda a: a.width), view3d()
            if not wait("shot%d" % state["variant"], lambda: all(a.as_pointer() in shelf_empty.texts
                                                              for a in (narrow, wide)), limit=15):
                for area in (narrow, wide):
                    area.tag_redraw()
                return .2
            path = capture("shelf-empty-%d-%s.png" % (state["variant"] + 1, tag))
            px = pixels(path)
            rect_wide, rect_narrow = (shelf_empty.places[a.as_pointer()][1] for a in (wide, narrow))
            inside = narrow.x <= rect_narrow[0] and rect_narrow[0] + rect_narrow[2] <= narrow.x + narrow.width
            from angelo_agent.ui_text import iface_
            record = {"wide": {"width": wide.width, "button": [round(v) for v in rect_wide],
                               "teal": teal_share(px, rect_wide), "message": shelf_empty.texts[wide.as_pointer()]},
                      "narrow": {"width": narrow.width, "button": [round(v) for v in rect_narrow],
                                 "teal": teal_share(px, rect_narrow), "message": shelf_empty.texts[narrow.as_pointer()],
                                 "button_inside": inside},
                      "button_text": iface_(shelf_empty.GENERATE), "capture": str(path)}
            state["variants"][tag] = record
            checks[tag + "_the_angelo_tab_says_so"] = record["wide"]["teal"] > 0.3
            checks[tag + "_narrow_keeps_the_button"] = record["narrow"]["teal"] > 0.3 and inside
            checks[tag + "_narrow_drops_the_message"] = record["narrow"]["message"] is False
            if language != "en_US":
                checks[tag + "_translated"] = record["button_text"] != shelf_empty.GENERATE
            with bpy.context.temp_override(window=window(), screen=window().screen, area=narrow,
                                           region=region('WINDOW', narrow)):
                bpy.ops.screen.area_close()
            state["phase"] = "unsplit"
            return .5
        if phase == "unsplit":
            if not wait("unsplit%d" % state["variant"], lambda: len(views()) == state["views_before"], limit=10):
                return .2
            state["variant"] += 1
            if state["variant"] < len(VARIANTS):
                state["phase"] = "variant"
                return .3
            prefs.view.language, prefs.view.ui_scale = "en_US", 1.0
            shelf_empty.places.clear()
            state["phase"] = "click"
            return 1.0
        if phase == "click":   # The button opens Media Studio (its 3D tab, from Object mode's Angelo tab).
            if not wait(phase, lambda: view3d().as_pointer() in shelf_empty.places, limit=10):
                view3d().tag_redraw()
                return .2
            state["empty_capture"] = str(capture("shelf-empty-5-en_x1-before-click.png"))
            x, y, w, h = shelf_empty.places[view3d().as_pointer()][1]
            state["workspace"] = window().workspace.name   # Manual's Media Studio tab replaces it; restored after.
            press(x + w / 2, y + h / 2)
            state["phase"] = "opened"
            return .6
        if phase == "opened":
            if not wait(phase, lambda: studio_window() is not None and studio_kind(studio_window()) == "3D"):
                return .2
            capture("shelf-empty-6-media-studio-opened.png", studio_window())
            checks["its_button_opens_media_studio_on_the_3d_tab"] = True
            from angelo_agent import studio_toggle
            own = studio_toggle.opened_window(windows())
            if own is not None:
                studio_toggle.close_studio(own)
            else:
                window().workspace = bpy.data.workspaces[state["workspace"]]
            state["phase"] = "closed"
            return .4
        if phase == "closed":
            from angelo_agent import studio_toggle
            if not wait(phase, lambda: studio_toggle.opened_window(windows()) is None and len(windows()) == 1
                        and window().workspace.name == state["workspace"]):
                return .2
            write_studio_item()
            from angelo_agent import studio_assets_host
            studio_assets_host.refresh_asset_browsers()   # The library's own refresh after its converter wrote.
            state["phase"] = "listed"
            return .5
        if phase == "listed":
            if time.monotonic() - state.setdefault("since", {}).setdefault(phase, time.monotonic()) > 12:
                checks["a_new_media_studio_item_reaches_the_shelf_through_the_librarys_refresh"] = False
                with shelf_override():
                    bpy.ops.asset.library_refresh()   # A manual Refresh, so the listed capture still shows.
                state["listed_by"] = "a manual Refresh (the library's refresh missed the shelf)"
                state["phase"] = "listed_manual"
                return .5
            view3d().tag_redraw()
            if not listed():
                return .3
            checks["a_new_media_studio_item_reaches_the_shelf_through_the_librarys_refresh"] = True
            state["listed_by"] = "the library's refresh"
            state["phase"] = "listed_shot"
            return .5
        if phase == "listed_manual":
            view3d().tag_redraw()
            if not wait(phase, listed, limit=15):
                return .3
            state["phase"] = "listed_shot"
            return .5
        if phase == "listed_shot":
            path = capture("shelf-empty-7-listed.png")
            shelf_box = region('ASSET_SHELF')
            share = changed(state["empty_capture"], path, (shelf_box.x, shelf_box.y, shelf_box.width,
                                                            shelf_box.height))
            state["listed_change"] = round(share, 3)
            checks["the_state_goes_once_an_item_lists"] = view3d().as_pointer() not in shelf_empty.places \
                and share > 0.02
            state["phase"], state["listed_variant"] = "listed_variant", 0
            return .2
        if phase == "listed_variant":   # The same shelf with its one item, in every language and scale.
            index = state["listed_variant"]
            if index == len(VARIANTS):
                prefs.view.language, prefs.view.ui_scale = "en_US", 1.0
                Path(state["fixture"]).unlink(missing_ok=True)
                state["phase"] = "complete"
                finish()
                return None
            language, scale = VARIANTS[index]
            if state.get("listed_set") != index:
                prefs.view.language, prefs.view.use_translate_interface, prefs.view.ui_scale = language, True, scale
                state["listed_set"] = index
                return 1.0
            view3d().tag_redraw()
            if not listed():
                return .3
            capture("shelf-empty-8-listed-%s_x%d.png" % (language[:2], int(scale)))
            state["listed_variant"] = index + 1
            return .2
    except Exception as error:   # noqa: BLE001 (the suite reports any failure in its receipt)
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
