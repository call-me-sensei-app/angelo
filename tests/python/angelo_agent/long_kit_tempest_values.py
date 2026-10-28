# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every stated number of ``scenario_oneshot_tempest`` (long candidate 6): one source for its brief, stand-in and checks.

The scene is a ship in a bottle on a period desk in a dark study, at real size in metres (Z up, the study's open side
toward -Y). ``V`` holds each number the brief states, by key; the brief (``long_kit_tempest_brief``) writes them with
``{key}``, the stand-in (``long_kit_tempest``) builds from them and the checks (``scenario_oneshot_tempest``) measure
them, so a check can only measure what the brief says. Derived tables (the bottle's profile, the sails) are built
from ``V`` here.
"""

V = {
    # The study: floor, three walls (the south side is open), a window, a bookcase, the desk.
    "floor_w": 5.0, "floor_d": 4.0, "wall_h": 3.0, "wall_t": 0.15, "wainscot_h": 1.0,
    "win_x": -1.1, "win_w": 1.3, "curtain_w": 0.6, "win_sill": 0.85, "win_head": 2.45,
    "case_x": 1.3, "case_y": 1.725, "case_w": 1.7, "case_d": 0.4, "case_h": 2.5,
    "book_low": 0.3, "book_high": 0.42,
    "desk_x": 0.0, "desk_y": 1.4, "desk_w": 1.8, "desk_d": 0.9, "desk_top": 0.76,
    "chart_x": -0.55, "chart_y": 1.25, "chart_w": 0.5, "chart_d": 0.38, "chart_turn": 15,
    "compass_x": -0.18, "compass_y": 1.06, "compass_dia": 0.08,
    "coins_x0": 0.32, "coins_x1": 0.6, "coins_y0": 1.0, "coins_y1": 1.2,
    "lamp_x": 0.55, "lamp_y": 1.62, "lamp_h": 0.28, "lamp_foot": 0.16,
    "candle_x": -0.75, "candle_y": 1.72, "candle_h": 0.16,
    # The display stand and the bottle (its axis along X).
    "plinth_w": 0.56, "plinth_d": 0.21, "plinth_top": 0.8,
    "cradle_wx": -0.16, "cradle_ex": 0.14, "cradle_t": 0.025, "cradle_d": 0.19, "cradle_horn": 0.86, "finial": 0.012,
    "axis_y": 1.35, "axis_z": 0.975, "glass_wall": 0.005,
    "base_x": -0.3, "body_x0": -0.21, "body_x1": 0.16, "body_dia": 0.292, "neck_x0": 0.275, "neck_x1": 0.34,
    "neck_dia": 0.104, "lip_x": 0.35, "lip_dia": 0.112,
    "cork_x0": 0.33, "cork_x1": 0.385, "cork_in": 0.094, "cork_out": 0.102,
    "rope_x0": 0.3, "rope_x1": 0.33, "rope_turns": 8,
    "knot_x": 0.327, "knot_y": 1.293, "knot_z": 0.97,
    "hc_x": 0.365, "hc_y": 1.278, "hc_z": 0.89, "hc_dia": 0.05,
    "hl_x": 0.307, "hl_y": 1.275, "hl_z": 0.884, "hl_h": 0.05,
    "plaque_x": -0.02, "plaque_z": 0.78, "plaque_w": 0.146, "plaque_h": 0.026,
    # Inside the bottle.
    "sea_z": 0.92, "swell": 0.012, "cloud_base": 1.0,
    "moon_x": -0.18, "moon_y": 1.42, "moon_z": 1.05, "moon_dia": 0.014,
    # The frigate Asterion: bow toward the neck (+X), on the bottle's axis line.
    "stern_x": -0.12, "stem_x": 0.066, "beam": 0.056, "keel_z": 0.89, "rail_z": 0.959, "sheer": 0.006,
    "rub_z": 0.94, "deck_z": 0.948, "qd_x": -0.077, "qd_z": 0.962,
    "port_z": 0.934, "port_w": 0.006, "port_h": 0.005, "port_x0": -0.085, "port_dx": 0.0214, "gun_out": 0.006,
    "fore_x": 0.019, "fore_top": 1.069, "main_x": -0.03, "main_top": 1.087, "mizzen_x": -0.079, "mizzen_top": 1.071,
    "mast_dia": 0.0042, "nest_dia": 0.016, "nest_down": 0.04, "brace": 23,
    "sprit_x0": 0.036, "sprit_z0": 0.953, "sprit_x1": 0.1, "sprit_z1": 0.976,
    "pin_x0": -0.063, "pin_x1": -0.0126, "pin_x2": 0.027, "pin_off": 0.0192, "barrel_x0": -0.062, "barrel_x1": 0.0096,
    "barrel_y": 1.3614, "barrel_dia": 0.0055, "barrel_h": 0.006, "pennant_l": 0.018,
    "hatch_x": -0.007, "capstan_x": 0.034, "helm_x": -0.097, "helm_z": 0.97, "helm_rim": 0.012, "helm_reach": 0.016,
    # Below decks.
    "gd_z": 0.907, "gd_w": 0.039, "hold_z": 0.897, "hold_w": 0.023, "bulkhead_x": -0.079,
    "table_x": -0.109, "table_w": 0.015, "table_d": 0.019, "table_top": 0.921,
    "mess_x": -0.012, "mess_top": 0.917,
    # The blue whale, under the surface beside the ship, swimming toward the neck.
    "wh_y": 1.27, "wh_z": 0.862, "wh_nose": 0.08, "wh_tail": -0.064, "wh_w": 0.024, "wh_h": 0.022, "wh_span": 0.05,
    "eye_x": 0.062, "eye_z": 0.86,
    # The hero camera.
    "cam_x": 0.6, "cam_y": 0.39, "cam_z": 1.2, "look_x": 0.02, "look_y": 1.35, "look_z": 0.94, "lens": 28,
}

# Masts, west (aft) to east: (name, x, top). Sails per mast, low to high: (name, yard height, width, depth).
MASTS = (("Mizzen", V["mizzen_x"], V["mizzen_top"]), ("Main", V["main_x"], V["main_top"]),
         ("Fore", V["fore_x"], V["fore_top"]))
SAIL_NAMES = ("Course", "Topsail", "Topgallant")
_SAILS = {"Mizzen": ((1.023, 0.049, 0.039), (1.048, 0.038, 0.029), (1.067, 0.03, 0.019)),
          "Main": ((1.027, 0.068, 0.039), (1.057, 0.053, 0.029), (1.081, 0.038, 0.019)),
          "Fore": ((1.019, 0.059, 0.039), (1.048, 0.046, 0.029), (1.064, 0.033, 0.019))}
SAILS = tuple(("Asterion %s %s" % (mast, SAIL_NAMES[i]), mast, x, yard, width, depth)
              for mast, x, _top in MASTS for i, (yard, width, depth) in enumerate(_SAILS[mast]))
for _name, _mast, _x, _yard, _width, _depth in SAILS:
    key = _name.split(" ", 1)[1].lower().replace(" ", "_")
    V[key + "_yard"], V[key + "_w"], V[key + "_d"] = _yard, _width, _depth

PORTS = tuple(round(V["port_x0"] + i * V["port_dx"], 4) for i in range(6))
GUN_DECK_GUNS = (-0.062, -0.041, -0.02, 0.001, 0.023)

# The bottle's outer profile: (x, radius) from the base to the lip, as the brief describes it.
PROFILE = ((V["base_x"], 0.0), (-0.291, 0.072), (-0.26, 0.123), (V["body_x0"], V["body_dia"] / 2),
           (V["body_x1"], V["body_dia"] / 2), (0.21, 0.135), (0.25, 0.096), (V["neck_x0"], V["neck_dia"] / 2),
           (V["neck_x1"], V["neck_dia"] / 2), (V["lip_x"], V["lip_dia"] / 2))


def radius_at(x):
    """The bottle's outer radius at ``x`` (0 outside it), straight between profile points."""
    for (x0, r0), (x1, r1) in zip(PROFILE, PROFILE[1:]):
        if x0 <= x <= x1:
            return r0 + (r1 - r0) * (x - x0) / (x1 - x0) if x1 > x0 else r1
    return 0.0


def hull_half(x):
    """The hull's half-beam at ``x`` (the stand-in's plan shape: full amidships, fining to the stem, square stern)."""
    t = (x - V["stern_x"]) / (V["stem_x"] - V["stern_x"])
    if t < 0.0 or t > 1.0:
        return 0.0
    half = V["beam"] / 2
    if t > 0.62:
        return half * max(0.08, (1.0 - ((t - 0.62) / 0.38) ** 2)) ** 0.5
    if t < 0.12:
        return half * (0.86 + 0.14 * t / 0.12)
    return half
