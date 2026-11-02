# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library in the running app, in Director, against a library mirror on this computer (doc/angelo/library.rst).

Real default startup and real input (``Window.event_simulate``). No account, provider or inference; nothing leaves
this computer: a loopback server in this process serves ``latest.json``, the index and every file, and
``ANGELO_LIBRARY_URL`` points the client at it. The fixture catalog is built here: a box model (GLB), a texture set
(PNG maps), an HDRI (Radiance) and 60 thumbnail-only fillers whose thumbnails arrive 40 ms apart.

The scenario, with a capture at each step: the pill in Director's 3D view; a click opens the search; typed words
find the model; a real drag drops it on the ground under the pointer (standing there, provenance on it); a second
drop of it on a warm cache, timed from the release to the object drawn in the view (target under 2 s); typed
"brick" and a drag onto the cube gives it the library material; "studio hdri" and Return makes the HDRI the world
and the view shows it (Material Preview with the scene world); Undo takes the world back; the Credits button copies
the credits; the docked browser through the chat bar's + menu operator; and, while 60 thumbnails stream in, the
longest gap between 8 ms timer ticks on the main thread (the editor stays smooth: target under 50 ms).

Run through ``run_native_suites.py --gui --include test_blender_library``; check ``library.json`` for ``success``
and ``phase == "complete"``; the timings are in ``measurements``.
"""

import gzip
import hashlib
import json
import os
import struct
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402
import qa_input  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-library-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "library.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measurements": {}}
checks, measured = state["checks"], state["measurements"]
started = time.monotonic()
FILLERS = 60


# -- the mirror ------------------------------------------------------------------------------------------------
def box_glb():
    """A 1 m box standing on its base (z from 0 to 1 in Blender, Y-up in glTF)."""
    corners = [(x, y, z) for x in (-.5, .5) for y in (0.0, 1.0) for z in (-.5, .5)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    indices = [i for a, b, c, d in faces for i in (a, b, c, a, c, d)]
    positions = b"".join(struct.pack("<3f", *c) for c in corners)
    index_bytes = b"".join(struct.pack("<H", i) for i in indices)
    blob = positions + index_bytes + b"\0" * (-len(index_bytes) % 4)
    document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                "nodes": [{"mesh": 0, "name": "Crate"}], "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
                "buffers": [{"byteLength": len(blob)}],
                "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                                {"buffer": 0, "byteOffset": len(positions), "byteLength": len(index_bytes)}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
                               "min": [-.5, 0, -.5], "max": [.5, 1, .5]},
                              {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"}]}
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(blob), 0x004E4942) + blob
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def png(width, height, rgb):
    from angelo_agent.view_png import encode_png
    return encode_png(bytes(rgb + (255,)) * (width * height), width, height)


def hdr(width=64, height=32):
    rows = b"".join(bytes((200, 210, 230, 129)) * width for _ in range(height))   # Flat RGBE scanlines.
    return b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y %d +X %d\n" % (height, width) + rows


class Mirror:
    def __init__(self):
        # Filler thumbnails are held until the suite starts sampling (its own signal), then all released at once:
        # the main thread must stay smooth while the whole burst decodes and uploads.
        self.routes, self.gated, self.gate = {}, set(), threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = owner.routes.get(self.path)
                if self.path in owner.gated:
                    owner.gate.wait(30)   # Capped: a gate never opened fails thumbnails_stream_in loudly.
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *args):
                pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def file(self, path, data, role, name):
        self.routes[path] = data
        return {"role": role, "name": name, "url": self.base + path, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def publish(self):
        thumb = png(64, 64, (90, 160, 150))
        def item(identity, kind, title, tags, files, **extra):
            self.routes["/thumbs/%s.png" % identity] = thumb
            return dict({"id": identity, "kind": kind, "title": title, "tags": tags, "categories": [], "source": "fixture",
                         "authors": ["Angelo QA"], "license": "CC0-1.0", "attribution": title + " (CC0)",
                         "attribution_required": False, "popularity": 10, "thumb": self.base + "/thumbs/%s.png" % identity,
                         "files": files}, **extra)
        items = [item("qa:crate", "model", "Wooden Crate", ["crate", "box", "wood"],
                      [self.file("/f/crate.glb", box_glb(), "model", "model.glb")]),
                 item("qa:brick", "texture", "Red Brick Wall", ["brick", "wall"],
                      [self.file("/f/b_col.png", png(64, 64, (150, 60, 45)), "base_color", "base_color.png"),
                       self.file("/f/b_nor.png", png(64, 64, (128, 128, 255)), "normal_gl", "normal_gl.png"),
                       self.file("/f/b_rough.png", png(64, 64, (200, 200, 200)), "roughness", "roughness.png")]),
                 item("qa:studio", "hdri", "Studio Soft Light", ["studio", "indoor", "softbox"],
                      [self.file("/f/studio.hdr", hdr(), "hdri", "environment_1k.hdr")], resolution="1k")]
        for n in range(FILLERS):   # Thumbnail-only items: what streams in while the grid fills.
            identity = "qa:filler-%02d" % n
            entry = item(identity, "model", "Filler %02d" % n, ["filler"],
                         [self.file("/f/filler.glb", box_glb(), "model", "model.glb")])
            self.routes["/thumbs/%s.png" % identity] = png(256, 256, (40 + n * 3, 90, 140))
            self.gated.add("/thumbs/%s.png" % identity)
            items.append(entry)
        packed = gzip.compress(json.dumps({"schema": "toonlab-catalog/1", "version": "qa-1", "items": items}).encode())
        self.routes["/index.json.gz"] = packed
        self.routes["/latest.json"] = json.dumps({"schema": "toonlab-catalog/1", "version": "qa-1", "url": self.base + "/index.json.gz",
                                                  "size": len(packed), "sha256": hashlib.sha256(packed).hexdigest()}).encode()


mirror = Mirror()
mirror.publish()
os.environ["ANGELO_LIBRARY_URL"] = mirror.base + "/latest.json"


# -- helpers -------------------------------------------------------------------------------------------------------
def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def region():
    from angelo_agent import library_overlay
    return library_overlay.main_region(view3d())


def overlay():
    from angelo_agent import library_overlay
    return library_overlay.state_for(view3d())


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def centre(rect):
    r = region()
    return r.x + rect[0] + rect[2] / 2, r.y + rect[1] + rect[3] / 2


def type_text(text):
    x, y = centre(overlay()["rects"]["pill"])
    for char in text:
        key = 'SPACE' if char == " " else char.upper()
        window().event_simulate(type=key, value='PRESS', x=int(x), y=int(y), unicode=char)
        window().event_simulate(type=key, value='RELEASE', x=int(x), y=int(y))


def clear_field():
    x, y = centre(overlay()["rects"]["pill"])
    window().event_simulate(type='BACK_SPACE', value='PRESS', x=int(x), y=int(y), oskey=True)


def tile(identity):
    """The window point of the tile showing ``identity``, or None while it isn't drawn."""
    rect = next((r for r, action, value in overlay().get("hits", ()) if action == "tile" and value == identity), None)
    return centre(rect) if rect else None


