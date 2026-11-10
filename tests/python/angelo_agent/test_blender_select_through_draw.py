# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select Through's drawing (Blender #73479), offscreen in a background Blender with a GPU.

Answers D6322's "you don't see the geometry you're selecting" and "interferes with edit-mesh
drawing" objections with pixels, for vertex, edge and face select modes:

* With nothing hidden selected, the toggle changes no pixel (face dots included).
* With a hidden cube selected, the toggle adds pixels only where that cube projects (behind the
  front cube's face), and nothing else moves.
* With X-ray on, the toggle changes no pixel (X-ray shows hidden elements itself).
* An In Front object shows its own selected elements behind its surface.
* Draw time with the toggle off and on, over the same frames (reported, labelled with the machine).

The scene: an orthographic front view; cube A (2 m) at the origin hides cube B (1 m) behind it.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
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


def cube(name, size, location):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if isinstance(size, int):
        bmesh.ops.create_cube(bm, size=size)
    else:  # A dense cube for timing: subdivided faces.
        bmesh.ops.create_cube(bm, size=size[0])
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=size[1], use_grid_fill=True)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def front_view():
    rotation = Euler((math.radians(90), 0, 0)).to_matrix().to_4x4()
    view = (Matrix.Translation((0, -10, 0)) @ rotation).inverted()
    scale = 1 / 3.0  # +-3 m visible vertically
    projection = Matrix(((scale * HEIGHT / WIDTH, 0, 0, 0), (0, scale, 0, 0), (0, 0, -.02, -1), (0, 0, 0, 1)))
    return view, projection


def project(point, view, projection):
    clip = projection @ view @ Vector((*point, 1))
    return ((clip.x / clip.w) * .5 + .5) * WIDTH, ((clip.y / clip.w) * .5 + .5) * HEIGHT


def draw(offscreen, space, region, view, projection):
    offscreen.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, view, projection,
                          do_color_management=False)
    with offscreen.bind():
        pixels = gpu.state.active_framebuffer_get().read_color(0, 0, WIDTH, HEIGHT, 4, 0, 'UBYTE')
    pixels.dimensions = WIDTH * HEIGHT * 4
    return bytes(pixels)


