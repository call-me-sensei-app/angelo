# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-app half of ``bench_uv_checker.py``: the viewport UV checker's draw time (doc/angelo/uv_checker.rst).

Run only by that driver, in a background Angelo with a GPU (``--factory-startup``). Each frame is an offscreen
draw of the factory 3D view at ``WIDTH`` x ``HEIGHT`` with a read-back, so the time includes the GPU's work, not only
Blender issuing it. Two kinds of frame per condition:

* **navigating**: the view turns a little every frame, as while orbiting, so every engine draws the scene again
  (Workbench and EEVEE restart their samples). This is the draw time a person feels.
* **static**: the same view again. Workbench and EEVEE then show what they already accumulated, while overlays
  (Face Orientation, the checker) draw again, so this frame isolates an overlay's own cost.

``ANGELO_UV_BENCH_MODE``:

* ``off``: the heavy scene in Solid with the checker off (a build without the checker simply has none), the frames
  of one condition: the driver compares builds run after run (ABAB).
* ``compare``: in one process, rounds over the conditions so load changes hit them alike: the heavy scene in Solid
  without and with the checker, Material Preview with the checker, and stock Blender's nearest route, a checker
  image texture in a material in Material Preview (the Color Grid image every object shares, as people set it up);
  then the same on one dense mesh in Edit Mode.

