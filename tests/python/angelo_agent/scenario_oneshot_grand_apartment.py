# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-grand-apartment``: a super-detailed twelve-room apartment from one message (long candidate 5).

LONG-TEST-SELECTION candidate 5, from the maintainer's brief: "a super detailed apartment with tons of furnitures"
of "200 pieces of items or something", "different enough that you can't just create one and then copy", and "a lot
of time, it's also level of details". Twelve rooms, an entrance hall and a balcony, about 240 pieces, every one its
own design with its construction and materials described (no two chairs, tables or lamps alike). Candidate 1
(``scenario_oneshot_apartment``) is unchanged. The brief says nothing about how to build or split the work.

One table (``long_kit_flat_north.PIECES`` + ``long_kit_flat_south.PIECES``) writes the brief's piece phrases,
builds the stand-in (``long_kit_flat.build_flat``) and derives the checks, so every stated number has one source.
The checks verify distinctness and detail where a closed check can measure them: each named piece's own seat or
top height (rays down that pass through anything else), its own footprint and overall height (the dining chairs'
widths and back heights all differ), open space under seats and tops (level rays meet nothing of the piece),
knobs and pulls standing proud of drawer and door fronts (the piece's box reaches past its stated depth), frames and mirrors on their walls, hollow basins and baths, windows in the walls, open
doorways, colour families, and every room's contents inside it. Every check is construction-agnostic (what a person sees, never how it was built) and every ray is aimed
with margin; ``long_probe_check`` proves it on the stand-in.

Axes: the floor is centred on the origin with its top at z = 0, north is +Y; the hall runs along X.
"""

import math

import long_kit_flat as flat
import long_kit_flat_north as north
import long_kit_flat_south as south
import oneshot_kit
import scenario_build_kit as kit

ID = "oneshot_grand_apartment"
TITLE = "A super-detailed twelve-room apartment"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "grand-apartment-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
# The calibration pilots' cap (BENCH-PREREG Amendment I7): a pilot that reaches it counts as ">= 7,200 s", never a
# measured time. Paired trials set their own with --task-timeout (Amendment I3: twice the qualifying pilot's time).
LIMITS = {"wall_s": 7200.0, "job_wait_s": 0.0, "cost_usd": 80.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

PIECES = north.PIECES + south.PIECES
ROOM_ORDER = ("Entrance Hall", "Living Room", "Balcony", "Dining Room", "Kitchen", "Pantry", "Laundry Room",
              "Master Bedroom", "Walk-in Wardrobe", "En-suite", "Kids Room", "Guest Bedroom", "Bathroom", "Study")
ROOM_WORDS = {"Entrance Hall": "the entrance hall", "Kids Room": "the kids' room", "En-suite": "the en-suite",
              "Walk-in Wardrobe": "the walk-in wardrobe"}
assert set(ROOM_ORDER) == set(flat.ROOMS) == {piece["room"] for piece in PIECES}


def _g(value):
    return "%g" % round(value, 3)


def _words(room):
    return ROOM_WORDS.get(room, "the " + room.lower())


def _look(location, target, label, lens=24.0):
    return {"location": location, "target": target, "lens": lens, "role": "closeup", "label": "Close-up: " + label}


HERO = {"location": (12.0, -17.0, 27.0), "target": (0.0, 1.0, 0.0), "lens": 30.0}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "top": {"location": (0.0, 1.0, 30.0), "target": (0.0, 1.0, 0.0), "ortho_scale": 29.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "front": {"location": (0.0, -40.0, 1.3), "target": (0.0, 0.0, 1.3), "ortho_scale": 23.0, "role": "reference",
              "label": "South elevation (orthographic, looking +Y)"},
    "side": {"location": (40.0, 1.0, 1.3), "target": (0.0, 1.0, 1.3), "ortho_scale": 17.0, "role": "reference",
             "label": "East elevation (orthographic, looking -X)"},
    "closeup_living": _look((-5.0, -1.5, 7.5), (-7.5, 4.5, 0.4), "the living room and the balcony from the south"),
    "closeup_dining_kitchen": _look((1.0, -1.6, 7.0), (0.5, 4.0, 0.4), "the dining room and the kitchen"),
    "closeup_service": _look((7.5, -1.8, 7.0), (8.0, 4.0, 0.4), "the pantry and the laundry room"),
    "closeup_master": _look((-6.0, 1.6, 7.5), (-7.5, -4.0, 0.4), "the master bedroom, wardrobe and en-suite"),
    "closeup_children": _look((1.5, 1.6, 7.0), (1.0, -4.0, 0.4), "the kids' room and the guest bedroom"),
    "closeup_study": _look((8.0, 1.6, 7.0), (8.0, -4.0, 0.4), "the bathroom and the study"),
    "closeup_hall": _look((12.5, -1.4, 3.6), (2.0, 0.0, 0.4), "the entrance hall from its east end"),
}
FLOOR, WALLS = {"whole": "Apartment Floor"}, {"prefix": "Apartment Wall"}   # Boxes measure the whole floor.
WINDOW_TARGET = {"prefix": "Apartment Window", "word": True}
SUBJECT = ({"prefix": ""},)
SEATS = ("chair", "carver", "cantilever", "arm", "sofa", "bench", "stool", "stool3", "lounger", "swivel", "peacock")
BEDS = ("bed", "housebed")
HOLLOW = ("sink", "basin", "usink", "bath", "tub")


def phrase(piece):
    """The brief's words for one piece, its numbers filled from the table."""
    w, d, h = piece["size"]
    x, y = piece["at"][:2]
    return "'%s', %s" % (piece["name"], piece["text"].format(
        w=_g(w), d=_g(d), h=_g(h), top=_g(piece["top"] or h), at="(%s, %s)" % (_g(x), _g(y)), x=_g(x), y=_g(y),
        z=_g(piece["z"] or 0.0)))


def _room_paragraph(room):
    pieces = [phrase(piece) for piece in PIECES if piece["room"] == room]
    lead = {"Balcony": "The balcony ('Balcony'), beyond the living room's north wall: 'Balcony Deck', teak decking, x = "
                       "-11 to -4, y = 7 to 9, level with the floor; 'Balcony Balustrade', frameless glass with a "
                       "stainless-steel handrail, %s m high, on its three open sides; 'Balcony Door', sliding glass in "
                       "slim black frames, %s m wide, %s m high, centred in the north wall at x = %s; " % (
                           _g(flat.BALUSTRADE), _g(flat.BALCONY_DOOR[1]), _g(flat.BALCONY_DOOR[2]),
                           _g(flat.BALCONY_DOOR[0]))}
    head = lead.get(room, "%s ('%s'): " % (_words(room)[0].upper() + _words(room)[1:], room))
    return head + "; ".join(pieces) + "."


def _windows():
    parts = []
    for side in "NSWE":
        rows = ["%s %s m at %s = %s%s" % (_words(room)[4:], _g(w), "x" if side in "NS" else "y", _g(c),
                                          "" if sill == 0.9 else " (sill %s m)" % _g(sill))
                for room, s, c, w, sill, _head in flat.WINDOWS if s == side]
        parts.append("%s wall: %s" % (flat.WALL_WORD[side], ", ".join(rows)))
    return "; ".join(parts)


def _list(values):
    values = [_g(v) for v in values]
    return ", ".join(values[:-1]) + " and " + values[-1]


SHELL = (
    "The shell. 'Apartment Floor', oak parquet, 22 m along X by 14 m along Y, centred on the origin, top at z = 0. "
    "Plaster walls 2.7 m high and 0.1 m thick, names starting 'Apartment Wall': along the four outer edges; the "
    "two walls of a 1.5 m wide entrance hall along the full length, centred on y = 0.75 and y = -0.75; between "
    "rooms north of the hall at x = " + _list(flat.NORTH_PARTITIONS) + " and south of it at x = "
    + _list(flat.SOUTH_PARTITIONS) + "; and between the walk-in wardrobe and the en-suite at y = -3.75, x = -5 to "
    "-2.5. North of the hall, west to east: living room (x = -11 to -4), dining room (-4 to 0.5), kitchen (0.5 to "
    "5.5), pantry (5.5 to 7.5), laundry room (7.5 to 11). South: master bedroom (x = -11 to -5); walk-in wardrobe "
    "(x = -5 to -2.5, y = -3.75 to -0.75) with the en-suite south of it (y = -7 to -3.75); kids' room (-2.5 to "
    "1.5); guest bedroom (1.5 to 5); bathroom (5 to 7.5); study (7.5 to 11). Open doorways 0.9 m wide and 2.1 m "
    "high, centred on the hall's walls at x = "
    + ", ".join("%s (%s)" % (_g(x), _words(room)[4:]) for room, x in flat.HALL_DOORWAYS.items()) + "; the "
    "wardrobe and en-suite open only off the master bedroom, through its east wall at x = -5, centred at y = -2.25 "
    "and y = -5.4. 'Entrance Hall Front Door', panelled, deep navy, brass knocker, letter plate and lever handle, "
    "1 m wide, in the east wall at the hall's end. Windows named 'Apartment Window ...', each with a painted frame, "
    "a glazing bar, glass and a sill, heads 2.1 m up, sills 0.9 m unless stated: " + _windows() + ".")

PROMPT = (
    "A large family apartment, seen from above with no ceiling: twelve rooms off a long entrance hall, and a "
    "balcony, densely furnished and dressed with over two hundred pieces, each its own design. Build it. The two "
    "attached pictures show it: the hero shot and a plan from above (+Y is north). Take the layout and mood from "
    "them; where they and this brief disagree, the brief wins. Make every piece at production detail, as it is "
    "really made: legs, frames, rails and stretchers; cushions with seams, piping or buttons; drawers and doors "
    "with their handles, knobs and hinges; fittings with taps, frames and rails; the materials described. No two "
    "pieces are alike: each chair, table, lamp or rug has its own shape, proportions and materials.\n\n"
    "Conventions. Metres, Z up. I find things by name: name the floor, walls, windows, doors and every piece "
    "exactly as quoted; each piece's name starts with its room's name. Give every part of a piece a name starting "
    "with the piece's name (e.g. 'Dining Room Windsor Chair Seat'), never the name of what it stands on, and "
    "anything you add a meaningful name starting with its room's name. Keep the file tidy: nothing from the "
    "default scene remains, no unused leftovers, and a material on everything that shows. No ceiling.\n\n"
    + SHELL + "\n\n"
    "Pieces stand on the floor centred at their (x, y), seat, top or rim at the stated height; a piece on a wall "
    "has its back to it; a picture, mirror or clock hangs flat on its wall, centred where given. Sizes are "
    "overall: width (across the front) x depth x height.\n\n"
    + "\n\n".join(_room_paragraph(room) for room in ROOM_ORDER) + "\n\n"
    "Light and camera. Soft daylight from the south-east through the windows, and a camera named 'Hero Camera' at "
    "(%s) looking at (%s) with a %s mm lens, as the scene's camera.\n\n" % (
        ", ".join(_g(v) for v in HERO["location"]), ", ".join(_g(v) for v in HERO["target"]), _g(HERO["lens"])) +
    "Before you call it done, look at the apartment from the hero camera and room by room next to the pictures, "
    "and fix what is wrong.")


# ------------------------------------------------------------------ checks

def _rot(piece, local):
    """A local offset (front toward -Y) turned to the world the way ``long_kits.place`` turns the piece."""
    a = math.radians(piece["facing"] + 90.0)
    return (local[0] * math.cos(a) - local[1] * math.sin(a), local[0] * math.sin(a) + local[1] * math.cos(a))


def _extent(piece):
    """The world footprint (x, y) of an axis-aligned piece, or None when it is turned at an angle."""
    w, d, _h = piece["size"]
    facing = piece["facing"] % 180.0
    if facing == 90.0:
        return w, d
    if facing == 0.0:
        return d, w
    return None


def _own(piece):
    return {"prefix": piece["name"], "word": True}


def _down(piece, label, low, high, local):
    dx, dy = _rot(piece, local)
    x, y = piece["at"][:2]
    return {"kind": "probe", "origin": [round(x + dx, 3), round(y + dy, 3), 4.0], "direction": [0, 0, -1],
            "target": _own(piece), "axis": "z", "range": [round(low, 3), round(high, 3)], "distance": 5.0,
            "label": label}


def _top(piece):
    """A ray down onto the piece's own seat, mattress or top, off both of its centre lines (anything else on it is
    passed through: the ray looks for the piece by its own name)."""
    w, d, _h = piece["size"]
    top = piece["top"]
    if piece["kind"] in SEATS:
        local, low, high = (0.14 * w, -0.12 * d), top - 0.05, top + 0.12
    elif piece["kind"] in BEDS:
        local, low, high = (0.2 * w, -0.22 * d), top - 0.05, top + 0.12
    elif piece["kind"] in ("round", "cyl"):
        local, low, high = (0.18 * w, 0.14 * w), top - 0.04, top + 0.05
    else:
        local, low, high = (0.21 * w, 0.16 * d), top - 0.04, top + 0.05
    word = "seat" if piece["kind"] in SEATS else "mattress top" if piece["kind"] in BEDS else "top"
    check = _down(piece, "%s: its %s %s m high" % (piece["name"], word, _g(top)), low, high, local)
    lying = resting_on(piece) if piece["kind"] in SEATS + BEDS else []
    if lying:
        check["target"] = {"any": [_own(piece)] + [{"name": name} for name in lying]}
    return check


def _hollow(piece):
    w, d, _h = piece["size"]
    return _down(piece, "%s: hollow, its basin below its %s m rim" % (piece["name"], _g(piece["top"])),
                 piece["top"] - 0.45, piece["top"] - 0.06, (0.12 * w, -0.1 * d))


def _box(piece):
    x, y = piece["at"][:2]
    ex, ey = _extent(piece)
    return {"kind": "bbox", "target": _own(piece), "min": [x - ex / 2, y - ey / 2, None],
            "max": [x + ex / 2, y + ey / 2, piece["size"][2]], "tolerance": 0.1,
            "label": "%s: %s x %s m at (%s, %s), %s m high" % (piece["name"], _g(piece["size"][0]),
                                                               _g(piece["size"][1]), _g(x), _g(y),
                                                               _g(piece["size"][2]))}


def _height(piece):
    """Its highest point: its stated height above what it stands on (a worktop's top, 0.92 m up)."""
    high = (piece["at"][2] if len(piece["at"]) > 2 else 0.0) + piece["size"][2]
    return {"kind": "bbox", "target": _own(piece), "max": [None, None, round(high, 3)], "tolerance": 0.08,
            "label": "%s: its highest point %s m up" % (piece["name"], _g(high))}


def _footprint(piece, grow=0.05):
    x, y = piece["at"][:2]
    ex, ey = _extent(piece) or (math.hypot(*piece["size"][:2]),) * 2
    return [x - ex / 2 - grow, y - ey / 2 - grow], [x + ex / 2 + grow, y + ey / 2 + grow]


def _open_under(piece):
    """Open underneath: a level ray across the piece's width at 55% of its seat or top height, off its centre line,
    meets nothing of it, so it stands on legs and is no solid block, however the legs are made (above any low
    stretcher, below the rails under a seat). Only for pieces the brief puts on legs; none for pieces on short feet,
    whose height the brief does not state."""
    w, d, _h = piece["size"]
    x, y = piece["at"][:2]
    start = _rot(piece, (-(w / 2 + 0.3), 0.1 * d))
    step = _rot(piece, (1.0, 0.0))
    return {"kind": "probe", "origin": [round(x + start[0], 3), round(y + start[1], 3), round(0.55 * piece["top"], 3)],
            "direction": [round(step[0], 4), round(step[1], 4), 0], "target": _own(piece), "expect": "miss",
            "distance": round(w + 0.6, 3), "label": "%s: open underneath, on legs" % piece["name"]}


def _handles(piece):
    """Its knobs or pulls stand proud of its front: its box reaches 1.2 to 12 cm past the stated front face (what a
    person sees sticking out, however the handles are made)."""
    a = math.radians(piece["facing"])
    out = (round(math.cos(a)), round(math.sin(a)))
    axis = 0 if out[0] else 1
    sign = out[axis]
    front = piece["at"][axis] + sign * piece["size"][1] / 2
    bound = [None, None, None]
    bound[axis] = round(front + sign * 0.066, 3)
    return {"kind": "bbox", "target": _own(piece), "max" if sign > 0 else "min": bound, "tolerance": 0.054,
            "label": "%s: its %d knobs or pulls standing proud of its front" % (piece["name"], piece["n"])}


def _towards(piece, origin_out, lateral, z, label, near, far):
    """A level ray toward the piece's front (or the wall it hangs on): it must meet the piece between ``near`` and
    ``far`` metres in front of ``origin_out``'s reference plane."""
    a = math.radians(piece["facing"])
    out, side = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    x, y = piece["at"][:2]
    axis = 0 if abs(out[0]) > 0.5 else 1
    plane = (x, y)[axis] + out[axis] * origin_out
    bounds = sorted([plane + out[axis] * near, plane + out[axis] * far])
    origin = [x + out[0] * (origin_out + 0.7) + side[0] * lateral, y + out[1] * (origin_out + 0.7) + side[1] * lateral, z]
    return {"kind": "probe", "origin": [round(v, 3) for v in origin], "direction": [round(-out[0], 3), round(-out[1], 3), 0],
            "target": _own(piece), "axis": "xy"[axis], "range": [round(v, 3) for v in bounds], "distance": 1.2,
            "label": label}


def _on_wall(piece):
    w, _d, h = piece["size"]
    return _towards(piece, 0.0, 0.12 * w, piece["z"] + 0.1 * h,
                    "%s: on its wall, centred %s m up" % (piece["name"], _g(piece["z"])), -0.02, 0.16)


def _proud_door(piece):
    return _towards(piece, piece["size"][1] / 2, 0.06, 0.49, "%s: its door standing proud of its front" % piece["name"],
                    0.008, 0.15)


def _colour(piece):
    return {"kind": "material", "target": _own(piece), "color": [piece["col"]],
            "label": "%s: %s in colour" % (piece["name"], {"warm": "warm", "grey": "black, white or grey", "green": "green",
                                                            "blue": "blue"}[piece["col"]])}


def resting_on(piece):
    """The names of the table's things lying on a seat or bed (its bedding, a throw): found by place, not by name."""
    x0, y0 = _footprint(piece, 0.0)[0]
    x1, y1 = _footprint(piece, 0.0)[1]
    return [other["name"] for other in PIECES if other is not piece and len(other["at"]) > 2
            and abs(other["at"][2] - piece["top"]) < 0.03 and x0 <= other["at"][0] <= x1 and y0 <= other["at"][1] <= y1]


def piece_checks(piece):
    """A hard top's height is its own box's top ('b' or 'z': nothing standing on it can hide it); a seat's or bed's,
    under a back or headboard, is a ray down that also counts what lies on it ('T' forces the ray for a hard top)."""
    letters, out = piece["checks"], []
    soft = piece["kind"] in SEATS + BEDS
    if "t" in letters and not soft and "b" not in letters and "z" not in letters:
        letters += "z"
    for letter, make in (("t", _top), ("T", _top), ("r", _hollow), ("b", _box), ("z", _height), ("l", _open_under),
                         ("h", _handles), ("w", _on_wall), ("p", _proud_door), ("c", _colour)):
        if letter in letters and (letter != "t" or soft):
            out.append(make(piece))
    return out


def _inside(room):
    x0, x1, y0, y1 = flat.ROOMS[room]
    checks = []
    for axis, low, high in ((0, x0, x1), (1, y0, y1)):
        middle = [None, None, None]
        middle[axis] = (low + high) / 2
        checks.append({"kind": "bbox", "target": {"prefix": room, "word": True}, "min": list(middle), "max": list(middle),
                       "tolerance": (high - low) / 2 + 0.25,
                       "label": "everything named '%s ...' inside it, %s = %s to %s" % (room, "xy"[axis], _g(low), _g(high))})
    return checks


def _ray(origin, direction, target, label, axis=None, span=None, distance=1.2):
    check = {"kind": "probe", "origin": [round(v, 3) for v in origin], "direction": list(direction), "target": target,
             "distance": distance, "label": label}
    if span is None:
        check["expect"] = "miss"
    else:
        check.update(axis=axis, range=[round(v, 3) for v in span])
    return check


def shell_checks():
    hx, hy = flat.FLOOR[0] / 2, flat.FLOOR[1] / 2
    out = [{"kind": "bbox", "target": FLOOR, "size": [[21.6, 22.4], [13.6, 14.4], None], "max": [None, None, 0.0],
            "tolerance": 0.05, "label": "a 22 x 14 m floor, its top at z = 0"},
           {"kind": "bbox", "target": WALLS, "min": [-hx, -hy, 0.0], "max": [hx, hy, flat.WALL_HEIGHT], "tolerance": 0.15,
            "label": "walls around the 22 x 14 m floor, 2.7 m high"}]
    out += [_ray((-8.53, s * 0.07, 1.1), (0, s, 0), WALLS, "the hall's %s wall at y = %s" % (word, _g(s * flat.HALL)),
                 "y", (s * flat.HALL - 0.15, s * flat.HALL + 0.15)) for s, word in ((1, "north"), (-1, "south"))]
    out += [_ray((x - 0.5, 3.5, 1.1), (1, 0, 0), WALLS, "a wall between rooms at x = %s, north of the hall" % _g(x), "x",
                 (x - 0.15, x + 0.15)) for x in flat.NORTH_PARTITIONS]
    out += [_ray((x - 0.5, -3.1, 1.1), (1, 0, 0), WALLS, "a wall between rooms at x = %s, south of the hall" % _g(x), "x",
                 (x - 0.15, x + 0.15)) for x in flat.SOUTH_PARTITIONS]
    out.append(_ray((-3.4, -3.25, 1.1), (0, -1, 0), WALLS, "the wall between the wardrobe and the en-suite at y = -3.75",
                    "y", (flat.SPLIT_Y - 0.15, flat.SPLIT_Y + 0.15)))
    for room, x in flat.HALL_DOORWAYS.items():
        s = 1 if flat.ROOMS[room][2] > 0 else -1
        out.append(_ray((x + 0.12, s * 0.07, 1.1), (0, s, 0), WALLS, "%s doorway open at x = %s" % (room, _g(x))))
    for room, y in flat.SIDE_DOORWAYS.items():
        out.append(_ray((flat.SIDE_WALL_X - 0.7, y + 0.12, 1.1), (1, 0, 0), WALLS,
                        "%s doorway open from the master bedroom at y = %s" % (room, _g(y))))
    for room, side, c, _w, sill, head in flat.WINDOWS:
        normal = {"N": (0, 1), "S": (0, -1), "W": (-1, 0), "E": (1, 0)}[side]
        line = {"N": hy, "S": -hy, "W": -hx, "E": hx}[side]
        z = (sill + head) / 2 + 0.1
        origin = (c + 0.15, line - normal[1] * 0.6, z) if side in "NS" else (line - normal[0] * 0.6, c + 0.15, z)
        axis = "y" if side in "NS" else "x"
        where = "%s's %s window at %s = %s" % (room, flat.WALL_WORD[side], "x" if side in "NS" else "y", _g(c))
        out.append(_ray(origin, (normal[0], normal[1], 0), WALLS, where + ": an opening in the wall", distance=1.0))
        out.append(_ray(origin, (normal[0], normal[1], 0), WINDOW_TARGET, where + ": a window in it", axis,
                        (line - 0.15, line + 0.15), distance=1.0))
    bx0, bx1, by0, by1 = flat.ROOMS["Balcony"]
    out += [{"kind": "bbox", "target": {"whole": "Balcony Deck"}, "min": [bx0, by0, None], "max": [bx1, by1, 0.0],
             "tolerance": 0.12, "label": "the balcony deck, x = -11 to -4 and y = 7 to 9, level with the floor"},
            {"kind": "bbox", "target": {"whole": "Balcony Balustrade"}, "min": [bx0, None, 0.0],
             "max": [bx1, by1, flat.BALUSTRADE], "tolerance": 0.1, "label": "the balustrade round the balcony, 1.1 m high"},
            _ray((flat.BALCONY_DOOR[0] + 0.3, 6.4, 1.2), (0, 1, 0), {"name": "Balcony Door"},
                 "the balcony door in the north wall at x = -7.5", "y", (hy - 0.15, hy + 0.15), distance=1.0)]
    return out + [check for room in ROOM_ORDER for check in _inside(room)]


def checks():
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        *shell_checks(), *[check for piece in PIECES for check in piece_checks(piece)],
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.4, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [28.0, 32.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


def _what(rooms):
    """A grade class's description: every piece of ``rooms`` as the brief describes it."""
    return " ".join("%s: %s." % (room, "; ".join(phrase(piece) for piece in PIECES if piece["room"] == room))
                    for room in rooms)


GRADE_CLASSES = {
    "Living room and balcony": {"views": ["closeup_living", "hero"], "what": _what(("Living Room", "Balcony"))},
    "Dining room": {"views": ["closeup_dining_kitchen", "top"], "what": _what(("Dining Room",))},
    "Kitchen": {"views": ["closeup_dining_kitchen", "hero"], "what": _what(("Kitchen",))},
    "Pantry and laundry": {"views": ["closeup_service", "top"], "what": _what(("Pantry", "Laundry Room"))},
    "Master suite": {"views": ["closeup_master", "top"], "what": _what(("Master Bedroom", "Walk-in Wardrobe", "En-suite"))},
    "Kids' room": {"views": ["closeup_children", "hero"], "what": _what(("Kids Room",))},
    "Guest bedroom": {"views": ["closeup_children", "top"], "what": _what(("Guest Bedroom",))},
    "Bathroom and study": {"views": ["closeup_study", "hero"], "what": _what(("Bathroom", "Study"))},
    "Entrance hall": {"views": ["closeup_hall", "top"], "what": _what(("Entrance Hall",))},
    "Apartment": {"views": ["top", "hero"],
                  "what": "The parquet floor, plaster walls, open doorways, framed windows with sills, the navy front "
                          "door, the balcony with its glass balustrade and sliding door, and every room densely "
                          "furnished with distinct, detailed pieces: no two chairs, tables or lamps alike.",
                  "layout": "A long hall along the middle; living room, dining room, kitchen, pantry and laundry north "
                            "of it from west to east, the balcony beyond the living room; master bedroom with its "
                            "wardrobe and en-suite, kids' room, guest bedroom, bathroom and study south of it; each "
                            "piece where the brief puts it."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The apartment", "references": ["hero", "top"], "prompt": PROMPT, "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    flat.build_flat(bpy, PIECES)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Daylight", 40.0, -45.0, (1.0, 0.95, 0.88), 3.0, angle_deg=4.0)
    scene.world = kit.colour_world(bpy, "Daylight Sky", (0.55, 0.62, 0.7), strength=0.8)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
