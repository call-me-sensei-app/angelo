# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Data-API helpers the scenario ground-truth builders share (``scenario_<name>.build``).

Builders run in three places: a background stock Blender that renders the
references, the MCP add-on's socket during a dry run, and Angelo's reviewed
Python during a dry run. So they use only the data API (``bpy.data``, bmesh,
mathutils), never operators or the current context, and every value is fixed:
there is no unseeded randomness, so a scene built twice is identical.
"""

import math


def look_at_rotation(location, target, up=(0.0, 0.0, 1.0)):
    """Euler rotation that points a camera or light's -Z at ``target`` with +Y towards ``up``."""
    from mathutils import Matrix, Vector
    forward = (Vector(target) - Vector(location)).normalized()
    reference = Vector(up)
    if abs(forward.dot(reference)) > 0.999:
        reference = Vector((0.0, 1.0, 0.0))
    right = forward.cross(reference).normalized()
    true_up = right.cross(forward)
    matrix = Matrix((right, true_up, -forward)).transposed()
    return matrix.to_euler()


def link(bpy, obj, collection=None):
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def mesh_object(bpy, name, vertices, faces, location=(0.0, 0.0, 0.0), collection=None, smooth=False):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in vertices], [], [tuple(f) for f in faces])
    mesh.update()
    if smooth:
        for polygon in mesh.polygons:
            polygon.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    return link(bpy, obj, collection)


