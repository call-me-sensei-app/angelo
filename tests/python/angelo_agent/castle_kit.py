# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A fairytale castle built from plain meshes: the ``oneshot-castle`` stand-in and the heavy scene's castle core.

A generic hilltop castle in the spirit of the 19th-century romantic revival
(slender round towers with steep conical slate roofs, pale stone, a tall
hall-keep), made of plain meshes with the data API only, so the same code
runs in a background stock Blender, over the MCP add-on's socket and in
Angelo's reviewed Python. Everything is fixed: a castle built twice is
identical.

Axes: the courtyard is centred on the origin, the gate faces -Y, Z is up, metres.

* the curtain wall: four straight runs on the rectangle x = +-20, y = +-15,
  2 m thick, its wall-walk 9 m up, with merlons on the outer edge
  ('Castle Wall <side>', 'Castle Wall <side> Battlements');
* four corner towers and two towers flanking the gate, round, each with a
  steep conical slate roof ('Castle Tower <id>', 'Castle Tower <id> Roof');
* the gatehouse on the south run with an arched passage through it
  ('Castle Gatehouse', its battlements 'Castle Gatehouse Battlements');
* the keep, a tall hall-tower on the north side of the courtyard with a steep
  gabled roof ('Castle Keep', 'Castle Keep Roof');
