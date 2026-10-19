# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure world_light_table.SKY_TABLE from Blender's own Sky texture (run it again for a new Blender version).

    Blender -b --factory-startup --python tests/python/angelo_agent/measure_sky_table.py [-- --write]

For each sky type and sun elevation it renders the world alone with Cycles (16 x 16 px, 4 samples, CPU, a 50 mm
camera) in three directions: the horizon toward the sun (the sun at +Y: sun_rotation 0, or sun_direction
(0, cos e, sin e) for the analytic types), the horizon away from it and the zenith, and keeps the mean scene-linear RGB
from a 32-bit EXR. It prints the table; ``--write`` replaces the table in scripts/addons_core/angelo_agent/
world_light_table.py. test_native_world_light pins how it is read, so a new measurement is a reviewed change. About one
minute on an M-series Mac. The default atmosphere only (world_light_table's docstring).
"""

import math
import os
import sys
import tempfile
from pathlib import Path

import bpy
import numpy

SKY_MODULE = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/world_light_table.py"
TYPES = ("SINGLE_SCATTERING", "MULTIPLE_SCATTERING", "PREETHAM", "HOSEK_WILKIE")
ELEVATIONS = (-6, -3, 0, 2, 5, 8, 11, 15, 20, 25, 30, 45, 60, 90)
DIRECTIONS = ((math.radians(88), 0.0, 0.0), (math.radians(88), 0.0, math.pi), (math.pi, 0.0, 0.0))


def setup():
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    world = bpy.data.worlds.new("Sky table")
    scene.world = world
    world.use_nodes = True
    tree = world.node_tree
    sky = tree.nodes.new("ShaderNodeTexSky")
    tree.links.new(sky.outputs["Color"], tree.nodes["Background"].inputs["Color"])
    camera = bpy.data.objects.new("Sky table camera", bpy.data.cameras.new("Sky table camera"))
    camera.data.lens = 50
    scene.collection.objects.link(camera)
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.samples, scene.cycles.device = 4, "CPU"
    scene.render.resolution_x = scene.render.resolution_y = 16
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format, scene.render.image_settings.color_depth = "OPEN_EXR", "32"
    sky.sun_rotation = 0.0
    return scene, sky, camera


def mean_colour(scene, path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path, check_existing=False)
    try:
        pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
        return tuple(float("%.4g" % c) for c in pixels.reshape(-1, 4)[:, :3].mean(axis=0))
    finally:
        bpy.data.images.remove(image)


def measure():
    scene, sky, camera = setup()
    path = os.path.join(tempfile.mkdtemp(prefix="angelo-sky-table-"), "sky.exr")
    table = {}
    for kind in TYPES:
        sky.sky_type = kind
        rows = []
        for elevation in ELEVATIONS:
            angle = math.radians(elevation)
            sky.sun_elevation = angle
            sky.sun_direction = (0.0, math.cos(angle), math.sin(angle))
            colours = []
            for rotation in DIRECTIONS:
                camera.rotation_euler = rotation
                colours.append(mean_colour(scene, path))
            rows.append((elevation, *colours))
        table[kind] = rows
    return table


def table_source(table):
    lines = ["SKY_TABLE = {"]
    for kind, rows in table.items():
        lines.append("    %r: (" % kind)
        lines += ["        (%d, %r, %r, %r)," % row for row in rows]
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write(source):
    text = SKY_MODULE.read_text(encoding="utf-8")
    start = text.index("SKY_TABLE = {")
    end = text.index("\n}\n", start) + 3
    SKY_MODULE.write_text(text[:start] + source + text[end:], encoding="utf-8")


if __name__ == "__main__":
    source = table_source(measure())
    print(source)
    if "--write" in sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else False:
        write(source)
        print("wrote", SKY_MODULE)
