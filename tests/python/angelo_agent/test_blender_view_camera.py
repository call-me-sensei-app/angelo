# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""angelo_view's free cameras and reference pairs in the compiled app (G09): pixels, read-only state, memory.

Run by ``run_native_suites.py --gui --include test_blender_view_camera`` (offscreen drawing needs a window).
A free camera item {location, look_at, lens | ortho_scale} is drawn from matrices, so it must give the picture
an equal scene camera gives: for a perspective, an orthographic and a straight-down camera, each pair of
pictures is compared pixel by pixel (SAME_PICTURE, OUTLIERS). A reference pair puts an attached image (PNG or
JPEG, landscape or portrait) on the left and the view at the image's aspect on the right; with the reference
made through the scene camera, the right half equals the left. Every call is wrapped in a full snapshot of the
document, the person's viewports and the data-block inventory, plus a depsgraph-update counter and the
project's own SceneCheck: none may change. Motion blur keeps its fresh offscreen per draw, a strip holds a free
camera and puts the frame back, WEIGHT pairs, a camera facing nothing says so, bad references are VALIDATION
refusals, and seventy-five six-pair calls at 1024 do not keep growing the process. No provider, account or network.
Inspect results.json; a zero exit status alone is not proof.
"""

import inspect
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import traceback
import zlib

import bpy
import numpy as np

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-camera-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # Its sibling qa_machine (Blender adds no suite folder).
from angelo_agent import scene_queries, view_cache, view_scene_check  # noqa: E402
from angelo_agent.action_camera_light_spec import look_at_euler  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.refusal_codes import refusal_code  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.view_pixels import encode_rgba_png  # noqa: E402
import rendered_view_fixtures as fx  # noqa: E402

report = {"checks": {}, "measurements": {}, "binary": bpy.app.binary_path}
ctx = {"snapshots": [], "depsgraph_updates": 0}
# Mean absolute difference (8-bit levels, RGB) under which a free camera's picture equals the scene camera's, and
# the share of pixels that may differ by more than OUTLIER_LEVELS (edges rasterized a hair apart: the scene camera's
# matrix is single precision, the free camera's double, and their clip planes differ). Measured on this fixture:
# see report["measurements"]["equal_cameras"].
SAME_PICTURE = 0.5
OUTLIER_LEVELS = 48
OUTLIERS = 0.002
PERSPECTIVE = {"location": [7.5, -6.5, 4.5], "look_at": [0.0, 0.0, 0.6], "lens": 35.0}
ORTHOGRAPHIC = {"location": [-6.0, -9.0, 5.0], "look_at": [0.5, 0.5, 0.5], "ortho_scale": 11.0}
STRAIGHT_DOWN = {"location": [0.4, 0.3, 18.0], "look_at": [0.4, 0.3, 0.0], "lens": 30.0}
# Six-pair LIT calls at 1024, one per tick. A leaked offscreen (about 5 MB at 1024x576) or a pair's picture
# buffers (about 7 MB) would grow the process by 5 MB or more per call, over 200 MB from call LEAK_WARM to
# LEAK_CALLS. What does grow is allocator working size, in steps: measured over 150 calls in a lighter scene,
# +9 to +11 MB from call 30 to 150 for six pairs (blur on or off), +2.2 MB for base code's three LIT views; this
# fixture (blur on, more objects) measured +16 MB from call 30 to 75.
LEAK_WARM, LEAK_CALLS, LEAK_MB = 30, 75, 40


def check(name, condition):
    report["checks"][name] = bool(condition)


def decode(path):
    """RGB rows of the encoders' own filter-0 PNGs (no Blender Image needed)."""
    data, offset, idat, size, channels = Path(path).read_bytes(), 8, b"", None, 3
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            size, channels = struct.unpack(">II", body[:8]), {2: 3, 6: 4}[body[9]]
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    width, height = size
    raw = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, width * channels + 1)
    assert not raw[:, 0].any(), "unexpected PNG filter"
    return raw[:, 1:].reshape(height, width, channels)[:, :, :3].astype(np.int16)


