# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Video tab in the running app (1.0.8): a fal video from a library frame to a Sequencer strip.

Real default startup (Director), the native Studio canvas; clicks go through Studio's own input handler at the drawn
hit rectangles and popover rows (as ``test_blender_studio_workflows`` does), the Generate confirmation is answered
with ``Window.event_simulate``. A fixture fal account and a stand-in fal (``StudioJobs.transport``) answer the upload,
the submit, the status reads, the result and the download with a clip this suite renders itself: no provider is
called and nothing is paid.

Covered, each with a screenshot: the chat bar's **+** menu has a Video row that opens Media Studio in its own window
on the Video tab and leaves Director's layout as it was; the tab strip is Image, 3D, Video, Workflows; the model is
the fal account's; a Media Studio image picked as the first frame (from the frame popover); the length menu; the price
line before Generate and the confirmation; exactly one submit to fal's queue for the image-to-video endpoint, with the
key only on fal's hosts; the finished card with its first-frame picture; the right-click menu's Add to Sequencer puts
a movie strip in the scene, and one Undo takes it back.

Run through ``run_native_suites.py --gui --include test_blender_fal_video_gui``; check ``fal-video-gui.json`` for
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-fal-video-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
for name, folder in (("ANGELO_STATE_DIR", "state"), ("ANGELO_CONNECTIONS_DIR", "accounts"), ("ANGELO_LIBRARY_DIR", "library")):
    os.environ.setdefault(name, str(OUT / folder))   # run_native_suites sets them before the add-on starts.
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402
import qa_operator  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')
REPORT = OUT / "fal-video-gui.json"
KEY = "qa-fixture-fal-key-not-a-real-account"
REQUEST = "019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b"
QUEUED = "https://queue.fal.run/bytedance/seedance-2.0/requests/" + REQUEST
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "calls": [],
         "fixture": "A fixture fal account and a stand-in fal only; no provider call, nothing paid"}
checks = state["checks"]
started = time.monotonic()
phase_started = {}


def log(*parts):
    print("[fal-video-gui]", *parts, flush=True)


def wait(phase, ready, limit=20.0):
    phase_started.setdefault(phase, time.monotonic())
    if ready():
        return True
    if time.monotonic() - phase_started[phase] > limit:
        raise TimeoutError("timed out in " + phase)
    return False


def main_window():
    return bpy.context.window_manager.windows[0]


def studio_area():
    from angelo_agent import studio_toggle
    target = studio_toggle.opened_window(bpy.context.window_manager.windows)
    return target, (target.screen.areas[0] if target else None)


def region():
    return next(r for r in studio_area()[1].regions if r.type == 'WINDOW')


def view():
    from angelo_agent import studio
    return studio.view(studio_area()[1])


def redraw():
    from angelo_agent import studio
    target, area = studio_area()
    with bpy.context.temp_override(window=target, area=area, region=region()):
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    assert studio.last_error is None, studio.last_error


def capture(name, target=None):
    path = OUT / name
    if target is None:
        redraw()
    with bpy.context.temp_override(window=target or studio_area()[0]):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def press(x, y, kind='LEFTMOUSE'):
    """A real press at region coordinates, through Studio's own input handler."""
    from angelo_agent import studio
    target, area = studio_area()
    r = region()
    event = SimpleNamespace(type=kind, value='PRESS', mouse_x=r.x + x, mouse_y=r.y + y, mouse_prev_y=r.y + y,
                            unicode="", ctrl=False, oskey=False, alt=False, shift=False)
    handler = qa_operator.stand_in(studio.ANGELO_OT_studio_input, window_pointer=target.as_pointer(), focus_area=None,
                                   dragging=False)
    with bpy.context.temp_override(window=target, area=area, region=r):
        studio.ANGELO_OT_studio_input.modal(handler, bpy.context, event)


def click(action, value=None):
    redraw()
    rect = next(r for r, a, v in view().get("hits", []) if a == action and (value is None or v == value))
    press(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)


