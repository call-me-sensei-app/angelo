# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""TRELLIS 2 in Media Studio's 3D tab in the running app (1.0.8): a library image to a model in the scene.

Real default startup (Director), the native Studio canvas, clicks through Studio's own input handler (as
``test_blender_fal_video_gui``). A fixture fal account and a stand-in fal (``StudioJobs.transport``) answer the upload,
the submit, the status, the result and the download with a GLB this suite exports itself: no provider is called and
nothing is paid.

Covered, each with a screenshot: TRELLIS 2 is a model of the 3D tab on the fal account; a Media Studio image dropped on
Studio is its reference; the confirmation; exactly one submit to ``fal-ai/trellis-2`` carrying the target polygon
count, with the key only on fal's hosts; the finished 3D card; Import into the scene adds the model's objects, and one
Undo takes them back.

Run through ``run_native_suites.py --gui --include test_blender_fal_trellis_gui``; check ``fal-trellis-gui.json``.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-fal-trellis-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
for name, folder in (("ANGELO_STATE_DIR", "state"), ("ANGELO_CONNECTIONS_DIR", "accounts"), ("ANGELO_LIBRARY_DIR", "library")):
    os.environ.setdefault(name, str(OUT / folder))
from angelo_agent import product_env  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
import qa_operator  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')
REPORT = OUT / "fal-trellis-gui.json"
KEY = "qa-fixture-fal-key-not-a-real-account"
REQUEST = "019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b"
QUEUED = "https://queue.fal.run/fal-ai/trellis-2/requests/" + REQUEST
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


def export_glb():
    """A small textured-free GLB (a UV sphere) exported with Blender's glTF exporter from a scene of its own."""
    scene = bpy.data.scenes.new("QA trellis model")
    try:
        mesh = bpy.data.meshes.new("QA chair")
        import bmesh
        shape = bmesh.new()
        bmesh.ops.create_uvsphere(shape, u_segments=16, v_segments=8, radius=0.5)
        shape.to_mesh(mesh)
        shape.free()
        scene.collection.objects.link(bpy.data.objects.new("QA chair", mesh))
        with bpy.context.temp_override(window=main_window(), scene=scene):
            bpy.ops.export_scene.gltf(filepath=str(OUT / "trellis.glb"), export_format='GLB', use_active_scene=True)
    finally:
        for item in list(scene.collection.objects):
            bpy.data.objects.remove(item)
        bpy.data.scenes.remove(scene)
    return (OUT / "trellis.glb").read_bytes()


def fake_fal(glb):
    def transport(url, body=None, headers=None, service=False, method=None, accept=(200, 201), limit=None, timeout=None):
        verb = method or ("POST" if body is not None else "GET")
        sent = json.loads(body) if body and url.startswith("https://queue.fal.run/") else None
        state["calls"].append([verb, url, (headers or {}).get("Authorization", "")[:4], sent])
        if url.startswith("https://rest.fal.ai/storage/upload/initiate"):
            return json.dumps({"upload_url": "https://storage.example/u?sig=1", "file_url": "https://v3b.fal.media/files/f.png"}).encode()
        if url.startswith("https://storage.example/"):
            return b""
        if url.endswith("/status"):
            return json.dumps({"status": "COMPLETED", "request_id": REQUEST}).encode()
        if url == QUEUED:
            return json.dumps({"model_glb": {"url": "https://v3b.fal.media/files/out.glb"}}).encode()
        if url == "https://v3b.fal.media/files/out.glb":
            return glb
        if url.startswith("https://queue.fal.run/fal-ai/trellis-2"):
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
    from angelo_agent import fal_video, studio, ui
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
            state["frame"] = str(frame_png())
            studio._jobs.transport = fake_fal(export_glb())
            fal_video.POLL_SECONDS = 0.2
            with bpy.context.temp_override(window=main_window()):
                bpy.ops.angelo.open_media_studio(kind='3D')
            state["phase"] = "opened"
            return .5
        if phase == "opened":
            if not wait(phase, lambda: studio_area()[1] is not None):
                return .2
            redraw()
            click("model")
            capture("01-3d-models.png")
            choose((state["connection"], "trellis-2"))
            v = view()
            model = studio.selected(v)
            checks["trellis_is_a_3d_model_of_the_fal_account"] = bool(
                v["kind"] == "3D" and model and model["id"] == "trellis-2" and model["ready"])
            studio.attach(v, [state["frame"]])   # As dropping the image on Studio does.
            v["prompt"], v["polycount"] = "A round stool", "20000"
            checks["the_image_is_its_reference"] = len(v["references"]) == 1
            capture("02-ready-to-generate.png")
            click("generate")
            state["phase"] = "confirm"
            return 1.0
        if phase == "confirm":
            capture("03-confirmation.png", studio_area()[0])
            key(studio_area()[0], 'RET')
            state["phase"] = "generating"
            return .5
        if phase == "generating":
            library = studio.library()
            models = [a for a in library.assets.values() if a["kind"] == "3D" and a.get("model_id") == "trellis-2"]
            if not wait(phase, lambda: models and models[0]["status"] == "ready", limit=60):
                return .3
            asset = models[0]
            submits = [c for c in state["calls"] if c[0] == "POST" and c[1].startswith("https://queue.fal.run/")]
            checks["one_submit_to_trellis_with_the_polygon_target"] = [(c[1], c[3].get("decimation_target")) for c in submits] == [
                ("https://queue.fal.run/fal-ai/trellis-2", 20000)]
            checks["the_key_goes_only_to_fals_api"] = all(
                (c[2] == "Key ") == c[1].startswith(("https://queue.fal.run/", "https://rest.fal.ai/")) for c in state["calls"])
            checks["the_glb_is_saved"] = library.checked_file(asset, "model.glb").stat().st_size > 0
            capture("04-finished-card.png")
            before = {o.name for o in bpy.data.objects}
            view3d = next(a for a in main_window().screen.areas if a.type == 'VIEW_3D')
            here = dict(window=main_window(), area=view3d, region=next(r for r in view3d.regions if r.type == 'WINDOW'))
            # An operator a script calls pushes no undo step (a click does), so the steps around it are pushed here.
            with bpy.context.temp_override(**here):
                bpy.ops.ed.undo_push(message="Before import")
                bpy.ops.angelo.studio_import_3d('EXEC_DEFAULT', asset_id=asset["id"])
                bpy.ops.ed.undo_push(message="Import 3D asset")
            added = {o.name for o in bpy.data.objects} - before
            checks["import_adds_the_models_objects"] = bool(added)
            capture("05-in-the-scene.png", main_window())
            with bpy.context.temp_override(**here):
                bpy.ops.ed.undo()
            checks["one_undo_takes_them_back"] = not ({o.name for o in bpy.data.objects} - before)
            finish()
            return None
    except Exception as error:   # noqa: BLE001 - the receipt records it.
        finish(error)
        return None
    return .3


bpy.app.timers.register(tick, first_interval=1.0)
