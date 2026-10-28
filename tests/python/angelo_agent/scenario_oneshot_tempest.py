# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-tempest``: a ship in a bottle on a period desk in a dark study (long candidate 6).

LONG-TEST-SELECTION candidate 6, from the maintainer's proposal of a much richer scene than candidates 1-5: a Three.js
showpiece (an 11.6 h GPT 6.1 Sol Max project) rebuilt as a still Blender scene at that level of detail. A study
(walls, wainscot, window, curtains, a bookcase of atlases), a pedestal desk with a chart, dividers, a compass, coins
and two lanterns, a carved walnut stand with cradles and an engraved plaque, and the bottle: cork, rope, knot, a
hanging compass and lantern; inside it a stormy sea, volumetric storm clouds, a moon, the frigate Asterion (planked
hull, gun ports and cannons, three masts with nine sails, two jibs, full standing and running rigging, deck fittings,
a helm) with a gun deck, a captain's cabin (chart table, dip pen, 1847 chronometer) and a cargo hold, and a blue
whale beneath the surface. The source's real-time work (FFT ocean, buoyancy, cloth, audio, frame pacing) is left
out: it is a still. The brief says nothing about how to build or split the work.

Every number comes from ``long_kit_tempest_values.V`` (brief, stand-in and checks share it). The checks measure what a
person sees, never how it was built: rays find a piece by its own name and pass through everything else, so the
glass, the clouds and the sea never block a ray aimed at what is inside or under them; boxes give sizes, places and
containment (below decks inside the hull, the whale under the surface); colour families, a glowing moon and a volume
for the clouds. Probes are aimed with margin by one rule for every probe (``probe_margin``: 5 cm, or a fifth of the
target's smallest stated extent across the ray when that is smaller), proved on the stand-in by
``long_probe_scaled``. Box tolerances follow one rule too (``tolerance``).
"""

import math

import long_kit_tempest as stand_in
import long_kit_tempest_brief as brief
import long_kit_tempest_values as values
import oneshot_kit

V = values.V
ID = "oneshot_tempest"
TITLE = "A ship in a bottle on a period desk"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "tempest-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
# The calibration pilots' cap (BENCH-PREREG Amendment I7); paired trials set theirs with --task-timeout (I3).
LIMITS = {"wall_s": 7200.0, "job_wait_s": 0.0, "cost_usd": 80.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

HERO = {"location": (V["cam_x"], V["cam_y"], V["cam_z"]), "target": (V["look_x"], V["look_y"], V["look_z"]),
        "lens": float(V["lens"])}


def _look(location, target, label, lens=35.0):
    return {"location": location, "target": target, "lens": lens, "role": "closeup", "label": "Close-up: " + label}


CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "front": {"location": (0.02, -2.0, 0.95), "target": (0.02, 1.35, 0.95), "ortho_scale": 0.8, "role": "reference",
              "label": "Front elevation of the bottle (orthographic, looking +Y)"},
    "top": {"location": (0.0, 1.35, 3.0), "target": (0.0, 1.35, 0.9), "ortho_scale": 1.0, "role": "reference",
            "label": "The stand and bottle from above (orthographic, +Y up)"},
    "side": {"location": (2.0, 1.35, 0.95), "target": (0.0, 1.35, 0.95), "ortho_scale": 0.4, "role": "reference",
             "label": "End elevation from the neck (orthographic, looking -X)"},
    "room": _look((0.0, -2.4, 1.6), (0.0, 1.5, 1.2), "the study from its open side", lens=24.0),
    "closeup_ship": _look((0.12, 1.05, 1.06), (-0.02, 1.35, 0.99), "the Asterion through the glass"),
    "closeup_rigging": _look((-0.15, 1.12, 1.1), (-0.03, 1.35, 1.03), "masts, sails and rigging", lens=40.0),
    "closeup_neck": _look((0.55, 1.0, 0.98), (0.34, 1.3, 0.92), "cork, rope, knot, compass and lantern"),
    "closeup_stand": _look((0.05, 0.9, 0.86), (-0.02, 1.25, 0.8), "the plinth, cradles and plaque"),
    "closeup_whale": _look((0.0, 1.0, 0.86), (0.01, 1.27, 0.862), "the whale under the surface", lens=50.0),
    "closeup_desk": _look((0.3, 0.4, 1.4), (-0.2, 1.3, 0.76), "the desk and its instruments", lens=30.0),
}

SUBJECT = ({"prefix": ""},)
PROMPT = brief.text()
MARGINS = {}        # probe label -> its sideways margin (``probe_margin``), set where the probe is made.
USED = set()        # V keys the checks measure (the test proves the brief states each).


def v(key):
    USED.add(key)
    return V[key]


def probe_margin(check):
    """The sideways move a probe must survive: 5 cm, or a fifth of the target's smallest stated extent across it."""
    return MARGINS[check["label"]]


def tolerance(largest, smallest=None):
    """A box's tolerance: 5% of the piece's largest stated size, 4 mm to 10 cm, at most 0.3 x its smallest."""
    tol = min(0.1, max(0.004, 0.05 * largest))
    return round(max(0.004, min(tol, 0.3 * smallest)) if smallest else tol, 4)


def _ray(origin, direction, target, label, across, axis=None, span=None, distance=1.5):
    check = {"kind": "probe", "origin": [round(c, 4) for c in origin], "direction": list(direction),
             "target": target, "distance": distance, "label": label}
    if span is None:
        check["expect"] = "miss"
    else:
        check.update(axis=axis, range=[round(c, 4) for c in span])
    MARGINS[label] = round(min(0.05, 0.2 * across), 4)
    return check


def _down(x, y, name, label, low, high, across):
    return _ray((x, y, 1.6), (0, 0, -1), _own(name), label, across, "z", (low, high), distance=1.2)


def _own(name):
    return {"prefix": name, "word": True}


def _box(name, label, low=(None,) * 3, high=(None,) * 3, tol=0.01, size=None):
    check = {"kind": "bbox", "target": _own(name), "min": list(low), "max": list(high), "tolerance": tol, "label": label}
    if size:
        check["size"] = size
    return check


def _inside(name, axis, low, high, label, slack=0.0):
    """Everything named ``name ...`` lies between ``low`` and ``high`` along ``axis`` (both of its box's faces)."""
    middle = [None, None, None]
    middle["xyz".index(axis)] = round((low + high) / 2, 4)
    return {"kind": "bbox", "target": _own(name), "min": list(middle), "max": list(middle),
            "tolerance": round((high - low) / 2 + slack, 4), "label": label}


def _colour(name, families, word):
    return {"kind": "material", "target": _own(name), "color": list(families), "label": "%s: %s in colour" % (name, word)}


def study_checks():
    hx, hy = v("floor_w") / 2, v("floor_d") / 2
    window_z = (v("win_sill") + v("win_head")) / 2 + 0.1
    case = (v("case_x") - v("case_w") / 2, v("case_y") - v("case_d") / 2, v("case_x") + v("case_w") / 2,
            v("case_y") + v("case_d") / 2)
    desk = (v("desk_x") - v("desk_w") / 2, v("desk_y") - v("desk_d") / 2, v("desk_x") + v("desk_w") / 2,
            v("desk_y") + v("desk_d") / 2)
    top = v("desk_top")
    return [
        {"kind": "bbox", "target": {"whole": "Study Floor"}, "size": [[4.9, 5.1], [3.9, 4.1], None],
         "max": [None, None, 0.0], "tolerance": 0.05, "label": "a 5 x 4 m study floor, its top at z = 0"},
        _box("Study Wall", "walls on the north, west and east edges, 3 m high", (-hx, None, 0.0),
             (hx, hy, v("wall_h")), 0.15),
        _colour("Study Wall", ("blue", "grey"), "blue-grey"),
        _box("Study Wainscot", "the wainscot 1 m high", high=(None, None, v("wainscot_h")), tol=0.05),
        _ray((v("win_x") + 0.15, 1.4, window_z), (0, 1, 0), _own("Study Wall"),
             "the north wall open at the window, x = %g" % V["win_x"], v("win_w"), distance=1.0),
        _ray((v("win_x") + 0.15, 1.4, window_z), (0, 1, 0), _own("Study Window"), "a window in the north wall",
             v("win_w"), "y", (hy - 0.15, hy + 0.15), distance=1.0),
        _inside("Study Curtains", "x", v("win_x") - v("win_w") / 2 - v("curtain_w") - 0.1,
                V["win_x"] + V["win_w"] / 2 + V["curtain_w"] + 0.1, "curtains either side of the window"),
        _box("Study Bookcase", "the bookcase on the north wall, 1.7 x 0.4 x 2.5 m", (case[0], case[1], 0.0),
             (case[2], case[3], v("case_h")), tolerance(2.5, 0.4)),
        _inside("Study Atlases", "x", case[0], case[2], "the atlases in the bookcase (x)", 0.05),
        _inside("Study Atlases", "y", case[1], case[3], "the atlases in the bookcase (y)", 0.05),
        _box("Study Desk", "the pedestal desk, 1.8 x 0.9 m, top 0.76 m", (desk[0], desk[1], 0.0),
             (desk[2], desk[3], top), tolerance(1.8, 0.76)),
        _colour("Study Desk", ("warm",), "oak"),
        _colour("Study Curtains", ("warm",), "brown"),
        _colour("Study Lantern", ("warm",), "brass"),
        _down(v("chart_x") + 0.05, v("chart_y") - 0.03, "Study Chart", "the chart lying on the desk",
              top - 0.005, top + 0.025, v("chart_d")),
        _down(v("compass_x") + 0.018, v("compass_y") + 0.012, "Study Brass Compass", "the brass compass on the desk",
              top - 0.002, top + 0.02, v("compass_dia")),
        _inside("Study Coins", "x", v("coins_x0"), v("coins_x1"), "the coins scattered on the desk (x)", 0.02),
        _inside("Study Coins", "y", v("coins_y0"), v("coins_y1"), "the coins scattered on the desk (y)", 0.02),
        _inside("Study Coins", "z", top, top + 0.01, "the coins lying on the desk", 0.004),
        _box("Study Lantern", "the desk lantern 0.28 m tall at (0.55, 1.62)",
             (v("lamp_x") - v("lamp_foot") / 2, v("lamp_y") - v("lamp_foot") / 2, top),
             (v("lamp_x") + v("lamp_foot") / 2, v("lamp_y") + v("lamp_foot") / 2, top + v("lamp_h")), 0.02),
        _box("Study Candle Lantern", "the candle lantern 0.16 m tall", high=(None, None, top + v("candle_h")),
             tol=0.02),
        _inside("Study Candle Lantern", "x", v("candle_x") - 0.1, v("candle_x") + 0.1, "the candle lantern at x = -0.75"),
    ]


def stand_checks():
    y, z = v("axis_y"), v("axis_z")
    out = [_box("Bottle Plinth", "the walnut plinth 0.56 x 0.21 m, top 0.8 m",
                (-v("plinth_w") / 2, y - v("plinth_d") / 2, v("desk_top")),
                (v("plinth_w") / 2, y + v("plinth_d") / 2, v("plinth_top")), tolerance(0.56, 0.04)),
           _colour("Bottle Plinth", ("warm",), "walnut")]
    for name, key in (("Bottle Cradle West", "cradle_wx"), ("Bottle Cradle East", "cradle_ex")):
        x = v(key)
        out.append(_box(name, "%s: 0.19 m across, horns and finials to 0.872 m" % name,
                        (None, y - v("cradle_d") / 2, v("plinth_top")),
                        (None, y + v("cradle_d") / 2, v("cradle_horn") + v("finial")), tolerance(0.19, 0.025)))
        out.append(_inside(name, "x", x - v("cradle_t") / 2, x + V["cradle_t"] / 2, "%s: at x = %g" % (name, x), 0.012))
    r = v("body_dia") / 2
    out += [
        _box("Bottle Glass", "the bottle 0.65 m long, 0.292 m across, axis at z = 0.975",
             (v("base_x"), y - r, z - r), (v("lip_x"), y + r, z + r), tolerance(0.65, 0.292)),
        {"kind": "surface", "target": _own("Bottle Glass"), "transmission_min": 0.5, "roughness": [0.0, 0.2],
         "label": "Bottle Glass: clear, smooth glass"},
        _ray((-0.02, 0.9, z), (0, 1, 0), _own("Bottle Glass"), "the bottle's body 0.292 m across", r * 2, "y",
             (y - r - 0.006, y - r + 0.012)),
        _ray((0.305, 1.1, z), (0, 1, 0), _own("Bottle Glass"), "the bottle's neck 0.104 m across", v("neck_dia"), "y",
             (y - V["neck_dia"] / 2 - 0.008, y - V["neck_dia"] / 2 + 0.01)),
        _box("Bottle Cork", "the cork in the neck, x = 0.33 to 0.385", (v("cork_x0"), y - V["cork_out"] / 2,
             z - V["cork_out"] / 2), (v("cork_x1"), y + v("cork_out") / 2, z + V["cork_out"] / 2), tolerance(0.102, 0.055)),
        _colour("Bottle Cork", ("warm",), "cork"),
        _colour("Bottle Neck Rope", ("warm",), "hemp"),
        _colour("Bottle Compass", ("warm",), "brass"),
        _box("Bottle Neck Rope", "the rope round the neck, x = 0.3 to 0.33", (v("rope_x0"), y - 0.055, z - 0.055),
             (v("rope_x1"), y + 0.055, z + 0.055), tolerance(0.11, 0.03)),
        _inside("Bottle Knot", "x", v("knot_x") - 0.015, V["knot_x"] + 0.015, "the knot on the neck (x)"),
        _inside("Bottle Knot", "z", v("knot_z") - 0.015, V["knot_z"] + 0.015, "the knot on the neck (z)"),
        _box("Bottle Compass", "the hanging compass 0.05 m across",
             (v("hc_x") - v("hc_dia") / 2, None, v("hc_z") - V["hc_dia"] / 2), (V["hc_x"] + V["hc_dia"] / 2, None, None),
             tolerance(0.05)),
        _ray((V["hc_x"] + 0.008, 1.1, V["hc_z"] + 0.006), (0, 1, 0), _own("Bottle Compass"),
             "the hanging compass's face toward -Y", V["hc_dia"], "y", (v("hc_y") - 0.01, V["hc_y"] + 0.007)),
        _box("Bottle Lantern", "the hanging lantern's foot at z = 0.859", (None, None, v("hl_z") - v("hl_h") / 2),
             tol=tolerance(0.05)),
        _inside("Bottle Lantern", "x", v("hl_x") - 0.03, V["hl_x"] + 0.03, "the hanging lantern at x = 0.307"),
        _ray((v("plaque_x") + 0.03, 1.0, v("plaque_z") + 0.004), (0, 1, 0), _own("Bottle Plaque"),
             "the plaque on the plinth's front", v("plaque_h"), "y", (y - 0.115, y - 0.094)),
        _box("Bottle Plaque", "the plaque 0.146 x 0.026 m", (V["plaque_x"] - v("plaque_w") / 2, None,
             V["plaque_z"] - V["plaque_h"] / 2), (V["plaque_x"] + V["plaque_w"] / 2, None, V["plaque_z"] + V["plaque_h"] / 2),
             0.006),
    ]
    return out


def inside_checks():
    y, sea = v("axis_y"), v("sea_z")
    swell = v("swell")
    out = [_down(x, y - 0.07, "Bottle Sea", "the sea's surface at x = %g" % x, sea - swell - 0.003, sea + swell + 0.003,
                 0.26) for x in (-0.16, -0.02, 0.1)]
    inner = V["body_dia"] / 2 - V["glass_wall"]
    out += [_box("Bottle Sea", "the sea inside the glass, up to its surface",
                 (v("base_x"), y - 0.13, v("axis_z") - inner), (0.27, y + 0.13, sea + V["swell"]), tolerance(0.57, 0.1)),
            _colour("Bottle Sea", ("blue", "green"), "blue-green"),
            {"kind": "volume", "target": _own("Bottle Storm Clouds"), "label": "volumetric storm clouds"},
            _box("Bottle Moon", "the moon 1.4 cm across at (-0.18, 1.42, 1.05)",
                 (v("moon_x"), v("moon_y"), v("moon_z")), (V["moon_x"], V["moon_y"], V["moon_z"]),
                 v("moon_dia") / 2 + 0.006),
            {"kind": "surface", "target": _own("Bottle Moon"), "emission_min": 0.3, "label": "Bottle Moon: glowing"}]
    return out


def ship_checks():
    y, half = v("axis_y"), v("beam") / 2
    out = [_box("Asterion Hull", "the hull, x = -0.12 to 0.066, 0.056 m beam, keel 0.89 m, rail 0.959 m",
                (v("stern_x"), y - half, v("keel_z")), (v("stem_x"), y + half, v("rail_z") + v("sheer")),
                tolerance(0.186, 0.056)),
           _colour("Asterion Hull", ("warm",), "oak"),
           _ray((-0.045, y - 0.1, 0.912), (0, 1, 0), _own("Asterion Hull"), "the hull's -Y side at its beam",
                V["rail_z"] - V["keel_z"], "y", (y - half - 0.006, y - half + 0.014), distance=0.3),
           _ray((-0.045, y + 0.1, 0.912), (0, -1, 0), _own("Asterion Hull"), "the hull's +Y side at its beam",
                V["rail_z"] - V["keel_z"], "y", (y + half - 0.014, y + half + 0.006), distance=0.3),
           _box("Asterion Main Deck", "the main deck at z = 0.948, from x = -0.077 forward", (v("qd_x"), None, None),
                (None, None, v("deck_z")), 0.006),
           _box("Asterion Quarterdeck", "the quarterdeck at z = 0.962, aft of x = -0.077", (None, None, None),
                (V["qd_x"], None, v("qd_z")), 0.005),
           _box("Asterion Cannons", "twelve cannons run out 6 mm through the ports at z = 0.934",
                (v("port_x0") - 0.002, y - half - v("gun_out"), v("port_z") - 0.002),
                (values.PORTS[-1] + 0.002, y + half + V["gun_out"], V["port_z"] + 0.002), 0.004)]
    for mast, x, top in values.MASTS:
        name = "Asterion %s Mast" % mast
        out.append(_box(name, "%s: its top at z = %g" % (name, top), high=(None, None, top), tol=0.004))
        widest = max(width for _n, sail_mast, _x, _y, width, _d in values.SAILS if sail_mast == mast)
        reach = max(v("nest_dia") / 2, widest / 2 * math.sin(math.radians(v("brace"))) + 0.003)
        out.append(_inside(name, "x", x, x, "%s: at x = %g" % (name, x), reach + 0.004))
    for name, _mast, _x, yard, width, depth in values.SAILS:
        out.append(_box(name, "%s: under its yard at %g, %g x %g m" % (name, yard, width, depth),
                        (None, None, yard - depth), (None, None, yard), 0.005,
                        size=[None, [round(0.75 * width, 4), round(1.15 * width, 4)], None]))
    out += [
        _colour("Asterion Main Course", ("warm", "grey"), "linen"),
        _colour("Asterion Fore Topsail", ("warm", "grey"), "linen"),
        _colour("Asterion Cannons", ("grey",), "gunmetal grey"),
        _colour("Asterion Main Deck", ("warm",), "teak"),
        _box("Asterion Jibs", "the jibs reaching out to the bowsprit", high=(v("sprit_x1"), None, None), tol=0.015),
        _box("Asterion Bowsprit", "the bowsprit from (0.036, 0.953) to (0.1, 0.976)", (v("sprit_x0"), None, v("sprit_z0")),
             (V["sprit_x1"], None, v("sprit_z1")), 0.005),
        _box("Asterion Standing Rigging", "standing rigging from the stern to the bowsprit's end, up to the tops",
             (V["stern_x"], y - half, None), (V["sprit_x1"], y + half, V["main_top"] - 0.006), 0.01),
        _box("Asterion Helm", "the helm wheel 1.6 cm across its handles, centred 0.97 m up",
             (None, y - v("helm_reach") / 2, None), (None, y + V["helm_reach"] / 2, v("helm_z") + V["helm_reach"] / 2),
             0.004),
        _inside("Asterion Helm", "x", v("helm_x"), V["helm_x"], "the helm at x = -0.097", 0.006),
        _inside("Asterion Capstan", "x", v("capstan_x"), V["capstan_x"], "the capstan at x = 0.034", 0.01),
        _inside("Asterion Companion Hatch", "x", v("hatch_x"), V["hatch_x"], "the hatch at x = -0.007", 0.016),
        _inside("Asterion Pin Rails", "x", v("pin_x0"), v("pin_x2"), "the pin rails from x = -0.063 to 0.027", 0.006),
        _inside("Asterion Pin Rails", "y", y - v("pin_off"), y + V["pin_off"], "the pin rails either side, on the "
                "bulwarks", 0.004),
        _inside("Asterion Pin Rails", "z", v("deck_z"), V["rail_z"] + V["sheer"], "the pin rails above the deck", 0.004),
        _inside("Asterion Water Barrels", "x", v("barrel_x0"), v("barrel_x1"), "the water barrels at x = -0.062 and "
                "0.0096", v("barrel_dia") / 2 + 0.002),
        _box("Asterion Water Barrels", "the water barrels standing on the main deck, 6 mm tall",
             (None, None, V["deck_z"]), (None, None, V["deck_z"] + v("barrel_h")), 0.002),
        _inside("Asterion Pennant", "z", V["main_top"] - 0.02, V["main_top"] + 0.004, "the pennant at the main top"),
        _box("Asterion Pennant", "the pennant 1.8 cm long", tol=0.005,
             size=[[round(v("pennant_l") - 0.006, 4), round(V["pennant_l"] + 0.006, 4)], None, None]),
    ]
    return out


def below_checks():
    y, half = v("axis_y"), V["beam"] / 2
    table = v("table_x")
    out = [
        _box("Asterion Gun Deck Floor", "the gun deck at z = 0.907, 0.039 m wide", high=(None, None, v("gd_z")), tol=0.004,
             size=[None, [round(v("gd_w") - 0.008, 4), round(V["gd_w"] + 0.008, 4)], None]),
        _inside("Asterion Gun Deck Guns", "z", V["gd_z"], V["deck_z"], "the gun deck's guns between its floor and the "
                "main deck", 0.003),
        _inside("Asterion Gun Deck Guns", "y", y - half, y + half, "the gun deck's guns inside the hull", 0.002),
        _inside("Asterion Hammocks", "z", 0.92, V["deck_z"], "the hammocks slung under the main deck", 0.003),
        _box("Asterion Mess Table", "the mess table, top 0.917 m up", high=(None, None, v("mess_top")), tol=0.003),
        _inside("Asterion Mess Table", "x", v("mess_x") - 0.012, V["mess_x"] + 0.012, "the mess table at x = -0.012"),
        _box("Asterion Chart Table", "the cabin's chart table, 0.015 x 0.019 m, top 0.921 m up",
             (table - v("table_w") / 2, y - v("table_d") / 2, None), (table + V["table_w"] / 2, y + V["table_d"] / 2,
             v("table_top")), 0.003),
        _inside("Asterion Frames", "y", y - half, y + half, "the frames inside the hull", 0.002),
        _inside("Asterion Cabin", "x", V["stern_x"], v("bulkhead_x"), "the cabin's things aft of its bulkhead", 0.02),
        _inside("Asterion Chronometer", "x", table - V["table_w"] / 2, table + V["table_w"] / 2,
                "the chronometer on the chart table (x)", 0.002),
        _inside("Asterion Chronometer", "z", V["table_top"], V["table_top"] + 0.007, "the chronometer on the chart "
                "table (z)", 0.002),
        _inside("Asterion Dip Pen", "x", table - V["table_w"] / 2, table + V["table_w"] / 2,
                "the dip pen on the chart table (x)", 0.002),
        _inside("Asterion Dip Pen", "z", V["table_top"], V["table_top"] + 0.004, "the dip pen on the chart table (z)",
                0.002),
        _inside("Asterion Sea Chest", "x", V["stern_x"], V["bulkhead_x"], "the sea chest in the cabin", 0.003),
        _inside("Asterion Cot", "x", V["stern_x"], V["bulkhead_x"], "the cot in the cabin", 0.003),
        _box("Asterion Hold Floor", "the hold floor at z = 0.897, 0.023 m wide", high=(None, None, v("hold_z")), tol=0.004,
             size=[None, [round(v("hold_w") - 0.006, 4), round(V["hold_w"] + 0.006, 4)], None]),
        _inside("Asterion Hold", "z", V["keel_z"], V["gd_z"], "the hold's stores between the keel and the gun deck",
                0.003),
        _inside("Asterion Hold", "y", y - half, y + half, "the hold's stores inside the hull", 0.002),
    ]
    return out


def whale_checks():
    y, z = v("wh_y"), v("wh_z")
    return [
        _box("Blue Whale", "the whale from x = -0.064 to 0.08", (v("wh_tail"), None, None), (v("wh_nose"), None, None),
             tolerance(0.144, 0.022)),
        _inside("Blue Whale", "z", V["axis_z"] - V["body_dia"] / 2, v("sea_z") - 0.015, "the whale under the surface"),
        _down(0.012, y + 0.004, "Blue Whale", "the whale's back about 0.873 m up", z + 0.0, z + V["wh_h"] / 2 + 0.009,
              v("wh_w")),
        _box("Blue Whale Flukes", "its flukes 0.05 m from tip to tip", tol=0.004,
             size=[None, [round(v("wh_span") - 0.008, 4), round(V["wh_span"] + 0.008, 4)], None]),
        _inside("Blue Whale Flukes", "x", V["wh_tail"], V["wh_tail"] + 0.03, "its flukes at its tail", 0.006),
        _inside("Blue Whale Eyes", "x", v("eye_x") - 0.007, V["eye_x"] + 0.007, "its eyes on the head (x)"),
        _inside("Blue Whale Eyes", "z", v("eye_z") - 0.006, V["eye_z"] + 0.006, "its eyes on the head (z)"),
        _box("Blue Whale Eyes", "two eyes, one each side", tol=0.004, size=[None, [0.008, 0.03], None]),
        _colour("Blue Whale", ("blue", "grey"), "blue-grey"),
    ]


def checks():
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        *study_checks(), *stand_checks(), *inside_checks(), *ship_checks(), *below_checks(), *whale_checks(),
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.05, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [26.0, 30.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


def _what(*paragraphs):
    return " ".join(paragraph.format(**{key: "%g" % value for key, value in V.items()}) for paragraph in paragraphs)


GRADE_CLASSES = {
    "The study": {"views": ["room", "hero"], "what": _what(brief.STUDY)},
    "The desk": {"views": ["closeup_desk", "hero"], "what": _what(brief.DESK)},
    "Stand and plaque": {"views": ["closeup_stand", "front"], "what": _what(brief.STAND)},
    "Bottle and neck": {"views": ["closeup_neck", "front"], "what": _what(brief.BOTTLE)},
    "Sea and sky": {"views": ["hero", "front"], "what": _what(brief.INSIDE)},
    "The Asterion": {"views": ["closeup_ship", "side"], "what": _what(brief.HULL)},
    "Sails and rigging": {"views": ["closeup_rigging", "front"], "what": _what(brief.RIG)},
    "Below decks": {"views": ["closeup_ship", "top"], "what": _what(brief.BELOW)},
    "Blue whale": {"views": ["closeup_whale", "front"], "what": _what(brief.WHALE)},
    "The whole showpiece": {"views": ["hero", "room"],
                            "what": "A ship in a bottle on a carved walnut stand on a period desk in a dark study: "
                                    "the glass bottle with its cork, rope, knot, hanging compass and lantern; inside "
                                    "it a stormy sea, storm clouds, a moon, a fully rigged frigate and a blue whale.",
                            "layout": "The bottle lies along X on its stand in the middle of the desk against the "
                                      "north wall, the window behind to the left, the bookcase behind to the right; "
                                      "the ship on the bottle's axis, bow to the neck; the whale under the surface "
                                      "on the near side; each piece where the brief puts it."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The ship in a bottle", "references": ["hero", "front"], "prompt": PROMPT,
          "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    stand_in.build(bpy)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
