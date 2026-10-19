# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render the not-connected chat's showcase still: a stone bridge over a river at dusk, in Cycles.

Run with any Blender 5.2 (bpy only):

    blender --background --factory-startup --python release/branding/angelo/hero_still.py

It builds the scene from primitives (a masonry bridge with a ring of
voussoirs, a parapet and two teal lanterns, grassy banks with trees, distant
hills, a river valley, a low golden sun and a graded sky), renders it at 1600x900 with denoising and writes
``scripts/addons_core/angelo_agent/assets/hero-stone-bridge.webp``. Text-free:
the app draws its caption live, so it is translated. A placeholder until the
recorded bridge demo exists (``connect_hero.MEDIA``).
"""

import math
import random
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
TARGET = HERE.parents[2] / "scripts/addons_core/angelo_agent/assets/hero-stone-bridge.webp"


def material(name, color, roughness=0.6, emission=None, strength=0.0, bump=0.0, transmission=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Transmission Weight"].default_value = transmission
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1)
        bsdf.inputs["Emission Strength"].default_value = strength
    if bump:
        noise = nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 18.0
        noise.inputs["Detail"].default_value = 8.0
        bumped = nodes.new("ShaderNodeBump")
        bumped.inputs["Strength"].default_value = bump
        links.new(noise.outputs["Fac"], bumped.inputs["Height"])
        links.new(bumped.outputs["Normal"], bsdf.inputs["Normal"])
        ramp = nodes.new("ShaderNodeMix")
        ramp.data_type = 'RGBA'
        ramp.inputs["A"].default_value = (*[c * 0.78 for c in color], 1)
        ramp.inputs["B"].default_value = (*color, 1)
        links.new(noise.outputs["Fac"], ramp.inputs["Factor"])
        links.new(ramp.outputs["Result"], bsdf.inputs["Base Color"])
    return mat


def tree(location, height, foliage, bark):
    """A stylized tree: a tapered trunk and two stacked cones, for scale and silhouette."""
    x, y, z = location
    bpy.ops.mesh.primitive_cylinder_add(radius=0.09 * height, depth=height * 0.5, location=(x, y, z + height * 0.25))
    bpy.context.object.data.materials.append(bark)
    for index, (radius, lift) in enumerate(((0.42, 0.55), (0.3, 0.85))):
        bpy.ops.mesh.primitive_cone_add(radius1=radius * height, depth=height * 0.55, vertices=24,
                                        location=(x, y, z + height * lift))
        bpy.context.object.data.materials.append(foliage)
        bpy.ops.object.shade_smooth()


def valley(river, mat):
    """Ground with a river channel along Y: low in the middle, rising into grassy banks, with gentle noise."""
    from mathutils import noise
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=220, y_subdivisions=220, size=90, location=(0, 20, 0))
    ground = bpy.context.object
    for vertex in ground.data.vertices:
        x, y = vertex.co.x, vertex.co.y + 20
        rise = min(max((abs(x) - river + 0.6) / 2.8, 0.0), 1.0)
        rise = rise * rise * (3 - 2 * rise)
        vertex.co.z = -1.2 + rise * 1.9 + rise * 0.35 * noise.noise(Vector((x * 0.18, y * 0.18, 0.0)))
    ground.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return ground


def block(name, location, scale, mat, rotation=(0, 0, 0), bevel=0.06):
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(scale=True)
    modifier = obj.modifiers.new("Bevel", 'BEVEL')
    modifier.width, modifier.segments = bevel, 3
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return obj


def masonry(name, light, dark, scale=3.2):
    """Coursed stone: a brick pattern in object space with bumped mortar joints."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.88
    coords, mapping = nodes.new("ShaderNodeTexCoord"), nodes.new("ShaderNodeMapping")
    mapping.inputs["Rotation"].default_value = (math.radians(90), 0, 0)  # Courses run along X on the X/Z faces.
    bricks = nodes.new("ShaderNodeTexBrick")
    bricks.inputs["Scale"].default_value = scale
    bricks.inputs["Mortar Size"].default_value = 0.018
    bricks.inputs["Color1"].default_value = (*light, 1)
    bricks.inputs["Color2"].default_value = (*dark, 1)
    bricks.inputs["Mortar"].default_value = (*[c * 0.55 for c in dark], 1)
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 22.0
    noise.inputs["Detail"].default_value = 10.0
    tint = nodes.new("ShaderNodeMix")
    tint.data_type, tint.blend_type = 'RGBA', 'MULTIPLY'
    tint.inputs["Factor"].default_value = 0.35
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.6
    links.new(coords.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], bricks.inputs["Vector"])
    links.new(bricks.outputs["Color"], tint.inputs["A"])
    links.new(noise.outputs["Color"], tint.inputs["B"])
    links.new(tint.outputs["Result"], bsdf.inputs["Base Color"])
    links.new(bricks.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    random.seed(7)
    walls = masonry("Masonry", (0.66, 0.55, 0.42), (0.44, 0.38, 0.31), scale=1.1)
    stones = [material("Stone%d" % i, tone, 0.85, bump=0.45) for i, tone in
              enumerate(((0.60, 0.53, 0.44), (0.53, 0.47, 0.39), (0.64, 0.56, 0.46)))]
    cap = material("Coping", (0.42, 0.38, 0.33), 0.8, bump=0.3)
    grass = material("Grass", (0.10, 0.20, 0.06), 0.95, bump=0.6)
    hills = material("Hills", (0.22, 0.22, 0.30), 1.0)
    foliage = material("Foliage", (0.05, 0.13, 0.05), 0.9, bump=0.4)
    bark = material("Bark", (0.14, 0.09, 0.06), 0.9)
    water = material("Water", (0.01, 0.035, 0.04), 0.02, bump=0.03)
    lantern = material("Lantern", (0.05, 0.3, 0.3), 0.3, emission=(0.10, 0.75, 0.72), strength=6.0)
    radius, depth, top = 2.8, 2.0, 3.2
    length = 2 * radius + 6.0  # The ends run into the banks.
    body = block("Bridge", (0, 0, (top - 0.4) / 2), (length / 2, depth / 2, (top + 0.4) / 2), walls, bevel=0.03)
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth + 1, vertices=96, location=(0, 0, -0.1),
                                        rotation=(math.radians(90), 0, 0))
    opening = bpy.context.object
    opening.hide_render = opening.hide_viewport = True
    cut = body.modifiers.new("Arch", 'BOOLEAN')
    cut.operation, cut.object = 'DIFFERENCE', opening
    body.modifiers.move(len(body.modifiers) - 1, 0)  # Cut first, then bevel the cut edges.
    pieces = 21
    for index in range(pieces):  # A ring of voussoirs standing proud of both faces, each turned to the arc.
        angle = math.pi * (index + 0.5) / pieces
        middle = radius + 0.24
        x, z = -math.cos(angle) * middle, math.sin(angle) * middle - 0.1
        block("Voussoir%02d" % index, (x, 0, z), (0.17 * random.uniform(0.92, 1.0), depth / 2 + 0.06, 0.26),
              random.choice(stones), rotation=(0, angle - math.pi / 2, 0), bevel=0.035)
    for side in (-1, 1):
        block("Parapet%d" % side, (0, side * (depth / 2 - 0.12), top + 0.28), (length / 2, 0.12, 0.28), walls, bevel=0.02)
        block("Coping%d" % side, (0, side * (depth / 2 - 0.12), top + 0.6), (length / 2 + 0.05, 0.17, 0.05), cap, bevel=0.02)
        post = (side * (length / 2 - 0.25), -depth / 2 + 0.12, top + 0.9)
        block("LanternPost%d" % side, post, (0.13, 0.13, 0.3), cap, bevel=0.02)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.16, location=(post[0], post[1], top + 1.36))
        bpy.context.object.data.materials.append(lantern)
        bpy.ops.object.shade_smooth()
        light = bpy.data.lights.new("Glow%d" % side, 'POINT')
        light.energy, light.color, light.shadow_soft_size = 30, (0.2, 0.85, 0.8), 0.2
        glow = bpy.data.objects.new("Glow%d" % side, light)
        glow.location = (post[0], post[1] - 0.3, top + 1.36)
        bpy.context.collection.objects.link(glow)
    valley(radius + 0.4, grass)
    for _ in range(26):  # Trees along both banks, for scale and silhouette; none in front of the bridge.
        side, x = random.choice((-1, 1)), random.uniform(radius + 3.0, radius + 14.0)
        tree((side * x, random.uniform(-2.0, 18.0), 0.55), random.uniform(2.2, 3.8), foliage, bark)
    for index in range(5):  # Distant hills: cool, low-contrast layers for depth.
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=48, ring_count=24,
                                             location=(-30 + index * 15, 48 + (index % 2) * 8, -2.0))
        hill = bpy.context.object
        hill.scale = (random.uniform(11, 16), 7, random.uniform(4.0, 7.0))
        hill.data.materials.append(hills)
        bpy.ops.object.shade_smooth()
    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -0.25))
    bpy.context.object.data.materials.append(water)
    sun = bpy.data.lights.new("Sun", 'SUN')
    sun.energy, sun.color, sun.angle = 6.0, (1.0, 0.68, 0.40), math.radians(2)
    sun_object = bpy.data.objects.new("Sun", sun)
    sun_object.rotation_euler = (math.radians(80), 0, math.radians(-64))  # Low from the left: grazing light on the stone.
    bpy.context.collection.objects.link(sun_object)
    fill = bpy.data.lights.new("Fill", 'SUN')
    fill.energy, fill.color = 0.6, (0.55, 0.65, 0.9)
    fill_object = bpy.data.objects.new("Fill", fill)
    fill_object.rotation_euler = (math.radians(60), 0, math.radians(30))
    bpy.context.collection.objects.link(fill_object)
    world = bpy.data.worlds.new("Sky")
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    coords, split = nodes.new("ShaderNodeTexCoord"), nodes.new("ShaderNodeSeparateXYZ")
    ramp = nodes.new("ShaderNodeValToRGB")  # By elevation: warm haze at the horizon, deep blue overhead.
    ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[0].color = 0.0, (1.0, 0.55, 0.28, 1)
    ramp.color_ramp.elements[1].position, ramp.color_ramp.elements[1].color = 0.35, (0.10, 0.20, 0.42, 1)
    middle = ramp.color_ramp.elements.new(0.1)
    middle.color = (0.75, 0.45, 0.40, 1)
    links.new(coords.outputs["Generated"], split.inputs["Vector"])
    links.new(split.outputs["Z"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], nodes["Background"].inputs["Color"])
    nodes["Background"].inputs["Strength"].default_value = 0.6
    bpy.context.scene.world = world
    camera_data = bpy.data.cameras.new("Camera")
    camera_data.lens, camera_data.dof.use_dof, camera_data.dof.aperture_fstop = 35, True, 5.6
    camera = bpy.data.objects.new("Camera", camera_data)
    camera.location = (-3.0, -23.0, 1.5)  # Low over the river, which leads the eye to the arch.
    direction = Vector((1.2, 0, 2.3)) - camera.location
    camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    camera_data.dof.focus_distance = direction.length
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera


def render():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples, scene.cycles.use_denoising = 96, True
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1600, 900, 100
    scene.view_settings.view_transform, scene.view_settings.look = 'AgX', 'AgX - Medium High Contrast'
    scene.render.image_settings.file_format = 'WEBP'
    scene.render.image_settings.quality = 82
    scene.render.filepath = str(TARGET)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    print("Wrote", TARGET)


build()
render()