def difference(first, second):
    delta = np.abs(first - second)
    return {"mean": round(float(delta.mean()), 4),
            "outliers": round(float((delta.max(axis=2) > OUTLIER_LEVELS).mean()), 5)}


def same(measure):
    return measure["mean"] <= SAME_PICTURE and measure["outliers"] <= OUTLIERS


# ---- The full snapshot every view call is wrapped in ----------------------------------------------------------

def _plain(value):
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, bpy.types.ID):
        return "ID:" + value.name
    try:
        return [_plain(item) for item in value]
    except TypeError:
        return repr(value)


def _rna(struct_value):
    """Every non-pointer, non-collection RNA property of a struct."""
    values = {}
    for prop in struct_value.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.type in ("POINTER", "COLLECTION"):
            continue
        try:
            values[prop.identifier] = _plain(getattr(struct_value, prop.identifier))
        except Exception as error:  # Some properties are context-only; record that they could not be read.
            values[prop.identifier] = "unreadable: " + type(error).__name__
    return values


def snapshot():
    window = ctx["window"]
    scene, layer = window.scene, window.view_layer
    data = {}
    for prop in bpy.data.bl_rna.properties:
        if prop.type == "COLLECTION" and prop.identifier not in ("window_managers", "workspaces", "screens"):
            data[prop.identifier] = sorted((item.name, item.session_uid) for item in getattr(bpy.data, prop.identifier))
    objects = {obj.name: {"matrix_world": _plain(obj.matrix_world), "transform": _plain(
        (obj.location, obj.rotation_euler, obj.scale)), "hide": (obj.hide_viewport, obj.hide_render,
        obj.hide_get(view_layer=layer)), "select": obj.select_get(view_layer=layer), "data": _plain(obj.data),
        "parent": _plain(obj.parent), "mode": obj.mode} for obj in scene.objects}
    cameras = {camera.name: _rna(camera) for camera in bpy.data.cameras}
    screens = []
    for each in bpy.context.window_manager.windows:
        for area in each.screen.areas:
            entry = {"type": area.type, "ui_type": area.ui_type, "size": (area.width, area.height)}
            if area.type == "VIEW_3D":
                space = area.spaces.active
                entry.update(space=_rna(space), shading=_rna(space.shading), overlay=_rna(space.overlay),
                             regions=[_rna(region) for region in (tuple(space.region_quadviews) or (space.region_3d,))],
                             camera=_plain(space.camera))
            screens.append(entry)
    return {"data": data, "objects": objects, "cameras": cameras, "screens": screens, "scene": _rna(scene),
            "render": _rna(scene.render), "view_settings": _rna(scene.view_settings),
            "display_settings": _rna(scene.display_settings), "scene_display": _rna(scene.display),
            "camera": _plain(scene.camera), "world": _plain(scene.world),
            "frame": (scene.frame_current, scene.frame_subframe), "active": _plain(layer.objects.active),
            "selected": sorted(obj.name for obj in layer.objects if obj.select_get(view_layer=layer)),
            "mode": bpy.context.mode, "dirty": bpy.data.is_dirty, "layer": _rna(layer)}


def count_updates(_scene, _depsgraph):
    ctx["depsgraph_updates"] += 1


def view(args, *, label):
    """Generator: one angelo_view call wrapped in the snapshot, SceneCheck and the depsgraph-update counter. A look
    whose shaders are still compiling is pending (``view_warm``): it is polled through the event loop, as
    ``Controller._pump_events`` does, and the wrap covers the wait too."""
    window = ctx["window"]
    before, updates = snapshot(), ctx["depsgraph_updates"]
    scene_check = view_scene_check.SceneCheck(window, deep=True)
    try:
        result = ctx["executor"].execute("angelo_view", args)
        if not isinstance(result, dict):
            result = yield from fx.pending_result(result)
    finally:
        after = snapshot()
        found = scene_check.finish()
        changed = sorted(key for key in before if before[key] != after[key])
        ctx["snapshots"].append({"call": label, "changed": changed, "scene_check": found["changes"],
                                 "depsgraph_updates": ctx["depsgraph_updates"] - updates})
    assert result.get("success"), result
    return result


