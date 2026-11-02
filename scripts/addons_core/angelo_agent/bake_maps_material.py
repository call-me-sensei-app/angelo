# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The baked material: a new Principled material wired to a finished texture set's published files.

Contract: doc/angelo/long_jobs.rst, "Texture sets". Made only on request
(``material``), after the files are published, so it reads them where they
stay. The original materials are never changed and the new one is not assigned:
it is kept by a fake user, and the card's Use button (or the agent) assigns it.
Each packed file is unpacked by nodes as its preset packs it (glTF/ORM green and
blue, Unity's smoothness inverted, Unreal's DirectX green flipped); emission
above 1 gets its strength back, and height drives a Displacement node with the
midlevel and scale that give the baked values back. Ambient occlusion has no
Principled input and is left for the engine. Main thread only.
"""

from pathlib import Path

import bpy

from .bake_maps_spec import DIRECTX

GAP = 300.0


def _image(folder, entries, name, colour, image_format):
    """The image of one map: a file, or a UDIM image when the set has tiles."""
    files = [entry for entry in entries if entry["map"] == name]
    if not files:
        return None
    first = files[0]
    if "tile" in first:
        pattern = first["file"].replace(".%d." % first["tile"], ".<UDIM>.", 1)
        image = bpy.data.images.load(str(Path(folder) / pattern), check_existing=False)
        image.source = 'TILED'
    else:
        image = bpy.data.images.load(str(Path(folder) / first["file"]), check_existing=False)
    if not colour:
        image.colorspace_settings.is_data = True
        image.alpha_mode = 'CHANNEL_PACKED'   # A packed map's A is data (smoothness), never alpha.
    elif image_format != "OPEN_EXR":   # An OpenEXR colour map holds scene-linear values, Angelo's default for it.
        image.colorspace_settings.name = "sRGB"
    return image


def _unique(base):
    name, number = base[:60], 1
    while bpy.data.materials.get(name) is not None:
        number += 1
        name = "%s %d" % (base[:56], number)
    return name


def create(target_name, entries, folder, request, channels, facts):
    """Make the material for the set of ``channels``; returns its name."""
    material = bpy.data.materials.new(_unique("%s Baked" % target_name))
    if material.node_tree is None:
        material.use_nodes = True
    material.use_fake_user = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output.location, bsdf.location = (GAP, 0.0), (0.0, 0.0)
    links.new(bsdf.outputs[0], output.inputs["Surface"])
    image_format, packing = request.get("format", "PNG"), request["packing"]
    row = [0.0]

    def texture(name, colour):
        image = _image(folder, entries, name, colour, image_format)
        if image is None:
            return None
        node = nodes.new("ShaderNodeTexImage")
        node.image, node.label = image, name
        node.location = (-3 * GAP, row[0])
        row[0] -= GAP
        return node

    def separate(node):
        split = nodes.new("ShaderNodeSeparateColor")
        split.location = (node.location.x + GAP, node.location.y)
        links.new(node.outputs["Color"], split.inputs[0])
        return split

    def invert(socket, at):
        maths = nodes.new("ShaderNodeMath")
        maths.operation, maths.location = 'SUBTRACT', at
        maths.inputs[0].default_value = 1.0
        links.new(socket, maths.inputs[1])
        return maths.outputs[0]

    base = texture("BaseMap" if packing == "UNITY" else "BaseColor", True)
    if base is not None:
        links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
        if packing != "SEPARATE" and "ALPHA" in channels:   # Packed presets keep alpha in the base colour's A.
            links.new(base.outputs["Alpha"], bsdf.inputs["Alpha"])
    for name, socket in (("Metallic", "Metallic"), ("Roughness", "Roughness"), ("Alpha", "Alpha")):
        node = texture(name, False)
        if node is not None:
            links.new(node.outputs["Color"], bsdf.inputs[socket])
    packed = texture({"UNITY": "MaskMap", "GLTF": "OcclusionRoughnessMetallic"}.get(packing, "ORM"), False) \
        if packing != "SEPARATE" else None
    if packed is not None:
        split = separate(packed)
        if packing == "UNITY":
            links.new(split.outputs[0], bsdf.inputs["Metallic"])
            links.new(invert(packed.outputs["Alpha"], (split.location.x, split.location.y - 160)),
                      bsdf.inputs["Roughness"])
        else:
            links.new(split.outputs[1], bsdf.inputs["Roughness"])
            links.new(split.outputs[2], bsdf.inputs["Metallic"])
    normal = texture("Normal", False)
    if normal is not None:
        mapping = nodes.new("ShaderNodeNormalMap")
        mapping.location = (-GAP, normal.location.y)
        colour = normal.outputs["Color"]
        if packing in DIRECTX:   # DirectX stores green flipped: flip it back for Angelo's OpenGL convention.
            split = separate(normal)
            join = nodes.new("ShaderNodeCombineColor")
            join.location = (split.location.x + GAP / 2, split.location.y)
            links.new(split.outputs[0], join.inputs[0])
            links.new(invert(split.outputs[1], (split.location.x, split.location.y - 160)), join.inputs[1])
            links.new(split.outputs[2], join.inputs[2])
            colour = join.outputs[0]
        links.new(colour, mapping.inputs["Color"])
        links.new(mapping.outputs[0], bsdf.inputs["Normal"])
    emission = texture("Emission", True)
    if emission is not None:
        links.new(emission.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = float(facts.get("emission_strength", 1.0))
    height = texture("Height", False)
    if height is not None:
        displacement = nodes.new("ShaderNodeDisplacement")
        displacement.location = (0.0, -2 * GAP)
        settings = facts.get("height") or {"midlevel": 0.5, "scale": 1.0}
        displacement.inputs["Midlevel"].default_value = settings["midlevel"]
        displacement.inputs["Scale"].default_value = settings["scale"]
        links.new(height.outputs["Color"], displacement.inputs["Height"])
        links.new(displacement.outputs[0], output.inputs["Displacement"])
    if "ALPHA" in channels:
        material.surface_render_method = 'DITHERED'
    from . import material_display_sync
    # Solid view shows the baked maps' look (their images were just loaded, so reading them is cheap).
    material_display_sync.sync(material, material_display_sync.CREATED,
                               images=material_display_sync.LOAD)
    return material.name