def box_geometry(size, centre=(0.0, 0.0, 0.0), offset=0):
    """Vertices and faces of an axis-aligned box (``offset`` shifts face indices for merged meshes)."""
    sx, sy, sz = (s / 2 for s in size)
    cx, cy, cz = centre
    vertices = [(cx + x * sx, cy + y * sy, cz + z * sz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return vertices, [tuple(i + offset for i in face) for face in faces]


def merged_boxes(boxes):
    vertices, faces = [], []
    for size, centre in boxes:
        v, f = box_geometry(size, centre, offset=len(vertices))
        vertices += v
        faces += f
    return vertices, faces


def box_object(bpy, name, size, location, collection=None):
    vertices, faces = box_geometry(size)
    return mesh_object(bpy, name, vertices, faces, location, collection)


def cylinder_geometry(radius, depth, segments=16, centre=(0.0, 0.0, 0.0), offset=0, axis="Z"):
    cx, cy, cz = centre
    vertices, faces = [], []
    for ring, z in enumerate((-depth / 2, depth / 2)):
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            a, b = radius * math.cos(angle), radius * math.sin(angle)
            local = {"Z": (a, b, z), "X": (z, a, b), "Y": (a, z, b)}[axis]
            vertices.append((cx + local[0], cy + local[1], cz + local[2]))
    for i in range(segments):
        j = (i + 1) % segments
        faces.append((offset + i, offset + j, offset + segments + j, offset + segments + i))
    faces.append(tuple(offset + i for i in reversed(range(segments))))
    faces.append(tuple(offset + segments + i for i in range(segments)))
    return vertices, faces


def uv_sphere_geometry(radius, segments=16, rings=8, centre=(0.0, 0.0, 0.0), offset=0, scale=(1.0, 1.0, 1.0)):
    cx, cy, cz = centre
    vertices = [(cx, cy, cz - radius * scale[2])]
    for ring in range(1, rings):
        phi = math.pi * ring / rings - math.pi / 2
        for i in range(segments):
            theta = 2 * math.pi * i / segments
            vertices.append((cx + radius * scale[0] * math.cos(phi) * math.cos(theta),
                             cy + radius * scale[1] * math.cos(phi) * math.sin(theta),
                             cz + radius * scale[2] * math.sin(phi)))
    vertices.append((cx, cy, cz + radius * scale[2]))
    top = len(vertices) - 1
    faces = []
    for i in range(segments):
        faces.append((0, 1 + (i + 1) % segments, 1 + i))
    for ring in range(rings - 2):
        start = 1 + ring * segments
        for i in range(segments):
            j = (i + 1) % segments
            faces.append((start + i, start + j, start + segments + j, start + segments + i))
    start = 1 + (rings - 2) * segments
    for i in range(segments):
        faces.append((start + i, start + (i + 1) % segments, top))
    return vertices, [tuple(index + offset for index in face) for face in faces]


def heightfield_block(bpy, name, xs, ys, height, bottom, collection=None):
    """A solid terrain block (like a diorama): a smooth-shaded height-field top, flat sides and a flat base.

    ``xs``/``ys`` are increasing sample coordinates, ``height(x, y)`` the top surface.
    """
    columns, rows = len(xs), len(ys)
    vertices = [(x, y, height(x, y)) for y in ys for x in xs]
    faces = [(j * columns + i, j * columns + i + 1, (j + 1) * columns + i + 1, (j + 1) * columns + i)
             for j in range(rows - 1) for i in range(columns - 1)]
    top_faces = len(faces)
    # The boundary loop of the top grid, counter-clockwise seen from above.
    loop = ([i for i in range(columns)] + [j * columns + columns - 1 for j in range(1, rows)]
            + [(rows - 1) * columns + i for i in range(columns - 2, -1, -1)]
            + [j * columns for j in range(rows - 2, 0, -1)])
    base = len(vertices)
    vertices += [(vertices[index][0], vertices[index][1], bottom) for index in loop]
    for k, index in enumerate(loop):
        following = (k + 1) % len(loop)
        faces.append((index, base + k, base + following, loop[following]))
    faces.append(tuple(base + k for k in reversed(range(len(loop)))))
    obj = mesh_object(bpy, name, vertices, faces, collection=collection)
    for polygon in obj.data.polygons[:top_faces]:
        polygon.use_smooth = True
    return obj


def graded_samples(low, high, fine_low, fine_high, fine_step, coarse_step):
    """Increasing samples from ``low`` to ``high``: ``fine_step`` inside [fine_low, fine_high], coarse outside."""
    values, value = [], low
    while value < high - 1e-9:
        values.append(round(value, 6))
        value += fine_step if fine_low - 1e-9 <= value < fine_high - 1e-9 else coarse_step
    return values + [high]


def rock_geometry(radius, seed, squash=0.62, segments=12, rings=7):
    """A lumpy, deterministic rock: a UV sphere with seeded radial noise, flattened in Z."""
    vertices, faces = uv_sphere_geometry(radius, segments, rings, scale=(1.0, 0.86, squash))
    state = (seed * 2654435761) & 0xFFFFFFFF
    lumpy = []
    for x, y, z in vertices:
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        factor = 0.82 + 0.3 * (state / 0x7FFFFFFF)
        lumpy.append((x * factor, y * factor, z * (0.9 + 0.2 * (state % 97) / 97)))
    return lumpy, faces


def principled(bpy, name, color, roughness=0.5, metallic=0.0, emission=None, emission_strength=0.0,
               transmission=0.0, specular=None):
    material = bpy.data.materials.new(name)
    if hasattr(material, "use_nodes"):
        material.use_nodes = True
    node = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    node.inputs["Base Color"].default_value = (*color[:3], 1.0)
    node.inputs["Roughness"].default_value = roughness
    node.inputs["Metallic"].default_value = metallic
    if transmission:
        node.inputs["Transmission Weight"].default_value = transmission
    if specular is not None:
        node.inputs["Specular IOR Level"].default_value = specular
    if emission is not None:
        node.inputs["Emission Color"].default_value = (*emission[:3], 1.0)
        node.inputs["Emission Strength"].default_value = emission_strength
    material.diffuse_color = (*color[:3], 1.0)
    return material


def assign(obj, *materials):
    for material in materials:
        obj.data.materials.append(material)
    return obj


def height_ramp_material(bpy, name, stops, roughness=0.85):
    """A terrain material whose base colour follows world height through a Color Ramp."""
    material = principled(bpy, name, stops[-1][1], roughness)
    tree = material.node_tree
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    geometry = tree.nodes.new("ShaderNodeNewGeometry")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    ramp = tree.nodes.new("ShaderNodeMapRange")
    colours = tree.nodes.new("ShaderNodeValToRGB")
    low, high = stops[0][0], stops[-1][0]
    ramp.inputs["From Min"].default_value, ramp.inputs["From Max"].default_value = low, high
    tree.links.new(geometry.outputs["Position"], separate.inputs["Vector"])
    tree.links.new(separate.outputs["Z"], ramp.inputs["Value"])
    tree.links.new(ramp.outputs["Result"], colours.inputs["Fac"])
    tree.links.new(colours.outputs["Color"], bsdf.inputs["Base Color"])
    elements = colours.color_ramp.elements
    while len(elements) < len(stops):
        elements.new(0.5)
    for element, (height, colour) in zip(elements, stops):
        element.position = (height - low) / (high - low)
        element.color = (*colour[:3], 1.0)
    return material


def camera_object(bpy, name, location, target, lens=35.0, collection=None, ortho_scale=None, clip_end=500.0):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    data.clip_end = clip_end
    if ortho_scale:
        data.type, data.ortho_scale = "ORTHO", ortho_scale
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    obj.rotation_euler = look_at_rotation(location, target, (0.0, 1.0, 0.0) if abs(location[2] - target[2]) > 0.99 *
                                          math.dist(location, target) else (0.0, 0.0, 1.0))
    return link(bpy, obj, collection)


def sun_object(bpy, name, elevation_deg, azimuth_deg, color, energy, angle_deg=1.5, collection=None):
    """A Sun light that shines FROM (azimuth, elevation): azimuth 0 is +X, 90 is +Y."""
    data = bpy.data.lights.new(name, "SUN")
    data.color, data.energy, data.angle = color, energy, math.radians(angle_deg)
    obj = bpy.data.objects.new(name, data)
    e, a = math.radians(elevation_deg), math.radians(azimuth_deg)
    towards = (math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e))
    obj.location = tuple(30 * v for v in towards)
    obj.rotation_euler = look_at_rotation(obj.location, (0.0, 0.0, 0.0))
    return link(bpy, obj, collection)


def point_light(bpy, name, location, color, energy, radius=0.1, collection=None):
    data = bpy.data.lights.new(name, "POINT")
    data.color, data.energy, data.shadow_soft_size = color, energy, radius
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    return link(bpy, obj, collection)


def sky_world(bpy, name, elevation_deg, azimuth_deg, strength=0.6, sky_type="MULTIPLE_SCATTERING", aerosol=1.0,
              air=1.0):
    """A physical sky whose sun sits at the same elevation and azimuth as ``sun_object``.

    The Sky texture's sun disc is off: the Sun light casts the light and shadows.
    """
    world = bpy.data.worlds.new(name)
    if hasattr(world, "use_nodes"):
        world.use_nodes = True
    tree = world.node_tree
    background = next(n for n in tree.nodes if n.type == "BACKGROUND")
    sky = tree.nodes.new("ShaderNodeTexSky")
    sky.sky_type = sky_type
    sky.sun_disc = False
    sky.sun_elevation = math.radians(elevation_deg)
    sky.aerosol_density, sky.air_density = aerosol, air   # Haze warms a low sun.
    # Measured in Blender 5.2: rotation 0 puts the texture's sun on +Y (azimuth 90), and the
    # sun moves clockwise as the rotation grows; azimuth here is counter-clockwise from +X.
    sky.sun_rotation = math.radians((90.0 - azimuth_deg) % 360.0)
    tree.links.new(sky.outputs["Color"], background.inputs["Color"])
    background.inputs["Strength"].default_value = strength
    return world


def colour_world(bpy, name, colour, strength=1.0):
    world = bpy.data.worlds.new(name)
    if hasattr(world, "use_nodes"):
        world.use_nodes = True
    background = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    background.inputs["Color"].default_value = (*colour[:3], 1.0)
    background.inputs["Strength"].default_value = strength
    world.color = colour[:3]
    return world


def keyframes(obj, path, frames, interpolation="BEZIER"):
    """Insert ``{frame: value}`` keys on ``obj.<path>`` and set their interpolation (channelbag-agnostic)."""
    for frame, value in sorted(frames.items()):
        setattr(obj, path, value)
        obj.keyframe_insert(data_path=path, frame=frame)
    action = obj.animation_data.action if obj.animation_data else None
    for curve in _action_curves(obj, action):
        if curve.data_path == path:
            for point in curve.keyframe_points:
                point.interpolation = interpolation


def _action_curves(owner, action):
    """F-curves of the slot ``owner`` uses (Blender 4.4+ layered Actions), or the legacy list."""
    if action is None:
        return []
    if hasattr(action, "layers") and action.layers:
        slot = owner.animation_data.action_slot
        curves = []
        for layer in action.layers:
            for strip in layer.strips:
                channelbag = strip.channelbag(slot) if slot is not None else None
                if channelbag is not None:
                    curves += list(channelbag.fcurves)
        return curves
    return list(getattr(action, "fcurves", []))


def remove_default_objects(bpy):
    """The factory scene's Cube, Camera and Light, which no scenario keeps."""
    for name in ("Cube", "Camera", "Light"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