def refused(args):
    """The refusal code and message of a call that must be refused (None when it succeeded)."""
    try:
        ctx["executor"].execute("angelo_view", args)
    except Exception as error:  # noqa: BLE001 - the code is what is checked.
        return refusal_code(error), str(error)
    return None


# ---- Steps ----------------------------------------------------------------------------------------------------

def _material(name, colour):
    material = bpy.data.materials.new(name)
    material.diffuse_color = colour
    return material


def _add(kind, location, colour, **options):
    getattr(bpy.ops.mesh, "primitive_%s_add" % kind)(location=location, **options)
    obj = bpy.context.active_object
    obj.data.materials.append(_material(kind, colour))
    return obj


def _camera(name, values):
    data = bpy.data.cameras.new(name)
    if "ortho_scale" in values:
        data.type, data.ortho_scale = "ORTHO", values["ortho_scale"]
    else:
        data.lens = values["lens"]
    data.clip_end = 1000.0
    obj = bpy.data.objects.new(name, data)
    obj.location = values["location"]
    obj.rotation_euler = look_at_euler(values["location"], values["look_at"])  # camera_create's look_at.
    ctx["window"].scene.collection.objects.link(obj)
    return obj


def _attach(name, writer):
    source = OUT / "sources" / name
    source.parent.mkdir(exist_ok=True)
    writer(source)
    return ctx["refs"].import_images([str(source)])[0]["id"]


def setup():
    window = bpy.context.window_manager.windows[0]
    ctx.update(window=window, area=next(a for a in window.screen.areas if a.type == "VIEW_3D"))
    report["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    scene = window.scene
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    with bpy.context.temp_override(window=window, area=ctx["area"]):
        scene.objects["Cube"].data.materials.append(_material("Red", (0.8, 0.1, 0.1, 1)))
        _add("plane", (0, 0, -1), (0.5, 0.5, 0.5, 1), size=12)
        _add("uv_sphere", (3, 1, 0), (0.1, 0.7, 0.2, 1), radius=0.8)
        _add("cone", (-3, -1, 0), (0.1, 0.2, 0.8, 1))
        _add("cylinder", (0, 3, 0), (0.9, 0.8, 0.1, 1), radius=0.5, depth=2)
        bpy.ops.object.select_all(action="DESELECT")
    ctx["cameras"] = {"perspective": _camera("Persp", PERSPECTIVE), "orthographic": _camera("Ortho", ORTHOGRAPHIC),
                      "straight_down": _camera("Down", STRAIGHT_DOWN)}
    scene.camera = ctx["cameras"]["perspective"]
    window.view_layer.update()
    ctx["refs"] = ReferenceStore(OUT, "0f6c3c87-6b4e-4a35-9b7f-6c4c8f6f0c14")
    ctx["executor"] = BlenderExecutor(window)
    ctx["executor"].observation_store = ctx["refs"]
    ctx["cube"] = scene_queries.object_handle(scene.objects["Cube"])
    bpy.app.handlers.depsgraph_update_post.append(count_updates)

    def portrait_jpeg(path):  # A JPEG written by Blender's own image writer, outside Main (no data-block).
        import imbuf
        buffer = imbuf.new((300, 400))
        buffer.file_type = "JPEG"
        imbuf.write(buffer, filepath=str(path))
        buffer.free()

    def very_wide_png(path):
        path.write_bytes(encode_rgba_png(np.full((10, 200, 4), 255, np.uint8)))

    def sideways_photo(path):
        """A 400x300 JPEG stored red-left, blue-right with EXIF Orientation 6: displayed 300x400, red on top."""
        import imbuf
        rows = np.zeros((300, 400, 4), np.uint8)
        rows[:, :200, 0], rows[:, 200:, 2], rows[..., 3] = 230, 230, 255
        source = path.with_suffix(".png")
        source.write_bytes(encode_rgba_png(rows))
        buffer = imbuf.load(str(source))
        buffer.file_type = "JPEG"
        imbuf.write(buffer, filepath=str(path))
        buffer.free()
        entry = struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<HH", 6, 0)
        body = b"Exif\x00\x00" + b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 1) + entry + bytes(4)
        data = path.read_bytes()
        path.write_bytes(data[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + data[2:])

    def corrupt_png(path):  # A valid signature and header over data that does not decode.
        header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
        chunk = struct.pack(">I", 13) + b"IHDR" + header + struct.pack(">I", zlib.crc32(b"IHDR" + header))
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk + b"\x00\x00\x00\x10IDAT" + bytes(range(16)) + bytes(4))

    ctx["portrait"] = _attach("portrait.jpg", portrait_jpeg)
    ctx["wide"] = _attach("wide.png", very_wide_png)
    ctx["text"] = _attach("notes.txt", lambda path: path.write_text("not an image"))
    ctx["sideways"] = _attach("sideways.jpg", sideways_photo)
    ctx["corrupt"] = _attach("corrupt.png", corrupt_png)