def choose(value):
    """Click a row of the open popover."""
    redraw()
    rect = next(r for r, v in view().get("popup_hits", []) if v == value)
    press(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)


def key(target, kind):
    for value in ('PRESS', 'RELEASE'):
        target.event_simulate(type=kind, value=value, x=target.width // 2, y=target.height // 2)


def frame_png():
    """A 640x360 frame: a teal ground and a warm disc, so the card's picture is recognisable."""
    height, width = 360, 640
    yy, xx = np.mgrid[0:height, 0:width]
    rgba = np.zeros((height, width, 4), np.float32)
    rgba[...] = (0.12, 0.45, 0.42, 1.0)
    rgba[((xx - 320) ** 2 + (yy - 180) ** 2) < 90 ** 2] = (0.95, 0.62, 0.25, 1.0)
    image = bpy.data.images.new("QA frame", width, height)
    image.pixels.foreach_set(rgba[::-1].ravel())
    path = OUT / "frame.png"
    image.filepath_raw, image.file_format = str(path), 'PNG'
    image.save()
    bpy.data.images.remove(image)
    return path


def render_clip():
    """A 24-frame 320x180 H.264 clip rendered by Workbench in a scene of its own, removed after."""
    scene = bpy.data.scenes.new("QA fal clip")
    try:
        scene.frame_start, scene.frame_end = 1, 24
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 180, 100
        scene.render.image_settings.media_type = 'VIDEO'
        scene.render.image_settings.file_format = 'FFMPEG'
        scene.render.ffmpeg.format, scene.render.ffmpeg.codec = 'MPEG4', 'H264'
        scene.render.filepath = str(OUT / "clip.mp4")
        camera = bpy.data.objects.new("QA camera", bpy.data.cameras.new("QA camera"))
        camera.location, camera.rotation_euler = (0, -6, 0), (1.5708, 0, 0)
        scene.collection.objects.link(camera)
        scene.camera = camera
        import bmesh   # A warm sphere on a teal ground: the card's picture shows the clip, not black.
        mesh, shape = bpy.data.meshes.new("QA ball"), bmesh.new()
        bmesh.ops.create_uvsphere(shape, u_segments=24, v_segments=12, radius=1.2)
        shape.to_mesh(mesh)
        shape.free()
        ball = bpy.data.objects.new("QA ball", mesh)
        ball.color = (0.95, 0.62, 0.25, 1.0)
        scene.collection.objects.link(ball)
        scene.display.shading.light, scene.display.shading.color_type = 'STUDIO', 'OBJECT'
        scene.display.shading.background_type, scene.display.shading.background_color = 'VIEWPORT', (0.12, 0.45, 0.42)
        with bpy.context.temp_override(window=main_window(), scene=scene):
            bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        for item in list(scene.collection.objects):
            bpy.data.objects.remove(item)
        bpy.data.scenes.remove(scene)
    return (OUT / "clip.mp4").read_bytes()


def fake_fal(clip):
    def transport(url, body=None, headers=None, service=False, method=None, accept=(200, 201), limit=None, timeout=None):
        verb = method or ("POST" if body is not None else "GET")
        state["calls"].append([verb, url, (headers or {}).get("Authorization", "")[:4]])
        if url.startswith("https://rest.fal.ai/storage/upload/initiate"):
            return json.dumps({"upload_url": "https://storage.example/u?sig=1", "file_url": "https://v3b.fal.media/files/f.png"}).encode()
        if url.startswith("https://storage.example/"):
            return b""
        if url.endswith("/status"):
            return json.dumps({"status": "COMPLETED", "request_id": REQUEST}).encode()
        if url == QUEUED:
            return json.dumps({"video": {"url": "https://v3b.fal.media/files/out.mp4"}}).encode()
        if url == "https://v3b.fal.media/files/out.mp4":
            return clip
        if url.startswith("https://queue.fal.run/bytedance/seedance-2.0/"):
            return json.dumps({"request_id": REQUEST, "status": "IN_QUEUE", "status_url": QUEUED + "/status",
                               "response_url": QUEUED, "cancel_url": QUEUED + "/cancel"}).encode()
        raise AssertionError("unexpected request " + url)
    return transport


def finish(error=None):
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


def tick():
    from angelo_agent import chat_destinations, fal_video, studio, studio_video_ui, ui
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
            record = ui.connections().add("FAL", "API_KEY", "QA fal", group="GENAI")
            ui._memory_keys[record["id"]] = KEY
            ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS["fal_api"]["models"])
            state["connection"] = record["id"]
            library = studio.library()
            image = library.import_image(str(frame_png()))
            library.update(dict(image, prompt="QA frame: a warm disc on teal"))
            state["image"] = image["id"]
            studio._jobs.transport = fake_fal(render_clip())
            fal_video.POLL_SECONDS = 0.2
            row = chat_destinations.feature("video")
            checks["the_plus_menu_has_a_video_row_opening_the_video_tab"] = bool(row) and (
                row.operator, dict(row.properties)) == ("angelo.open_media_studio", {"kind": "VIDEO"})
            state["layout"] = sorted((a.type, a.width, a.height) for a in main_window().screen.areas)
            with bpy.context.temp_override(window=main_window()):
                bpy.ops.angelo.open_media_studio(kind='VIDEO')   # What the + ▸ Video row runs.
            state["phase"] = "opened"
            return .5
        if phase == "opened":
            if not wait(phase, lambda: studio_area()[1] is not None):
                return .2
            v = view()
            redraw()
            checks["video_opens_studio_in_its_own_window_on_the_tab"] = v["kind"] == "VIDEO"
            checks["directors_layout_is_unchanged"] = sorted(
                (a.type, a.width, a.height) for a in main_window().screen.areas) == state["layout"]
            checks["the_tabs_are_image_3d_video_workflows"] = [value for _, a, value in v["hits"] if a == "tab"] == [
                "IMAGE", "3D", "VIDEO", "WORKFLOW"]
            model = studio.selected(v)
            checks["the_model_is_the_fal_accounts"] = bool(model and model["ready"] and model["connection"] == state["connection"])
            from angelo_agent import studio_draw, studio_state
            sizes = {"video_aspect": 10, "video_resolution": 11}   # studio_video_ui.settings' chip text sizes
            chips = [(r, a, value) for r, a, value in v["hits"] if a in sizes]
            checks["resolution_and_aspect_chips_show_their_labels_whole"] = bool(chips) and all(
                r[2] >= studio_draw.canvas.measure(value, sizes[a]) + studio_state.BUTTON_PAD - 1 for r, a, value in chips)
            capture("01-video-tab.png")
            click("model")   # The model menu: the same model on fal and BytePlus, each with its provider and price.
            redraw()
            notes = {e["description"] for e in view()["popover"]["entries"] if e["label"] == "Seedance 2.0 Fast"}
            state["model_notes"] = sorted(notes)
            checks["the_model_menu_offers_seedance_on_fal_and_byteplus_with_prices"] = len(notes) == 2 and all(
                "/s at" in n for n in notes) and any(n.startswith("BytePlus") for n in notes)
            capture("01b-model-menu.png")
            choose((model["connection"], model["id"]))
            click("video_frame", "video_first")
            capture("02-frame-popover.png")
            choose(state["image"])
            redraw()
            checks["a_studio_image_is_the_first_frame"] = bool(view().get("video_first"))
            click("video_duration_menu")
            choose(4)
            v = view()
            v["prompt"] = "The disc rolls slowly to the right; the camera follows"
            line = studio_video_ui.price_line(v, studio.selected(v))
            state["price_line"] = line
            checks["the_price_shows_before_generate"] = "≈ $" in line and "4 s" in line
            capture("03-ready-to-generate.png")
            click("generate")
            state["phase"] = "confirm"
            return 1.0
        if phase == "confirm":
            capture("04-confirmation.png", studio_area()[0])
            key(studio_area()[0], 'RET')
            state["phase"] = "generating"
            return .5
        if phase == "generating":
            library = studio.library()
            videos = [a for a in library.assets.values() if a["kind"] == "VIDEO"]
            if not wait(phase, lambda: videos and videos[0]["status"] == "ready" and videos[0].get("thumbnail"), limit=60):
                return .3
            asset = videos[0]
            state["asset"] = asset
            submits = [c for c in state["calls"] if c[0] == "POST" and c[1].startswith("https://queue.fal.run/")]
            checks["one_submit_to_the_image_to_video_endpoint"] = [c[1] for c in submits] == [
                "https://queue.fal.run/bytedance/seedance-2.0/fast/image-to-video"]
            checks["the_key_goes_only_to_fals_api"] = all(
                (c[2] == "Key ") == c[1].startswith(("https://queue.fal.run/", "https://rest.fal.ai/")) for c in state["calls"])
            checks["the_video_and_its_picture_are_saved"] = (library.checked_file(asset, "video.mp4").stat().st_size > 0
                                                             and asset["thumbnail"] == "preview.png")
            checks["the_price_is_recorded"] = isinstance(asset.get("price"), float)
            checks["generate_clears_the_frame_notice"] = not view().get("notice")
            capture("05-finished-card.png")
            scene = bpy.context.window_manager.windows[0].scene
            before = len(scene.sequence_editor.strips_all) if scene.sequence_editor else 0
            view3d = next(a for a in main_window().screen.areas if a.type == 'VIEW_3D')
            here = dict(window=main_window(), area=view3d, region=next(r for r in view3d.regions if r.type == 'WINDOW'))
            # An operator a script calls pushes no undo step (a menu click does), so the steps around it are pushed here.
            with bpy.context.temp_override(**here):
                bpy.ops.ed.undo_push(message="Before Add to Sequencer")
                bpy.ops.angelo.studio_video(action='SEQUENCER', item_ids=asset["id"])
                bpy.ops.ed.undo_push(message="Add to Sequencer")
            strips = list(scene.sequence_editor.strips_all)
            checks["add_to_sequencer_adds_a_movie_strip"] = any(s.type == 'MOVIE' for s in strips) and len(strips) > before
            with bpy.context.temp_override(**here):
                bpy.ops.ed.undo()
            scene = bpy.context.window_manager.windows[0].scene
            checks["one_undo_takes_it_back"] = (len(scene.sequence_editor.strips_all) if scene.sequence_editor else 0) == before
            state["phase"] = "languages"
            return .3
        if phase == "languages":   # Dogfood B-006: translated labels are never cut (studio_state.button_width).
            from angelo_agent import chat_view as canvas
            from angelo_agent.studio_state import BUTTON_PAD, ICON_ROOM
            view_prefs = bpy.context.preferences.view
            original = (view_prefs.language, view_prefs.use_translate_interface)
            fits = {}
            try:
                for language in ("ja_JP", "de_DE"):
                    view_prefs.language, view_prefs.use_translate_interface = language, True
                    redraw()
                    v = view()
                    for rect, action, value in v["hits"]:
                        if action in {"tab", "collapse", "import"}:
                            from angelo_agent.ui_text import iface_
                            label = {"tab": dict(studio.TABS).get(value, ""), "import": "Import"}.get(
                                action, "Create" if v["collapsed"] else "Hide Create")
                            size = 11 if action == "import" else 12
                            room = rect[2] - BUTTON_PAD - (ICON_ROOM if action == "import" or (action == "collapse" and v["collapsed"]) else 0)
                            fits[f"{language}:{action}:{value}"] = canvas.measure(iface_(label), size) <= room + 0.5
                    capture(f"06-{language}.png")
            finally:
                view_prefs.language, view_prefs.use_translate_interface = original
            state["label_fits"] = fits
            checks["translated_top_row_and_import_labels_fit"] = bool(fits) and all(fits.values())
            finish()
            return None
    except Exception as error:   # noqa: BLE001 - the receipt records it.
        finish(error)
        return None
    return .3


bpy.app.timers.register(tick, first_interval=1.0)
