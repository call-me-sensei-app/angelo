# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make Video from This Shot in the running app, from the chat bar's + menu in Director (GUI; no provider, no account).

Run a GUI Angelo with --factory-startup --python <this-file> (``run_native_suites.py --gui --include
test_blender_shot_video_gui``). The default startup (Director), a camera that moves around the cube and tracks it:
the chat's + button opens the menu, whose Video tile (Create) opens a page listing Make Video from This Shot (1.0.8);
a click on that row (the real
chat input handler) queues the shot's three render jobs, whose cards show in the chat; once they end, the chat has a
card that opens the Video tab (never a folder path), the frames show there as pictures, the camera move is
a complete MP4 and the person's render settings are as they were. The operator is in the Render menu and in View >
Cameras. Screenshots (the menu as drawn, the window while it renders, the window after) and shot-video-gui.json go to
ANGELO_QA_DIR; inspect them, a zero exit status alone is not proof. Needs a build with FFmpeg for the MP4 part; the
development build checks the move's refusal instead.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
from pathlib import Path
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-shot-video-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, chat_view, console  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator and qa_boot, beside this suite.
import qa_operator  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_plus_menu  # noqa: E402  (the + menu's pages, read from the chat's canvas state)
from angelo_agent import product_env  # noqa: E402
product_env.clear('DISABLE_DEFAULT_CHAT')   # The default startup builds Director (the runner's opt-out skips it).

ui._authentication.start = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
ui.connection_available = lambda: True
ui.prepare_controller = lambda: None
bpy.context.preferences.view.show_splash = False
# ANGELO_QA_LANGUAGE (a Blender locale such as ja_JP): the same run in another language, to see clipped text. The
# checks then look at state and hit regions; the English-text checks run only in English.
LANGUAGE = os.environ.get("ANGELO_QA_LANGUAGE", "")
if LANGUAGE not in ("", "en_US"):
    bpy.context.preferences.view.language = LANGUAGE
    bpy.context.preferences.view.use_translate_interface = True


def english():
    """True while the interface draws English (however the language was chosen): the text checks run only then."""
    return bpy.app.translations.pgettext_iface("Open Video tab") == "Open Video tab"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "checks": {}, "started": time.monotonic(), "screenshots": []}
window = handler = capture_request = None
FRAMES = 24
TIMEOUT = 420


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


def capture_menu(name):
    """The + menu exactly as the chat drew it (its own framebuffer pixels)."""
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
    state["screenshots"].append(name)


def screenshot(name):
    redraw()
    with bpy.context.temp_override(window=window):
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def drawn_runs(draw=None):
    """Every text run drawn by one redraw of the chat (or ``draw``: Media Studio's window)."""
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        (draw or redraw)()
    finally:
        chat_view.text = original
    return runs


def attachment_pictures():
    """The attachments Media Studio has drawn as pictures (studio_media_tiles): their file paths."""
    from angelo_agent import studio_tile_previews
    return set(studio_tile_previews.previews()._ready)


def event(kind, value='PRESS', x=None, y=None):
    area, region = area_region()
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


def blocking_shot(scene):
    """A camera that circles the cube and keeps it framed, so first and last frame differ and both show it."""
    camera, cube = scene.camera, bpy.data.objects["Cube"]
    track = camera.constraints.new('TRACK_TO')
    track.target = cube
    camera.location = (7.0, -7.0, 4.0)
    camera.keyframe_insert("location", frame=1)
    camera.location = (-7.0, -7.0, 2.0)
    camera.keyframe_insert("location", frame=FRAMES)
    scene.frame_start, scene.frame_end = 1, FRAMES


def studio_window():
    """``(window, area)`` of the window showing Media Studio, or ``(None, None)``."""
    from angelo_agent import live_windows, studio
    for candidate in live_windows.windows(bpy.context.window_manager):
        for area in live_windows.areas(candidate):
            if studio.is_studio(area):
                return candidate, area
    return None, None


def video_tab_state():
    """Media Studio's state (the Video tab's frames and reference video), or {}."""
    from angelo_agent import studio
    _target, area = studio_window()
    return studio.view(area) if area is not None else {}


