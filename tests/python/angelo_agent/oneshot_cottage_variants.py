# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cottage variants: doors and textured surfaces, compliant or deliberately wrong, with no rendering.

The textured ones prove the ``surface`` check's base colour in Blender: a
Brick texture, a noise mix, a Voronoi-driven mix and a mix chosen through the
roof's UV map are read as the node graph computes them over the roof's faces
(``surface_reading``, its texture kernels), the UV one only passing when the
UVs are read per face corner; a blue Voronoi roof must fail.
"""

import scenario_build_kit as kit


def _build(bpy):
    import oneshot_kit
    import scenario_oneshot_cottage as cottage
    cottage.build(bpy)
    return oneshot_kit.cottage_plan(cottage.SIZE, cottage.EAVES, cottage.RIDGE)


def _replace_leaf(bpy, boxes):
    """Keep the leaf's name and material while replacing its box with disconnected parts."""
    vertices, faces = kit.merged_boxes(boxes)
    mesh = bpy.data.objects["Cottage Door"].data
    mesh.clear_geometry()
    mesh.from_pydata(vertices, [], faces)
    mesh.update()


def _remove_leaf(bpy):
    leaf = bpy.data.objects["Cottage Door"]
    mesh = leaf.data
    bpy.data.objects.remove(leaf, do_unlink=True)
    bpy.data.meshes.remove(mesh)


def wide_doorstep(bpy):
    plan = _build(bpy)
    kit.assign(kit.box_object(bpy, "Cottage Doorstep", (1.6, 0.5, 0.12),
                              (0.0, plan["front"] - 0.3, 0.06)), bpy.data.materials["Cottage Stone"])


def plank_door(bpy):
    """A single leaf mesh made from six planks, with a 7 mm seam on its centre line."""
    plan = _build(bpy)
    _replace_leaf(bpy, [((0.143, 0.08, 2.0), (-0.375 + index * 0.15, plan["front"], 1.0))
                        for index in range(6)])


def plank_objects(bpy):
    """The leaf as six .001 sibling objects, with a 6 mm seam on its centre line."""
    plan = _build(bpy)
    _remove_leaf(bpy)
    for index in range(6):
        kit.assign(kit.box_object(bpy, "Cottage Door", (0.144, 0.08, 2.0),
                                  (-0.375 + index * 0.15, plan["front"], 1.0)), bpy.data.materials["Cottage Timber"])


def door_overlays(bpy):
    """Separately named decorative planks cover the named leaf's front face, one of them its middle."""
    plan = _build(bpy)
    for index in range(5):
        kit.assign(kit.box_object(bpy, "Cottage Door Oak Plank %02d" % index, (0.143, 0.03, 2.0),
                                  (-0.3 + index * 0.15, plan["front"] - 0.06, 1.0)),
                   bpy.data.materials["Cottage Timber"])


def no_door(bpy):
    _build(bpy)
    _remove_leaf(bpy)


def wide_door(bpy):
    _build(bpy)
    bpy.data.objects["Cottage Door"].scale.x = 1.6 / 0.9


def off_centre_door(bpy):
    _build(bpy)
    bpy.data.objects["Cottage Door"].location.x += 1.0


def rear_door(bpy):
    _build(bpy)
    bpy.data.objects["Cottage Door"].location.y += 5.5


def sparse_door(bpy):
    """Two narrow edge strips have the right bounds but leave almost the whole doorway empty."""
    plan = _build(bpy)
    _replace_leaf(bpy, [((0.04, 0.08, 2.0), (x, plan["front"], 1.0)) for x in (-0.43, 0.43)])


def obscured_door(bpy):
    """Most of the door is covered; a few visible samples must not satisfy the visibility check."""
    plan = _build(bpy)
    kit.assign(kit.box_object(bpy, "Cottage Wall Door Cover", (0.55, 0.08, 2.0),
                              (-0.125, plan["front"] - 0.10, 1.0)), bpy.data.materials["Cottage Stone"])


# ---------------------------------------------------------------- textured surfaces