def equal_cameras():
    """A free camera's picture equals an equal scene camera's: perspective, orthographic, straight down."""
    measurements = {}
    for name, values in (("perspective", PERSPECTIVE), ("orthographic", ORTHOGRAPHIC),
                         ("straight_down", STRAIGHT_DOWN)):
        ctx["window"].scene.camera = ctx["cameras"][name]  # Fixture change, outside any wrapped call.
        ctx["window"].view_layer.update()
        for shading in ("SOLID", "LIT", "WIREFRAME"):
            view_cache.clear()
            result = yield from view({"views": ["SCENE_CAMERA", dict(values)], "shading": shading},
                                     label=name + "/" + shading)
            first, second = (decode(item["path"]) for item in result["views"])
            measurements["%s/%s" % (name, shading)] = difference(first, second)
            check("%s_%s_equals_the_scene_camera" % (name, shading.lower()),
                  first.shape == second.shape and same(measurements["%s/%s" % (name, shading)]))
            free = result["views"][1]
            check("%s_%s_reports_a_free_camera" % (name, shading.lower()),
                  free["framing"] == "CAMERA" and free["camera"]["source"] == "CAMERA"
                  and free["camera"]["location"] == [float(v) for v in values["location"]])
    report["measurements"]["equal_cameras"] = measurements
    ctx["window"].scene.camera = ctx["cameras"]["perspective"]
    ctx["window"].view_layer.update()