def changed_pixels(first, second):
    return [(index // 4 % WIDTH, index // 4 // WIDTH) for index in range(0, len(first), 4)
            if first[index:index + 3] != second[index:index + 3]]


try:
    gpu.init()
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    space.shading.type = 'SOLID'
    space.shading.show_xray = False
    for name in ("show_floor", "show_axis_x", "show_axis_y", "show_cursor"):
        setattr(space.overlay, name, False)
    bpy.data.batch_remove(tuple(bpy.data.objects))
    front, back = cube("A", 2, (0, 0, 0)), cube("B", 1, (0, 4, 0))
    view, projection = front_view()
    offscreen = gpu.types.GPUOffScreen(WIDTH, HEIGHT)
    for obj in (front, back):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = front
    bpy.ops.object.mode_set(mode='EDIT')

    # Where B lands on screen (all of it is inside A's front face), with room for vertex points.
    corners = [project(back.matrix_world @ Vector(c), view, projection) for c in back.bound_box]
    margin = 8
    box = (min(x for x, _ in corners) - margin, max(x for x, _ in corners) + margin,
           min(y for _, y in corners) - margin, max(y for _, y in corners) + margin)
    observed["hidden_cube_screen_box"] = [round(v, 1) for v in box]

    def select(obj, state, front_only=False):
        """Select (or clear) every element, or only those of the face toward the camera (all visible)."""
        bm = bmesh.from_edit_mesh(obj.data)
        for element in (*bm.verts, *bm.edges, *bm.faces):
            verts = [element] if isinstance(element, bmesh.types.BMVert) else element.verts
            element.select = state and (not front_only or all(v.co.y < 0 for v in verts))
        bmesh.update_edit_mesh(obj.data)
        bpy.context.view_layer.update()   # A window evaluates before it draws; an offscreen draw does not.

    for mode in ("VERT", "EDGE", "FACE"):
        bpy.context.tool_settings.mesh_select_mode = (mode == "VERT", mode == "EDGE", mode == "FACE")
        for obj in (front, back):
            select(obj, False)
        select(front, True, front_only=True)  # Visible selection only.
        frames = {}
        for toggle in (False, True):
            space.use_select_through = toggle
            frames[toggle] = draw(offscreen, space, region, view, projection)
        checks[f"{mode.lower()}_no_hidden_selection_no_pixel_changes"] = frames[False] == frames[True]

        select(back, True)  # Now the hidden cube is selected too.
        for toggle in (False, True):
            space.use_select_through = toggle
            frames[toggle] = draw(offscreen, space, region, view, projection)
        changes = changed_pixels(frames[False], frames[True])
        outside = [p for p in changes if not (box[0] <= p[0] <= box[1] and box[2] <= p[1] <= box[3])]
        observed[f"{mode.lower()}_hidden_selection"] = {"changed": len(changes), "outside_the_hidden_cube": len(outside)}
        checks[f"{mode.lower()}_hidden_selection_is_shown"] = len(changes) > 20
        checks[f"{mode.lower()}_hidden_selection_changes_nothing_else"] = not outside

        space.shading.show_xray = True
        for toggle in (False, True):
            space.use_select_through = toggle
            frames[toggle] = draw(offscreen, space, region, view, projection)
        checks[f"{mode.lower()}_xray_draws_the_same_with_or_without_the_toggle"] = frames[False] == frames[True]
        space.shading.show_xray = False

    # In Front (the retopology setup): the selected elements behind its own surface show too (Workbench merges In
    # Front depth into the scene depth the pass reads). Only A's far face is selected; nothing else is.
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    front.show_in_front = True
    bpy.context.view_layer.update()
    for obj in (front, back):
        select(obj, False)
    bm = bmesh.from_edit_mesh(front.data)
    for vert in bm.verts:
        vert.select = vert.co.y > 0
    bm.select_flush_mode()
    bmesh.update_edit_mesh(front.data)
    bpy.context.view_layer.update()
    frames = {}
    for toggle in (False, True):
        space.use_select_through = toggle
        frames[toggle] = draw(offscreen, space, region, view, projection)
    observed["in_front_hidden_selection_changed"] = len(changed_pixels(frames[False], frames[True]))
    checks["in_front_hidden_selection_is_shown"] = observed["in_front_hidden_selection_changed"] > 20
    front.show_in_front = False
    space.use_select_through = False

    # Cost: a dense edit mesh, everything selected (the most the pass draws), toggle off and on, same frames.
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.batch_remove(tuple(bpy.data.objects))
    dense = cube("Dense", (2, 120), (0, 0, 0))  # ~87,000 vertices
    dense.select_set(True)
    bpy.context.view_layer.objects.active = dense
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    select(dense, True)
    observed["dense_vertices"] = len(dense.data.vertices)
    samples = {False: [], True: []}
    for toggle in (False, True):  # Warm-up: shaders and batches.
        space.use_select_through = toggle
        draw(offscreen, space, region, view, projection)
    for _ in range(40):
        for toggle in (False, True):  # Interleaved, so load changes hit both alike.
            space.use_select_through = toggle
            started = time.perf_counter()
            draw(offscreen, space, region, view, projection)
            samples[toggle].append((time.perf_counter() - started) * 1000)
    report["timings"] = {"off_median_ms": round(statistics.median(samples[False]), 3),
                         "on_median_ms": round(statistics.median(samples[True]), 3),
                         "off_p75_ms": round(statistics.quantiles(samples[False], n=4)[2], 3),
                         "on_p75_ms": round(statistics.quantiles(samples[True], n=4)[2], 3),
                         "note": "offscreen draw + read-back, interleaved, 40 frames each; untimed claim unless on a quiet machine"}
    offscreen.free()
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("SELECT_THROUGH_DRAW failed checks:", json.dumps(failed), "timings:", json.dumps(report["timings"]))
assert report["success"], failed
