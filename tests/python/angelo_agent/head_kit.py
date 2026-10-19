# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender builders of the face and hair one-shot tests' heads (inside Blender; ground truths and start files).

``head_mesh`` meshes a ``head_designs`` design with ``head_sdf.surface_nets``
into one smooth-shaded mesh object; ``eyes`` adds the two eyeballs as their
own objects (a white sclera, an iris and a pupil looking along -Y);
``portrait_lights`` is the three-point light every head test's ground truth
is lit and rendered with. Names follow the briefs: ``Head``, ``Head Eye L``
(the person's left eye, at +X) and ``Head Eye R``.

The start file of the portrait and hair tests is made here too
(``save_start``, ``append_start``): both sides append the same bytes into
their factory scene before the first message.
"""

import hashlib
import math
from pathlib import Path

import numpy

import head_designs
import head_sdf
import scenario_build_kit as kit

SKIN = (0.80, 0.62, 0.52)
CLAY = (0.62, 0.56, 0.50)
EYE_WHITE, IRIS, PUPIL = (0.86, 0.85, 0.82), (0.20, 0.13, 0.08), (0.01, 0.01, 0.01)
START_OBJECTS = ("Head", "Head Eye L", "Head Eye R")


def mesh_from_quads(bpy, name, vertices, quads):
    """A mesh data-block from NumPy vertices and quads, filled in bulk."""
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", numpy.ascontiguousarray(vertices, dtype=numpy.float32).ravel())
    mesh.loops.add(quads.size)
    mesh.loops.foreach_set("vertex_index", numpy.ascontiguousarray(quads, dtype=numpy.int32).ravel())
    mesh.polygons.add(len(quads))
    mesh.polygons.foreach_set("loop_start", numpy.arange(0, quads.size, 4, dtype=numpy.int32))
    mesh.update(calc_edges=True)
    mesh.validate(clean_customdata=False)
    mesh.shade_smooth()
    return mesh


def head_mesh(bpy, design, name="Head", cell=0.0012, material=None):
    """The design's surface as one mesh object named ``name`` (smooth shaded, one material)."""
    vertices, quads = head_sdf.surface_nets(design, design["low"], design["high"], cell)
    obj = bpy.data.objects.new(name, mesh_from_quads(bpy, name, vertices, quads))
    kit.link(bpy, obj)
    if material is not None:
        obj.data.materials.append(material)
    return obj


def eye_material(bpy, name, radius, iris=IRIS, iris_start=0.80):
    """Sclera, iris and pupil from the eyeball's own -Y axis (Object coordinates over its radius)."""
    material = kit.principled(bpy, name, EYE_WHITE, roughness=0.15)
    tree = material.node_tree
    bsdf = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    scale = tree.nodes.new("ShaderNodeMath")
    scale.operation, scale.inputs[1].default_value = "MULTIPLY", -1.0 / radius
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    tree.links.new(coordinates.outputs["Object"], separate.inputs["Vector"])
    tree.links.new(separate.outputs["Y"], scale.inputs[0])
    tree.links.new(scale.outputs["Value"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    elements = ramp.color_ramp.elements
    ramp.color_ramp.interpolation = "LINEAR"
    elements[0].position, elements[0].color = 0.0, (*EYE_WHITE, 1.0)
    elements[1].position, elements[1].color = iris_start, (*EYE_WHITE, 1.0)
    pupil = iris_start + (1.0 - iris_start) * 0.65
    for position, colour in ((iris_start + 0.015, iris), (pupil, iris), (pupil + 0.015, PUPIL), (1.0, PUPIL)):
        element = elements.new(position)
        element.color = (*colour, 1.0)
    return material


def eyes(bpy, design, segments=48, rings=24):
    """``Head Eye L`` (+X) and ``Head Eye R`` (-X): UV spheres, scaled where the design says, one material."""
    made = []
    for spec in design["eyes"]:
        radius = spec["radius"]
        material = eye_material(bpy, "Head Eye", radius, iris_start=spec.get("iris", 0.80))
        for side, sign in (("L", 1.0), ("R", -1.0)):
            x, y, z = spec["centre"]
            vertices, faces = kit.uv_sphere_geometry(radius, segments, rings)
            obj = kit.mesh_object(bpy, "Head Eye " + side, vertices, faces, location=(sign * x, y, z), smooth=True)
            obj.scale = spec.get("scale", (1.0, 1.0, 1.0))
            obj.data.materials.append(material)
            made.append(obj)
    return made


def build_head(bpy, name, colour=SKIN, cell=0.0012):
    """The named design's head and eyes in the current scene; returns ``(head, [eye L, eye R])``."""
    design = head_designs.design(name)
    material = kit.principled(bpy, "Head Skin", colour, roughness=0.55)
    head = head_mesh(bpy, design, "Head", cell, material)
    return head, eyes(bpy, design)


def area_light(bpy, name, location, target, energy, size, colour=(1.0, 1.0, 1.0)):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.size, data.color = energy, size, colour
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    obj.rotation_euler = kit.look_at_rotation(location, target)
    return kit.link(bpy, obj)


def portrait_lights(bpy, target=(0.0, 0.0, 0.0)):
    """A key from the camera's left, above; a softer fill from the right; a rim from behind (the briefs' lights)."""
    return [area_light(bpy, "Key Light", (-0.9, -1.1, 0.75), target, 60.0, 0.6, (1.0, 0.97, 0.92)),
            area_light(bpy, "Fill Light", (1.2, -0.9, 0.1), target, 18.0, 1.0, (0.92, 0.96, 1.0)),
            area_light(bpy, "Rim Light", (0.3, 1.2, 0.6), target, 35.0, 0.5)]


def studio_world(bpy, name="Studio", colour=(0.30, 0.31, 0.33), strength=0.6):
    return kit.colour_world(bpy, name, colour, strength)


def light_distance(location, target):
    return math.dist(location, target)


# ---------------------------------------------------------------- start files


def save_start(bpy, path, objects=START_OBJECTS):
    """Save only ``objects`` (and what they use) to ``path``; returns its name, SHA-256 and size."""
    path = Path(path)
    chosen = {bpy.data.objects[name] for name in objects}
    bpy.data.libraries.write(str(path), chosen, compress=False)
    data = path.read_bytes()
    return {"file": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "objects": list(objects)}


def append_start(bpy, scene, path, sha256, objects=START_OBJECTS):
    """Both sides, before the first message: the start file's objects appended into ``scene`` (SHA-256 checked)."""
    path = Path(path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != sha256:
        raise ValueError("Start file %s is not the one the references were made with (%s != %s)"
                         % (path, actual, sha256))
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        target.objects = [name for name in source.objects if name in objects]
    appended = []
    for obj in target.objects:
        if obj is not None:
            scene.collection.objects.link(obj)
            appended.append(obj.name)
    return {"appended": sorted(appended), "sha256": actual}