def _base_colour(bpy, material):
    """``(node tree, the Principled BSDF's Base Color input)`` of one of the stand-in's materials."""
    tree = bpy.data.materials[material].node_tree
    return tree, next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED").inputs["Base Color"]


def _mix(tree, first, second, base):
    """A Mix of two colours into ``base``; returns its factor input (a Mix names several sockets A and B)."""
    mixed = tree.nodes.new("ShaderNodeMix")
    mixed.data_type = "RGBA"
    by_id = {socket.identifier: socket for socket in list(mixed.inputs) + list(mixed.outputs)}
    by_id["A_Color"].default_value, by_id["B_Color"].default_value = (*first, 1.0), (*second, 1.0)
    tree.links.new(by_id["Result_Color"], base)
    return by_id["Factor_Float"]


def _object_coordinates(tree, texture):
    tree.links.new(tree.nodes.new("ShaderNodeTexCoord").outputs["Object"], texture.inputs["Vector"])


def brick_roof(bpy):
    """Terracotta tiles as a Brick texture of two terracottas and a dark mortar: read from the graph."""
    _build(bpy)
    tree, base = _base_colour(bpy, "Cottage Terracotta Tiles")
    tiles = tree.nodes.new("ShaderNodeTexBrick")
    for name, colour in (("Color1", (0.55, 0.2, 0.09)), ("Color2", (0.42, 0.15, 0.06)), ("Mortar", (0.15, 0.06, 0.03))):
        tiles.inputs[name].default_value = (*colour, 1.0)
    tiles.inputs["Scale"].default_value = 5.0
    _object_coordinates(tree, tiles)
    tree.links.new(tiles.outputs["Color"], base)


def noise_walls(bpy):
    """Stone walls as two warm greys mixed by a noise texture: read from the graph (a noise's mean is a half)."""
    _build(bpy)
    tree, base = _base_colour(bpy, "Cottage Stone")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    _object_coordinates(tree, noise)
    tree.links.new(noise.outputs["Fac"], _mix(tree, (0.42, 0.39, 0.35), (0.3, 0.28, 0.25), base))


def voronoi_roof(bpy, first=(0.62, 0.24, 0.1), second=(0.42, 0.13, 0.05)):
    """Tiles whose colour a Voronoi texture's distance mixes: the graph cannot read it, so it is measured."""
    _build(bpy)
    tree, base = _base_colour(bpy, "Cottage Terracotta Tiles")
    cells = tree.nodes.new("ShaderNodeTexVoronoi")
    cells.inputs["Scale"].default_value = 6.0
    _object_coordinates(tree, cells)
    tree.links.new(cells.outputs["Distance"], _mix(tree, first, second, base))


def blue_voronoi_roof(bpy):
    """The same texture in two blues: measured, and still not terracotta."""
    voronoi_roof(bpy, (0.05, 0.12, 0.45), (0.03, 0.07, 0.3))


def uv_roof(bpy):
    """Blue on the first 15 % of the roof's UV map along X, terracotta on the rest: measured through the UVs.

    Without its UVs the whole roof would read the blue end and fail.
    """
    _build(bpy)
    mesh = bpy.data.objects["Cottage Roof"].data
    xs = [vertex.co.x for vertex in mesh.vertices]
    low, span = min(xs), (max(xs) - min(xs)) or 1.0
    layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        layer.data[loop.index].uv = ((mesh.vertices[loop.vertex_index].co.x - low) / span, 0.5)
    tree, base = _base_colour(bpy, "Cottage Terracotta Tiles")
    along = tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(tree.nodes.new("ShaderNodeTexCoord").outputs["UV"], along.inputs["Vector"])
    past = tree.nodes.new("ShaderNodeMath")
    past.operation, past.inputs[1].default_value = "GREATER_THAN", 0.15
    tree.links.new(along.outputs["X"], past.inputs[0])
    tree.links.new(past.outputs["Value"], _mix(tree, (0.05, 0.12, 0.45), (0.55, 0.2, 0.09), base))


