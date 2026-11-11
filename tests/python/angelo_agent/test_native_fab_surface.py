# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which map of a texture set is which, from names and from pixels.

Developed on the Megascans layouts seen on Fab 2026-11-11 (``<Name>_<id>_1K_BaseColor.jpg`` ... and the UE glTF's
``T_<id>_1K_{B,N,ORM}``). Held out, never looked at while writing the rules: Poly Haven (``_diff_``, ``_nor_gl_``,
``_rough_``, ``_disp_``, ``_arm_``), Substance exports (``_BaseColor``, ``_Normal_OpenGL``, ``_Height``,
``_Mixed_AO``, ``_Metallic``), ambientCG (``_Color``, ``_NormalDX``, ``_Displacement``, ``_AmbientOcclusion``)
and Unreal's own letters (``T_Brick_D``, ``_N``, ``_R``, ``_M``). Pixels are synthetic: a height field's true
normal map in both conventions, flat colour, grey scalars.
"""
import suite_isolation  # noqa: F401
import team_library_fixtures  # noqa: F401  Puts the add-on on the path.
import unittest

import numpy as np

from angelo_agent import fab_surface as surface


def height_field(side=64):
    y, x = np.mgrid[0:side, 0:side] / side
    return 0.5 + 0.2 * np.sin(6.3 * x) * np.cos(4.1 * y) + 0.1 * np.sin(11.0 * x + 7.0 * y)


def normal_map(height, convention='gl', strength=0.3):
    dy, dx = np.gradient(height)   # Rows run bottom to top, as Blender's pixels do.
    n = np.dstack([-dx * strength * len(height), -dy * strength * len(height), np.ones_like(height)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    if convention == 'dx':
        n[:, :, 1] *= -1
    return (n + 1.0) / 2.0


def grey(value=0.5, side=16):
    return np.full((side, side, 3), value)


def colour(side=16):
    pixels = np.zeros((side, side, 3))
    pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2] = 0.6, 0.35, 0.2
    pixels[::2, :, 1] = 0.45
    return pixels


class NameTests(unittest.TestCase):
    def check(self, cases):
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(surface.role_from_name(name), expected)

    def test_megascans_names(self):
        self.check({
            'Rock_Wall_tfomfado_1K_BaseColor.jpg': ('base_color', None),
            'Rock_Wall_tfomfado_1K_Normal.jpg': ('normal', None),
            'Rock_Wall_tfomfado_1K_Roughness.jpg': ('roughness', None),
            'Rock_Wall_tfomfado_1K_Gloss.jpg': ('gloss', None),
            'Rock_Wall_tfomfado_1K_Displacement.jpg': ('displacement', None),
            'Rock_Wall_tfomfado_1K_AO.jpg': ('ao', None),
            'Rock_Wall_tfomfado_1K_Cavity.jpg': ('cavity', None),
            'Rock_Wall_tfomfado_1K_Bump.jpg': ('bump', None),
            'Textures/T_tfomfado_1K_B.jpg': ('base_color', None),
            'Textures/T_tfomfado_1K_N.jpg': ('normal', None),
            'Textures/T_tfomfado_1K_ORM.jpg': ('packed', 'orm'),
            'textures/Historic_Rug_Low_1K_albedo.jpeg': ('base_color', None),
        })

    def test_held_out_conventions(self):
        self.check({
            'brick_wall_02_diff_2k.jpg': ('base_color', None), 'brick_wall_02_nor_gl_2k.exr': ('normal', 'gl'),
            'brick_wall_02_nor_dx_2k.png': ('normal', 'dx'), 'brick_wall_02_rough_2k.jpg': ('roughness', None),
            'brick_wall_02_disp_2k.png': ('displacement', None), 'brick_wall_02_arm_2k.jpg': ('packed', 'arm'),
            'Crate_BaseColor.png': ('base_color', None), 'Crate_Normal_OpenGL.png': ('normal', 'gl'),
            'Crate_Height.png': ('displacement', None), 'Crate_Mixed_AO.png': ('ao', None),
            'Crate_Metallic.png': ('metallic', None), 'Crate_Opacity.png': ('opacity', None),
            'Bricks076C_1K-PNG_Color.png': ('base_color', None), 'Bricks076C_1K-PNG_NormalDX.png': ('normal', 'dx'),
            'Bricks076C_1K-PNG_AmbientOcclusion.png': ('ao', None),
            'Bricks076C_1K-PNG_Displacement.png': ('displacement', None),
            'T_Brick_D.tga': ('base_color', None), 'T_Brick_N.tga': ('normal', None), 'T_Brick_R.tga': ('roughness', None),
            'T_Brick_M.tga': ('metallic', None), 'Brick_N.tga': (None, None), 'woodFloorRoughness.png': ('roughness', None),
            'wood_floor_mra.png': ('packed', 'mra'), 'notes.png': (None, None),
        })


class PixelTests(unittest.TestCase):
    def test_normal_maps_are_recognised_and_scalars_are_not(self):
        self.assertTrue(surface.features(normal_map(height_field()))['normal_like'])
        self.assertFalse(surface.features(colour())['normal_like'])
        self.assertTrue(surface.features(grey())['gray'])
        self.assertFalse(surface.features(colour())['gray'])

    def test_the_curl_test_tells_opengl_from_directx(self):
        for convention in ('gl', 'dx'):
            for height in (height_field(64), height_field(128).T[::-1]):
                with self.subTest(convention=convention):
                    self.assertEqual(surface.normal_convention(normal_map(height, convention)), convention)

    def test_a_flat_normal_map_is_undecided(self):
        flat = np.dstack([np.full((16, 16), 0.5), np.full((16, 16), 0.5), np.ones((16, 16))])
        self.assertIsNone(surface.normal_convention(flat))

    def test_alpha_and_binary(self):
        rgba = np.dstack([colour(), np.where(np.arange(16)[None, :] < 8, 1.0, 0.0) * np.ones((16, 16))])
        self.assertTrue(surface.features(rgba)['alpha_varies'])
        self.assertTrue(surface.features(np.dstack([grey(1.0)[:, :, 0]] * 3) * (np.arange(16) % 2)[None, :, None])
                        ['binary'])


def row(name, pixels, size=1024 * 1024):
    facts = surface.features(pixels)
    entry = {'name': name, 'features': facts, 'size': size}
    if facts['normal_like'] or facts['normal_score'] >= surface.NAMED_NORMAL_SCORE:
        entry['pixels'] = pixels   # As the converter samples a map that may be a normal map more closely.
    return entry


def padded(pixels, fraction=0.35, value=(0.0, 0.0, 0.0)):
    """A baked model map: its UV islands hold the data, the rest is padding (here the last ``fraction`` of rows)."""
    pixels = pixels.copy()
    pixels[int(len(pixels) * (1 - fraction)):, :, :] = value
    return pixels


class ClassifyTests(unittest.TestCase):
    def test_a_named_baked_normal_map_with_padding_is_the_normal(self):
        """A model's normal map (the Megascans rug FBX, 2026-11-11) is a normal map only inside its UV islands."""
        for value in ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.5, 0.5, 0.5)):
            with self.subTest(padding=value):
                baked = padded(normal_map(height_field()), value=value)
                self.assertFalse(surface.features(baked)['normal_like'])   # Too much padding to call it by content.
                result = surface.classify([row('Rug_Low_1K_Normal.jpg', baked), row('Rug_Low_1K_BaseColor.jpg', colour())])
                self.assertEqual(result['roles'].get('normal'), 'Rug_Low_1K_Normal.jpg')
                self.assertEqual(result['normal'], 'gl')   # The curl test reads the islands, not the padding.
                dx = padded(normal_map(height_field(), 'dx'), value=value)
                self.assertEqual(surface.classify([row('Rug_Normal.jpg', dx)])['normal'], 'dx')

    def test_a_named_normal_that_is_mostly_not_one_is_still_refused(self):
        result = surface.classify([row('Rug_Normal.jpg', padded(colour(), 0.0)), row('Rug_BaseColor.jpg', colour())])
        self.assertNotIn('normal', result['roles'])

    def test_megascans_surface(self):
        result = surface.classify([
            row('X_1K_BaseColor.jpg', colour()), row('X_1K_Normal.jpg', normal_map(height_field())),
            row('X_1K_Roughness.jpg', grey(0.7)), row('X_1K_Gloss.jpg', grey(0.3)), row('X_1K_AO.jpg', grey(0.8)),
            row('X_1K_Displacement.jpg', grey(0.5)), row('X_1K_Cavity.jpg', grey(0.6)), row('X_1K_Bump.jpg', grey()),
            row('X_1K_Specular.jpg', grey(0.2))])
        self.assertEqual(set(result['roles']), {'base_color', 'normal', 'roughness', 'gloss', 'ao', 'displacement',
                                                'cavity', 'bump', 'specular'})
        self.assertEqual(result['normal'], 'gl')

    def test_pixels_overrule_a_wrong_name_and_name_an_unnamed_map(self):
        dx = normal_map(height_field(), 'dx')
        result = surface.classify([row('Metal_Plate_2K.jpg', colour()), row('Metal_Plate_2K_N.jpg', dx),
                                   row('Metal_Plate_Normal_GL.jpg', grey(0.5))])
        self.assertEqual(result['roles']['base_color'], 'Metal_Plate_2K.jpg')
        self.assertEqual(result['roles']['normal'], 'Metal_Plate_2K_N.jpg')
        self.assertEqual(result['normal'], 'dx')
        self.assertIn('Metal_Plate_Normal_GL.jpg', result['unused'])
        self.assertIn('named_normal_not_normal', result['notes'])
        self.assertIn('named_scalar_is_colour', result['notes'])

    def test_measured_convention_beats_the_name(self):
        result = surface.classify([row('Wall_Normal_GL.png', normal_map(height_field(), 'dx'))])
        self.assertEqual(result['normal'], 'dx')
        self.assertIn('normal_convention_from_pixels', result['notes'])

    def test_the_largest_of_two_maps_for_a_role_is_used(self):
        result = surface.classify([row('a_BaseColor_1k.jpg', colour(), 1 << 20),
                                   row('a_BaseColor_4k.jpg', colour(), 1 << 24)])
        self.assertEqual(result['roles']['base_color'], 'a_BaseColor_4k.jpg')
        self.assertEqual(result['unused'], ['a_BaseColor_1k.jpg'])

    def test_packed_maps_and_nothing_usable(self):
        result = surface.classify([row('T_X_ORM.jpg', colour()), row('T_X_B.jpg', colour())])
        self.assertEqual(result['packed'], {'T_X_ORM.jpg': ('ao', 'roughness', 'metallic')})
        with self.assertRaises(ValueError):
            surface.classify([row('notes.png', grey()), row('other.png', grey(0.2))])


if __name__ == '__main__':
    unittest.main()
