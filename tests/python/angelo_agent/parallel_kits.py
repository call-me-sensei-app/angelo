# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stand-ins of the parallel-agents one-shot tests (``scenario_oneshot_{chess,parlour,roadster,outfit}``).

Each test is a job a person would split by unique design: a set of pieces (a chess set), a room's furniture, a car's
body and repeated wheels, a character's clothing parts. ``build_*`` makes the plainest version that follows its
brief, the scene the reference pictures are rendered from; every check of a test is derived from the constants
here and holds for any build that follows the brief, however it is made (and whether one agent or several made it).
Pure geometry plus ``scenario_build_kit``'s object helpers; metres, Z up.
"""

import math

import scenario_build_kit as kit


# ------------------------------------------------------------------ geometry

def lathe(profile, segments=32, offset=0):
    """A solid of revolution about Z from ``profile`` [(radius, z), ...] bottom to top; closed at both ends."""
    vertices, faces = [], []
    for radius, z in profile:
        for index in range(segments):
            angle = 2 * math.pi * index / segments
            vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
    for ring in range(len(profile) - 1):
        for index in range(segments):
            a, b = ring * segments + index, ring * segments + (index + 1) % segments
            faces.append((offset + a, offset + b, offset + b + segments, offset + a + segments))
    bottom, top = len(vertices), len(vertices) + 1
    vertices += [(0.0, 0.0, profile[0][1]), (0.0, 0.0, profile[-1][1])]
    last = (len(profile) - 1) * segments
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append((offset + bottom, offset + nxt, offset + index))
        faces.append((offset + top, offset + last + index, offset + last + nxt))
    return vertices, faces


def prism(outline, thickness, offset=0):
    """``outline`` [(y, z), ...] (counter-clockwise seen from +X) extruded along X, centred on x = 0."""
    count, half = len(outline), thickness / 2
    vertices = [(-half, y, z) for y, z in outline] + [(half, y, z) for y, z in outline]
    faces = [tuple(offset + i for i in reversed(range(count))), tuple(offset + count + i for i in range(count))]
    faces += [(offset + i, offset + (i + 1) % count, offset + count + (i + 1) % count, offset + count + i)
              for i in range(count)]
    return vertices, faces


def combine(parts):
    vertices, faces = [], []
    for part_vertices, part_faces in parts:
        shift = len(vertices)
        vertices += part_vertices
        faces += [tuple(shift + i for i in face) for face in part_faces]
    return vertices, faces


def box(size, centre):
    return kit.box_geometry(size, centre)


def sphere(radius, centre, segments=16, rings=8):
    return kit.uv_sphere_geometry(radius, segments, rings, centre)


def ring_of(count, radius, z, make):
    return [make((radius * math.cos(2 * math.pi * i / count), radius * math.sin(2 * math.pi * i / count), z))
            for i in range(count)]


# ------------------------------------------------------------------ the chess set

SQUARE, BORDER, BOARD_THICKNESS = 0.05, 0.025, 0.02          # A 40 cm playing area, its top at z = 0.
FILES, RANKS = "abcdefgh", "12345678"
HEIGHTS = {"King": 0.095, "Queen": 0.083, "Bishop": 0.071, "Knight": 0.060, "Rook": 0.054, "Pawn": 0.046}
BACK_RANK = ("Rook", "Knight", "Bishop", "Queen", "King", "Bishop", "Knight", "Rook")
LIGHT_SQUARE, DARK_SQUARE, FRAME = (0.86, 0.78, 0.62), (0.30, 0.17, 0.08), (0.16, 0.08, 0.035)
IVORY, EBONY = (0.90, 0.85, 0.74), (0.035, 0.03, 0.028)


def square_centre(square):
    """World (x, y) of a square's centre: file a at -X, rank 1 at -Y (White's side), the board centred on the origin."""
    return ((FILES.index(square[0]) - 3.5) * SQUARE, (RANKS.index(square[1]) - 3.5) * SQUARE)


def starting_position():
    """{square: (colour, piece)} of the 32 pieces at the start."""
    position = {}
    for index, piece in enumerate(BACK_RANK):
        position[FILES[index] + "1"], position[FILES[index] + "8"] = ("White", piece), ("Black", piece)
        position[FILES[index] + "2"], position[FILES[index] + "7"] = ("White", "Pawn"), ("Black", "Pawn")
    return position


def is_light(square):
    return (FILES.index(square[0]) + RANKS.index(square[1])) % 2 == 1   # a1 is dark, h1 light.


def _base(radius):
    return [(radius, 0.0), (radius, 0.004), (radius * 0.84, 0.0075), (radius * 0.62, 0.011)]


def piece_geometry(piece):
    """One Staunton-style piece of the stand-in, standing at the origin (a knight faces +Y)."""
    h = HEIGHTS[piece]
    if piece == "Pawn":
        return combine([lathe(_base(0.014) + [(0.0055, 0.024), (0.0085, 0.027), (0.004, 0.030), (0.0, 0.031)]),
                        sphere(0.0075, (0.0, 0.0, h - 0.0075))])
    if piece == "Rook":
        body = lathe(_base(0.016) + [(0.0095, 0.016), (0.0090, 0.040), (0.0125, 0.044), (0.0125, 0.048),
                                     (0.0, 0.048)])
        return combine([body] + ring_of(5, 0.0098, 0.051, lambda c: box((0.0055, 0.0055, 0.006), c)))
    if piece == "Bishop":
        return combine([lathe(_base(0.016) + [(0.0060, 0.020), (0.0052, 0.038), (0.0100, 0.041), (0.0058, 0.044),
                                               (0.0088, 0.050), (0.0094, 0.056), (0.0062, 0.062), (0.0, 0.065)]),
                        sphere(0.0032, (0.0, 0.0, h - 0.0032))])
    if piece == "Queen":
        body = lathe(_base(0.018) + [(0.0068, 0.022), (0.0058, 0.054), (0.0115, 0.058), (0.0070, 0.061),
                                     (0.0105, 0.072), (0.0, 0.074)])
        crown = ring_of(8, 0.0098, 0.0745, lambda c: sphere(0.0025, c, 8, 4))
        return combine([body, sphere(0.0042, (0.0, 0.0, h - 0.0042))] + crown)
    if piece == "King":
        body = lathe(_base(0.019) + [(0.0072, 0.024), (0.0062, 0.060), (0.0122, 0.064), (0.0074, 0.067),
                                     (0.0098, 0.077), (0.0, 0.080)])
        return combine([body, box((0.0042, 0.0042, 0.016), (0.0, 0.0, 0.087)),
                        box((0.0125, 0.0042, 0.0042), (0.0, 0.0, 0.089))])
    if piece == "Knight":
        # A horse's head in profile, facing +Y: chest, throat, jaw, muzzle, forehead, ears, then the mane.
        head = [(-0.011, 0.016), (0.009, 0.016), (0.010, 0.024), (0.006, 0.032), (0.012, 0.038), (0.019, 0.041),
                (0.020, 0.046), (0.016, 0.050), (0.008, 0.054), (0.004, 0.058), (0.001, h), (-0.002, 0.056),
                (-0.007, 0.054), (-0.011, 0.047), (-0.013, 0.036), (-0.013, 0.024)]
        return combine([lathe(_base(0.016) + [(0.0110, 0.014), (0.0105, 0.016), (0.0, 0.016)]), prism(head, 0.013)])
    raise KeyError(piece)


def build_chess(bpy, collection=None):
    """The board ('Chess Board', its frame 'Chess Board Frame') and the 32 pieces in their starting position."""
    light = kit.principled(bpy, "Board Light", LIGHT_SQUARE, roughness=0.45)
    dark = kit.principled(bpy, "Board Dark", DARK_SQUARE, roughness=0.45)
    frame = kit.principled(bpy, "Board Frame", FRAME, roughness=0.4)
    ivory = kit.principled(bpy, "Ivory", IVORY, roughness=0.3)
    ebony = kit.principled(bpy, "Ebony", EBONY, roughness=0.25)
    vertices, faces, colours = [], [], []
    for square in (f + r for f in FILES for r in RANKS):
        x, y = square_centre(square)
        part = box((SQUARE, SQUARE, BOARD_THICKNESS), (x, y, -BOARD_THICKNESS / 2))
        shift = len(vertices)
        vertices += part[0]
        faces += [tuple(shift + i for i in face) for face in part[1]]
        colours += [1 if is_light(square) else 0] * len(part[1])
    board = kit.mesh_object(bpy, "Chess Board", vertices, faces, collection=collection)
    kit.assign(board, dark, light)
    for polygon, colour in zip(board.data.polygons, colours):
        polygon.material_index = colour
    outer, inner = 8 * SQUARE / 2 + BORDER, 8 * SQUARE / 2
    rails = [box((2 * outer, BORDER, BOARD_THICKNESS * 1.2), (0.0, sign * (inner + BORDER / 2), -BOARD_THICKNESS * 0.6))
             for sign in (-1, 1)]
    rails += [box((BORDER, 2 * inner, BOARD_THICKNESS * 1.2), (sign * (inner + BORDER / 2), 0.0, -BOARD_THICKNESS * 0.6))
              for sign in (-1, 1)]
    border = kit.mesh_object(bpy, "Chess Board Frame", *combine(rails), collection=collection)
    kit.assign(border, frame)
    meshes = {}
    for square, (colour, piece) in sorted(starting_position().items()):
        key = (colour, piece)
        if key not in meshes:
            obj = kit.mesh_object(bpy, "%s %s" % key, *piece_geometry(piece), collection=collection,
                                  smooth=piece != "Knight")   # A knight's carved planes stay crisp.
            kit.assign(obj, ivory if colour == "White" else ebony)
            meshes[key] = obj.data
        else:
            obj = bpy.data.objects.new("%s %s" % key, meshes[key])
            kit.link(bpy, obj, collection)
        x, y = square_centre(square)
        obj.location = (x, y, 0.0)
        obj.rotation_euler = (0.0, 0.0, 0.0 if colour == "White" else math.pi)   # Knights face the other side.
    return board


# ------------------------------------------------------------------ the parlour (a furnished living room)

ROOM = (6.0, 5.0)
SEAT, TABLE_TOP, COFFEE_TOP, LAMP_TOP, SHELF_TOP = 0.45, 0.75, 0.42, 1.6, 1.8
SOFA_AT, COFFEE_AT, DINING_AT = (0.0, 1.85), (0.0, 0.85), (0.0, -1.35)
ARMCHAIRS = ((-1.55, 0.85), (1.55, 0.85))
DINING_CHAIRS = ((-0.45, -0.7), (0.45, -0.7), (-0.45, -2.0), (0.45, -2.0))
LAMP_AT, SHELF_AT = (2.55, 2.05), (-2.35, 2.25)


def _chair_parts(seat=SEAT, width=0.45, depth=0.45, back=0.85, facing=1):
    legs = [box((0.04, 0.04, seat - 0.04), (sx * (width / 2 - 0.03), sy * (depth / 2 - 0.03), (seat - 0.04) / 2))
            for sx in (-1, 1) for sy in (-1, 1)]
    return combine(legs + [box((width, depth, 0.04), (0, 0, seat - 0.02)),
                           box((width, 0.04, back - seat), (0, -facing * (depth / 2 - 0.02), (seat + back) / 2))])


def build_parlour(bpy, collection=None):
    floor = kit.mesh_object(bpy, "Room Floor", *box((ROOM[0], ROOM[1], 0.05), (0, 0, -0.025)), collection=collection)
    kit.assign(floor, kit.principled(bpy, "Oak Boards", (0.45, 0.30, 0.17), roughness=0.55))
    wall = kit.mesh_object(bpy, "Room Wall", *combine([box((ROOM[0], 0.1, 2.7), (0, ROOM[1] / 2 + 0.05, 1.35)),
                                                       box((0.1, ROOM[1], 2.7), (-ROOM[0] / 2 - 0.05, 0, 1.35))]),
                           collection=collection)
    kit.assign(wall, kit.principled(bpy, "Plaster", (0.86, 0.84, 0.79), roughness=0.9))
    fabric = kit.principled(bpy, "Sofa Fabric", (0.22, 0.35, 0.42), roughness=0.85)
    wood = kit.principled(bpy, "Walnut", (0.25, 0.14, 0.07), roughness=0.4)
    sofa = kit.mesh_object(bpy, "Sofa", *combine([box((2.0, 0.85, SEAT), (0, 0, SEAT / 2)),
                                                  box((2.0, 0.2, 0.45), (0, 0.33, SEAT + 0.22)),
                                                  box((0.18, 0.85, 0.22), (-0.91, 0, SEAT + 0.11)),
                                                  box((0.18, 0.85, 0.22), (0.91, 0, SEAT + 0.11))]),
                           location=(*SOFA_AT, 0), collection=collection)
    kit.assign(sofa, fabric)
    coffee = kit.mesh_object(bpy, "Coffee Table", *combine([box((1.1, 0.6, 0.04), (0, 0, COFFEE_TOP - 0.02))] + [
        box((0.05, 0.05, COFFEE_TOP - 0.04), (sx * 0.5, sy * 0.25, (COFFEE_TOP - 0.04) / 2)) for sx in (-1, 1)
        for sy in (-1, 1)]), location=(*COFFEE_AT, 0), collection=collection)
    kit.assign(coffee, wood)
    armchair_mesh = None
    for index, (x, y) in enumerate(ARMCHAIRS):
        if armchair_mesh is None:
            chair = kit.mesh_object(bpy, "Armchair", *combine([box((0.8, 0.8, SEAT), (0, 0, SEAT / 2)),
                                                               box((0.8, 0.18, 0.4), (0, -0.31, SEAT + 0.2))]),
                                    collection=collection)
            kit.assign(chair, fabric)
            armchair_mesh = chair.data
        else:
            chair = bpy.data.objects.new("Armchair", armchair_mesh)
            kit.link(bpy, chair, collection)
        chair.location, chair.rotation_euler = (x, y, 0), (0, 0, -math.pi / 2 if x < 0 else math.pi / 2)
    table = kit.mesh_object(bpy, "Dining Table", *combine([box((1.6, 0.9, 0.04), (0, 0, TABLE_TOP - 0.02))] + [
        box((0.06, 0.06, TABLE_TOP - 0.04), (sx * 0.74, sy * 0.39, (TABLE_TOP - 0.04) / 2)) for sx in (-1, 1)
        for sy in (-1, 1)]), location=(*DINING_AT, 0), collection=collection)
    kit.assign(table, wood)
    chair_mesh = None
    for x, y in DINING_CHAIRS:
        facing = 1 if y > DINING_AT[1] else -1
        if chair_mesh is None:
            chair = kit.mesh_object(bpy, "Dining Chair", *_chair_parts(), collection=collection)
            kit.assign(chair, wood)
            chair_mesh = chair.data
        else:
            chair = bpy.data.objects.new("Dining Chair", chair_mesh)
            kit.link(bpy, chair, collection)
        chair.location, chair.rotation_euler = (x, y, 0), (0, 0, 0 if facing < 0 else math.pi)
    lamp = kit.mesh_object(bpy, "Floor Lamp", *combine([kit.cylinder_geometry(0.16, 0.03, 24, (0, 0, 0.015)),
                                                        kit.cylinder_geometry(0.015, LAMP_TOP - 0.3, 12,
                                                                              (0, 0, (LAMP_TOP - 0.3) / 2)),
                                                        lathe([(0.12, LAMP_TOP - 0.32), (0.2, LAMP_TOP - 0.32),
                                                               (0.13, LAMP_TOP), (0.08, LAMP_TOP)], 24)]),
                           location=(*LAMP_AT, 0), collection=collection)
    kit.assign(lamp, kit.principled(bpy, "Lamp Linen", (0.92, 0.88, 0.78), roughness=0.8))
    shelf = kit.mesh_object(bpy, "Bookshelf", *combine([box((1.0, 0.32, 0.03), (0, 0, z)) for z in
                                                        (0.015, 0.45, 0.9, 1.35, SHELF_TOP - 0.015)] +
                                                       [box((0.03, 0.32, SHELF_TOP), (sx * 0.485, 0, SHELF_TOP / 2))
                                                        for sx in (-1, 1)]),
                            location=(*SHELF_AT, 0), collection=collection)
    kit.assign(shelf, wood)


# ------------------------------------------------------------------ the roadster (a car with repeated wheels)

WHEEL_RADIUS, WHEEL_WIDTH, TRACK, WHEELBASE = 0.33, 0.22, 1.56, 2.6
CAR_LENGTH, CAR_WIDTH, ROOF = 4.2, 1.8, 1.32
WHEELS = tuple((sx * WHEELBASE / 2, sy * TRACK / 2) for sx in (1, -1) for sy in (-1, 1))


def build_roadster(bpy, collection=None):
    paint = kit.principled(bpy, "Car Paint", (0.62, 0.05, 0.04), roughness=0.25, metallic=0.6)
    tyre = kit.principled(bpy, "Tyre Rubber", (0.03, 0.03, 0.03), roughness=0.85)
    chrome = kit.principled(bpy, "Chrome", (0.85, 0.85, 0.86), roughness=0.15, metallic=1.0)
    glass = kit.principled(bpy, "Car Glass", (0.05, 0.07, 0.09), roughness=0.05)
    body = kit.mesh_object(bpy, "Car Body", *combine([box((CAR_LENGTH, CAR_WIDTH, 0.5), (0, 0, 0.6)),
                                                      box((2.0, CAR_WIDTH - 0.16, 0.08), (-0.2, 0, 0.89))]),
                           collection=collection)
    kit.assign(body, paint)
    cabin = kit.mesh_object(bpy, "Car Cabin", *box((1.8, CAR_WIDTH - 0.2, ROOF - 0.93), (-0.3, 0, (ROOF + 0.93) / 2)),
                            collection=collection)
    kit.assign(cabin, glass)
    wheel_mesh = None
    for x, y in WHEELS:
        if wheel_mesh is None:
            rubber = kit.cylinder_geometry(WHEEL_RADIUS, WHEEL_WIDTH, 32, axis="Y")
            wheel = kit.mesh_object(bpy, "Car Wheel", *combine([
                rubber, kit.cylinder_geometry(WHEEL_RADIUS * 0.6, WHEEL_WIDTH + 0.01, 24, axis="Y")]),
                collection=collection, smooth=True)
            kit.assign(wheel, tyre, chrome)
            for polygon in wheel.data.polygons[len(rubber[1]):]:   # The hub's faces follow the tyre's.
                polygon.material_index = 1
            wheel_mesh = wheel.data
        else:
            wheel = bpy.data.objects.new("Car Wheel", wheel_mesh)
            kit.link(bpy, wheel, collection)
        wheel.location = (x, y, WHEEL_RADIUS)
    for sy in (-1, 1):
        light = kit.mesh_object(bpy, "Car Headlight", *kit.cylinder_geometry(0.09, 0.04, 20, axis="X"),
                                location=(CAR_LENGTH / 2 + 0.01, sy * 0.6, 0.66), collection=collection)
        kit.assign(light, chrome)


# ------------------------------------------------------------------ the outfit (a character in clothing parts)

HEIGHT, SHOE_LENGTH = 1.75, 0.28
PARTS = ("Character Body", "Character Shirt", "Character Trousers", "Character Shoe", "Character Hat")


def build_outfit(bpy, collection=None):
    skin = kit.principled(bpy, "Skin", (0.80, 0.60, 0.48), roughness=0.55)
    shirt_cloth = kit.principled(bpy, "Shirt Cotton", (0.85, 0.36, 0.20), roughness=0.8)
    denim = kit.principled(bpy, "Denim", (0.12, 0.18, 0.36), roughness=0.85)
    leather = kit.principled(bpy, "Shoe Leather", (0.18, 0.10, 0.05), roughness=0.5)
    felt = kit.principled(bpy, "Hat Felt", (0.10, 0.16, 0.10), roughness=0.9)
    body = kit.mesh_object(bpy, "Character Body", *combine([
        sphere(0.12, (0, 0, 1.52), 24, 12), kit.cylinder_geometry(0.05, 0.08, 12, (0, 0, 1.40)),
        kit.cylinder_geometry(0.045, 0.62, 12, (-0.24, 0, 1.05)), kit.cylinder_geometry(0.045, 0.62, 12, (0.24, 0, 1.05))]),
        collection=collection, smooth=True)
    kit.assign(body, skin)
    shirt = kit.mesh_object(bpy, "Character Shirt", *lathe([(0.17, 0.9), (0.19, 1.05), (0.2, 1.3), (0.14, 1.38),
                                                            (0.06, 1.4)], 24), collection=collection, smooth=True)
    kit.assign(shirt, shirt_cloth)
    legs = [kit.cylinder_geometry(0.08, 0.88, 16, (sx * 0.1, 0, 0.52)) for sx in (-1, 1)]
    trousers = kit.mesh_object(bpy, "Character Trousers", *combine(legs + [box((0.36, 0.2, 0.12), (0, 0, 0.94))]),
                               collection=collection, smooth=True)
    kit.assign(trousers, denim)
    shoe_mesh = None
    for sx in (-1, 1):
        if shoe_mesh is None:
            shoe = kit.mesh_object(bpy, "Character Shoe", *box((0.11, SHOE_LENGTH, 0.08), (0, 0.04, 0.04)),
                                   collection=collection)
            kit.assign(shoe, leather)
            shoe_mesh = shoe.data
        else:
            shoe = bpy.data.objects.new("Character Shoe", shoe_mesh)
            kit.link(bpy, shoe, collection)
        shoe.location = (sx * 0.1, 0, 0)
    hat = kit.mesh_object(bpy, "Character Hat", *lathe([(0.24, 1.6), (0.24, 1.615), (0.13, 1.62), (0.12, HEIGHT),
                                                        (0.0, HEIGHT)], 32), collection=collection)
    kit.assign(hat, felt)
