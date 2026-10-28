# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stand-in's frigate, its rooms below decks and the whale (``long_kit_tempest``), from the brief's numbers.

Placeholder shapes at the stated places and heights: a lofted hull, decks, masts with their yards as parts of the
masts, sails under their yards, rigging lines as thin spars, the gun deck, cabin and hold inside the hull, and the
whale (a stretched sphere with flukes and eyes) under the sea.
"""

import math

import long_kit_tempest_values as values
from long_kit_tempest import beam, box, combine, cyl, loft

V = values.V


def _hull_top(x):
    middle = (V["stern_x"] + V["stem_x"]) / 2
    half = (V["stem_x"] - V["stern_x"]) / 2
    return V["rail_z"] + V["sheer"] * ((x - middle) / half) ** 2


def build_ship(b):
    y = V["axis_y"]
    sections = []
    steps = 30
    for i in range(steps + 1):
        x = V["stern_x"] + (V["stem_x"] - V["stern_x"]) * i / steps
        h = max(values.hull_half(x), 0.0015)
        top, keel = _hull_top(x), V["keel_z"]
        ring = [(-0.3 * h, keel), (0.3 * h, keel), (0.85 * h, keel + 0.01), (h, keel + 0.015), (h, top), (-h, top),
                (-h, keel + 0.015), (-0.85 * h, keel + 0.01)]
        sections.append([(x, y + py, pz) for py, pz in ring])
    b.add("Asterion Hull", loft(sections), "hull")
    half = V["beam"] / 2
    b.add("Asterion Main Deck", box((V["qd_x"], y - half + 0.003, V["deck_z"] - 0.0015), (0.06, y + half - 0.003, V["deck_z"])),
          "teak")
    b.add("Asterion Quarterdeck", box((V["stern_x"] + 0.001, y - 0.0195, V["qd_z"] - 0.0015), (V["qd_x"], y + 0.0195, V["qd_z"])),
          "teak")
    guns = []
    for x in values.PORTS:
        for s in (-1, 1):
            side = y + s * values.hull_half(x)
            guns.append(beam((x, side - s * 0.0036, V["port_z"]), (x, side + s * V["gun_out"], V["port_z"]), 0.0015))
    b.add("Asterion Cannons", combine(guns), "iron")
    for mast, x, top in values.MASTS:
        name = "Asterion %s Mast" % mast
        base = V["deck_z"] if x > V["qd_x"] else V["qd_z"]
        middle = (base + top) / 2
        parts = [cyl(0.0019, (x, y, (base + middle) / 2), middle - base, segments=10),
                 cyl(0.0012, (x, y, (middle + top) / 2), top - middle, segments=10),
                 cyl(V["nest_dia"] / 2, (x, y, top - V["nest_down"]), 0.004, segments=16)]
        b.add(name, combine(parts), "oak")
        reach = math.sin(math.radians(V["brace"])) / 2
        yards = []
        for sail, sail_mast, sx, yard, width, depth in values.SAILS:
            if sail_mast != mast:
                continue
            dx, dy = width * reach, width * math.cos(math.radians(V["brace"])) / 2
            yards.append(beam((x + 0.003 + dx, y - dy, yard + 0.001), (x + 0.003 - dx, y + dy, yard + 0.001), 0.0006))
            b.add(sail, box((x + 0.004, y - dy, yard - depth), (x + 0.0055, y + dy, yard)), "sail")
        b.add(name + " Yards", combine(yards), "oak")
    b.add("Asterion Jibs", combine([box((0.03, y - 0.0007, 0.965), (0.095, y + 0.0007, 1.03)),
                                    box((0.025, y + 0.002, 0.97), (0.08, y + 0.0034, 1.05))]), "sail")
    b.add("Asterion Bowsprit", beam((V["sprit_x0"] + 0.0015, y, V["sprit_z0"] + 0.0015),
                                    (V["sprit_x1"] - 0.0015, y, V["sprit_z1"] - 0.0015), 0.0012), "oak")
    lines = []
    tops = {mast: (x, top) for mast, x, top in values.MASTS}
    order = ("Mizzen", "Main", "Fore")
    for index, mast in enumerate(order):
        x, top = tops[mast]
        head = top - 0.0054
        rail = _hull_top(x)
        for s in (-1, 1):
            for k in range(4):
                fx = x - 0.029 + k * 0.0054
                lines.append(beam((fx, y + s * (values.hull_half(fx) - 0.0004), rail), (x - 0.0027, y + s * 0.0018, head), 0.0003))
            aft = max(V["stern_x"] + 0.002, x - 0.039)
            lines.append(beam((x, y + s * 0.001, top - 0.0054), (aft, y + s * (values.hull_half(aft) - 0.0004),
                                                                  _hull_top(aft)), 0.0003))
        ahead = (tops[order[index + 1]][0], y, V["deck_z"] + 0.0006) if index < 2 else \
            (V["sprit_x1"] - 0.0004, y, V["sprit_z1"])
        lines.append(beam((x, y, top - 0.0042), ahead, 0.0003))
    b.add("Asterion Standing Rigging", combine(lines), "rope")
    lifts = [beam((x, y, top - 0.0012), (x + 0.003, y + s * 0.02, yard), 0.00025)
             for _sail, mast, x, yard, _w, _d in values.SAILS for s in (-1, 1) for x, top in [tops[mast]]]
    b.add("Asterion Running Rigging", combine(lifts), "rope")
    rails = [box((x - 0.0039, y + s * 0.0192 - 0.0018, 0.955), (x + 0.0039, y + s * 0.0192 + 0.0018, 0.957))
             for x in (-0.063, -0.0126, 0.027) for s in (-1, 1)]
    b.add("Asterion Pin Rails", combine(rails), "oak")
    b.add("Asterion Line Coils", cyl(0.0035, (0.0054, y - 0.012, V["deck_z"] + 0.0006), 0.0012), "rope")
    b.add("Asterion Companion Hatch", box((V["hatch_x"] - 0.0105, y - 0.0085, V["deck_z"]), (V["hatch_x"] + 0.0105, y + 0.0085,
                                                                                            V["deck_z"] + 0.0015)), "brass")
    b.add("Asterion Capstan", cyl(0.0055, (V["capstan_x"], y, V["deck_z"] + 0.005), 0.01), "oak")
    b.add("Asterion Water Barrels", combine([cyl(0.00275, (x, y + 0.0114, V["deck_z"] + 0.003), 0.006) for x in (-0.062, 0.0096)]),
          "oak")
    main_x, main_top = tops["Main"]
    b.add("Asterion Pennant", box((main_x, y - 0.0003, main_top - 0.006), (main_x + 0.018, y + 0.0003, main_top - 0.001)),
          "linen")
    hx, hz, rim = V["helm_x"], V["helm_z"], V["helm_reach"] / 2
    b.add("Asterion Helm", combine([cyl(rim, (hx, y, hz), 0.0012, "X", 16), cyl(0.0008, (hx + 0.001, y, hz), 0.004, "X", 8),
                                    box((hx + 0.002, y - 0.0012, V["qd_z"]), (hx + 0.0044, y + 0.0012, hz - 0.002))]), "oak")


def build_below(b):
    y, gd, hold = V["axis_y"], V["gd_z"], V["hold_z"]
    b.add("Asterion Frames", combine([box((x - 0.0004, y - 0.025, V["keel_z"] + 0.002), (x + 0.0004, y + 0.025, V["deck_z"] - 0.002))
                                      for x in (-0.07, -0.04, -0.01, 0.02)] +
                                     [box((-0.1, y - 0.0018, hold), (0.05, y + 0.0018, hold + 0.0036))]), "oak")
    b.add("Asterion Hold Floor", box((-0.1, y - V["hold_w"] / 2, hold - 0.001), (0.05, y + V["hold_w"] / 2, hold)), "oak")
    b.add("Asterion Gun Deck Floor", box((-0.116, y - V["gd_w"] / 2, gd - 0.001), (0.062, y + V["gd_w"] / 2, gd)), "teak")
    guns = []
    for x in values.GUN_DECK_GUNS:
        for s in (-1, 1):
            guns.append(box((x - 0.0045, y + s * 0.012 - 0.002, gd), (x + 0.0045, y + s * 0.012 + 0.002, gd + 0.004)))
            guns.append(beam((x, y + s * 0.008, gd + 0.006), (x, y + s * 0.022, gd + 0.008), 0.0016))
    b.add("Asterion Gun Deck Guns", combine(guns), "iron")
    b.add("Asterion Hammocks", combine([box((x - 0.013, y + s * 0.012 - 0.0033, 0.935), (x + 0.013, y + s * 0.012 + 0.0033, 0.938))
                                        for x in (-0.052, -0.018, 0.016) for s in (-1, 1)]), "canvas")
    mx = V["mess_x"]
    b.add("Asterion Mess Table", combine([box((mx - 0.007, y - 0.004, V["mess_top"] - 0.0015), (mx + 0.007, y + 0.004, V["mess_top"])),
                                          box((mx - 0.0006, y - 0.0006, gd), (mx + 0.0006, y + 0.0006, V["mess_top"] - 0.0015))]),
          "oak")
    b.add("Asterion Companion Ladder", combine([box((V["hatch_x"] - 0.004 + i * 0.0036, y - 0.0065, V["deck_z"] - 0.005 - i * 0.005),
                                                    (V["hatch_x"] - 0.001 + i * 0.0036, y + 0.0065, V["deck_z"] - 0.004 - i * 0.005))
                                                for i in range(7)]), "oak")
    b.add("Asterion Gun Deck Lanterns", combine([cyl(0.0018, (x, y + 0.002, V["deck_z"] - 0.006), 0.005, segments=6)
                                                 for x in (-0.047, 0.026)]), "brass")
    bx = V["bulkhead_x"]
    b.add("Asterion Cabin Bulkhead", box((bx - 0.0007, y - 0.022, gd), (bx + 0.0007, y + 0.022, V["deck_z"] - 0.001)), "oak")
    tx, tw, td, top = V["table_x"], V["table_w"], V["table_d"], V["table_top"]
    b.add("Asterion Chart Table", combine([box((tx - tw / 2, y - td / 2, top - 0.0012), (tx + tw / 2, y + td / 2, top))] +
                                          [box((tx + sx * 0.0055 - 0.0004, y + sy * 0.0072 - 0.0004, gd),
                                               (tx + sx * 0.0055 + 0.0004, y + sy * 0.0072 + 0.0004, top - 0.0012))
                                           for sx in (-1, 1) for sy in (-1, 1)]), "oak")
    b.add("Asterion Cabin Chart", box((tx - 0.0055, y - 0.008, top), (tx + 0.0055, y + 0.008, top + 0.0002)), "parchment")
    b.add("Asterion Dip Pen", box((tx + 0.002, y - 0.006, top + 0.0002), (tx + 0.0038, y - 0.0057, top + 0.0005)), "oak")
    b.add("Asterion Chronometer", combine([box((tx - 0.0054, y + 0.0036, top + 0.0002), (tx - 0.0026, y + 0.0064, top + 0.0015)),
                                           cyl(0.0009, (tx - 0.004, y + 0.005, top + 0.0016), 0.0003, segments=12)]), "walnut")
    b.add("Asterion Cot", box((-0.115, y + 0.0095, gd + 0.005), (-0.091, y + 0.018, gd + 0.008)), "canvas")
    b.add("Asterion Sea Chest", box((-0.0915, y - 0.0185, gd), (-0.0825, y - 0.0125, gd + 0.016)), "walnut")
    b.add("Asterion Cabin Chair", box((-0.1, y - 0.004, gd), (-0.093, y + 0.003, gd + 0.01)), "oak")
    b.add("Asterion Cabin Shelf", box((-0.1, y - 0.0175, gd + 0.024), (-0.085, y - 0.0133, gd + 0.025)), "oak")
    b.add("Asterion Ships Clock", cyl(0.0026, (bx - 0.001, y - 0.0135, gd + 0.028), 0.0004, "X"), "brass")
    b.add("Asterion Cabin Lantern", cyl(0.0015, (-0.11, y - 0.0096, top + 0.002), 0.004, segments=6), "brass")
    casks = [cyl(0.003, (-0.066 + i * 0.0153, y + s * 0.0065, hold + 0.004), 0.008) for i in range(6) for s in (-1, 1)]
    b.add("Asterion Hold Casks", combine(casks), "oak")
    b.add("Asterion Hold Crates", combine([box((x - 0.003, y + s * 0.0065 - 0.003, hold), (x + 0.003, y + s * 0.0065 + 0.003,
                                                                                        hold + 0.0047))
                                           for x in (-0.0588, -0.0282, 0.0024) for s in (-1, 1)]), "teak")
    b.add("Asterion Ballast", combine([box((-0.09 + i * 0.008, y - 0.002, V["keel_z"] + 0.004), (-0.087 + i * 0.008, y + 0.002,
                                                                                                V["keel_z"] + 0.0055))
                                       for i in range(19)]), "iron")
    b.add("Asterion Hold Rope", cyl(0.0018, (0.027, y + 0.009, hold + 0.0005), 0.001), "rope")
    b.add("Asterion Hold Stanchions", combine([cyl(0.0011, (x, y - 0.0018, (hold + gd) / 2), gd - hold - 0.0002) for x in (-0.066, 0.003)]),
          "oak")
    b.add("Asterion Hold Lamp", cyl(0.0012, (0.0042, y + 0.0006, hold + 0.003), 0.003, segments=6), "brass")


def build_whale(b):
    y, z = V["wh_y"], V["wh_z"]
    body_x0 = V["wh_tail"] + 0.018
    centre = ((V["wh_nose"] + body_x0) / 2, y, z)
    a = (V["wh_nose"] - body_x0) / 2
    from scenario_build_kit import uv_sphere_geometry
    b.add("Blue Whale", uv_sphere_geometry(1.0, 24, 12, centre, scale=(a, V["wh_w"] / 2, V["wh_h"] / 2)), "whale", smooth=True)
    tail = V["wh_tail"]
    b.add("Blue Whale Flukes", box((tail, y - V["wh_span"] / 2, z - 0.0008), (tail + 0.02, y + V["wh_span"] / 2, z + 0.0008)),
          "whale")
    dx = (V["eye_x"] - centre[0]) / a
    side = V["wh_w"] / 2 * math.sqrt(max(0.0, 1 - dx * dx))
    b.add("Blue Whale Eyes", combine([uv_sphere_geometry(0.0006, 8, 4, (V["eye_x"], y + s * side, V["eye_z"])) for s in (-1, 1)]),
          "eye")