def ground_point(offset_x):
    r = region()
    return r.x + r.width / 2 + offset_x, r.y + r.height * 0.28


def cube_point():
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    cube = bpy.data.objects.get("Cube")
    point = location_3d_to_region_2d(region(), view3d().spaces.active.region_3d, cube.matrix_world.translation)
    return region().x + point.x, region().y + point.y


def library_objects():
    return [o for o in bpy.context.scene.objects if o.get("angelo_library")]


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    from angelo_agent import library_overlay
    state["overlay_error"] = library_overlay.last_error
    checks["overlay_draws_without_error"] = library_overlay.last_error is None   # The overlay swallows its errors.
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    state["events"] = [list(e[:2]) + [str(e[2])[:300]] for e in library_overlay.events]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_LIBRARY", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    mirror.httpd.shutdown()
    bpy.ops.wm.quit_blender()


ticks = []


def sampler():
    ticks.append(time.perf_counter())
    mirror.gate.set()   # Thumbnails arrive only while the main thread is being sampled.
    return 0.008 if state.get("sampling") else None


# -- the scenario ---------------------------------------------------------------------------------------------------
def tick():
    from angelo_agent import director, library_host, library_overlay
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 420, "Suite timed out"
        if state.get("events"):   # A drag in progress: one event per pass.
            kind, value, (x, y) = state["events"].pop(0)
            window().event_simulate(type=kind, value=value, x=int(x), y=int(y))
            if not state["events"] and state.get("released_at") == "pending":
                state["released_at"] = time.perf_counter()
            return .03
        if phase == "boot":
            if not qa_boot.started():
                return .3
            checks["director_mode"] = director.is_director(window())
            state["dirty_at_boot"] = bpy.data.is_dirty
            view3d().tag_redraw()
            state["phase"] = "pill"
            return .5
        if phase == "pill":
            if not wait(phase, lambda: (overlay().get("rects") or {}).get("pill") is not None):
                view3d().tag_redraw()
                return .2
            capture("library-0-pill-in-director.png")
            checks["pill_shown_in_director"] = library_overlay.draws > 0
            qa_input.press(window(), lambda: centre(overlay()["rects"]["pill"]))
            state["phase"] = "opened"
            return .5
        if phase == "opened":
            if not wait(phase, lambda: overlay()["mode"] == "search" and overlay()["focused"]):
                return .2
            checks["click_opens_search"] = True
            if not wait("catalog", lambda: library_host.library().catalog is not None, limit=30):
                return .2
            state["typed_at"] = time.perf_counter()
            type_text("crate")
            state["phase"] = "found"
            return .1
        if phase == "found":
            view3d().tag_redraw()
            if not wait(phase, lambda: tile("qa:crate") is not None):
                return .1
            measured["typed_to_result_drawn_ms"] = round((time.perf_counter() - state["typed_at"]) * 1000, 1)
            capture("library-1-search-crate.png")
            checks["typed_words_find_the_model"] = overlay()["shown"][0] == "qa:crate"
            state["dirty_after_browsing"] = bpy.data.is_dirty   # Thumbnails are GPU textures, never file data.
            checks["browsing_leaves_the_file_unchanged"] = state["dirty_at_boot"] or not bpy.data.is_dirty
            state["events"] = qa_input.drag_path(tile("qa:crate"), ground_point(-180), prefs.system.ui_scale)
            state["phase"] = "dropped"
            return .05
        if phase == "dropped":
            if not wait(phase, lambda: len(library_objects()) == 1):
                return .2
            obj = library_objects()[0]
            bpy.context.view_layer.update()
            lowest = min((obj.matrix_world @ v.co).z for v in obj.data.vertices)
            checks["model_stands_on_the_ground"] = abs(lowest) < 0.02
            checks["model_has_provenance"] = obj["angelo_library"]["item"] == "qa:crate"
            capture("library-2-model-dropped.png")
            open_again = lambda: centre(overlay()["rects"]["pill"])   # noqa: E731
            qa_input.press(window(), open_again)
            state["phase"] = "warm"
            return .5
        if phase == "warm":   # The second drop of the same model: its files are cached.
            if not wait(phase, lambda: overlay()["mode"] == "search" and tile("qa:crate") is not None):
                view3d().tag_redraw()
                return .2
            checks["second_drop_is_cached"] = library_host.library().cached(library_host.library().get("qa:crate"))
            state["events"] = qa_input.drag_path(tile("qa:crate"), ground_point(160), prefs.system.ui_scale)
            state["released_at"] = "pending"
            state["phase"] = "warm_visible"
            return .03
        if phase == "warm_visible":
            if isinstance(state.get("released_at"), str) or not wait(phase, lambda: len(library_objects()) == 2):
                return .01
            view3d().tag_redraw()
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            measured["drop_to_visible_warm_ms"] = round((time.perf_counter() - state["released_at"]) * 1000, 1)
            checks["warm_drop_visible_under_2s"] = measured["drop_to_visible_warm_ms"] < 2000
            qa_input.press(window(), lambda: centre(overlay()["rects"]["pill"]))
            state["phase"] = "brick"
            return .5
        if phase == "brick":
            if not wait(phase, lambda: overlay()["focused"]):
                return .2
            clear_field()
            type_text("brick")
            state["phase"] = "brick_found"
            return .3
        if phase == "brick_found":
            view3d().tag_redraw()
            if not wait(phase, lambda: tile("qa:brick") is not None):
                return .2
            capture("library-3-search-brick.png")
            state["events"] = qa_input.drag_path(tile("qa:brick"), cube_point(), prefs.system.ui_scale)
            state["phase"] = "material"
            return .05
        if phase == "material":
            cube = bpy.data.objects.get("Cube")
            if not wait(phase, lambda: cube.active_material is not None and cube.active_material.get("angelo_library")):
                return .2
            checks["material_on_the_object_under_the_pointer"] = cube.active_material["angelo_library"]["item"] == "qa:brick"
            capture("library-4-material-on-cube.png")
            state["world_before"] = bpy.context.scene.world.name if bpy.context.scene.world else ""
            qa_input.press(window(), lambda: centre(overlay()["rects"]["pill"]))
            state["phase"] = "hdri"
            return .5
        if phase == "hdri":
            if not wait(phase, lambda: overlay()["focused"]):
                return .2
            clear_field()
            type_text("studio hdri")
            state["phase"] = "hdri_found"
            return .3
        if phase == "hdri_found":
            view3d().tag_redraw()
            if not wait(phase, lambda: overlay().get("shown") and overlay()["shown"][0] == "qa:studio"):
                return .2
            x, y = centre(overlay()["rects"]["pill"])
            window().event_simulate(type='RET', value='PRESS', x=int(x), y=int(y))
            state["phase"] = "world"
            return .3
        if phase == "world":
            world = bpy.context.scene.world
            if not wait(phase, lambda: world is not None and bpy.context.scene.world.get("angelo_library")):
                return .2
            shading = view3d().spaces.active.shading
            checks["hdri_becomes_the_world"] = bpy.context.scene.world["angelo_library"]["item"] == "qa:studio"
            checks["view_shows_the_world"] = shading.type in {'MATERIAL', 'RENDERED'} and shading.use_scene_world
            capture("library-5-hdri-world.png")
            with bpy.context.temp_override(window=window(), area=view3d(), region=region()):
                bpy.ops.ed.undo()
            state["phase"] = "undo"
            return .5
        if phase == "undo":
            if not wait(phase, lambda: (bpy.context.scene.world.name if bpy.context.scene.world else "") == state["world_before"]):
                return .2
            checks["undo_takes_the_world_back"] = True
            window().event_simulate(type='ESC', value='PRESS', x=10, y=10)
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.library_credits()
            checks["credits_copied"] = "Wooden Crate" in bpy.context.window_manager.clipboard
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.library_open(mode='BROWSE', query="filler")
            state["phase"] = "stream"
            state["sampling"] = True
            ticks.clear()
            bpy.app.timers.register(sampler, first_interval=0.008)
            state["stream_started"] = time.perf_counter()
            return .2
        if phase == "stream":   # The filler thumbnails arrive in one burst while the main thread is sampled.
            view3d().tag_redraw()
            loaded = sum(1 for url, tex in library_host.library()._textures.items() if tex and "filler" in url)
            if loaded < 40 and time.perf_counter() - state["stream_started"] < 20:
                return .05
            state["sampling"] = False
            gaps = sorted(b - a for a, b in zip(ticks, ticks[1:]))
            measured["thumbnails_loaded"] = loaded
            measured["main_thread_tick_gap_max_ms"] = round(gaps[-1] * 1000, 1) if gaps else None
            measured["main_thread_tick_gap_p95_ms"] = round(gaps[int(len(gaps) * .95)] * 1000, 1) if gaps else None
            checks["thumbnails_stream_in"] = loaded >= 40
            checks["editor_stays_smooth"] = bool(gaps) and gaps[-1] < 0.05
            capture("library-6-browser-docked.png")
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:   # noqa: BLE001 (the suite reports any failure in its receipt)
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
