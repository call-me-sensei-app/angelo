# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Noise, Voronoi, Brick and Checker ports (``texture_kernels``) against values Cycles rendered.

Each case is a texture configuration put in a World that reads the view direction through a Mapping; the
golden values are pixels of a Cycles 5.2 panorama of that World (1 sample, a 0.01-pixel box filter) at the
coordinates the Mapping gave them. The full comparison, every recorded configuration over 18,432 pixels each,
is ``doc/angelo/oneshot_tests.rst``, "How material colours are read". Tolerances: Noise 5e-4 (float32
summation over octaves), the others 1e-5.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import numpy
    import texture_kernels as kernels
except ImportError:   # The kernels need NumPy, as Blender's own Python has; CI installs it.
    numpy = kernels = None

GOLDEN = [   # (node type, settings, unlinked inputs, output, [[vector, value Cycles rendered], ...])
    ('TEX_CHECKER', {}, {'Color1': [0.8, 0.3, 0.1], 'Color2': [0.1, 0.4, 0.2], 'Scale': 3.0}, 'Color',
     [[[3.0313673, -7.8288841, -1.0127919], [0.8, 0.3, 0.1]], [[8.586071, -6.3903313, 0.0324876], [0.8, 0.3, 0.1]], [[1.3777626, -7.4215422, 1.4839953], [0.8, 0.3, 0.1]]]),
    ('TEX_CHECKER', {}, {'Color1': [0.8, 0.3, 0.1], 'Color2': [0.1, 0.4, 0.2], 'Scale': 3.0}, 'Fac',
     [[[3.0313673, -7.8288841, -1.0127919], [1.0, 1.0, 1.0]], [[8.586071, -6.3903313, 0.0324876], [1.0, 1.0, 1.0]], [[1.3777626, -7.4215422, 1.4839953], [1.0, 1.0, 1.0]]]),
    ('TEX_NOISE', {'noise_dimensions': '3D', 'noise_type': 'FBM', 'normalize': True}, {'Detail': 3.5, 'Distortion': 2.0, 'Lacunarity': 2.3, 'Roughness': 0.6, 'Scale': 4.0}, 'Fac',
     [[[3.0313673, -7.8288841, -1.0127919], [0.4302437, 0.4302437, 0.4302437]], [[8.586071, -6.3903313, 0.0324876], [0.3635724, 0.3635724, 0.3635724]], [[1.3777626, -7.4215422, 1.4839953], [0.5183941, 0.5183941, 0.5183941]]]),
    ('TEX_NOISE', {'noise_dimensions': '3D', 'noise_type': 'FBM', 'normalize': True}, {'Detail': 3.5, 'Distortion': 2.0, 'Lacunarity': 2.3, 'Roughness': 0.6, 'Scale': 4.0}, 'Color',
     [[[3.0313673, -7.8288841, -1.0127919], [0.4302437, 0.337042, 0.6050573]], [[8.586071, -6.3903313, 0.0324876], [0.3635724, 0.4864659, 0.4695041]], [[1.3777626, -7.4215422, 1.4839953], [0.5183941, 0.5944468, 0.5150453]]]),
    ('TEX_NOISE', {'noise_dimensions': '3D', 'noise_type': 'FBM', 'normalize': False}, {'Detail': 2.0, 'Distortion': 0.0, 'Lacunarity': 2.0, 'Roughness': 0.5, 'Scale': 7.0}, 'Fac',
     [[[3.0313673, -7.8288841, -1.0127919], [-0.0878646, -0.0878646, -0.0878646]], [[8.586071, -6.3903313, 0.0324876], [0.5590923, 0.5590923, 0.5590923]], [[1.3777626, -7.4215422, 1.4839953], [-0.2093367, -0.2093367, -0.2093367]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F1', 'distance': 'EUCLIDEAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Distance',
     [[[3.0313673, -7.8288841, -1.0127919], [0.3777398, 0.3777398, 0.3777398]], [[8.586071, -6.3903313, 0.0324876], [0.293257, 0.293257, 0.293257]], [[1.3777626, -7.4215422, 1.4839953], [0.3087676, 0.3087676, 0.3087676]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F1', 'distance': 'EUCLIDEAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Color',
     [[[3.0313673, -7.8288841, -1.0127919], [0.7730694, 0.2289976, 0.6833001]], [[8.586071, -6.3903313, 0.0324876], [0.919844, 0.6622558, 0.3718762]], [[1.3777626, -7.4215422, 1.4839953], [0.1380281, 0.3000875, 0.3620772]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F1', 'distance': 'EUCLIDEAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Position',
     [[[3.0313673, -7.8288841, -1.0127919], [3.0106282, -7.8071094, -1.0597262]], [[8.586071, -6.3903313, 0.0324876], [8.6262951, -6.4058828, 0.0497808]], [[1.3777626, -7.4215422, 1.4839953], [1.3498771, -7.4509878, 1.4730384]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F1', 'distance': 'MANHATTAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Distance',
     [[[3.0313673, -7.8288841, -1.0127919], [0.3459555, 0.3459555, 0.3459555]], [[8.586071, -6.3903313, 0.0324876], [0.2382526, 0.2382526, 0.2382526]], [[1.3777626, -7.4215422, 1.4839953], [0.2710243, 0.2710243, 0.2710243]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F1', 'distance': 'MANHATTAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Color',
     [[[3.0313673, -7.8288841, -1.0127919], [0.7730694, 0.2289976, 0.6833001]], [[8.586071, -6.3903313, 0.0324876], [0.919844, 0.6622558, 0.3718762]], [[1.3777626, -7.4215422, 1.4839953], [0.1380281, 0.3000875, 0.3620772]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F1', 'distance': 'MANHATTAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Position',
     [[[3.0313673, -7.8288841, -1.0127919], [3.0106282, -7.8071094, -1.0597262]], [[8.586071, -6.3903313, 0.0324876], [8.6262951, -6.4058828, 0.0497808]], [[1.3777626, -7.4215422, 1.4839953], [1.3498771, -7.4509878, 1.4730384]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F2', 'distance': 'EUCLIDEAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Distance',
     [[[3.0313673, -7.8288841, -1.0127919], [0.2581445, 0.2581445, 0.2581445]], [[8.586071, -6.3903313, 0.0324876], [0.238744, 0.238744, 0.238744]], [[1.3777626, -7.4215422, 1.4839953], [0.1863013, 0.1863013, 0.1863013]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F2', 'distance': 'EUCLIDEAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Color',
     [[[3.0313673, -7.8288841, -1.0127919], [0.6390507, 0.3269113, 0.4300964]], [[8.586071, -6.3903313, 0.0324876], [0.5744614, 0.815782, 0.3778022]], [[1.3777626, -7.4215422, 1.4839953], [0.7295178, 0.1680537, 0.4397166]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F2', 'distance': 'EUCLIDEAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Position',
     [[[3.0313673, -7.8288841, -1.0127919], [3.0758982, -7.8182945, -0.9572464]], [[8.586071, -6.3903313, 0.0324876], [8.5834293, -6.3150721, -0.0389062]], [[1.3777626, -7.4215422, 1.4839953], [1.4124482, -7.4654512, 1.4568112]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F2', 'distance': 'MANHATTAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Distance',
     [[[3.0313673, -7.8288841, -1.0127919], [0.2077014, 0.2077014, 0.2077014]], [[8.586071, -6.3903313, 0.0324876], [0.1929309, 0.1929309, 0.1929309]], [[1.3777626, -7.4215422, 1.4839953], [0.1659662, 0.1659662, 0.1659662]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F2', 'distance': 'MANHATTAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Color',
     [[[3.0313673, -7.8288841, -1.0127919], [0.2389721, 0.3646266, 0.4636702]], [[8.586071, -6.3903313, 0.0324876], [0.5744614, 0.815782, 0.3778022]], [[1.3777626, -7.4215422, 1.4839953], [0.7295178, 0.1680537, 0.4397166]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'F2', 'distance': 'MANHATTAN', 'normalize': True}, {'Detail': 1.5, 'Exponent': 1.5, 'Lacunarity': 2.0, 'Randomness': 0.8, 'Roughness': 0.6, 'Scale': 4.0}, 'Position',
     [[[3.0313673, -7.8288841, -1.0127919], [3.028614, -7.8747354, -1.0311669]], [[8.586071, -6.3903313, 0.0324876], [8.5834293, -6.3150721, -0.0389062]], [[1.3777626, -7.4215422, 1.4839953], [1.4124482, -7.4654512, 1.4568112]]]),
    ('TEX_VORONOI', {'voronoi_dimensions': '3D', 'feature': 'DISTANCE_TO_EDGE', 'distance': 'EUCLIDEAN', 'normalize': True}, {'Detail': 2.5, 'Lacunarity': 2.0, 'Randomness': 1.0, 'Roughness': 0.5, 'Scale': 3.0}, 'Distance',
     [[[3.0313673, -7.8288841, -1.0127919], [0.0785133, 0.0785133, 0.0785133]], [[8.586071, -6.3903313, 0.0324876], [0.0623355, 0.0623355, 0.0623355]], [[1.3777626, -7.4215422, 1.4839953], [0.0765246, 0.0765246, 0.0765246]]]),
    ('TEX_BRICK', {'offset': 0.3, 'offset_frequency': 3, 'squash': 0.7, 'squash_frequency': 2}, {'Bias': 0.2, 'Brick Width': 0.4, 'Color1': [0.6, 0.2, 0.1], 'Color2': [0.3, 0.1, 0.05], 'Mortar': [0.2, 0.2, 0.2], 'Mortar Size': 0.03, 'Mortar Smooth': 0.0, 'Row Height': 0.2, 'Scale': 3.0}, 'Color',
     [[[3.0313673, -7.8288841, -1.0127919], [0.3368211, 0.1122737, 0.0561369]], [[8.586071, -6.3903313, 0.0324876], [0.2, 0.2, 0.2]], [[1.3777626, -7.4215422, 1.4839953], [0.334173, 0.111391, 0.0556955]]]),
    ('TEX_BRICK', {'offset': 0.3, 'offset_frequency': 3, 'squash': 0.7, 'squash_frequency': 2}, {'Bias': 0.2, 'Brick Width': 0.4, 'Color1': [0.6, 0.2, 0.1], 'Color2': [0.3, 0.1, 0.05], 'Mortar': [0.2, 0.2, 0.2], 'Mortar Size': 0.03, 'Mortar Smooth': 0.0, 'Row Height': 0.2, 'Scale': 3.0}, 'Fac',
     [[[3.0313673, -7.8288841, -1.0127919], [0.0, 0.0, 0.0]], [[8.586071, -6.3903313, 0.0324876], [1.0, 1.0, 1.0]], [[1.3777626, -7.4215422, 1.4839953], [0.0, 0.0, 0.0]]]),
]

DEFAULTS = {"TEX_NOISE": {"Scale": 5.0, "Detail": 2.0, "Roughness": 0.5, "Lacunarity": 2.0, "Distortion": 0.0},
            "TEX_VORONOI": {"Scale": 5.0, "Detail": 0.0, "Roughness": 0.5, "Lacunarity": 2.0, "Randomness": 1.0,
                            "Exponent": 0.5},
            "TEX_BRICK": {"Scale": 5.0, "Mortar Size": 0.02, "Mortar Smooth": 0.1, "Bias": 0.0, "Brick Width": 0.5,
                          "Row Height": 0.25, "Color1": [0.8, 0.8, 0.8], "Color2": [0.2, 0.2, 0.2],
                          "Mortar": [0.0, 0.0, 0.0]},
            "TEX_CHECKER": {"Scale": 5.0, "Color1": [0.8, 0.8, 0.8], "Color2": [0.2, 0.2, 0.2]}}


@unittest.skipIf(numpy is None, "NumPy is not installed")
class TextureKernelTests(unittest.TestCase):
    def test_every_kernel_reproduces_what_cycles_rendered(self):
        for kind, settings, inputs, output, samples in GOLDEN:
            with self.subTest(kind=kind, settings=settings, output=output):
                node = NS(type=kind, name=kind, texture_mapping=None, **settings)
                values = dict(DEFAULTS[kind], **inputs)
                count = len(samples)
                columns = {"Vector": numpy.array([vector for vector, _ in samples], dtype=numpy.float32)}
                for key, value in values.items():
                    shape = (count, len(value)) if isinstance(value, list) else (count,)
                    columns[key] = numpy.broadcast_to(numpy.array(value, dtype=numpy.float32), shape).copy()
                computed = kernels.evaluate(node, columns)[output]
                computed = computed if computed.ndim == 2 else numpy.repeat(computed[:, None], 3, axis=1)
                expected = numpy.array([value for _, value in samples], dtype=numpy.float32)
                tolerance = 5e-4 if kind == "TEX_NOISE" else 1e-5
                self.assertLessEqual(float(numpy.abs(computed - expected).max()), tolerance)

    def test_unported_variants_are_refused_not_guessed(self):
        columns = {key: numpy.zeros((2, 3) if key == "Vector" else 2, dtype=numpy.float32)
                   for key in ("Vector", "Scale", "Detail", "Roughness", "Lacunarity", "Distortion", "Randomness")}
        for node in (NS(type="TEX_NOISE", name="n", noise_dimensions="4D", texture_mapping=None),
                     NS(type="TEX_NOISE", name="n", noise_dimensions="3D", noise_type="RIDGED_MULTIFRACTAL",
                        texture_mapping=None),
                     NS(type="TEX_VORONOI", name="v", voronoi_dimensions="3D", feature="SMOOTH_F1", texture_mapping=None),
                     NS(type="TEX_MAGIC", name="m"),
                     NS(type="TEX_NOISE", name="n", texture_mapping=NS(translation=(1, 0, 0), rotation=(0, 0, 0),
                                                                       scale=(1, 1, 1)))):
            with self.subTest(node=node):
                with self.assertRaises(kernels.Unsupported):
                    kernels.evaluate(node, columns)

    def test_hashes_match_cycles_bit_for_bit(self):
        # Printed by intern/cycles/util/hash.h's own hash_uint2/3/4 (lookup3), compiled from the source tree.
        u = numpy.uint32
        self.assertEqual(int(kernels.hash_uint2(u(0), u(0))), 0xDC3D74F9)
        self.assertEqual(int(kernels.hash_uint3(u(1), u(2), u(3))), 0xB8D17A0A)
        self.assertEqual(int(kernels.hash_uint4(u(1), u(2), u(3), u(4))), 0x0584DEC1)


if __name__ == "__main__":
    unittest.main()
