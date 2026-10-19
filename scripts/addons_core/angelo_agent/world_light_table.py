# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The radiance a physical Sky texture shows, measured once from Blender 5.2's own Sky: no rendering when read.

SKY_TABLE holds, per sky type and sun elevation from -6 to 90 degrees, the mean scene-linear RGB Cycles renders of
the World alone (Background strength 1) through a square 50 mm view (about 40 degrees across) in three directions:
centred 2 degrees below the horizon toward the sun's bearing, the same away from it, and on the zenith. ``tests/python/angelo_agent/measure_sky_table.py`` re-measures
it in a new Blender. It holds the default atmosphere only (``ATMOSPHERE``): a hazier, thinner or ozone-free sky is
not read from it. The Sky texture's ``sun_intensity`` and ``sun_size`` change only the sun's disc, which a direction
centred off it does not hold (Cycles ``svm/sky.h``), so they do not change these readings.
"""

DIRECTIONS = ("horizon toward the sun", "horizon away from the sun", "zenith")
# (sun elevation in degrees, toward the sun, away from it, zenith); every type's rows ascend by elevation.
SKY_TABLE = {
    'SINGLE_SCATTERING': (
        (-6, (0.01704, 0.01378, 0.01107), (9.212e-08, 8.353e-09, 8.372e-11), (0.0005355, 0.0005745, 0.0007017)),
        (-3, (0.152, 0.1169, 0.08607), (0.01602, 0.01053, 0.007834), (0.02279, 0.02734, 0.04097)),
        (0, (3.94, 1.332, 0.3381), (0.3754, 0.2982, 0.182), (0.1429, 0.1869, 0.2418)),
        (2, (9.051, 3.813, 0.8059), (0.9281, 0.8481, 0.5207), (0.2505, 0.3578, 0.4646)),
        (5, (17.15, 9.415, 2.915), (1.573, 1.697, 1.252), (0.3667, 0.5668, 0.8095)),
        (8, (20.95, 13.09, 5.352), (1.978, 2.274, 1.922), (0.4488, 0.7093, 1.082)),
        (11, (21.7, 14.62, 7.072), (2.229, 2.643, 2.424), (0.5101, 0.8112, 1.292)),
        (15, (20.0, 14.41, 8.095), (2.417, 2.93, 2.873), (0.5775, 0.9148, 1.513)),
        (20, (16.13, 12.41, 7.971), (2.519, 3.098, 3.192), (0.6528, 1.025, 1.741)),
        (25, (12.3, 10.03, 7.181), (2.535, 3.142, 3.341), (0.7306, 1.134, 1.95)),
        (30, (9.312, 8.052, 6.329), (2.499, 3.112, 3.379), (0.817, 1.249, 2.157)),
        (45, (4.712, 4.776, 4.611), (2.248, 2.812, 3.158), (1.221, 1.716, 2.859)),
        (60, (3.001, 3.389, 3.645), (1.965, 2.454, 2.796), (2.261, 2.71, 3.963)),
        (90, (1.857, 2.284, 2.625), (1.857, 2.284, 2.625), (5.118, 5.234, 6.321)),
    ),
    'MULTIPLE_SCATTERING': (
        (-6, (0.01572, 0.02201, 0.05122), (0.002571, 0.01223, 0.04038), (0.0007575, 0.005301, 0.02627)),
        (-3, (0.1735, 0.1367, 0.1796), (0.02222, 0.03671, 0.1038), (0.02328, 0.03209, 0.09419)),
        (0, (3.314, 1.187, 0.5392), (0.5614, 0.4003, 0.4008), (0.1702, 0.2163, 0.3902)),
        (2, (6.944, 3.435, 1.268), (1.16, 1.065, 0.9209), (0.2798, 0.4251, 0.7393)),
        (5, (12.34, 8.359, 4.078), (1.809, 2.117, 2.089), (0.4056, 0.7235, 1.343)),
        (8, (14.75, 11.81, 7.426), (2.238, 2.93, 3.272), (0.5011, 0.9628, 1.898)),
        (11, (15.1, 13.35, 9.861), (2.523, 3.499, 4.206), (0.5782, 1.15, 2.342)),
        (15, (13.79, 13.33, 11.36), (2.781, 4.001, 5.066), (0.667, 1.349, 2.783)),
        (20, (11.15, 11.75, 11.33), (2.991, 4.391, 5.749), (0.7702, 1.56, 3.217)),
        (25, (8.777, 9.952, 10.56), (3.13, 4.636, 6.189), (0.8724, 1.76, 3.605)),
        (30, (7.113, 8.596, 9.814), (3.224, 4.793, 6.482), (0.9786, 1.957, 3.98)),
        (45, (4.909, 6.698, 8.641), (3.357, 4.985, 6.878), (1.357, 2.604, 5.137)),
        (60, (4.181, 5.97, 8.067), (3.397, 5.009, 6.964), (2.07, 3.572, 6.563)),
        (90, (3.573, 5.211, 7.237), (3.573, 5.211, 7.237), (4.203, 6.033, 9.514)),
    ),
    'PREETHAM': (
        (-6, (1.937, 1.313, 0.07009), (0.4827, 0.3945, 0.1081), (0.09009, 0.1051, 0.1017)),
        (-3, (2.099, 1.447, 0.1276), (0.5189, 0.4334, 0.1633), (0.09565, 0.1167, 0.128)),
        (0, (2.217, 1.552, 0.2651), (0.5503, 0.4699, 0.2247), (0.1005, 0.1283, 0.1569)),
        (2, (2.277, 1.615, 0.3827), (0.5687, 0.4928, 0.2683), (0.1033, 0.1359, 0.1774)),
        (5, (2.329, 1.685, 0.5683), (0.5922, 0.5244, 0.3363), (0.1073, 0.1474, 0.2099)),
        (8, (2.339, 1.727, 0.753), (0.6113, 0.5527, 0.4059), (0.1109, 0.1588, 0.2439)),
        (11, (2.31, 1.74, 0.9278), (0.6262, 0.5778, 0.475), (0.1144, 0.1704, 0.2791)),
        (15, (2.225, 1.718, 1.129), (0.6404, 0.6061, 0.5633), (0.1193, 0.1861, 0.3274)),
        (20, (2.056, 1.634, 1.308), (0.6502, 0.6334, 0.6618), (0.1264, 0.2062, 0.3884)),
        (25, (1.86, 1.521, 1.404), (0.6531, 0.6519, 0.7418), (0.1354, 0.2271, 0.4483)),
        (30, (1.666, 1.4, 1.431), (0.6503, 0.6625, 0.7996), (0.1469, 0.2493, 0.5059)),
        (45, (1.179, 1.067, 1.262), (0.6214, 0.6566, 0.8462), (0.2023, 0.3262, 0.6573)),
        (60, (0.8565, 0.8252, 0.9999), (0.5822, 0.6221, 0.7857), (0.295, 0.4306, 0.7902)),
        (90, (0.682, 0.7331, 0.9999), (0.682, 0.7331, 0.9999), (0.5298, 0.7371, 1.31)),
    ),
    'HOSEK_WILKIE': (
        (-6, (0.07304, 0.03683, 0.01059), (0.02626, 0.0126, 0.004461), (0.004873, 0.005123, 0.005218)),
        (-3, (0.07304, 0.03683, 0.01059), (0.02626, 0.0126, 0.004461), (0.004873, 0.005123, 0.005218)),
        (0, (0.07304, 0.03683, 0.01059), (0.02626, 0.0126, 0.004461), (0.004873, 0.005123, 0.005218)),
        (2, (0.1423, 0.08085, 0.02459), (0.06202, 0.03996, 0.01535), (0.007125, 0.008826, 0.01083)),
        (5, (0.2092, 0.1447, 0.06114), (0.09337, 0.07939, 0.04224), (0.008715, 0.01295, 0.01975)),
        (8, (0.2428, 0.1926, 0.1012), (0.1109, 0.1093, 0.07229), (0.009931, 0.01604, 0.02726)),
        (11, (0.2546, 0.2236, 0.1371), (0.1207, 0.1317, 0.1009), (0.01104, 0.01863, 0.03365)),
        (15, (0.2513, 0.2445, 0.1748), (0.1279, 0.1538, 0.134), (0.01247, 0.02168, 0.04099)),
        (20, (0.2335, 0.2503, 0.2063), (0.1337, 0.1732, 0.1664), (0.01418, 0.02516, 0.04907)),
        (25, (0.212, 0.245, 0.2246), (0.1384, 0.1863, 0.1899), (0.01579, 0.02841, 0.05652)),
        (30, (0.1926, 0.2353, 0.2336), (0.1426, 0.1946, 0.2064), (0.01728, 0.03147, 0.06363)),
        (45, (0.1575, 0.2054, 0.2327), (0.1463, 0.1988, 0.2292), (0.02177, 0.03993, 0.08353)),
        (60, (0.1416, 0.185, 0.2189), (0.1363, 0.1872, 0.2299), (0.03064, 0.05143, 0.1033)),
        (90, (0.1225, 0.1771, 0.2175), (0.1225, 0.1771, 0.2175), (0.05322, 0.08788, 0.1294)),
    ),
}


# The atmosphere SKY_TABLE was measured with: a new Sky node's values in Blender 5.2.2, per sky type. Anything else
# (a hazier, thinner or ozone-free sky) renders other colours, so it is not read from the table.
ATMOSPHERE = {
    "SINGLE_SCATTERING": {"altitude": 100.0, "air_density": 1.0, "aerosol_density": 1.0, "ozone_density": 1.0},
    "MULTIPLE_SCATTERING": {"altitude": 100.0, "air_density": 1.0, "aerosol_density": 1.0, "ozone_density": 1.0},
    "PREETHAM": {"turbidity": 2.2},
    "HOSEK_WILKIE": {"turbidity": 2.2, "ground_albedo": 0.3},
}


def reading(sky_type, elevation, atmosphere):
    """``(rows, reason)``: ``[(direction, (r, g, b))]`` interpolated by the sun's elevation, or why there is none.

    ``atmosphere`` is ``{setting: value}`` of the node; any setting off its measured default, a type the table does
    not hold, or a sun below the table's twilight is a reason instead of a reading.
    """
    rows = SKY_TABLE.get(sky_type)
    if rows is None:
        return None, "a %s Sky texture is not in the measured table" % sky_type
    for name, default in ATMOSPHERE[sky_type].items():
        value = float(atmosphere.get(name, default))
        if abs(value - default) > 1e-3 * max(1.0, abs(default)):
            return None, "%s is %.4g; the measured table holds the default atmosphere (%.4g)" % (name, value, default)
    if elevation < rows[0][0]:
        return None, "the sun is %.1f degrees below the horizon, past the measured twilight" % -elevation
    elevation = min(rows[-1][0], float(elevation))
    for low, high in zip(rows, rows[1:]):
        if low[0] <= elevation <= high[0]:
            t = 0.0 if high[0] == low[0] else (elevation - low[0]) / (high[0] - low[0])
            return [(DIRECTIONS[i], tuple(a + (b - a) * t for a, b in zip(low[i + 1], high[i + 1])))
                    for i in range(3)], None
    return [(DIRECTIONS[i], rows[-1][i + 1]) for i in range(3)], None