* windows ('Castle Window NN'): a stone frame and warm glowing glass each.
"""

import math

import scenario_build_kit as kit

HALF_X, HALF_Y = 20.0, 15.0                 # The curtain wall's centreline rectangle.
WALL_THICK, WALL_TOP = 2.0, 9.0
MERLON, MERLON_PITCH = (1.0, 0.6, 1.2), 1.8  # Length, depth, height; one merlon every 1.8 m.
CORNER_RADIUS, CORNER_TOP, CORNER_APEX = 3.5, 17.0, 25.0
GATE_TOWER_RADIUS, GATE_TOWER_TOP, GATE_TOWER_APEX = 2.4, 16.0, 23.0
ROOF_OVERHANG = 0.5
TOWERS = {"NE": ((HALF_X, HALF_Y), CORNER_RADIUS, CORNER_TOP, CORNER_APEX),
          "NW": ((-HALF_X, HALF_Y), CORNER_RADIUS, CORNER_TOP, CORNER_APEX),
          "SE": ((HALF_X, -HALF_Y), CORNER_RADIUS, CORNER_TOP, CORNER_APEX),
          "SW": ((-HALF_X, -HALF_Y), CORNER_RADIUS, CORNER_TOP, CORNER_APEX),
          "Gate West": ((-6.5, -17.5), GATE_TOWER_RADIUS, GATE_TOWER_TOP, GATE_TOWER_APEX),
          "Gate East": ((6.5, -17.5), GATE_TOWER_RADIUS, GATE_TOWER_TOP, GATE_TOWER_APEX)}
GATE_X, GATE_Y, GATE_DEPTH, GATE_TOP = (-5.0, 5.0), (-18.5, -11.5), 7.0, 13.0
PASSAGE_HALF, SPRINGING, ARCH_RADIUS = 2.0, 3.5, 2.0   # A round arch: crown at 5.5 m.
KEEP_CENTRE, KEEP_SIZE, KEEP_EAVES, KEEP_RIDGE = (0.0, 6.0), (14.0, 9.0), 25.0, 33.0
WINDOW_SIZE = (1.0, 2.0)                    # Width, height of the opening each window frames.
SEGMENTS = 32


def materials(bpy):
    """``{role: material}`` of the castle: pale warm stone, blue slate, window stone and warm glass."""
    return {"stone": kit.principled(bpy, "Castle Stone", (0.66, 0.62, 0.56), roughness=0.85),
            "slate": kit.principled(bpy, "Castle Slate", (0.09, 0.13, 0.22), roughness=0.55),
            "frame": kit.principled(bpy, "Castle Window Stone", (0.52, 0.48, 0.42), roughness=0.8),
            "glass": kit.principled(bpy, "Castle Window Glass", (0.05, 0.05, 0.06), roughness=0.1,
                                    emission=(1.0, 0.62, 0.3), emission_strength=2.5)}


# ---------------------------------------------------------------- geometry

def cone_geometry(radius, base_z, apex_z, centre=(0.0, 0.0), segments=SEGMENTS, offset=0):
    """A closed cone: a ring at ``base_z``, an apex above the centre and a flat underside."""
    cx, cy = centre
    vertices = [(cx + radius * math.cos(2 * math.pi * i / segments), cy + radius * math.sin(2 * math.pi * i / segments),
                 base_z) for i in range(segments)] + [(cx, cy, apex_z)]
    faces = [(i, (i + 1) % segments, segments) for i in range(segments)]
    faces.append(tuple(reversed(range(segments))))
    return vertices, [tuple(index + offset for index in face) for face in faces]


def oriented_box(size, centre, yaw, offset=0):
    """An axis-aligned box of ``size`` turned ``yaw`` radians about Z around its own centre."""
    vertices, faces = kit.box_geometry(size, (0.0, 0.0, 0.0), offset)
    c, s = math.cos(yaw), math.sin(yaw)
    turned = [(centre[0] + x * c - y * s, centre[1] + x * s + y * c, centre[2] + z) for x, y, z in vertices]
    return turned, faces


def prism_xz(profile, y_low, y_high, offset=0):
    """A closed prism: a polygon in the XZ plane (counter-clockwise seen from -Y) extruded from y_low to y_high."""
    count = len(profile)
    vertices = [(x, y_low, z) for x, z in profile] + [(x, y_high, z) for x, z in profile]
    faces = [tuple(range(count)), tuple(count + i for i in reversed(range(count)))]
    faces += [(i, count + i, count + (i + 1) % count, (i + 1) % count) for i in range(count)]
    return vertices, [tuple(index + offset for index in face) for face in faces]


def prism_yz(profile, x_low, x_high, offset=0):
    """A closed prism: a polygon in the YZ plane (counter-clockwise seen from +X) extruded from x_low to x_high."""
    count = len(profile)
    vertices = [(x_low, y, z) for y, z in profile] + [(x_high, y, z) for y, z in profile]
    faces = [tuple(reversed(range(count))), tuple(count + i for i in range(count))]
    faces += [(i, (i + 1) % count, count + (i + 1) % count, count + i) for i in range(count)]
    return vertices, [tuple(index + offset for index in face) for face in faces]


def merged(parts):
    vertices, faces = [], []
    for part_vertices, part_faces in parts:
        faces += [tuple(index + len(vertices) for index in face) for face in part_faces]
        vertices += part_vertices
    return vertices, faces


def gate_profile(steps=12):
    """The gatehouse's front: a block with the arched passage cut from its foot (XZ, counter-clockwise)."""
    low, high = GATE_X
    angles = [math.pi * (1 - i / steps) for i in range(steps + 1)]
    arch = [(ARCH_RADIUS * math.cos(a), SPRINGING + ARCH_RADIUS * math.sin(a)) for a in angles]
    # Counter-clockwise from -Y: along the foot, up the far side, across the top, down the near side.
    return ([(low, 0.0), (-PASSAGE_HALF, 0.0)] + [(x, z) for x, z in arch] + [(PASSAGE_HALF, 0.0), (high, 0.0),
                                                                             (high, GATE_TOP), (low, GATE_TOP)])