def pairs():
    """A pair puts the attached image left of the view drawn at its aspect; matching viewpoints match."""
    view_cache.clear()
    through = yield from view({"views": ["SCENE_CAMERA"], "shading": "SOLID"}, label="reference source")
    source = OUT / "sources" / "scene-camera.png"
    source.write_bytes(Path(through["views"][0]["path"]).read_bytes())
    landscape = ctx["refs"].import_images([str(source)])[0]["id"]
    images_before = len(bpy.data.images)
    result = yield from view({"views": [dict(PERSPECTIVE, reference=landscape)], "shading": "SOLID"},
                             label="landscape pair")
    record = result["views"][0]
    picture = decode(record["path"])
    width, height = record["view_size"]
    left, right = picture[:, :width], picture[:, width + 2:]
    reference = decode(source)
    measure = difference(right, reference)
    report["measurements"]["landscape_pair_view_against_reference"] = measure
    check("pair_picture_is_reference_gap_view", (record["width"], record["height"]) == (2 * width + 2, height)
          == picture.shape[1::-1] and (width, height) == (512, 288))
    check("pair_left_half_is_the_reference_as_attached", bool((left == reference).all()))
    check("pair_right_half_matches_the_reference_viewpoint", same(measure))
    check("pair_record_states_layout_and_reference", record["layout"] == "REFERENCE_LEFT_VIEW_RIGHT"
          and record["reference"]["reference_id"] == landscape and record["reference"]["source_size"] == [512, 288])
    check("pair_made_no_image_data_block", len(bpy.data.images) == images_before)
    check("pair_note_states_the_layout", "attached image on the left" in result["note"])
    # REUSE of a pair's view_id: its camera at the image's aspect, as a plain view equal to the right half.
    reused = yield from view({"views": [record["view_id"]], "shading": "SOLID"}, label="reuse a pair")
    check("pair_view_id_reuses_its_camera", same(difference(decode(reused["views"][0]["path"]), right)))
    portrait = (yield from view({"views": [dict(PERSPECTIVE, reference=ctx["portrait"])], "resolution": 512,
                                 "shading": "LIT"}, label="portrait jpeg pair"))["views"][0]
    check("portrait_jpeg_pair_is_drawn_at_its_aspect", portrait["view_size"] == [384, 512]
          and (portrait["width"], portrait["height"]) == (770, 512))
    # EXIF Orientation 6: stored 400x300 red-left, shown 300x400 red on top, the view drawn at 3:4.
    turned = (yield from view({"views": [dict(PERSPECTIVE, reference=ctx["sideways"])], "shading": "SOLID"},
                              label="exif pair"))["views"][0]
    left = decode(turned["path"])[:, :turned["view_size"][0]]
    top, bottom = left[:left.shape[0] // 4].mean(axis=(0, 1)), left[-left.shape[0] // 4:].mean(axis=(0, 1))
    report["measurements"]["exif_pair_top_bottom_rgb"] = [top.round(1).tolist(), bottom.round(1).tolist()]
    check("exif_orientation_is_applied", turned["view_size"] == [384, 512]
          and turned["reference"]["source_size"] == [300, 400] and turned["reference"].get("exif_orientation") == 6
          and top[0] > 150 > top[2] and bottom[2] > 150 > bottom[0])
    ctx["landscape"] = landscape


def six_pairs():
    items = [dict(PERSPECTIVE, location=[7.5 - i, -6.5, 4.5], reference=(ctx["landscape"], ctx["portrait"])[i % 2])
             for i in range(6)]
    started = time.perf_counter()
    result = yield from view({"views": items, "shading": "LIT", "resolution": 512}, label="six pairs")
    report["measurements"]["six_lit_pairs_wall_s"] = round(time.perf_counter() - started, 3)
    check("six_pairs_in_one_call", len(result["views"]) == 6 and all(
        v["width"] == 2 * v["view_size"][0] + 2 for v in result["views"]))
    report["measurements"]["six_pair_bytes"] = [v["bytes"] for v in result["views"]]


def refusals():
    cases = {"unknown_id": "0f6c3c87-0000-4000-8000-000000000000", "text_attachment": ctx["text"],
             "too_wide": ctx["wide"], "corrupt": ctx["corrupt"]}
    found = {}
    for name, identity in cases.items():
        found[name] = refused({"views": [dict(PERSPECTIVE, reference=identity)]})
        check("refused_%s_as_validation" % name, found[name] is not None and found[name][0] == "VALIDATION"
              and "views[0].reference" in found[name][1] and str(ctx["refs"].directory) not in found[name][1])
    try:  # An integer past a float's range is an invalid call (VALIDATION once admitted), never OverflowError.
        ctx["executor"].execute("angelo_view", {"views": [dict(PERSPECTIVE, lens=10 ** 400)]})
        found["huge_integer"] = None
    except Exception as error:  # noqa: BLE001
        found["huge_integer"] = [type(error).__name__, str(error)]
    check("refused_a_huge_integer_as_invalid", found["huge_integer"] is not None
          and found["huge_integer"][0] in ("ValueError", "RefusalError")
          and "views[0].lens" in found["huge_integer"][1])
    found["seven_pairs"] = refused({"views": [dict(PERSPECTIVE, location=[i, -6.5, 4.5], reference=ctx["landscape"])
                                              for i in range(7)]})
    check("refused_seven_pairs", found["seven_pairs"] is not None)
    report["measurements"]["refusals"] = found


def motion_blur():
    """MATERIAL and LIT free cameras keep the fresh offscreen per draw: each view equals its drawing alone."""
    scene = ctx["window"].scene
    cube = scene.objects["Cube"]
    cube.location = (-3, 0, 0)
    cube.keyframe_insert("location", frame=1)
    cube.location = (3, 0, 0)
    cube.keyframe_insert("location", frame=40)
    scene.render.use_motion_blur = True
    scene.frame_set(20)
    ctx["window"].view_layer.update()
    items = [dict(PERSPECTIVE), dict(ORTHOGRAPHIC), dict(PERSPECTIVE, location=[-7.0, -7.0, 3.0])]
    for shading in ("LIT", "MATERIAL"):
        view_cache.clear()
        together = yield from view({"views": items, "shading": shading, "resolution": 256}, label="blur " + shading)
        differences = []
        for index, item in enumerate(items):
            view_cache.clear()
            alone = yield from view({"views": [item], "shading": shading, "resolution": 256}, label="blur alone")
            differences.append(difference(decode(together["views"][index]["path"]), decode(alone["views"][0]["path"])))
        report["measurements"]["blur_" + shading] = differences
        # A view smeared toward the one before it differed by 14-17 levels on average (view_blur, G08); EEVEE's own
        # draw-to-draw variation on this fixture was 0.0-0.37 levels with no outlier pixel.
        check("blur_%s_free_cameras_match_alone" % shading.lower(), all(same(d) for d in differences))


def strip_and_weights():
    scene = ctx["window"].scene
    frame = scene.frame_current
    # A strip refuses to run a test's own depsgraph handler (view_motion: Python a frame change would run).
    bpy.app.handlers.depsgraph_update_post.remove(count_updates)
    try:
        strip = yield from view({"views": [dict(PERSPECTIVE)], "frames": [1, 30], "shading": "SOLID"}, label="strip")
    finally:
        bpy.app.handlers.depsgraph_update_post.append(count_updates)
    check("strip_holds_a_free_camera", strip["views"][0]["framing"] == "STRIP"
          and strip["views"][0]["camera"]["source"] == "CAMERA" and scene.frame_current == frame)
    cube = scene.objects["Cube"]
    if not cube.vertex_groups:
        group = cube.vertex_groups.new(name="Lift")
        group.add([v.index for v in cube.data.vertices if v.co.z > 0], 1.0, "REPLACE")
        ctx["window"].view_layer.update()  # The fixture's edit is evaluated before the wrapped call.
    weights = (yield from view({"targets": [ctx["cube"]], "views": [dict(PERSPECTIVE, reference=ctx["landscape"])],
                                "shading": "WEIGHT"}, label="weight pair"))["views"][0]
    check("weight_pair_is_a_pair", weights["width"] == 2 * weights["view_size"][0] + 2)
    # A pair with targets states the outline comparison an OUTLINE would.
    outline = weights.get("outline") or {}
    report["measurements"]["pair_with_targets_outline"] = {key: outline.get(key) for key in ("iou", "p95_px", "compared")}
    check("a_pair_with_targets_states_its_outline", outline.get("compared") is not None and "iou" in outline)
    away = yield from view({"views": [{"location": [0, 0, 60], "look_at": [0, 0, 200]}], "shading": "SOLID"},
                           label="away")
    check("camera_facing_nothing_says_so", any(note["code"] == "camera_faces_no_geometry"
                                               for note in away["uncertainty"]))
    bpy.app.handlers.depsgraph_update_post.remove(count_updates)
    try:
        away_strip = yield from view({"views": [{"location": [0, 0, 60], "look_at": [0, 0, 200]}], "frames": [1, 30],
                           "shading": "SOLID"}, label="away strip")
    finally:
        bpy.app.handlers.depsgraph_update_post.append(count_updates)
    check("a_strip_facing_nothing_says_so", any(note["code"] == "camera_faces_no_geometry"
                                                for note in away_strip["uncertainty"]))


def near_and_reuse():
    """Clip planes from geometry, never look_at; a REUSE clips the scene as it is now."""
    window = ctx["window"]
    scene = window.scene
    with bpy.context.temp_override(window=window, area=ctx["area"]):
        far_away = [_add("plane", (1000, 0, 0), (0.5, 0.5, 0.5, 1), size=300),
                    _add("plane", (1000, 40, 1.6), (0.9, 0.1, 0.1, 1), size=4, rotation=(1.5707963, 0, 0)),
                    _add("plane", (1000, 40.02, 1.6), (0.1, 0.8, 0.1, 1), size=4, rotation=(1.5707963, 0, 0))]
        bpy.ops.object.select_all(action="DESELECT")
    eye = {"location": [1000.0, 0.0, 1.7], "look_at": [1000.0, 1.0, 1.7], "lens": 50.0}
    camera = _camera("Eye", eye)  # Blender's new camera's clip range, 0.1 to 1000.
    previous, scene.camera = scene.camera, camera
    window.view_layer.update()
    # Against a camera object with Blender's default range: Workbench's cavity and shadows depend on the far plane
    # (a free camera clipped just past its geometry was 23 levels darker in SOLID).
    measures = {}
    for aim in (40.0, 0.1, 0.0002):  # look_at only aims: the picture never depends on its distance.
        item = dict(eye, look_at=[1000.0, aim, 1.7])
        for shading in ("SOLID", "LIT"):
            result = yield from view({"views": ["SCENE_CAMERA", item], "shading": shading}, label="eye level %g" % aim)
            first, second = (decode(record["path"]) for record in result["views"])
            rows, columns = second.shape[0] // 5, second.shape[1] // 5
            centre = second[rows * 2:rows * 3, columns * 2:columns * 3]
            measures["%g/%s" % (aim, shading)] = dict(
                difference(first, second), clip=[result["views"][1]["camera"]["clip_start"],
                                                  result["views"][1]["camera"]["clip_end"]],
                red_share=round(float(((centre[..., 0] > 120) & (centre[..., 1] < 90)).mean()), 3))
    report["measurements"]["eye_level_aim"] = measures
    check("eye_level_free_camera_equals_a_default_camera_object_at_any_aim", all(same(m) for m in measures.values()))
    check("the_nearer_plane_hides_the_one_behind_at_any_aim",  # SOLID shows its colour; LIT is lit (and equal).
          all(m["red_share"] > 0.5 for key, m in measures.items() if key.endswith("SOLID")))
    check("clip_planes_never_depend_on_look_at", len({tuple(m["clip"]) for m in measures.values()}) == 1)
    # A 20 km ground under the camera and a small cube 0.6 ahead: the cube is drawn, the ground's far plane past
    # its edge (a near plane pushed out by distant geometry cut the cube).
    far_away[0].scale = (66.7, 66.7, 1.0)
    with bpy.context.temp_override(window=window, area=ctx["area"]):
        far_away.append(_add("cube", (1000, 0.6, 1.7), (0.9, 0.1, 0.1, 1), size=0.3))
        bpy.ops.object.select_all(action="DESELECT")
    window.view_layer.update()
    close = yield from view({"views": [dict(eye, look_at=[1000.0, 0.6, 1.7])], "shading": "SOLID"},
                            label="20 km ground")
    picture = decode(close["views"][0]["path"])
    rows, columns = picture.shape[0] // 5, picture.shape[1] // 5
    centre = picture[rows * 2:rows * 3, columns * 2:columns * 3]
    red = round(float(((centre[..., 0] > 120) & (centre[..., 1] < 90)).mean()), 3)
    report["measurements"]["twenty_km_ground"] = {"red_share": red, "clip": [
        close["views"][0]["camera"]["clip_start"], close["views"][0]["camera"]["clip_end"]],
        "uncertainty": [note["code"] for note in close["uncertainty"]]}
    check("a_near_subject_survives_a_vast_ground", red > 0.5 and close["views"][0]["camera"]["clip_end"] > 20000
          and not close["uncertainty"])
    scene.camera = previous
    with bpy.context.temp_override(window=window, area=ctx["area"]):
        for obj in far_away:
            bpy.data.objects.remove(obj)
    bpy.data.objects.remove(camera)
    window.view_layer.update()
    # REUSE after an object appears nearer than the old clip_start: drawn, as a fresh free camera draws it.
    baseline = (yield from view({"views": [dict(PERSPECTIVE)], "shading": "SOLID"}, label="reuse baseline"))["views"][0]
    with bpy.context.temp_override(window=window, area=ctx["area"]):
        near = _add("cube", (6.6, -5.7, 4.0), (0.1, 0.2, 0.9, 1), size=0.4)
        bpy.ops.object.select_all(action="DESELECT")
    window.view_layer.update()
    reused = yield from view({"views": [baseline["view_id"]], "shading": "SOLID"}, label="reuse after a nearer object")
    fresh = yield from view({"views": [dict(PERSPECTIVE)], "shading": "SOLID"}, label="fresh after a nearer object")
    measure = difference(decode(reused["views"][0]["path"]), decode(fresh["views"][0]["path"]))
    report["measurements"]["reuse_after_nearer_object"] = dict(
        measure, clip_before=baseline["camera"]["clip_start"], clip_reused=reused["views"][0]["camera"]["clip_start"])
    check("reuse_clips_the_scene_as_it_is_now", same(measure)
          and reused["views"][0]["camera"]["clip_start"] < baseline["camera"]["clip_start"]
          and reused["views"][0]["camera"].get("reused_from") == baseline["view_id"])
    bpy.data.objects.remove(near)
    window.view_layer.update()


def _rss_mb():   # Every platform (qa_machine.resident_mb: no ps on Windows); unreadable fails the growth check.
    from qa_machine import resident_mb
    value = resident_mb(os.getpid())
    return float("inf") if value is None else value


def memory_call():
    """One six-pair LIT call at 1024 per event-loop tick, as tool calls run in the application.

    Many draws inside one callback hold their GPU objects until the loop drains its autorelease pool (macOS),
    which grows the process on base code too; between ticks the memory is returned.
    """
    items = [dict(PERSPECTIVE, location=[7.5 - i, -6.5, 4.5], reference=ctx["landscape"]) for i in range(6)]
    view_cache.clear()
    result = ctx["executor"].execute("angelo_view", {"views": items, "shading": "LIT", "resolution": 1024})
    if not isinstance(result, dict):   # Drawn only once its shaders are compiled (view_warm): a call that drew.
        result = yield from fx.pending_result(result)
    assert result.get("success"), result
    calls = ctx["memory_calls"] = ctx.get("memory_calls", 0) + 1
    if calls in (LEAK_WARM, LEAK_CALLS):
        ctx["rss_%d" % calls] = _rss_mb()


def memory_check():
    growth = ctx["rss_%d" % LEAK_CALLS] - ctx["rss_%d" % LEAK_WARM]
    report["measurements"]["rss_growth_mb_calls_%d_to_%d" % (LEAK_WARM, LEAK_CALLS)] = round(growth, 1)
    check("repeated_six_pair_calls_do_not_grow_the_process", growth < LEAK_MB)


def read_only():
    calls = ctx["snapshots"]
    report["measurements"]["wrapped_calls"] = len(calls)
    report["measurements"]["changed_calls"] = [c for c in calls if c["changed"] or c["scene_check"]
                                               or c["depsgraph_updates"]]
    check("every_call_left_the_document_viewports_and_data_unchanged",
          len(calls) >= 20 and not report["measurements"]["changed_calls"])


STEPS = ([setup, equal_cameras, pairs, six_pairs, refusals, motion_blur, strip_and_weights, near_and_reuse]
         + [memory_call] * LEAK_CALLS + [memory_check, read_only])


def run():
    index = ctx.setdefault("step", 0)
    try:
        with bpy.context.temp_override(window=ctx.get("window") or bpy.context.window_manager.windows[0]):
            running = ctx.pop("running", None) or STEPS[index]()
            if inspect.isgenerator(running):   # A step waiting on a pending look: the event loop runs meanwhile.
                try:
                    next(running)
                except StopIteration:
                    pass
                else:
                    ctx["running"] = running
                    return 0.01
    except Exception:
        report.update(success=False, failed_step=STEPS[index].__name__, error=traceback.format_exc())
        return finish()
    ctx["step"] += 1
    if ctx["step"] < len(STEPS):
        return 0.25  # Let the event loop run between steps, as in the application.
    report["success"] = all(report["checks"].values())
    return finish()


def finish():
    report.setdefault("success", False)
    if count_updates in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(count_updates)
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_VIEW_CAMERA", json.dumps({"success": report["success"], "failed": sorted(
        name for name, passed in report["checks"].items() if not passed)}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
