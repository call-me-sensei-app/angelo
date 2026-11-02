# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The viewport UV checker (doc/angelo/uv_checker.rst), offscreen in a background Blender with a GPU.

Proves with pixels and data counts, on planes facing an orthographic front camera:

* Defaults: a factory startup file (saved before the checker existed) gets them through versioning.
* On and off: the checker changes the shaded planes and nothing else; no image, material, node group, texture or
  material slot is added or changed by drawing any pattern, in Solid and Material Preview; saved and reopened, the
  view setting persists and the file holds no new data-block.
* UV maps: Active, Render and Named draw their own map; two views on two maps of one mesh, drawn in turn, each draw
  their own; a mesh without the named map is hatched as missing.
* Edit Mode: moving UVs in a BMesh changes the drawing at the next draw (live).
* UDIM: UVs moved one tile over (1002) draw the same pattern as in 1001; nothing is lost outside 0-1.
* Texel density: on target is green, four times over is red, a quarter is blue. Stretch: uniform is blue, a 4:1
  map red. Flipped UVs are hatched only with Mark Flipped on. Selected Only skips unselected objects.
* X-ray: nothing drawn. Overlays hidden: still drawn (its own toggle).
* Cost: a heavy scene (many dense objects), off and on interleaved over the same frames (reported, labelled).
"""

import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import tempfile
import time
import traceback

import bmesh
import bpy
import gpu
from mathutils import Euler, Matrix, Vector

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "observed": {}, "timings": {}, "binary": bpy.app.binary_path,
          "machine": platform.node(), "success": False}
checks, observed = report["checks"], report["observed"]
WIDTH, HEIGHT = 320, 240
DATA = ("images", "materials", "node_groups", "textures")


def plane(name, x, size=2.0, uv_scale=(1.0, 1.0), uv_offset=(0.0, 0.0), uv_maps=("UVMap",), mirror=False):
    """A ``size`` m square facing -Y at (x, 0, 0), each UV map spanning the square once times ``uv_scale``."""
    mesh = bpy.data.meshes.new(name)
    half = size / 2
    mesh.from_pydata([(-half, 0, -half), (half, 0, -half), (half, 0, half), (-half, 0, half)], [], [(0, 1, 2, 3)])
    corners = ((0, 0), (1, 0), (1, 1), (0, 1))
    for uv_name in uv_maps:
        layer = mesh.uv_layers.new(name=uv_name)
        for loop, (u, v) in zip(layer.data, corners):
            u = 1 - u if mirror else u
            loop.uv = (u * uv_scale[0] + uv_offset[0], v * uv_scale[1] + uv_offset[1])
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (x, 0, 0)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def dense_grid(name, location, cuts):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=cuts, y_segments=cuts, size=0.4, calc_uvs=True)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def front_view():
    rotation = Euler((math.radians(90), 0, 0)).to_matrix().to_4x4()
    view = (Matrix.Translation((0, -10, 0)) @ rotation).inverted()
    scale = 1 / 3.0
    projection = Matrix(((scale * HEIGHT / WIDTH, 0, 0, 0), (0, scale, 0, 0), (0, 0, -.02, -1), (0, 0, 0, 1)))
    return view, projection


def draw():
    bpy.context.view_layer.update()   # A window evaluates before it draws; an offscreen draw does not.
    offscreen.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, view, projection,
                          do_color_management=False)
    with offscreen.bind():
        pixels = gpu.state.active_framebuffer_get().read_color(0, 0, WIDTH, HEIGHT, 4, 0, 'UBYTE')
    pixels.dimensions = WIDTH * HEIGHT * 4
    return bytes(pixels)


def screen(x, z):
    clip = projection @ view @ Vector((x, 0, z, 1))
    return int(((clip.x / clip.w) * .5 + .5) * WIDTH), int(((clip.y / clip.w) * .5 + .5) * HEIGHT)


def region_pixels(frame, x, half=0.6):
    """RGB of every pixel inside the square of half-width ``half`` m around a plane centred at (x, 0, 0)."""
    (x0, y0), (x1, y1) = screen(x - half, -half), screen(x + half, half)
    return [tuple(frame[(row * WIDTH + column) * 4:(row * WIDTH + column) * 4 + 3])
            for row in range(min(y0, y1), max(y0, y1)) for column in range(min(x0, x1), max(x0, x1))]


def mean(pixels):
    return tuple(sum(p[i] for p in pixels) / len(pixels) for i in range(3))


def magenta_share(pixels):
    """How much of a region is the missing-map / flipped hatching: red and blue well over green."""
    return sum(1 for r, g, b in pixels if r > g + 60 and b > g + 40) / len(pixels)


def names(kind):
    return [item.name for item in getattr(bpy.data, kind) if getattr(item, "type", "") not in {"RENDER_RESULT", "COMPOSITING"}]


def counts():
    """Data-blocks a file can hold (Blender's own Render Result and Viewer images are never saved)."""
    return {name: len([item for item in getattr(bpy.data, name)
                       if getattr(item, "type", "") not in {"RENDER_RESULT", "COMPOSITING"}]) for name in DATA}


def save_png(name, frame):
    image = bpy.data.images.new("qa_" + name, WIDTH, HEIGHT, alpha=True)
    image.pixels.foreach_set([value / 255 for value in frame])
    image.filepath_raw = str(OUT / f"{name}.png")
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)