Writes ``ANGELO_UV_BENCH_OUT`` (JSON): per condition the frame times (ms), median and p75, and the scene.
"""

import json
import math
import os
import statistics
import sys
import time

import bmesh
import bpy
import gpu
from mathutils import Euler, Matrix

assert bpy.app.background and "--factory-startup" in sys.argv
MODE, OUT = os.environ["ANGELO_UV_BENCH_MODE"], os.environ["ANGELO_UV_BENCH_OUT"]
WIDTH, HEIGHT = 1920, 1080
ROUNDS, WARM, FRAMES = 5, 3, 12


def grid(name, location, cuts, size):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_grid(bm, x_segments=cuts, y_segments=cuts, size=size, calc_uvs=True)
    bm.to_mesh(mesh)
    bm.free()
    second = mesh.uv_layers.new(name="Second")   # A second map, as a lightmap would be (two views, two maps).
    for loop in second.data:
        loop.uv = (loop.uv[0] * 2.0, loop.uv[1] * 2.0)
    obj = bpy.data.objects.new(name, mesh)
    obj.location, obj.rotation_euler = location, (math.radians(90), 0, 0)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def heavy_scene():
    """400 grids of 1,600 quads (1,280,000 triangles), each with its own UVs: the GUI suite's heavy scene."""
    return [grid(f"Dense{index}", ((index % 20 - 9.5) * 0.45, 0, (index // 20 - 9.5) * 0.45), 40, 0.2)
            for index in range(400)]


def camera():
    rotation = Euler((math.radians(90), 0, 0)).to_matrix().to_4x4()
    view = (Matrix.Translation((0, -20, 0)) @ rotation).inverted()
    scale = 1 / 5.0
    projection = Matrix(((scale * HEIGHT / WIDTH, 0, 0, 0), (0, scale, 0, 0), (0, 0, -.02, -1), (0, 0, 0, 1)))
    return view, projection


_turn = [0]


def frame_ms(navigating):
    """One frame; ``navigating`` turns the view by up to 3 degrees about the scene's vertical axis first."""
    matrix = view
    if navigating:
        _turn[0] += 1
        matrix = view @ Matrix.Rotation(math.radians(3.0 * math.sin(_turn[0] * 0.37)), 4, 'Z')
    started = time.perf_counter()
    offscreen.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, matrix, projection,
                          do_color_management=True)
    with offscreen.bind():
        gpu.state.active_framebuffer_get().read_color(0, 0, 1, 1, 4, 0, 'UBYTE')   # Waits for the GPU.
    return (time.perf_counter() - started) * 1000


def summary(samples):
    return {"median_ms": round(statistics.median(samples), 3), "p75_ms": round(statistics.quantiles(samples, n=4)[2], 3),
            "frames": len(samples), "samples_ms": [round(s, 3) for s in samples]}


def checker(show):
    if hasattr(space, "uv_checker"):
        space.uv_checker.show = show
        space.uv_checker.pattern = 'COLOR_GRID'


def stock_material(objects, on):
    """Blender's way without the checker: one material with the Color Grid image on an Image Texture node."""
    material = bpy.data.materials.get("StockChecker")
    if material is None:
        image = bpy.data.images.new("StockColorGrid", 1024, 1024)
        image.generated_type = 'COLOR_GRID'
        material = bpy.data.materials.new("StockChecker")
        material.use_nodes = True
        nodes = material.node_tree.nodes
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(texture.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    for obj in objects:
        obj.data.materials.clear()
        if on:
            obj.data.materials.append(material)


def run(conditions, objects):
    """Rounds over ``conditions`` {name: (shading, checker, stock)}: per round each one warms, then is measured,
    navigating and static."""
    samples = {(name, kind): [] for name in conditions for kind in ("navigating", "static")}
    for round_index in range(ROUNDS):
        for name, (shading, show, stock) in conditions.items():
            space.shading.type = shading
            checker(show)
            stock_material(objects, stock)
            bpy.context.view_layer.update()
            for _ in range(WARM * (6 if round_index == 0 else 1)):   # Shaders compile over the first draws.
                frame_ms(True)
            samples[(name, "navigating")] += [frame_ms(True) for _ in range(FRAMES)]
            for _ in range(WARM * 4):   # Let the engines settle on the still view.
                frame_ms(False)
            samples[(name, "static")] += [frame_ms(False) for _ in range(FRAMES)]
    return {name: {kind: summary(samples[(name, kind)]) for kind in ("navigating", "static")} for name in conditions}


def timed(n=1):
    return [round(frame_ms(False), 3) for _ in range(n)]


def activation():
    """One-time and switching costs, in draws that finish everything in place (offscreen): what an interactive view
    moves to worker threads shows here as the first frame's time."""
    space.shading.type = 'SOLID'
    checker(False)
    steady_off = timed(20)
    checker(True)
    space.uv_checker.pattern = 'COLOR_GRID'
    cold = timed(4)                                  # Shaders, the Color Grid image, the UV buffer: once.
    steady_on = timed(20)
    checker(False)
    timed(5)
    checker(True)
    space.uv_checker.pattern = 'COLOR_GRID'
    warm = timed(4)                                  # Off and on again: nothing to make.
    space.uv_checker.pattern = 'CHECKER'
    first_checker = timed(2)                         # The UV Grid image's first use.
    space.uv_checker.pattern = 'TEXEL_DENSITY'
    first_density = timed(2)                         # No image: the placeholder.
    switches = []
    for index in range(20):                          # Back and forth between made images.
        space.uv_checker.pattern = ('COLOR_GRID', 'CHECKER')[index % 2]
        switches += timed(1)
    space.uv_checker.pattern = 'COLOR_GRID'
    space.uv_checker.uv_map = 'ACTIVE'
    single = timed(20)
    alternating = []
    for index in range(40):                          # Two views on two maps of the same meshes, drawn in turn.
        if index % 2:
            space.uv_checker.uv_map, space.uv_checker.uv_map_name = 'NAMED', "Second"
        else:
            space.uv_checker.uv_map = 'ACTIVE'
        alternating += timed(1)
    med = lambda values: round(statistics.median(values), 3)
    p75 = lambda values: round(statistics.quantiles(values, n=4)[2], 3)
    return {"steady_off_median": med(steady_off), "steady_on_median": med(steady_on), "steady_on_p75": p75(steady_on),
            "cold_frames": cold, "warm_frames": warm, "first_checker_frames": first_checker,
            "first_density_frames": first_density, "switch_median": med(switches), "switch_p75": p75(switches),
            "switch_max": max(switches), "single_map_median": med(single),
            "alternating_maps_median": med(alternating[4:]), "alternating_maps_p75": p75(alternating[4:]),
            "alternating_maps_max": max(alternating[4:])}


report = {"mode": MODE, "binary": bpy.app.binary_path, "version": bpy.app.version_string, "has_checker": None,
          "size": [WIDTH, HEIGHT]}
try:
    gpu.init()
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    report["has_checker"] = hasattr(space, "uv_checker")
    for name in ("show_floor", "show_axis_x", "show_axis_y", "show_cursor"):
        setattr(space.overlay, name, False)
    space.shading.show_xray = False
    bpy.data.batch_remove(tuple(bpy.data.objects))
    view, projection = camera()
    offscreen = gpu.types.GPUOffScreen(WIDTH, HEIGHT)
    objects = heavy_scene()
    report["scene"] = {"objects": len(objects), "triangles": sum(len(o.data.polygons) * 2 for o in objects)}
    if MODE == "off":
        report["conditions"] = run({"solid_off": ('SOLID', False, False)}, objects)
    elif MODE == "activation":
        report["activation"] = activation()
    else:
        # First use in this process: the first frame with the checker on pays its one-time costs (shader compile in
        # place, as offscreen draws do; the UV buffer's extraction), the next ones are steady.
        space.shading.type = 'SOLID'
        checker(False)
        for _ in range(WARM):
            frame_ms(True)
        checker(True)
        first = [frame_ms(True) for _ in range(4)]
        report["first_use_ms"] = {"first_frame": round(first[0], 3), "next_frames": [round(v, 3) for v in first[1:]]}
        report["conditions"] = run({
            "solid_off": ('SOLID', False, False),
            "solid_checker": ('SOLID', True, False),
            "material_preview_checker": ('MATERIAL', True, False),
            "material_preview_stock_checker_material": ('MATERIAL', False, True),
        }, objects)
        bpy.data.batch_remove(tuple(objects))
        dense = grid("EditDense", (0, 0, 0), 300, 4.0)   # 180,000 triangles in Edit Mode.
        dense.select_set(True)
        bpy.context.view_layer.objects.active = dense
        bpy.ops.object.mode_set(mode='EDIT')
        report["edit_scene"] = {"triangles": len(dense.data.polygons) * 2}
        report["edit_conditions"] = run({
            "edit_solid_off": ('SOLID', False, False),
            "edit_solid_checker": ('SOLID', True, False),
            "edit_material_preview_stock_checker_material": ('MATERIAL', False, True),
        }, [dense])
    offscreen.free()
    report["success"] = True
except Exception:
    import traceback
    report["success"], report["error"] = False, traceback.format_exc()
finally:
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