def studio_draw():
    """Draw Media Studio's window now; what it drew is recorded (its hit regions, its last error)."""
    from angelo_agent import studio
    target, area = studio_window()
    if target is None:
        return None
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=target, area=area, region=region):
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    return {"hits": len(studio.view(area).get("hits") or ()), "error": str(studio.last_error or ""),
            "size": [target.width, target.height]}


def studio_screenshot(name):
    """The window showing Media Studio's Video tab, drawn on an earlier pass and again now."""
    target, _area = studio_window()
    if target is None:
        return
    state.setdefault("studio_draws", {})[name] = studio_draw()
    with bpy.context.temp_override(window=target):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def person(scene):
    image = scene.render.image_settings
    return (scene.render.engine, scene.render.resolution_x, scene.render.resolution_y, image.file_format,
            image.media_type, scene.render.filepath)


def tick():
    global window, handler
    try:
        if time.monotonic() - state["started"] > TIMEOUT:
            raise RuntimeError("Shot-to-video GUI QA timed out in " + state["phase"])
        from angelo_agent import chat_destinations, director, job_render_video, job_ui, shot_capture
        if state["phase"] == "boot":
            if not qa_boot.started():
                return .1
            window = bpy.context.window_manager.windows[0]
            if not director.is_director(window):
                return .3
            # Director's layout is built a few frames after the mode settles: act once it has drawn for a moment.
            if time.monotonic() - state.setdefault("director_since", time.monotonic()) < 1.0:
                return .2
            check("checkout_modules_loaded", str(ROOT) in shot_capture.__file__)
            check("runs_in_director", director.is_director(window))
            scene = window.scene
            blocking_shot(scene)
            state["person"] = person(scene)
            state["video"] = job_render_video.available()
            if not any(console.is_chat(area) for area in window.screen.areas):
                bpy.ops.angelo.open_chat(side="RIGHT")
            screenshot("shot-0-director.png")
            state["phase"] = "menu"
            return 0.6
        if not qa_boot.started():
            return 0.05
        controller = ui.get_controller()
        area, region = area_region()
        scene = window.scene
        if state["phase"] == "menu":
            with bpy.context.temp_override(window=window, area=area, region=region):
                console.render(bpy.context, force=True)
            handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window.as_pointer(),
                                           focused_area=area.as_pointer(), session=controller.state["id"],
                                           selected_all=False, anchor=None, dragging=False, pending_click=None)
            click("attach", popup=False)
            group = qa_plus_menu.group_of("shot_to_video")   # Its group's tile opens its page (chat_destinations.PARENTS).
            click("popup_add_group", group)
            runs = drawn_runs()   # The page's rows are filled when it is first drawn.
            check("its_group_opens_as_a_page", qa_plus_menu.on_page(view(), group))
            rows = (view().get("popover") or {}).get("rows") or []
            row = next((r for r in rows if r.get("value") == "shot_to_video"), None)
            check("plus_menu_lists_the_row", row is not None and row.get("operator") == "angelo.shot_to_video")
            motion_row = next((r for r in rows if r.get("value") == "shot_motion"), None)
            check("plus_menu_lists_the_camera_move", motion_row is not None
                  and dict(motion_row.get("properties") or ()).get("mode") == "MOTION")
            if english():
                check("row_drawn_with_its_label", any("Make Video from This Shot" in run for run in runs))
                # What the person gets, not how it is built (no "Renders", "MP4" or "reference video" in the menu).
                drawn = " ".join(" ".join(run.split()) for run in runs)   # A wrapped description is several runs.
                check("menu_says_what_you_get", "starts and ends on this camera's view" in drawn
                      and "MP4" not in drawn and "Renders the" not in drawn)
            check("video_rows_use_the_video_icon", row is not None and row.get("glyph") == "video"
                  and motion_row is not None and motion_row.get("glyph") == "video")
            capture_menu("shot-1-plus-menu.png")
            click("popup_add_open", "shot_to_video")
            pending = shot_capture.pending()
            check("click_started_one_shot", len(pending) == 1)
            state["shot"] = pending[0] if pending else None
            parts = set(state["shot"].jobs) if state["shot"] else set()
            check("frames_entry_queued_first_and_last", parts == {"first", "last"})
            state["phase"] = "rendering"
            return 0.5
        if state["phase"] == "rendering":
            shot = state["shot"]
            active = job_ui.manager().active()
            if active is not None and "rendering_shot" not in state:
                state["rendering_shot"] = True
                from angelo_agent.job_chat import title
                titles = {title(job_ui.manager().record(job)) for job in shot.jobs.values()}
                state["card_titles"] = sorted(titles)
                check("render_cards_say_what_they_capture", titles == {
                    bpy.app.translations.pgettext_iface(text) for text in (
                        shot_capture.JOB_TITLES["first"], shot_capture.JOB_TITLES["last"])})
                screenshot("shot-2-rendering.png")
            if shot is None or not shot.finished:
                return 0.25
            state["files"] = {part: str(path) for part, path in shot.files.items()}
            state["errors"] = dict(shot.errors)
            state["phase"] = "after"
            return 0.5
        if state["phase"] == "after":
            shot = state["shot"]
            first, last = shot.files.get("first"), shot.files.get("last")
            check("first_and_last_rendered", first is not None and last is not None and first.is_file()
                  and last.is_file() and first.read_bytes() != last.read_bytes())
            notices = [m for m in controller.state["messages"] if m.get("notice") and m.get("open_studio")]
            # The result is a card that opens the Video tab; it names the camera, never the workspace folder.
            check("chat_row_opens_the_video_tab", bool(notices) and notices[-1]["open_studio"] == "video"
                  and shot.camera in notices[-1]["text"] and shot.folder not in notices[-1]["text"]
                  and "shots/" not in notices[-1]["text"])
            # The shot's row comes after the cards of its parts (the last part's own notice first, job_ui).
            rows = controller.state["messages"]
            last_job = max((i for i, m in enumerate(rows) if m.get("job") in set(shot.jobs.values())), default=-1)
            check("shot_row_after_its_parts", bool(notices) and rows.index(notices[-1]) > last_job)
            state["chat_row"] = notices[-1]["text"] if notices else ""
            runs = drawn_runs()
            check("chat_card_has_its_open_button", any(action == "open_studio" and value == "video"
                                                       for _rect, action, value in view().get("hits") or ()))
            if english():
                check("chat_card_drawn", any("Your shot is ready" in run for run in runs)
                      and any("Open Video tab" in run for run in runs) and not any("shots/" in run for run in runs))
            from angelo_agent import shot_capture_ui
            if shot_capture_ui._video_tab() is not None:   # Opened in the Video tab, holding both frames.
                check("frames_opened_in_the_video_tab", not any(
                    "couldn't take the shot" in str(m.get("text")) for m in controller.state["messages"]))
                tab = video_tab_state()
                state["tab"] = {key: bool(tab.get(key)) for key in ("video_first", "video_last",
                                                                       "video_reference_video", "video_aspect")}
                check("video_tab_holds_first_and_last", bool(tab.get("video_first")) and bool(tab.get("video_last")))
                check("video_tab_takes_the_shots_aspect", tab.get("video_aspect") == shot.aspect)
            else:
                from angelo_agent.flow_host import library
                lineage = [a.get("lineage", {}) for a in library().assets.values()]
                check("frames_in_media_studio_library",
                      sum(1 for item in lineage if item.get("source") == "shot") == 2)
            check("person_settings_put_back", person(scene) == state["person"])
            check("render_menu_entry", any(getattr(f, "__name__", "") == "menu_item" and "shot_capture" in f.__module__
                                           for f in bpy.types.TOPBAR_MT_render._dyn_ui_initialize()))
            check("camera_menu_entry", any(getattr(f, "__name__", "") == "menu_item" and "shot_capture" in f.__module__
                                           for f in bpy.types.VIEW3D_MT_view_cameras._dyn_ui_initialize()))
            check("feature_registered", chat_destinations.feature("shot_to_video") is not None)
            screenshot("shot-3-ready.png")
            if shot_capture_ui._video_tab() is not None:   # The card's button, clicked as the person would.
                click("open_studio", "video", popup=False)
                check("open_button_shows_the_video_tab", studio_window()[0] is not None
                      and bool(video_tab_state().get("video_first")))
            state["phase"] = "tab_frames_drawn"   # Media Studio's new window draws on a later pass.
            return 0.6
        if state["phase"] == "tab_frames_drawn":
            studio_draw()
            state["phase"] = "tab_frames"
            return 0.4
        if state["phase"] == "tab_frames":
            from angelo_agent import shot_capture_ui
            if shot_capture_ui._video_tab() is not None:
                studio_screenshot("shot-3b-video-tab-frames.png")
                tab = video_tab_state()
                shown = attachment_pictures()
                check("create_column_open_for_the_shot", tab.get("collapsed") is False)
                check("frames_shown_as_pictures", all(tab.get(key) and tab[key]["path"] in shown
                                                      for key in ("video_first", "video_last")))
            with bpy.context.temp_override(window=window):   # The camera-move entry, as the Render menu runs it.
                bpy.ops.angelo.shot_to_video(mode='MOTION')
            pending = shot_capture.pending()
            state["motion_shot"] = pending[0] if pending else None
            check("motion_entry_queued_the_move", bool(pending) and set(pending[0].jobs) == (
                {"motion"} if state["video"] else set()) or (not state["video"] and not pending))
            state["phase"] = "motion"
            return 0.5
        if state["phase"] == "motion":
            motion_shot = state.get("motion_shot")
            if motion_shot is not None and not motion_shot.finished:
                return 0.25
            if state["video"]:
                motion = motion_shot.files.get("motion") if motion_shot else None
                check("motion_is_a_complete_mp4", motion is not None and job_render_video.mp4_whole(motion))
            else:
                check("motion_refused_without_ffmpeg", motion_shot is None or "FFmpeg" in str(
                    motion_shot.errors.get("motion", "")))
            check("person_settings_put_back_after_the_move", person(scene) == state["person"])
            from angelo_agent import shot_capture_ui
            if state["video"] and shot_capture_ui._video_tab() is not None:
                tab = video_tab_state()
                check("video_tab_holds_the_move_as_reference", bool(tab.get("video_reference_video"))
                      and not tab.get("video_first"))
            screenshot("shot-4-motion.png")
            state["phase"] = "tab_reference_drawn"
            return 0.6
        if state["phase"] == "tab_reference_drawn":
            studio_draw()
            state["phase"] = "tab_reference"
            return 0.4
        if state["phase"] == "tab_reference":
            from angelo_agent import shot_capture_ui
            if shot_capture_ui._video_tab() is not None:
                studio_screenshot("shot-4b-video-tab-reference.png")
                if state["video"]:
                    video = video_tab_state().get("video_reference_video") or {}
                    check("move_shown_as_its_poster", video.get("path") in attachment_pictures())
                    runs = drawn_runs(studio_draw)
                    if english():
                        check("move_explains_the_first_frame", any("camera move guides the motion" in run for run in runs)
                              and any("Use a first frame instead" in run for run in runs))
            state["phase"] = "image_tab_drawn"
            return 0.4
        if state["phase"] == "image_tab_drawn":   # The Image tab's references use the same picture tiles.
            from angelo_agent import studio
            tab = video_tab_state()
            first = (state["shot"].files or {}).get("first")
            if tab and first is not None:
                tab.update(kind="IMAGE", create_scroll=0)
                tab["references"] = studio._references.import_images([str(first)], tab.get("references") or [])
                studio_draw()
            state["phase"] = "image_tab"
            return 0.4
        if state["phase"] == "image_tab":
            tab = video_tab_state()
            if tab.get("kind") == "IMAGE":
                studio_screenshot("shot-5-image-tab-references.png")
                check("image_references_shown_as_pictures", bool(tab.get("references")) and all(
                    r["path"] in attachment_pictures() for r in tab["references"]))
                check("image_reference_has_its_remove_mark", any(
                    action == "remove_reference" for _rect, action, _value in tab.get("hits") or ()))
            state["phase"] = "complete"
            return finish()
        return 0.25
    except Exception:
        state["error"] = traceback.format_exc()
        return finish()


def finish():
    state["success"] = state.get("phase") == "complete" and "error" not in state and all(state["checks"].values())
    state["elapsed_seconds"] = round(time.monotonic() - state["started"], 1)
    state["interface"] = {"locale": bpy.app.translations.locale, "english": english(),
                          "window": [window.width, window.height] if window else None}
    (OUT / "shot-video-gui.json").write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_SHOT_VIDEO_GUI", json.dumps({"success": state["success"], "failed": sorted(
        name for name, passed in state["checks"].items() if not passed), "error": state.get("error", "")[-400:]}),
        flush=True)
    os._exit(0 if state["success"] else 1)


bpy.app.timers.register(tick, first_interval=1)
