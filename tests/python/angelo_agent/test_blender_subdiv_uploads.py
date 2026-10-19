# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native subdivision pixels and uncached redraw costs in an isolated GUI.

Use run_native_suites.py --gui --include test_blender_subdiv_uploads. Compare
the PNG hashes and numerical mesh checks between unmodified and patched builds.
Native draw GTests separately check the exact uploaded buffers, including sizes
larger than the upload chunk. This fixture covers the actual viewport consumers.
"""

import hashlib
import json
import os
from pathlib import Path
import statistics
import struct
import sys
import time
import traceback
import zlib

import bpy
import gpu
from mathutils import Matrix, Vector

assert not bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
report = {"checks": {}, "images": {}, "timings": {}, "binary": bpy.app.binary_path,
          "build_hash": bpy.app.build_hash.decode()}


def check(name, value):
    report["checks"][name] = bool(value)
    assert value, name


def png(path, pixels, width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    # Framebuffer rows start at the bottom; PNG rows start at the top.
    rows = b"".join(b"\0" + pixels[y * width * 4:(y + 1) * width * 4]
                    for y in reversed(range(height)))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" +
                     chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b""))


def make_mesh(edge_count=32, point_count=19):
    # A face, disconnected edges, and disconnected vertices share one subdivided mesh.
    vertices = [(-3, -1, 0), (-1, -1, 0), (-1, 1, 0), (-3, 1, 0)]
    edges = []
    for index in range(edge_count):
        y = -2 + 4 * (index % 128) / 128
        x = (index // 128) * .002
        edges.append((len(vertices), len(vertices) + 1))
        vertices.extend(((x, y, 0), (x + 1, y + .1, .2)))
    for index in range(point_count):
        vertices.append((2 + (index % 5) * .15, -1 + (index // 5) * .15, .1))
    mesh = bpy.data.meshes.new("SubdivUploadMesh")
    mesh.from_pydata(vertices, edges, [(0, 1, 2, 3)])
    obj = bpy.data.objects.new("SubdivUploadObject", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    modifier = obj.modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = 2
    modifier.show_in_editmode = True
    return obj


def run():
    offscreen = None
    try:
        check("opensubdiv", bpy.app.build_options.opensubdiv)
        bpy.context.preferences.system.use_gpu_subdivision = True
        check("gpu_subdivision_enabled", bpy.context.preferences.system.use_gpu_subdivision)
        bpy.data.batch_remove(tuple(bpy.data.objects))
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        space = area.spaces.active
        space.shading.light = "STUDIO"
        space.shading.color_type = "SINGLE"
        space.shading.single_color = (.65, .4, .2)
        space.shading.background_type = "WORLD"
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
        space.overlay.show_cursor = False
        space.shading.show_cavity = False
        width = height = 384
        offscreen = gpu.types.GPUOffScreen(width, height)
        rotation = Vector((0, 0, -1)).to_track_quat("-Z", "Y")
        view = (Matrix.Translation((0, 0, 10)) @ rotation.to_matrix().to_4x4()).inverted()
        projection = Matrix.Diagonal((.25, .25, -.02, 1))

        def draw():
            offscreen.draw_view3d(window.scene, window.view_layer, space, region, view, projection,
                                  do_color_management=True)
            pixels = offscreen.texture_color.read()
            pixels.dimensions = width * height * 4
            return bytes(pixels)

        def capture(name):
            window.view_layer.update()
            for _ in range(3):
                draw()
            pixels = draw()
            check(name + "_nonuniform", len(set(pixels[0::4])) > 8)
            report["images"][name] = hashlib.sha256(pixels).hexdigest()
            png(OUT / (name + ".png"), pixels, width, height)

        with bpy.context.temp_override(window=window, area=area, region=region):
            obj = make_mesh()
            mesh = obj.data
            counts = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons))
            for name, mode, shading in (("solid", "OBJECT", "SOLID"),
                                        ("wire", "OBJECT", "WIREFRAME"),
                                        ("edit", "EDIT", "SOLID"),
                                        ("paint", "VERTEX_PAINT", "SOLID")):
                if obj.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                if mode == "VERTEX_PAINT":
                    mesh.color_attributes.new(name="Color", type="BYTE_COLOR", domain="CORNER")
                    mesh.use_paint_mask = True
                bpy.ops.object.mode_set(mode=mode)
                space.shading.type = shading
                # Baseline paint-selection overlays on mixed loose geometry are nondeterministic.
                # Compare paint shading here; native draw tests verify the exact overlay flags.
                space.overlay.show_overlays = mode != "VERTEX_PAINT"
                capture(name)
            bpy.ops.object.mode_set(mode="OBJECT")
            check("mesh_unchanged", counts == (len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
            bpy.data.objects.remove(obj, do_unlink=True)

            # The same edit and forced readback are timed for every sample. No cached redraws.
            space.shading.type = "WIREFRAME"
            space.overlay.show_overlays = True
            obj = make_mesh(edge_count=4097, point_count=2001)
            times = []
            for index in range(10):
                start = time.perf_counter()
                obj.data.vertices[4].co.z = index * .001
                obj.data.update()
                window.view_layer.update()
                pixels = draw()
                if index >= 3:
                    times.append((time.perf_counter() - start) * 1000)
            report["timings"]["loose_geometry_redraw_ms"] = {
                "samples": times, "median": statistics.median(times), "max": max(times)}
            check("large_nonuniform", len(set(pixels[0::4])) > 8)
            report["images"]["large"] = hashlib.sha256(pixels).hexdigest()
            png(OUT / "large.png", pixels, width, height)

            # Face-only meshes take the empty loose-range path. Keep overlays enabled here,
            # including paint selection, and exercise custom interpolated corner normals.
            bpy.data.objects.remove(obj, do_unlink=True)
            obj = make_mesh(edge_count=0, point_count=0)
            mesh = obj.data
            space.shading.type = "SOLID"
            capture("face_only")
            mesh.polygons[0].use_smooth = True
            mesh.normals_split_custom_set([(0, .6, .8)] * len(mesh.loops))
            obj.modifiers[0].use_custom_normals = True
            check("custom_normals_enabled", mesh.has_custom_normals)
            capture("face_custom_normals")
            check("custom_normals_affect_shading",
                  report["images"]["face_only"] != report["images"]["face_custom_normals"])
            mesh.color_attributes.new(name="Color", type="BYTE_COLOR", domain="CORNER")
            mesh.use_paint_mask = True
            bpy.ops.object.mode_set(mode="VERTEX_PAINT")
            check("face_paint_overlay_enabled", space.overlay.show_overlays and mesh.use_paint_mask)
            capture("face_paint_overlay")
            bpy.ops.object.mode_set(mode="OBJECT")
            check("face_mesh_unchanged", (len(mesh.vertices), len(mesh.edges), len(mesh.polygons)) == (4, 4, 1))
        report["success"] = True
    except Exception:
        report["success"] = False
        report["error"] = traceback.format_exc()
        print(report["error"], flush=True)
    finally:
        if offscreen:
            offscreen.free()
        (OUT / "results.json").write_text(json.dumps(report, indent=2))
        print("SUBDIV_UPLOAD_CHECKS", json.dumps(report), flush=True)
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1.0)
