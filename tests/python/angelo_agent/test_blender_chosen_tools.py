# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu and "Start from a base mesh" in the compiled app (GUI; no provider, no paid request).

Run a GUI Angelo with --factory-startup --python <this-file> (``run_native_suites.py --gui --include
test_blender_chosen_tools``). It opens the chat, clicks +, finds the base meshes as one closed group and opens it
(1.0.5), scrolls the menu, picks a head (a chip appears), turns
on "Match the size of" with the start cube selected, runs the Send step (the head is placed, fitted to the cube,
selected, one chat row), places another at real size at the 3D cursor, times the step for every bundled base
mesh, runs the same operation through a staged program context, and checks a failing choice leaves nothing. 1.0.5:
the chat row says what was added in the menu's words, with no timing and no internal object or data-block name
(checked on every drawn row of the + menu and the chat, also with a placed mesh selected), and the menu spells the
bundle's names right.
Screenshots (the menu as drawn, the window) and chosen-tools.json go to ANGELO_QA_DIR; inspect them, a zero
exit status alone is not proof.
"""

import copy
import json
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

import addon_utils
import bpy
import gpu

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chosen-tools-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, chat_view, console  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
from angelo_agent.chat_settings import normalize_models  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

ui._authentication.start = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
ui.connection_available = lambda: True
ui.prepare_controller = lambda: None
bpy.context.preferences.view.show_splash = False
MODELS = normalize_models([{"model": "gpt-6.1-sol", "displayName": "GPT-6.1 Sol", "isDefault": True,
                            "supportedReasoningEfforts": [{"reasoningEffort": "low"}], "defaultReasoningEffort": "low"}])
state = {"phase": "boot", "checks": {}, "started": time.monotonic(), "timings_ms": {}}
window = handler = capture_request = None
TOL = 1e-3
# What a person must never read (maintainer rule, 1.0.5): an asset's object or data-block names ("GEO-head_stylized",
# snake_case ids), file names, data-block type prefixes or hex ids, and timings.
INTERNAL = re.compile(r"\bGEO-|\w_\w|\.blend\b|\b(?:OB|ME|MA|NT|GR|IM)[A-Z]?:|\b[0-9a-f]{12,}\b")
TIMING = re.compile(r"\d\s*(ms|мс|毫秒)\b")


def internal(runs):
    """The drawn runs a person must not read."""
    return [run for run in runs if INTERNAL.search(run) or TIMING.search(run)]


def check(name, condition):
    state["checks"][name] = bool(condition)


def capture_drawn_popup():
    request = capture_request
    if request is None or bpy.context.area.as_pointer() != request["area"]:
        return
    source = chat_view._views.get(request["area"], {})
    rect = source.get("popup_rect")
    if rect and source.get("popover"):
        x, y, w, h = [int(value) for value in rect]
        pixels = gpu.state.active_framebuffer_get().read_color(x, y, w, h, 4, 0, 'UBYTE')
        pixels.dimensions = w * h * 4
        request.update(pixels=[value / 255 for value in pixels], width=w, height=h)


capture_handler = bpy.types.SpaceConsole.draw_handler_add(capture_drawn_popup, (), 'WINDOW', 'POST_PIXEL')


def area_region():
    area = next(a for a in window.screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view():
    area, _ = area_region()
    return chat_view._views.setdefault(area.as_pointer(), {})


def redraw():
    area, region = area_region()
    with bpy.context.temp_override(window=window, area=area, region=region):
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    assert chat_view.last_error is None, chat_view.last_error


def capture(name):
    """The menu exactly as the chat drew it (its own framebuffer pixels)."""
    global capture_request
    area, _ = area_region()
    capture_request = {"area": area.as_pointer()}
    redraw()
    request, capture_request = capture_request, None
    assert "pixels" in request, "The menu's draw callback did not capture this frame"
    image = bpy.data.images.new("Angelo QA frame", width=request["width"], height=request["height"], alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(request.pop("pixels"))
        image.filepath_raw, image.file_format = str(OUT / name), 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)


def drawn_runs():
    """Redraw once and return every text run the chat drew."""
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        redraw()
    finally:
        chat_view.text = original
    return runs


def screenshot(name):
    redraw()
    with bpy.context.temp_override(window=window):
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def event(kind, value='PRESS', x=None, y=None):
    area, region = area_region()
    x = region.width - 100 if x is None else x
    y = 40 if y is None else y
    data = SimpleNamespace(type=kind, value=value, mouse_x=region.x + x, mouse_y=region.y + y, mouse_prev_y=region.y + y,
                           ctrl=False, oskey=False, shift=False, alt=False, unicode="")
    with bpy.context.temp_override(window=window, area=area, region=region):
        return chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, data)


def click(action, value=None, popup=True):
    redraw()
    hits = view()["popup_hits" if popup else "hits"]
    rect = next(rect for rect, found, arg in hits if found == action and (value is None or arg == value))
    x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    event('LEFTMOUSE', 'PRESS', x, y)
    event('LEFTMOUSE', 'RELEASE', x, y)


def frame_selected():
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_selected()


def tick():
    global window, handler
    try:
        if time.monotonic() - state["started"] > 240:
            raise RuntimeError("Chosen-tools QA timed out")
        from angelo_agent import asset_start, chosen_tools as ct, chosen_tools_host as host, human_base_meshes as hbm
        if state["phase"] == "boot":
            if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
                return .1
            window = bpy.context.window_manager.windows[0]
            check("checkout_modules_loaded", str(ROOT) in host.__file__)
            bpy.ops.angelo.open_chat(side="RIGHT")
            state["phase"] = "layout"
            return 0.6
        if not qa_boot.started():
            return 0.05
        controller = ui.get_controller()
        area, region = area_region()
        if state["phase"] == "layout":
            controller.models = copy.deepcopy(MODELS)
            controller.refresh_models = lambda: None
            controller.set_options(model="gpt-6.1-sol", effort="low", fast=False)
            controller.version += 1
            with bpy.context.temp_override(window=window, area=area, region=region):
                console.render(bpy.context, force=True)
            area.spaces.active.history[-1].body = "Sculpt this character's head from the pictures"
            handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window.as_pointer(),
                                           focused_area=area.as_pointer(), session=controller.state["id"],
                                           selected_all=False, anchor=None, dragging=False, pending_click=None)
            click("attach", popup=False)
            popup = view().get("popover") or {}
            redraw()
            closed = view()["popover"]["rows"]
            # 1.0.5: the base meshes are one group, closed until the person opens it (chat_destinations).
            check("base_meshes_are_one_closed_group", any(r["kind"] == "group" and r["value"] == "base_mesh"
                                                          and not r["open"] for r in closed)
                  and not any(r.get("action") == "popup_add_pick" for r in closed))
            capture("add-menu-closed.png")
            click("popup_add_group", "base_mesh")
            redraw()
            picks = [h for h in view().get("popup_hits", []) if h[1] == "popup_add_pick"]
            rows = view()["popover"]["rows"]
            check("a_click_opens_the_group_and_the_menu_stays", (view().get("popover") or {}).get("kind") == "add")
            check("plus_opens_the_menu", popup.get("kind") == "add")
            check("menu_lists_attach_and_every_base_mesh", any(r.get("action") == "popup_add_attach" for r in rows)
                  and sum(r.get("action") == "popup_add_pick" for r in rows) == len(hbm.table()["assets"]))
            check("fit_toggle_offered_with_the_cube_selected", any(r["kind"] == "toggle" for r in rows))
            state["picks_visible"] = len(picks)
            # 1.0.4 showed 6.5 base meshes; the menu grows with the chat, so its new rows take none of their room.
            check("at_least_six_base_meshes_in_view", len(picks) >= 6)
            drawn = [line for r in rows for line in r.get("subtitle_lines", [])]
            state["why_lines"] = drawn
            check("every_why_line_is_drawn_whole", bool(drawn) and not any(line.endswith("\u2026") for line in drawn))
            # 1.0.5 review row 20: the bundle's "Primitve" showed in the menu; the data-block keeps its own spelling.
            labels = [str(r.get("label", "")) for r in rows if r.get("action") == "popup_add_pick"]
            check("menu_names_are_spelled_as_people_read_them", bool(labels) and not any("Primitve" in v for v in labels))
            capture("add-menu.png")
            screenshot("window-add-menu.png")
            # 1.0.5 review SD3: every base-mesh row in view draws its own picture (its Essentials preview), at UI
            # scale 1 and 2 in English.
            from angelo_agent import base_mesh_thumbs
            shown = [arg[1] for _, _, arg in picks]
            state["thumbs_drawn"] = sorted(set(shown) & base_mesh_thumbs.drawn)
            check("every_base_mesh_row_in_view_shows_its_picture", bool(shown) and set(shown) <= base_mesh_thumbs.drawn)
            prefs = bpy.context.preferences
            prefs.view.ui_scale = 2.0
            base_mesh_thumbs.drawn.clear()
            redraw()
            capture("add-menu-x2.png")
            picks2 = [h for h in view().get("popup_hits", []) if h[1] == "popup_add_pick"]
            check("base_mesh_pictures_at_scale_2", bool(picks2) and {arg[1] for _, _, arg in picks2}
                  <= base_mesh_thumbs.drawn)
            prefs.view.ui_scale = 1.0
            redraw()
            state["menu_internal"] = internal(drawn_runs())
            check("no_plus_menu_row_shows_an_internal_name", not state["menu_internal"])
            state["phase"] = "scroll"
            return 0.2
        if state["phase"] == "scroll":
            x, y, w, h = view()["popup_rect"]
            event('WHEELDOWNMOUSE', 'PRESS', x + w / 2, y + h / 2)
            event('WHEELDOWNMOUSE', 'PRESS', x + w / 2, y + h / 2)
            redraw()
            check("menu_scrolls", view()["popover"]["scroll"] > 0)
            capture("add-menu-scrolled.png")
            for _ in range(3):
                event('WHEELUPMOUSE', 'PRESS', x + w / 2, y + h / 2)
            redraw()
            check("menu_scrolls_back_to_the_top", view()["popover"]["scroll"] == 0)
            # Wheel down until the pick shows, as a person would: the rows above it change as the menu grows.
            pick = ("base_mesh", "Head - Stylized")
            for _ in range(12):
                redraw()
                if any(found == "popup_add_pick" and arg == pick for _, found, arg in view()["popup_hits"]):
                    break
                event('WHEELDOWNMOUSE', 'PRESS', x + w / 2, y + h / 2)
            click("popup_add_pick", pick)
            check("picking_closes_the_menu_and_adds_a_chip", not view().get("popover")
                  and controller.chosen_tools == [{"tool": "base_mesh", "option": "Head - Stylized", "fit_selected": False}])
            redraw()
            check("chip_has_a_remove_control", any(h[1] == "remove_chosen" for h in view()["hits"]))
            screenshot("window-chip.png")
            state["phase"] = "fit"
            return 0.2
        if state["phase"] == "fit":
            click("attach", popup=False)
            redraw()
            check("the_chosen_mesh_is_in_view_when_the_menu_opens", any(
                found == "popup_add_pick" and arg == ("base_mesh", "Head - Stylized") for _, found, arg in view()["popup_hits"]))
            click("popup_add_fit", "base_mesh")
            check("fit_toggle_updates_the_choice", controller.chosen_tools[0]["fit_selected"] is True)
            capture("add-menu-fit-on.png")
            event('ESC', 'PRESS')
            check("esc_closes_the_menu", not view().get("popover"))
            state["phase"] = "send"
            return 0.2
        if state["phase"] == "send":
            cube = window.scene.objects["Cube"]
            performed = host.perform_for_request(controller, window)
            out = performed[0][0]
            queued = len(controller.events.queue)
            host.report(controller, performed)
            rows = [data for kind, data in list(controller.events.queue)[queued:] if kind == "message"]
            state["fact_fitted"] = out
            asset = asset_start.asset("Head - Stylized")
            main = window.view_layer.objects.active
            check("send_places_the_head_fitted_to_the_cube", out["done_by_angelo"]
                  and abs(out["placed"]["scale"] - 2.0 / asset["size"][2]) < TOL
                  and all(abs(a - b) < TOL for a, b in zip(out["placed"]["location"], (0, 0, 0)))
                  and main.name == asset["main"] and main.get(hbm.PROPERTY) == asset["name"])
            check("send_clears_the_chip_and_reports_one_row", controller.chosen_tools == [] and len(rows) == 1
                  and rows[0]["text"].startswith("Added Head – Stylized to start from"))
            state["chat_row"] = rows[0]["text"] if rows else None
            # 1.0.5: the row names the pick as the menu and chip do, with no timing and no internal object name.
            check("the_chat_row_says_what_was_added_in_the_menus_words", bool(rows)
                  and rows[0]["text"] == "Added Head – Stylized to start from")
            cube.hide_set(True)
            frame_selected()
            state["timings_ms"]["send_fitted"] = controller.last_chosen_timings
            screenshot("window-after-send.png")
            state["phase"] = "real_size"
            return 0.3
        if state["phase"] == "real_size":
            for obj in list(window.scene.objects):
                if obj.name != "Camera" and obj.name != "Light":
                    bpy.data.objects.remove(obj)
            window.scene.cursor.location = (1.0, 2.0, 3.0)
            collections_before = set(bpy.data.collections)
            # Through the Send step, as a person adds it, so the window shot's chat row is this placement's.
            controller.chosen_tools = ct.choose([], "base_mesh", "Head (Sculpting) - Realistic")
            performed = host.perform_for_request(controller, window)
            queued = len(controller.events.queue)
            host.report(controller, performed)
            real_rows = [data for kind, data in list(controller.events.queue)[queued:] if kind == "message"]
            controller.pump_events()   # Into the transcript now, so the window shot below shows this row.
            outcomes = performed[0]
            out = outcomes[0]
            asset = asset_start.asset("Head (Sculpting) - Realistic")
            nose = [v + o for v, o in zip(asset["landmarks"]["nose_tip"], (1, 2, 3))]
            check("real_size_at_the_cursor", out["done_by_angelo"] and out["placed"]["scale"] == 1.0
                  and all(abs(a - b) < TOL for a, b in zip(out["landmarks"]["nose_tip"], nose)))
            check("every_listed_and_unlisted_part_arrives_and_no_collection_is_left",
                  sum(1 for o in window.scene.objects if o.get(hbm.PROPERTY)) == len(asset["objects"]) + asset["other_parts"]
                  and set(bpy.data.collections) == collections_before)
            frame_selected()
            screenshot("window-real-size.png")
            # 1.0.5: the transcript said "Added Head – Stylized … (4 ms)", then "(GEO-head_sculpting_realistic)".
            runs = drawn_runs()
            state["real_size_row"] = {"rows": [r.get("text") for r in real_rows], "internal": internal(runs),
                                      "drawn": [run for run in runs if "start from" in run]}
            check("the_transcript_shows_no_internal_name_or_timing", len(real_rows) == 1
                  and real_rows[0]["text"] == "Added Head (Sculpting) – Realistic to start from"
                  and any(run.startswith("Added Head (Sculpting) – Realistic") for run in runs) and not internal(runs))
            # The + menu with that placed mesh selected: its fit toggle names it as the menu does, never its object.
            click("attach", popup=False)
            state["phase"] = "menu_with_a_placed_mesh"
            return 0.3
        if state["phase"] == "menu_with_a_placed_mesh":
            redraw()
            rows = (view().get("popover") or {}).get("rows", [])
            runs = drawn_runs()
            toggle = next((r for r in rows if r["kind"] == "toggle"), None)
            state["placed_mesh_menu"] = {"internal": internal(runs), "toggle": str(toggle and toggle.get("label")),
                                         "runs": [run for run in runs if "Match" in run]}
            check("no_plus_menu_row_or_chat_row_shows_an_internal_name", bool(rows) and not internal(runs))
            check("the_fit_toggle_names_a_placed_mesh_as_the_menu_does", bool(toggle) and any(
                "Head (Sculpting) – Realistic" in run for run in runs if "Match" in run))
            capture("add-menu-placed-mesh-selected.png")
            screenshot("window-add-menu-placed-mesh-selected.png")
            event('ESC', 'PRESS')
            state["phase"] = "measure"
            return 0.3
        if state["phase"] == "measure":
            per_asset, failures = {}, {}
            for asset in hbm.table()["assets"]:
                samples = []
                for _ in range(3):
                    for obj in [o for o in window.scene.objects if o.get(hbm.PROPERTY)]:
                        bpy.data.objects.remove(obj)
                    outcomes, ms = host.perform(ct.choose([], "base_mesh", asset["name"]), window)
                    if not outcomes[0]["done_by_angelo"]:
                        failures[asset["name"]] = outcomes[0]["not_done"]
                    samples.append(ms[0])
                per_asset[asset["name"]] = samples
            flat = sorted(v for samples in per_asset.values() for v in samples)
            state["timings_ms"]["per_asset"] = per_asset
            state["timings_ms"]["summary"] = {"n": len(flat), "median": statistics.median(flat),
                                              "p90": flat[int(0.9 * (len(flat) - 1))], "max": flat[-1]}
            state["asset_failures"] = failures
            check("every_base_mesh_places", not failures)
            idle = SimpleNamespace(chosen_tools=[], busy=False)
            started = time.perf_counter()
            for _ in range(1000):
                host.perform_for_request(idle, window)
            state["timings_ms"]["nothing_chosen_per_send"] = (time.perf_counter() - started)
            check("nothing_chosen_adds_no_measurable_time", state["timings_ms"]["nothing_chosen_per_send"] < 0.05)
            # The same operation through a staged program context: what a model's typed create will use.
            from angelo_agent.action_context import StagedActionContext
            stage = bpy.data.scenes.new("Angelo QA stage")
            stage_collection = bpy.data.collections.new("Angelo QA stage objects")
            stage.collection.children.link(stage_collection)
            context = StagedActionContext(stage, stage_collection)
            sink = SimpleNamespace(track=context.track_id, link=context.add_object)
            objects, main, facts = asset_start.place("Head - Stylized", sink)
            check("the_operation_runs_in_a_staged_context", objects and all(
                o in context.owned_ids and o.data in context.owned_ids for o in objects)
                and set(facts["landmarks"]) == set(state["fact_fitted"]["landmarks"]))
            for obj in objects:
                bpy.data.objects.remove(obj)
            bpy.data.scenes.remove(stage)
            names = sorted(o.name for o in bpy.data.objects)
            outcomes, _ = host.perform([{"tool": "base_mesh", "option": "No Such Mesh", "fit_selected": False}], window)
            check("a_failing_choice_is_reported_and_leaves_nothing", not outcomes[0]["done_by_angelo"]
                  and sorted(o.name for o in bpy.data.objects) == names)
            state["phase"] = "complete"
            return finish()
    except Exception:
        state["error"] = traceback.format_exc()
        return finish()


def finish():
    state["success"] = state.get("phase") == "complete" and "error" not in state and all(state["checks"].values())
    (OUT / "chosen-tools.json").write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_CHOSEN_TOOLS", json.dumps({"success": state["success"], "failed": sorted(
        name for name, passed in state["checks"].items() if not passed), "error": state.get("error", "")[-400:]}),
        flush=True)
    os._exit(0 if state["success"] else 1)


bpy.app.timers.register(tick, first_interval=1)
