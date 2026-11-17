# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Principled material from a PBR texture set. Converter child only (bpy, numpy).

Every image of the set is loaded once and a small copy sampled (``fab_surface.features``); ``fab_surface.classify``
gives each its role from names and pixels. ``build`` wires the roles the way Blender's own glTF importer and the
Angelo Library do: base colour in sRGB (times ambient occlusion), the rest as data; roughness, or one minus gloss;
metalness; a channel-packed ORM/ARM/MRA map split into its roles; OpenGL normals through a Normal Map node and
DirectX normals with green inverted first; a bump map when there is no normal map; opacity (or the base colour's
own alpha) into Alpha; emission; displacement as bump through a Displacement node. A Mapping node comes first so
the tiling stays editable. Maps with no Principled input (cavity, specular next to metalness, translucency) are
left out and listed.

``surface`` makes a texture set's material; ``complete_untextured`` gives a model whose own materials hold no
texture (Megascans FBX files and many sellers' zips ship the maps loose beside the mesh) the material of the maps
it came with. It runs after the download's images are packed (``fab_convert_clean.pack_images``), so an image node
whose file the download does not hold is empty by then and holds no texture: a material whose image nodes are all
empty gets the loose set's material, and an empty node in a material that keeps other textures gets the loose map of
the role its slot takes (base colour, alpha, roughness, metalness, emission, normal). Megascans' "English Ivy" raw FBX
names its base colour by a path from the author's disk, so its leaves rendered black (release shoot, 2026-11-12).
"""

from pathlib import Path

import bpy
import numpy as np

import fab_convert_guard as guard
import fab_formats
import fab_surface

SAMPLE, NORMAL_SAMPLE = 48, 128
BUMP_DISTANCE = 0.02            # Displacement as bump, metres (the Angelo Library's texture sets use the same).
COLOUR_ROLES = frozenset({'base_color', 'emission'})


def _load(folder, relative):
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder) or not path.is_file() or path.is_symlink():
        return None
    try:
        return bpy.data.images.load(str(path), check_existing=False)
    except RuntimeError:
        return None


def _sample(image, side):
    width, height = image.size
    if not width or not height:
        return None
    copy = image.copy()
    try:
        if (width, height) != (side, side):
            copy.scale(side, side)
        channels = copy.channels
        buffer = np.empty(side * side * channels, dtype=np.float32)
        copy.pixels.foreach_get(buffer)
        return buffer.reshape(side, side, channels)
    finally:
        bpy.data.images.remove(copy)


def _maps(folder, images):
    rows = []
    for relative in images:
        image = _load(folder, relative)
        if image is None:
            continue
        pixels = _sample(image, SAMPLE)
        if pixels is None:
            bpy.data.images.remove(image)
            continue
        facts = fab_surface.features(pixels)
        row = {'name': relative, 'features': facts, 'size': image.size[0] * image.size[1], 'image': image}
        if facts['normal_like'] or facts['normal_score'] >= fab_surface.NAMED_NORMAL_SCORE:
            row['pixels'] = _sample(image, NORMAL_SAMPLE)
        rows.append(row)
    return rows


def _texture(tree, image, data, location, mapping):
    node = tree.nodes.new('ShaderNodeTexImage')
    node.image = image
    node.location = location
    image.colorspace_settings.is_data = data
    tree.links.new(mapping.outputs['Vector'], node.inputs['Vector'])
    return node


def _by_id(sockets, identifier):
    return next(socket for socket in sockets if socket.identifier == identifier)


def build(name, roles, packed, convention, images, alpha=frozenset()):
    """The material for ``roles`` (``{role: name}``) and ``packed`` (``{name: (r, g, b roles)}``)."""
    material = bpy.data.materials.new(name[:63])
    material.use_nodes = True
    tree = material.node_tree
    shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    output = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
    link = tree.links.new
    x, y = shader.location.x, shader.location.y
    coordinates, mapping = tree.nodes.new('ShaderNodeTexCoord'), tree.nodes.new('ShaderNodeMapping')
    coordinates.location, mapping.location = (x - 1100, y), (x - 920, y)
    link(coordinates.outputs['UV'], mapping.inputs['Vector'])
    nodes, row = {}, 0
    for role, key in sorted(roles.items(), key=lambda pair: (pair[0] != 'base_color', pair[0])):
        nodes[role] = _texture(tree, images[key], role not in COLOUR_ROLES, (x - 640, y - 300 * row), mapping)
        row += 1
    channels = {}
    for key, order in packed.items():
        texture = _texture(tree, images[key], True, (x - 640, y - 300 * row), mapping)
        split = tree.nodes.new('ShaderNodeSeparateColor')
        split.location = (x - 380, y - 300 * row)
        link(texture.outputs['Color'], split.inputs['Color'])
        for socket, role in zip(('Red', 'Green', 'Blue'), order):
            channels.setdefault(role, split.outputs[socket])
        row += 1

    def scalar(role):
        return nodes[role].outputs['Color'] if role in nodes else channels.get(role)
    base = nodes['base_color'].outputs['Color'] if 'base_color' in nodes else None
    occlusion = scalar('ao')
    if base is not None and occlusion is not None:
        mix = tree.nodes.new('ShaderNodeMix')
        mix.data_type, mix.blend_type, mix.location = 'RGBA', 'MULTIPLY', (x - 220, y + 120)
        _by_id(mix.inputs, 'Factor_Float').default_value = 1.0   # Mix sockets share names; identifiers are unique.
        link(base, _by_id(mix.inputs, 'A_Color'))
        link(occlusion, _by_id(mix.inputs, 'B_Color'))
        base = _by_id(mix.outputs, 'Result_Color')
    if base is not None:
        link(base, shader.inputs['Base Color'])
    rough = scalar('roughness')
    if rough is None and scalar('gloss') is not None:
        invert = tree.nodes.new('ShaderNodeMath')
        invert.operation, invert.location = 'SUBTRACT', (x - 220, y - 120)
        invert.inputs[0].default_value = 1.0
        link(scalar('gloss'), invert.inputs[1])
        rough = invert.outputs['Value']
    if rough is not None:
        link(rough, shader.inputs['Roughness'])
    if scalar('metallic') is not None:
        link(scalar('metallic'), shader.inputs['Metallic'])
    if scalar('opacity') is not None:   # Its own map, or one channel of a map (``fab_surface.classify``).
        link(scalar('opacity'), shader.inputs['Alpha'])
    elif 'base_color' in nodes and roles['base_color'] in alpha:
        link(nodes['base_color'].outputs['Alpha'], shader.inputs['Alpha'])
    if 'emission' in nodes:
        link(nodes['emission'].outputs['Color'], shader.inputs['Emission Color'])
        shader.inputs['Emission Strength'].default_value = 1.0
    normal = None
    if 'normal' in nodes:
        colour = nodes['normal'].outputs['Color']
        if convention == 'dx':
            split, join = tree.nodes.new('ShaderNodeSeparateColor'), tree.nodes.new('ShaderNodeCombineColor')
            flip = tree.nodes.new('ShaderNodeMath')
            flip.operation = 'SUBTRACT'
            flip.inputs[0].default_value = 1.0
            split.location, flip.location, join.location = (x - 420, y - 520), (x - 330, y - 560), (x - 240, y - 520)
            link(colour, split.inputs['Color'])
            link(split.outputs['Red'], join.inputs['Red'])
            link(split.outputs['Green'], flip.inputs[1])
            link(flip.outputs['Value'], join.inputs['Green'])
            link(split.outputs['Blue'], join.inputs['Blue'])
            colour = join.outputs['Color']
        normal = tree.nodes.new('ShaderNodeNormalMap')
        normal.location = (x - 160, y - 480)
        link(colour, normal.inputs['Color'])
        link(normal.outputs['Normal'], shader.inputs['Normal'])
    elif scalar('bump') is not None:
        bump = tree.nodes.new('ShaderNodeBump')
        bump.location = (x - 160, y - 480)
        bump.inputs['Distance'].default_value = BUMP_DISTANCE
        link(scalar('bump'), bump.inputs['Height'])
        link(bump.outputs['Normal'], shader.inputs['Normal'])
    if scalar('displacement') is not None:
        displacement = tree.nodes.new('ShaderNodeDisplacement')
        displacement.location = (output.location.x - 200, output.location.y - 280)
        displacement.inputs['Scale'].default_value = BUMP_DISTANCE
        link(scalar('displacement'), displacement.inputs['Height'])
        link(displacement.outputs['Displacement'], output.inputs['Displacement'])
    try:
        import material_display_sync as display   # Solid view: the map's mean colour, as it renders.
        display.sync(material, display.CREATED, images=display.LOAD)
    except Exception:  # noqa: BLE001 - a display colour is a convenience; the material stands without it.
        pass
    return material


def _classified(folder, images):
    rows = _maps(folder, [i for i in images
                          if Path(i).suffix.lower() not in ('.json', '.txt')])
    if not rows:
        return None
    try:
        result = fab_surface.classify([{k: v for k, v in r.items() if k != 'image'} for r in rows])
    except ValueError:
        for row in rows:
            bpy.data.images.remove(row['image'])
        return None
    used = set(result['roles'].values()) | set(result['packed'])
    images_by_name = {row['name']: row['image'] for row in rows}
    for name, image in images_by_name.items():
        if name not in used:
            bpy.data.images.remove(image)
    result['alpha'] = frozenset(row['name'] for row in rows if row['features']['alpha_varies'])
    return result, images_by_name


def surface(folder, images, name):
    """The texture set's material and its roles report, or None when no image is a material map."""
    found = _classified(folder, images)
    if found is None:
        return None
    result, images_by_name = found
    block = build(name, result['roles'], result['packed'], result['normal'], images_by_name, result['alpha'])
    return block, {'roles': sorted(result['roles']), 'packed': len(result['packed']), 'normal': result['normal'],
                   'unused': len(result['unused']), 'notes': result['notes']}


def _textured(material):
    """Whether ``material`` holds a texture: an image node with an image (an empty one holds nothing)."""
    tree = material.node_tree if material is not None else None
    return tree is not None and any(n.type == 'TEX_IMAGE' and n.image is not None for n in tree.nodes)


# The shader input an image fills, and the loose map's role that belongs there.
SLOT_ROLES = {'Base Color': 'base_color', 'Alpha': 'opacity', 'Roughness': 'roughness', 'Metallic': 'metallic',
              'Emission Color': 'emission', 'Normal': 'normal'}


def _slots(node):
    """``{output name: shader input name}`` an image node's outputs fill: straight into the Principled shader, or its
    Color through a Normal Map node into Normal. Anything else between them (a mix, a math node) is not a slot."""
    found = {}
    for output in node.outputs:
        for link in output.links:
            target = link.to_node
            if target.type == 'BSDF_PRINCIPLED' and link.to_socket.name in SLOT_ROLES:
                found[output.name] = link.to_socket.name
            elif target.type == 'NORMAL_MAP' and output.name == 'Color':
                if any(l.to_node.type == 'BSDF_PRINCIPLED' and l.to_socket.name == 'Normal'
                       for l in target.outputs['Normal'].links):
                    found[output.name] = 'Normal'
    return found


def _gaps(material):
    """The empty image nodes of a material that keeps other textures, with the slots they fill."""
    tree = material.node_tree if material is not None else None
    if tree is None or not _textured(material):
        return []
    return [(node, slots) for node in tree.nodes
            if node.type == 'TEX_IMAGE' and node.image is None and (slots := _slots(node))]


def _packed_channel(result, role):
    """``(map name, channel)`` of a role read from one channel of a packed map, or ``(None, None)``."""
    for name, order in result['packed'].items():
        for channel, held in zip(('Red', 'Green', 'Blue'), order):
            if held == role:
                return name, channel
    return None, None


def _fill(material, node, slots, result, images_by_name):
    """Fill an empty node's slots with the loose maps of their roles; the number of slots filled."""
    tree, filled = material.node_tree, 0
    colour = slots.get('Color')
    role = SLOT_ROLES.get(colour)
    if role == 'normal' and result['normal'] == 'dx':
        role = None   # A DirectX normal map needs its green flipped (``build``): left empty rather than wrong.
    key = result['roles'].get(role)
    if key is not None:
        node.image = images_by_name[key]
        node.image.colorspace_settings.is_data = role not in COLOUR_ROLES
        filled += 1
    elif role is not None:   # The role is one channel of a map: the slot reads that channel through a split.
        name, channel = _packed_channel(result, role)
        if name is not None:
            node.image = images_by_name[name]
            node.image.colorspace_settings.is_data = True
            split = tree.nodes.new('ShaderNodeSeparateColor')
            split.location = (node.location.x + 260, node.location.y)
            for link in list(node.outputs['Color'].links):
                target = link.to_socket
                tree.links.remove(link)
                tree.links.new(split.outputs[channel], target)
            tree.links.new(node.outputs['Color'], split.inputs['Color'])
            filled += 1
    if slots.get('Alpha') == 'Alpha' and node.image is not None:   # The node's own alpha fills Alpha.
        opacity = result['roles'].get('opacity')
        packed_opacity, channel = _packed_channel(result, 'opacity') if opacity is None else (None, None)
        source = opacity or packed_opacity
        if source is not None and key != source and key not in result['alpha']:
            shader = next(l.to_node for l in node.outputs['Alpha'].links if l.to_node.type == 'BSDF_PRINCIPLED')
            mask = tree.nodes.new('ShaderNodeTexImage')
            mask.image, mask.location = images_by_name[source], (node.location.x, node.location.y - 280)
            mask.image.colorspace_settings.is_data = True
            if node.inputs['Vector'].is_linked:
                tree.links.new(node.inputs['Vector'].links[0].from_socket, mask.inputs['Vector'])
            value = mask.outputs['Color']
            if channel is not None:
                split = tree.nodes.new('ShaderNodeSeparateColor')
                split.location = (mask.location.x + 260, mask.location.y)
                tree.links.new(value, split.inputs['Color'])
                value = split.outputs[channel]
            tree.links.new(value, shader.inputs['Alpha'])
            filled += 1
    return filled


def complete_untextured(objects, folder, images, name, report):
    """Complete meshes' materials from the download's loose texture set (after ``pack_images``): a mesh whose
    materials hold no texture gets the set's material; an empty image node in a material that keeps other textures
    gets the loose map of its slot's role. Whether anything was completed."""
    meshes = [o for o in objects if o.type == 'MESH' and not any(_textured(s.material) for s in o.material_slots)]
    gaps = {}
    for obj in objects:
        if obj.type == 'MESH' and obj not in meshes:
            for slot in obj.material_slots:
                if slot.material is not None and slot.material not in gaps and _gaps(slot.material):
                    gaps[slot.material] = _gaps(slot.material)
    if not meshes and not gaps:
        return False
    # An image a file names may be anywhere, a network share included: resolved only through the guard, never opened.
    used = {guard.resolved(bpy.path.abspath(i.filepath)) for i in bpy.data.images if i.filepath}
    loose = [i for i in images if str((folder / i).resolve()) not in used
             and fab_formats.kind_of_content(_head(folder / i)) in fab_formats.IMAGES]
    found = _classified(folder, loose) if loose else None
    if found is None or not ({'base_color', 'normal'} & set(found[0]['roles'])):
        return False
    result, images_by_name = found
    if meshes:
        block = build(name, result['roles'], result['packed'], result['normal'], images_by_name, result['alpha'])
        for obj in meshes:
            if not obj.material_slots:
                obj.data.materials.append(block)
            for slot in obj.material_slots:
                slot.material = block
        report['surface'] = {'roles': sorted(result['roles']), 'normal': result['normal']}
    filled = sum(_fill(material, node, slots, result, images_by_name)
                 for material, pairs in gaps.items() for node, slots in pairs)
    if filled:
        report['filled_slots'] = filled
    return bool(meshes) or filled > 0


def _head(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read(65536)
    except OSError:
        return b''
