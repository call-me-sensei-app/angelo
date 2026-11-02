# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unrelated scenes for the region render pixel suite (test_blender_render_region_pixels).

Each is built from nothing, so no scene file or LFS object is needed, and each differs from the others in what a
region render must handle: geometry and camera, lights, materials, passes, view layers, film transparency and
denoising. None is tuned to the feature: they are ordinary look-dev scenes, and the suite's checks apply to all.
"""

import math

import bpy

PASSES = ("use_pass_z", "use_pass_normal", "use_pass_mist", "use_pass_diffuse_color", "use_pass_emit",
          "use_pass_ambient_occlusion")


def reset():
    for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras,
                       bpy.data.worlds):
        for item in list(collection):
            collection.remove(item)
    scene = bpy.context.scene
    while len(scene.view_layers) > 1:
        scene.view_layers.remove(scene.view_layers[-1])
    return scene


def material(name, color, metallic=0.0, roughness=0.5, emission=0.0, transmission=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Transmission Weight"].default_value = transmission
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*color, 1)
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def add(primitive, location, mat, **options):
    getattr(bpy.ops.mesh, "primitive_%s_add" % primitive)(location=location, **options)
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    return obj


def camera(location, target, lens=50.0):
    data = bpy.data.cameras.new("Camera")
    data.lens = lens
    obj = bpy.data.objects.new("Camera", data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    direction = [t - l for t, l in zip(target, location)]
    obj.rotation_euler = (math.atan2(math.hypot(direction[0], direction[1]), -direction[2]), 0,
                          math.atan2(direction[1], direction[0]) - math.pi / 2)
    bpy.context.scene.camera = obj
    return obj


def light(kind, location, energy, size=1.0, color=(1, 1, 1)):
    data = bpy.data.lights.new(kind.title(), kind)
    data.energy, data.color = energy, color
    if kind == 'AREA':
        data.size = size
    obj = bpy.data.objects.new(data.name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (math.radians(40), 0, math.radians(30))
    return obj


def world(color, strength):
    data = bpy.data.worlds.new("World")
    data.use_nodes = True
    background = data.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (*color, 1)
    background.inputs["Strength"].default_value = strength
    bpy.context.scene.world = data


def product():
    """A metal sphere and a matte cube on a floor under an area light, a front camera, every listed pass."""
    scene = reset()
    add("plane", (0, 0, 0), material("Floor", (0.6, 0.58, 0.55), roughness=0.8), size=20)
    add("uv_sphere", (-0.8, 0, 1), material("Metal", (0.9, 0.7, 0.4), metallic=1.0, roughness=0.25), radius=1)
    add("cube", (1.4, 0.6, 0.6), material("Paint", (0.1, 0.3, 0.8)), size=1.2)
    light('AREA', (3, -3, 5), 800, size=3)
    world((0.05, 0.06, 0.08), 1.0)
    camera((0, -7, 2.5), (0, 0, 0.9))
    return scene, "Paint"


def city():
    """A grid of towers with emissive windows at dusk, a high oblique camera, a second view layer."""
    scene = reset()
    add("plane", (0, 0, 0), material("Ground", (0.15, 0.15, 0.16)), size=60)
    concrete, glow = material("Concrete", (0.5, 0.5, 0.52)), material("Windows", (1.0, 0.75, 0.4), emission=4.0)
    for i in range(5):
        for j in range(5):
            height = 1.5 + ((i * 7 + j * 3) % 5)
            tower = add("cube", (i * 3 - 6, j * 3 - 6, height / 2), concrete, size=1)
            tower.scale = (1.2, 1.2, height)
            if (i + j) % 2:
                strip = add("cube", (i * 3 - 6, j * 3 - 6 - 0.62, height / 2), glow, size=1)
                strip.scale = (0.8, 0.02, height * 0.8)
    light('SUN', (0, 0, 10), 1.5, color=(1.0, 0.6, 0.4))
    world((0.1, 0.12, 0.25), 0.6)
    camera((14, -16, 14), (0, 0, 1), lens=35)
    scene.view_layers.new("Towers only")
    return scene, "Concrete"


def glass():
    """Glass and a glowing torus over a dark floor, point lights, transparent film (alpha matters)."""
    scene = reset()
    add("plane", (0, 0, 0), material("Dark", (0.02, 0.02, 0.025), roughness=0.3), size=10)
    add("monkey", (0, 0, 1.2), material("Glass", (0.95, 0.98, 1.0), roughness=0.05, transmission=1.0), size=1.6)
    add("torus", (1.8, 0.8, 0.5), material("Neon", (0.2, 1.0, 0.6), emission=8.0))
    light('POINT', (-2, -2, 3), 400, color=(1.0, 0.8, 0.9))
    light('POINT', (2, -1, 2.5), 250, color=(0.6, 0.8, 1.0))
    world((0, 0, 0), 0.0)
    camera((0.5, -5.5, 2.2), (0.3, 0, 1.0), lens=60)
    scene.render.film_transparent = True
    return scene, "Glass"


SCENES = {"product": product, "city": city, "glass": glass}


def configure(scene, engine, denoise):
    """Small frames and few samples (the checks are about pixels, not looks), every listed pass, one fixed seed."""
    render = scene.render
    render.engine = engine
    render.resolution_x, render.resolution_y, render.resolution_percentage = 320, 240, 100
    render.use_border = False
    render.use_crop_to_border = False
    render.image_settings.media_type = 'MULTI_LAYER_IMAGE'   # Every layer and pass in one OpenEXR (Blender 5).
    render.image_settings.color_depth = '32'
    for layer in scene.view_layers:
        for name in PASSES:
            if hasattr(layer, name):
                setattr(layer, name, True)
    if engine == 'CYCLES':
        scene.cycles.device = 'CPU'
        scene.cycles.samples = 16
        scene.cycles.seed = 7
        scene.cycles.use_denoising = denoise
        scene.cycles.use_adaptive_sampling = False
    else:
        scene.eevee.taa_render_samples = 8