def merlon_row(start, end, along_x, edge, top, offset=0.0):
    """Merlon boxes along a straight line from ``start`` to ``end`` at a fixed ``edge`` coordinate."""
    length, depth, height = MERLON
    span = end - start
    count = max(1, int((span - length) // MERLON_PITCH) + 1)
    first = start + (span - (count - 1) * MERLON_PITCH) / 2
    boxes = []
    for index in range(count):
        middle = first + index * MERLON_PITCH
        centre = (middle, edge, top + height / 2) if along_x else (edge, middle, top + height / 2)
        size = (length, depth, height) if along_x else (depth, length, height)
        boxes.append((size, (centre[0] + offset * along_x, centre[1], centre[2])))
    return boxes


def wall_runs():
    """``{side: (box size, box centre, merlon boxes)}`` of the four curtain-wall runs (the south run in two)."""
    depth = MERLON[1]
    t = WALL_THICK
    outer = t / 2 - depth / 2
    runs = {"North": ((2 * HALF_X - 2 * CORNER_RADIUS + 2.0, t, WALL_TOP), (0.0, HALF_Y, WALL_TOP / 2),
                      merlon_row(-HALF_X + CORNER_RADIUS, HALF_X - CORNER_RADIUS, True, HALF_Y + outer, WALL_TOP)),
            "East": ((t, 2 * HALF_Y - 2 * CORNER_RADIUS + 2.0, WALL_TOP), (HALF_X, 0.0, WALL_TOP / 2),
                     merlon_row(-HALF_Y + CORNER_RADIUS, HALF_Y - CORNER_RADIUS, False, HALF_X + outer, WALL_TOP)),
            "West": ((t, 2 * HALF_Y - 2 * CORNER_RADIUS + 2.0, WALL_TOP), (-HALF_X, 0.0, WALL_TOP / 2),
                     merlon_row(-HALF_Y + CORNER_RADIUS, HALF_Y - CORNER_RADIUS, False, -HALF_X - outer, WALL_TOP))}
    length = HALF_X - CORNER_RADIUS - GATE_X[1] + 1.5
    for side, sign in (("South West", -1), ("South East", 1)):
        middle = sign * (GATE_X[1] + HALF_X - CORNER_RADIUS) / 2
        low, high = sorted((sign * (GATE_X[1] + 0.2), sign * (HALF_X - CORNER_RADIUS)))
        runs[side] = ((length, t, WALL_TOP), (middle - sign * 0.25, -HALF_Y, WALL_TOP / 2),
                      merlon_row(low, high, True, -HALF_Y - outer, WALL_TOP))
    return runs


def window_parts(centre, yaw):
    """A window facing ``yaw`` (its outward direction) centred at ``centre``: frame boxes and the glass pane."""
    width, height = WINDOW_SIZE
    bar = 0.18
    c, s = math.cos(yaw), math.sin(yaw)

    def at(across, up, out):
        return (centre[0] + c * out - s * across, centre[1] + s * out + c * across, centre[2] + up)
    turn = yaw - math.pi / 2   # A box's local X runs across the wall face.
    frame = [oriented_box((width + 2 * bar, 0.24, bar), at(0, (height + bar) / 2, 0.0), turn),
             oriented_box((width + 2 * bar, 0.24, bar), at(0, -(height + bar) / 2, 0.0), turn),
             oriented_box((bar, 0.24, height), at(-(width + bar) / 2, 0, 0.0), turn),
             oriented_box((bar, 0.24, height), at((width + bar) / 2, 0, 0.0), turn)]
    glass = oriented_box((width, 0.08, height), at(0, 0, -0.04), turn)
    return merged(frame), glass


def window_spots():
    """``[(centre, outward yaw)]``: eight on the keep's courtyard front, one high on each tower's outer side."""
    spots = []
    front = KEEP_CENTRE[1] - KEEP_SIZE[1] / 2
    for z in (12.0, 19.0):
        for x in (-4.5, -1.5, 1.5, 4.5):
            spots.append(((x, front - 0.04, z), -math.pi / 2))
    for _, ((x, y), radius, top, _apex) in TOWERS.items():
        yaw = math.atan2(y, x) if abs(x) > 10 else -math.pi / 2
        spots.append(((x + (radius - 0.04) * math.cos(yaw), y + (radius - 0.04) * math.sin(yaw), top - 6.0), yaw))
    return spots


# ---------------------------------------------------------------- objects

def tower(bpy, name, centre, radius, top, apex, made, collection=None, segments=SEGMENTS):
    """A round tower with a corbelled band under its eaves and a steep conical roof; returns (body, roof)."""
    body = merged([kit.cylinder_geometry(radius, top, segments, (centre[0], centre[1], top / 2)),
                   kit.cylinder_geometry(radius + 0.35, 0.9, segments, (centre[0], centre[1], top - 0.45))])
    body = kit.assign(kit.mesh_object(bpy, name, *body, collection=collection, smooth=True), made["stone"])
    roof = cone_geometry(radius + ROOF_OVERHANG, top, apex, centre, segments)
    roof = kit.assign(kit.mesh_object(bpy, name + " Roof", *roof, collection=collection, smooth=True), made["slate"])
    return body, roof


def curtain_wall(bpy, made, collection=None):
    built = []
    for side, (size, centre, merlons) in wall_runs().items():
        built.append(kit.assign(kit.box_object(bpy, "Castle Wall " + side, size, centre, collection), made["stone"]))
        vertices, faces = kit.merged_boxes(merlons)
        built.append(kit.assign(kit.mesh_object(bpy, "Castle Wall %s Battlements" % side, vertices, faces,
                                                collection=collection), made["stone"]))
    return built


def gatehouse(bpy, made, collection=None):
    block = kit.assign(kit.mesh_object(bpy, "Castle Gatehouse", *prism_xz(gate_profile(), *GATE_Y),
                                       collection=collection), made["stone"])
    front = GATE_Y[0] + MERLON[1] / 2
    merlons = merlon_row(GATE_X[0], GATE_X[1], True, front, GATE_TOP) + merlon_row(
        GATE_X[0], GATE_X[1], True, GATE_Y[1] - MERLON[1] / 2, GATE_TOP)
    battlements = kit.assign(kit.mesh_object(bpy, "Castle Gatehouse Battlements", *kit.merged_boxes(merlons),
                                             collection=collection), made["stone"])
    return block, battlements


def keep(bpy, made, collection=None):
    (cx, cy), (sx, sy) = KEEP_CENTRE, KEEP_SIZE
    body = kit.assign(kit.box_object(bpy, "Castle Keep", (sx, sy, KEEP_EAVES), (cx, cy, KEEP_EAVES / 2), collection),
                      made["stone"])
    half, over = sy / 2 + ROOF_OVERHANG, ROOF_OVERHANG
    profile = [(cy - half, KEEP_EAVES - 0.3), (cy + half, KEEP_EAVES - 0.3), (cy, KEEP_RIDGE)]
    geometry = prism_yz(profile, cx - sx / 2 - over, cx + sx / 2 + over)
    roof = kit.assign(kit.mesh_object(bpy, "Castle Keep Roof", *geometry, collection=collection), made["slate"])
    return body, roof


def windows(bpy, made, collection=None, prefix="Castle Window"):
    built = []
    for index, (centre, yaw) in enumerate(window_spots(), 1):
        frame, glass = window_parts(centre, yaw)
        obj = kit.mesh_object(bpy, "%s %02d" % (prefix, index), *merged([frame, glass]), collection=collection)
        kit.assign(obj, made["frame"], made["glass"])
        glass_faces = len(glass[1])
        for polygon in obj.data.polygons[len(obj.data.polygons) - glass_faces:]:
            polygon.material_index = 1
        built.append(obj)
    return built


def castle(bpy, collection=None, made=None):
    """Every part of the castle; returns ``{"objects": [...], "materials": {...}}``."""
    made = made or materials(bpy)
    objects = curtain_wall(bpy, made, collection)
    for tower_id, (centre, radius, top, apex) in TOWERS.items():
        objects += tower(bpy, "Castle Tower " + tower_id, centre, radius, top, apex, made, collection)
    objects += gatehouse(bpy, made, collection)
    objects += keep(bpy, made, collection)
    objects += windows(bpy, made, collection)
    return {"objects": objects, "materials": made}


def extent():
    """The castle's world bounds ``(low, high)`` as built (towers, roofs and battlements included)."""
    xs, ys = [], []
    for (x, y), radius, _top, _apex in TOWERS.values():
        xs += [x - radius - ROOF_OVERHANG, x + radius + ROOF_OVERHANG]
        ys += [y - radius - ROOF_OVERHANG, y + radius + ROOF_OVERHANG]
    return (min(xs), min(ys), 0.0), (max(xs), max(ys), KEEP_RIDGE)