try:
    gpu.init()
    start_names = {name: names(name) for name in DATA}
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    checker = space.uv_checker
    defaults = {name: getattr(checker, name) for name in ("show", "pattern", "uv_map", "scale", "opacity",
                                                          "texture_size", "target_density", "use_selected_only",
                                                          "show_flipped")}
    observed["factory_defaults"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in defaults.items()}
    checks["defaults_from_versioning"] = defaults == {
        "show": False, "pattern": "COLOR_GRID", "uv_map": "ACTIVE", "scale": 1.0, "opacity": 1.0,
        "texture_size": 1024, "target_density": 512.0, "use_selected_only": False, "show_flipped": False}
    observed["rna_path"] = checker.path_from_id()
    checks["rna_path_names_its_view"] = bool(re.fullmatch(r"areas\[\d+\]\.spaces\[\d+\]\.uv_checker",
                                                          observed["rna_path"]))
    space.shading.type = 'SOLID'
    space.shading.show_xray = False
    for name in ("show_floor", "show_axis_x", "show_axis_y", "show_cursor", "show_object_origins", "show_outline_selected"):
        setattr(space.overlay, name, False)
    bpy.data.batch_remove(tuple(bpy.data.objects))
    view, projection = front_view()
    offscreen = gpu.types.GPUOffScreen(WIDTH, HEIGHT)

    # -- on and off, every pattern, no data change -----------------------------------------------------------
    target = plane("Target", -2.2)
    before = counts()
    slots = len(target.material_slots)
    checker.show = False
    off = draw()
    checker.show = True
    frames = {}
    for pattern in ("COLOR_GRID", "CHECKER", "TEXEL_DENSITY", "STRETCH"):
        checker.pattern = pattern
        frames[pattern] = draw()
        save_png("solid_" + pattern.lower(), frames[pattern])
    save_png("solid_off", off)
    for pattern, frame in frames.items():
        changed = sum(1 for a, b in zip(region_pixels(off, -2.2), region_pixels(frame, -2.2)) if a != b)
        observed[f"{pattern.lower()}_changed_pixels_on_plane"] = changed
        checks[f"{pattern.lower()}_drawn_on_the_plane"] = changed > 200
        checks[f"{pattern.lower()}_nothing_drawn_off_the_plane"] = region_pixels(off, 2.2) == region_pixels(frame, 2.2)
    checks["patterns_differ"] = len({frames[p] for p in frames}) == 4
    space.shading.type = 'MATERIAL'
    checker.pattern = 'COLOR_GRID'
    for _ in range(3):   # EEVEE compiles its shaders over the first draws.
        material_on = draw()
    checker.show = False
    material_off = draw()
    checker.show = True
    space.shading.type = 'SOLID'
    observed["material_preview_changed_pixels"] = sum(
        1 for a, b in zip(region_pixels(material_off, -2.2), region_pixels(material_on, -2.2)) if a != b)
    checks["material_preview_drawn"] = observed["material_preview_changed_pixels"] > 200
    save_png("material_preview_on", material_on)
    checks["no_data_block_added_or_changed"] = counts() == before and len(target.material_slots) == slots
    observed["data_counts"] = counts()

    # -- saved with the view; nothing else saved ------------------------------------------------------------
    checker.scale, checker.pattern = 4.0, 'CHECKER'
    path = os.path.join(tempfile.mkdtemp(), "uv_checker.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=True)
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    checker = space.uv_checker
    checks["view_setting_saved"] = (checker.show, checker.scale, checker.pattern) == (True, 4.0, 'CHECKER')
    # The file holds no data-block the factory file did not (Blender's own save and orphan handling may keep or drop
    # the factory cube's unused material; that is not the checker's).
    observed["new_after_reopen"] = {name: sorted(set(names(name)) - set(start_names[name])) for name in DATA}
    checks["no_data_block_saved"] = not any(observed["new_after_reopen"].values())
    checker.scale, checker.pattern = 1.0, 'COLOR_GRID'
    target = bpy.data.objects["Target"]

    # -- UV maps: active, render, named, missing -------------------------------------------------------------
    bpy.data.batch_remove(tuple(bpy.data.objects))
    two = plane("TwoMaps", -2.2, uv_maps=("Base", "Detail"))
    two.data.uv_layers["Detail"].active = True        # Editing Detail; Base stays the render map.
    for loop in two.data.uv_layers["Detail"].data:
        loop.uv = (loop.uv[0] * 3, loop.uv[1] * 3)     # Detail repeats three times.
    other = plane("Other", 2.2, uv_maps=("Base",))
    shots = {}
    for source in ("ACTIVE", "RENDER"):
        checker.uv_map = source
        shots[source] = draw()
    checker.uv_map, checker.uv_map_name = 'NAMED', "Detail"
    shots["NAMED_Detail"] = draw()
    checker.uv_map_name = "Base"
    shots["NAMED_Base"] = draw()
    checks["active_and_render_maps_differ"] = region_pixels(shots["ACTIVE"], -2.2) != region_pixels(shots["RENDER"], -2.2)
    checks["named_map_is_that_map"] = (region_pixels(shots["NAMED_Detail"], -2.2) == region_pixels(shots["ACTIVE"], -2.2)
                                       and region_pixels(shots["NAMED_Base"], -2.2) == region_pixels(shots["RENDER"], -2.2))
    # Two views on two maps of one mesh, drawn in turn (each keeps its slot): every frame is its own map's.
    turns = []
    for index in range(6):
        if index % 2:
            checker.uv_map, checker.uv_map_name = 'NAMED', "Base"
        else:
            checker.uv_map = 'ACTIVE'
        turns.append(region_pixels(draw(), -2.2))
    checks["two_maps_in_turn_each_draw_their_own"] = (
        all(frame == region_pixels(shots["ACTIVE"], -2.2) for frame in turns[0::2])
        and all(frame == region_pixels(shots["RENDER"], -2.2) for frame in turns[1::2]))
    checker.uv_map, checker.uv_map_name = 'NAMED', "Detail"
    missing = draw()
    save_png("named_missing", missing)
    observed["missing_magenta_share"] = round(magenta_share(region_pixels(missing, 2.2)), 3)
    checks["mesh_without_the_named_map_is_hatched"] = observed["missing_magenta_share"] > 0.3
    # The mesh that has the map draws it exactly as when it is the active map (the Color Grid has pink cells of its
    # own, so a colour test would misread it).
    checks["mesh_with_it_is_not"] = region_pixels(missing, -2.2) == region_pixels(shots["ACTIVE"], -2.2)
    checker.uv_map = 'ACTIVE'

    # -- Edit Mode: a UV edit shows at the next draw ---------------------------------------------------------
    bpy.data.batch_remove(tuple(bpy.data.objects))
    edited = plane("Edited", -2.2)
    edited.select_set(True)
    bpy.context.view_layer.objects.active = edited
    bpy.ops.object.mode_set(mode='EDIT')
    checker.show = False
    edit_off = draw()
    checker.show = True
    before_edit = draw()
    bm = bmesh.from_edit_mesh(edited.data)
    uv_layer = bm.loops.layers.uv.active
    for face in bm.faces:
        for loop in face.loops:
            loop[uv_layer].uv = loop[uv_layer].uv * 2.0
    bmesh.update_edit_mesh(edited.data)
    after_edit = draw()
    save_png("edit_mode_after_uv_edit", after_edit)
    checks["edit_mode_drawn"] = region_pixels(before_edit, -2.2) != region_pixels(edit_off, -2.2)
    checks["edit_mode_uv_edit_is_live"] = region_pixels(before_edit, -2.2) != region_pixels(after_edit, -2.2)
    bpy.ops.object.mode_set(mode='OBJECT')

    # -- UDIM: tile 1002 draws as tile 1001 ------------------------------------------------------------------
    bpy.data.batch_remove(tuple(bpy.data.objects))
    plane("Tile1001", -2.2)
    plane("Tile1002", 2.2, uv_offset=(1.0, 0.0))
    udim = draw()
    save_png("udim", udim)
    # Pixel grids align (the planes are 176 px apart); UVs one tile over differ only in float rounding.
    tile_difference = [abs(a - b) for p, q in zip(region_pixels(udim, -2.2), region_pixels(udim, 2.2))
                       for a, b in zip(p, q)]
    observed["udim_mean_abs_difference"] = round(sum(tile_difference) / len(tile_difference), 3)
    checks["udim_tile_draws_like_the_first"] = observed["udim_mean_abs_difference"] < 2.0
    checks["udim_tile_drawn"] = magenta_share(region_pixels(udim, 2.2)) < 0.05 and len(set(region_pixels(udim, 2.2))) > 8

    # -- texel density and stretch ---------------------------------------------------------------------------
    bpy.data.batch_remove(tuple(bpy.data.objects))
    plane("OnTarget", -2.2)                           # 2 m over one tile of 1024 px: 512 px/m, the target.
    plane("FourTimes", 2.2, uv_scale=(4.0, 4.0))      # 2048 px/m: four times over.
    checker.pattern = 'TEXEL_DENSITY'
    density = draw()
    save_png("texel_density", density)
    on, over = mean(region_pixels(density, -2.2)), mean(region_pixels(density, 2.2))
    observed["density_mean_rgb"] = {"on_target": [round(c) for c in on], "four_times": [round(c) for c in over]}
    checks["density_on_target_is_green"] = on[1] > on[0] + 40 and on[1] > on[2] + 40
    checks["density_four_times_is_red"] = over[0] > over[1] + 40 and over[0] > over[2] + 40
    checker.target_density = 2048.0
    retarget = mean(region_pixels(draw(), -2.2))
    checks["density_a_quarter_is_blue"] = retarget[2] > retarget[0] + 40 and retarget[2] > retarget[1]
    checker.target_density = 512.0
    bpy.data.batch_remove(tuple(bpy.data.objects))
    plane("Uniform", -2.2)
    plane("Stretched", 2.2, uv_scale=(4.0, 1.0))
    checker.pattern = 'STRETCH'
    stretch = draw()
    save_png("stretch", stretch)
    uniform, stretched = mean(region_pixels(stretch, -2.2)), mean(region_pixels(stretch, 2.2))
    checks["stretch_uniform_is_blue"] = uniform[2] > uniform[0] + 40
    checks["stretch_four_to_one_is_red"] = stretched[0] > stretched[2] + 40

    # -- flipped UVs, selected only, X-ray, overlays hidden --------------------------------------------------
    bpy.data.batch_remove(tuple(bpy.data.objects))
    plane("Regular", -2.2)
    mirrored = plane("Mirrored", 2.2, mirror=True)
    checker.pattern = 'CHECKER'
    plain = draw()
    checker.show_flipped = True
    flagged = draw()
    save_png("flipped", flagged)
    checks["flipped_hatched_only_when_asked"] = (magenta_share(region_pixels(plain, 2.2)) < 0.02
                                                 and magenta_share(region_pixels(flagged, 2.2)) > 0.2)
    checks["regular_never_hatched"] = magenta_share(region_pixels(flagged, -2.2)) < 0.02
    checker.show_flipped = False
    for obj in bpy.data.objects:
        obj.select_set(obj is mirrored)
    checker.use_selected_only = True
    selected_only = draw()
    checker.show = False
    off_now = draw()
    checker.show = True
    checks["selected_only_skips_unselected"] = (region_pixels(selected_only, -2.2) == region_pixels(off_now, -2.2)
                                                and region_pixels(selected_only, 2.2) != region_pixels(off_now, 2.2))
    checker.use_selected_only = False
    space.shading.show_xray = True
    xray_on = draw()
    checker.show = False
    xray_off = draw()
    checker.show = True
    space.shading.show_xray = False
    checks["xray_draws_nothing"] = xray_on == xray_off
    space.overlay.show_overlays = False
    hidden_on = draw()
    checker.show = False
    hidden_off = draw()
    checker.show = True
    space.overlay.show_overlays = True
    checks["drawn_with_overlays_hidden"] = region_pixels(hidden_on, -2.2) != region_pixels(hidden_off, -2.2)

    # -- cost: a heavy scene, off and on interleaved ---------------------------------------------------------
    bpy.data.batch_remove(tuple(bpy.data.objects))
    for index in range(400):
        dense_grid(f"Dense{index}", ((index % 20 - 9.5) * 0.3, 0, (index // 20 - 9.5) * 0.3), 40)
    observed["heavy_scene"] = {"objects": len(bpy.data.objects),
                               "triangles": sum(len(o.data.polygons) * 2 for o in bpy.data.objects)}
    checker.pattern = 'COLOR_GRID'
    samples = {False: [], True: []}
    for toggle in (False, True, False, True):   # Warm-up: shaders and batches for both.
        checker.show = toggle
        draw()
    for _ in range(40):
        for toggle in (False, True):
            checker.show = toggle
            started = time.perf_counter()
            draw()
            samples[toggle].append((time.perf_counter() - started) * 1000)
    report["timings"] = {"off_median_ms": round(statistics.median(samples[False]), 3),
                         "on_median_ms": round(statistics.median(samples[True]), 3),
                         "off_p75_ms": round(statistics.quantiles(samples[False], n=4)[2], 3),
                         "on_p75_ms": round(statistics.quantiles(samples[True], n=4)[2], 3),
                         "note": "offscreen draw + read-back at %dx%d, interleaved, 40 frames each; not a claim "
                                 "unless on a quiet machine" % (WIDTH, HEIGHT)}
    offscreen.free()
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("UV_CHECKER_DRAW failed checks:", json.dumps(failed), "timings:", json.dumps(report["timings"]))
assert report["success"], failed