def _hole(bpy, name, low, high):
    """Remove the named mesh's faces whose centres lie in an axis-aligned box: an opening through it."""
    import bmesh
    obj = bpy.data.objects[name]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    matrix = obj.matrix_world
    inverse = matrix.inverted()
    from mathutils import Vector
    for axis in (0, 1):   # Split along the opening's sides first, so only the opening is removed.
        for value in (low[axis], high[axis]):
            point = [0.0, 0.0, 0.0]
            point[axis] = value
            normal = Vector([1.0 if index == axis else 0.0 for index in range(3)])
            geometry = mesh.verts[:] + mesh.edges[:] + mesh.faces[:]
            bmesh.ops.bisect_plane(mesh, geom=geometry, plane_co=inverse @ Vector(point),
                                   plane_no=(inverse.to_3x3().transposed() @ normal).normalized())
    doomed = [face for face in mesh.faces
              if all(low[axis] <= (matrix @ face.calc_center_median())[axis] <= high[axis] for axis in range(3))]
    bmesh.ops.delete(mesh, geom=doomed, context="FACES")
    mesh.to_mesh(obj.data)
    mesh.free()
    obj.data.update()


def _glass(bpy, name, size, centre):
    kit.assign(kit.box_object(bpy, name, size, centre), bpy.data.materials["Cottage Lit Glass"])


def rear_window_opening(bpy):
    """A third window set through the back wall where its plain-wall probe aims (the brief allows it anywhere)."""
    import oneshot_kit
    plan = _build(bpy)
    (half_x, half_y), eaves = plan["half"], plan["eaves"]
    wall, (x0, x1, z0, z1) = oneshot_kit.WALL, (2.3, 3.5, 1.0, 2.1)
    y = half_y - wall / 2
    pieces = [kit.box_geometry((2 * half_x, wall, eaves), (0.0, -y, eaves / 2))]
    pieces += [kit.box_geometry((wall, 2 * half_y, eaves), (side * (half_x - wall / 2), 0.0, eaves / 2))
               for side in (-1, 1)]
    for (a, b), (c, d) in (((-half_x, x0), (0.0, eaves)), ((x1, half_x), (0.0, eaves)), ((x0, x1), (0.0, z0)),
                           ((x0, x1), (z1, eaves))):
        pieces.append(kit.box_geometry((b - a, wall, d - c), ((a + b) / 2, y, (c + d) / 2)))
    gable = [(-half_y, eaves), (half_y, eaves), (0.0, plan["ridge"] - 0.12)]
    pieces += [oneshot_kit._prism(gable, side * half_x - (wall if side > 0 else 0.0),
                                  side * half_x + (wall if side < 0 else 0.0)) for side in (-1, 1)]
    mesh = bpy.data.objects["Cottage Walls"].data
    mesh.clear_geometry()
    vertices, faces = oneshot_kit.merged(pieces)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    _glass(bpy, "Cottage Window Rear", (x1 - x0, 0.04, z1 - z0), ((x0 + x1) / 2, y, (z0 + z1) / 2))


def roof_window(bpy):
    """A roof window let into the front slope over the door, where the slope's probe aims."""
    plan = _build(bpy)
    _hole(bpy, "Cottage Roof", (-0.4, -2.4, 0.0), (0.4, -1.6, 10.0))
    z = plan["ridge"] - plan["slope"] * 2.0
    _glass(bpy, "Cottage Window Roof Light", (0.8, 0.8, 0.04), (0.0, -2.0, z))


def inboard_chimney(bpy):
    """The chimney through the ridge at x = -2.5 (still at the -X end), the roof opened around it."""
    plan = _build(bpy)
    chimney = bpy.data.objects["Cottage Chimney"]
    chimney.location.x += -2.5 - plan["chimney"][0]
    chimney.location.y -= plan["chimney"][1]
    _hole(bpy, "Cottage Roof", (-2.9, -0.4, 0.0), (-2.1, 0.4, 10.0))
    bpy.context.view_layer.update()
