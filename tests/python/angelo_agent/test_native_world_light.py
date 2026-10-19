# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the World's sun is and how bright the World shows (world_light), on stand-ins.

Recorded one-shots guessed the Sky texture's ``sun_rotation`` zero point, set the sky's sun
apart from the Sun light, and could not tell how bright a sky renders. These pin the one direction convention,
the Sun lights beside the sky, the read-back of the sky's settings, the brightness samples and the encoders the
display reading uses. ``test_blender_world_light.py`` checks the convention and the display transform against
Blender's own render and save in the compiled app.
"""

import math
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import world_light, world_light_table, world_surface  # noqa: E402
from test_native_world_surface import Link, Node, default_world, sky  # noqa: E402


def toward(bearing, elevation):
    b, e = math.radians(bearing), math.radians(elevation)
    return (math.cos(e) * math.sin(b), math.cos(e) * math.cos(b), math.sin(e))


def sun(name, bearing, elevation, energy=3.0, hide_render=False):
    """A Sun light whose +Z (where its light comes from) points at that bearing and elevation."""
    z = toward(bearing, elevation)
    matrix = [[0.0, 0.0, z[0], 0.0], [0.0, 0.0, z[1], 0.0], [0.0, 0.0, z[2], 5.0], [0.0, 0.0, 0.0, 1.0]]
    return SimpleNamespace(name=name, type="LIGHT", data=SimpleNamespace(type="SUN", energy=energy),
                           matrix_world=matrix, hide_render=hide_render)


def scene_with(world, objects=(), view_transform="AgX", exposure=0.0):
    view = SimpleNamespace(view_transform=view_transform, look="None", exposure=exposure, gamma=1.0)
    return SimpleNamespace(world=world, render=SimpleNamespace(engine="CYCLES"), objects=list(objects),
                           view_settings=view)


def sky_world(**settings):
    value, out, bg = default_world()
    node = sky(**settings)
    value.node_tree.nodes.append(node)
    value.node_tree.links.append(Link(node, "Color", bg, "Color"))
    return value, node, bg


class Directions(unittest.TestCase):
    def test_bearing_is_clockwise_from_plus_y_and_elevation_above_the_horizon(self):
        self.assertEqual(world_light.angles((0, 1, 0)), (0.0, 0.0))
        self.assertEqual(world_light.angles((1, 0, 0)), (90.0, 0.0))
        self.assertEqual(world_light.angles((0, -1, 0)), (180.0, 0.0))
        self.assertEqual(world_light.angles((-1, 0, 1)), (270.0, 45.0))
        self.assertIsNone(world_light.angles((0, 0, 0)))
        self.assertEqual(world_light.angles((-1e-7, 1, 0)), (0.0, 0.0), "359.99999 is bearing 0, never 360")

    def test_sun_rotation_is_the_bearing_and_an_elevation_past_ninety_folds(self):
        for rotation, elevation, expected in ((0, 10, (0.0, 10.0)), (90, 10, (90.0, 10.0)), (225, 8, (225.0, 8.0)),
                                              (-45, 30, (315.0, 30.0)), (90, 100, (270.0, 80.0))):
            with self.subTest(rotation=rotation, elevation=elevation):
                node = sky(elevation_deg=elevation, rotation_deg=rotation)
                facts, _ = world_light.sky(node)
                self.assertEqual((facts["sun_bearing_deg"], facts["sun_elevation_deg"]), expected)

    def test_analytic_skies_read_their_sun_direction(self):
        node = sky("PREETHAM", sun_direction=(1.0, 0.0, 1.0), turbidity=2.2, ground_albedo=0.3)
        facts, _ = world_light.sky(node)
        self.assertEqual((facts["sun_bearing_deg"], facts["sun_elevation_deg"], facts["turbidity"]), (90.0, 45.0, 2.2))
        self.assertNotIn("aerosol_density", facts)

    def test_the_settings_a_script_set_are_read_back_and_a_turned_sky_is_named(self):
        node = sky(aerosol_density=4.0, sun_intensity=0.5)
        facts, _ = world_light.sky(node)
        self.assertEqual((facts["aerosol_density"], facts["sun_intensity"]), (4.0, 0.5))
        node.texture_mapping = SimpleNamespace(rotation=(0.0, 0.0, 0.3), scale=(1, 1, 1), mapping_x="X",
                                               mapping_y="Y", mapping_z="Z")
        self.assertEqual(world_light.sky(node)[0]["sun_turned"], "texture_mapping")
        node.texture_mapping.rotation = (0.0, 0.0, 0.0)
        node.inputs["Vector"].is_linked = True
        self.assertEqual(world_light.sky(node)[0]["sun_turned"], "vector_input")


class SunLights(unittest.TestCase):
    def test_each_rendered_sun_light_beside_the_skys_sun(self):
        value, node, _ = sky_world(elevation_deg=8.0, rotation_deg=225.0)
        lights = [sun("Sun", 225.0, 8.0), sun("Fill", 45.0, 30.0), sun("Off", 0.0, 50.0, hide_render=True),
                  SimpleNamespace(name="Lamp", type="LIGHT", data=SimpleNamespace(type="POINT", energy=100.0))]
        shown = world_surface.shows(value, "CYCLES", scene_with(value, lights))
        self.assertEqual([row["name"] for row in shown["sun_lights"]], ["Sun", "Fill"])
        first, second = shown["sun_lights"]
        self.assertEqual((first["bearing_deg"], first["elevation_deg"], first["strength"]), (225.0, 8.0, 3.0))
        self.assertAlmostEqual(first["from_sky_deg"], 0.0, places=1)
        self.assertAlmostEqual(second["from_sky_deg"], 180 - 38.0, delta=0.5)  # Opposite bearings, 8 + 30 up.
        self.assertEqual(shown["directions"], world_light.DIRECTIONS)

    def test_the_count_is_bounded_and_a_colour_world_still_reports_its_suns(self):
        value, _, _ = default_world()
        lights = [sun("Sun %d" % index, 10.0 * index, 20.0) for index in range(world_light.MAX_SUNS + 2)]
        shown = world_surface.shows(value, "CYCLES", scene_with(value, lights))
        self.assertEqual((len(shown["sun_lights"]), shown["sun_lights_more"]), (world_light.MAX_SUNS, 2))
        self.assertNotIn("from_sky_deg", shown["sun_lights"][0])

    def test_without_a_scene_only_the_nodes_facts(self):
        value, _, _ = sky_world()
        shown = world_surface.shows(value, "CYCLES")
        self.assertIn("sun_bearing_deg", shown["sky"])
        self.assertFalse({"sun_lights", "brightness", "directions"} & set(shown))


class RenderedLighting(unittest.TestCase):
    """A RENDERED look's ``rendered.lighting`` (world_surface.lighting): what lit it, compactly."""

    def test_a_sky_its_sun_the_sun_lights_and_the_other_lights(self):
        value, _, _ = sky_world(elevation_deg=8.0, rotation_deg=225.0)
        lights = [sun("Sun", 45.0, 30.0), sun("Off", 0.0, 50.0, hide_render=True),
                  SimpleNamespace(name="Lamp", type="LIGHT", data=SimpleNamespace(type="POINT", energy=100.0)),
                  SimpleNamespace(name="Fill", type="LIGHT", data=SimpleNamespace(type="AREA", energy=50.0),
                                  hide_render=False)]
        lit = world_surface.lighting(scene_with(value, lights))
        self.assertEqual((lit["shows"], lit["strength"]), ("SKY", 1.0))
        self.assertEqual(lit["sky_sun"], {"bearing_deg": 225.0, "elevation_deg": 8.0})
        self.assertEqual([row["name"] for row in lit["sun_lights"]], ["Sun"])
        self.assertAlmostEqual(lit["sun_lights"][0]["from_sky_deg"], 180 - 38.0, delta=0.5)
        self.assertEqual(lit["other_lights"], {"POINT": 1, "AREA": 1})
        self.assertEqual(lit["directions"], world_light.DIRECTIONS)
        self.assertNotIn("brightness", lit, "The picture shows how bright it is")

    def test_only_lights_that_render_are_counted(self):
        """Regression: lights in a collection hidden for render, or excluded from the view layer, were listed."""
        value, _, _ = sky_world(elevation_deg=8.0, rotation_deg=225.0)
        pointer = iter(range(1, 100))

        def collection(hide_render=False):
            return SimpleNamespace(hide_render=hide_render, as_pointer=(lambda value=next(pointer): value))

        def layer(collection, exclude=False, children=()):
            return SimpleNamespace(collection=collection, exclude=exclude, children=list(children))

        visible, hidden, excluded = collection(), collection(hide_render=True), collection()
        shown_sun, hidden_sun, spot = sun("Sun", 45.0, 30.0), sun("Old Sun", 0.0, 50.0), sun("Spot", 0.0, 60.0)
        spot.data.type = "SPOT"
        lamp = sun("Lamp", 0.0, 70.0)
        lamp.data.type = "POINT"
        for obj, users in ((shown_sun, [visible]), (hidden_sun, [hidden]), (spot, [excluded]), (lamp, [visible])):
            obj.users_collection = users
        root = layer(collection(), children=[layer(visible), layer(hidden), layer(excluded, exclude=True)])
        scene = scene_with(value, [shown_sun, hidden_sun, spot, lamp])
        scene.view_layers = [SimpleNamespace(use=True, layer_collection=root)]
        # A second used layer that includes the excluded collection: rendered, unless the scene renders one layer.
        second = SimpleNamespace(use=True, layer_collection=layer(collection(), children=[layer(excluded)]))
        scene.view_layers.append(second)
        self.assertEqual(world_surface.lighting(scene)["other_lights"], {"POINT": 1, "SPOT": 1})
        scene.render.use_single_layer = True
        self.assertEqual(world_surface.lighting(scene, scene.view_layers[0])["other_lights"], {"POINT": 1})
        scene.view_layers.pop()
        lit = world_surface.lighting(scene)
        self.assertEqual([row["name"] for row in lit["sun_lights"]], ["Sun"])
        self.assertEqual(lit["other_lights"], {"POINT": 1})
        self.assertEqual([row["name"] for row in world_surface.shows(value, "CYCLES", scene)["sun_lights"]], ["Sun"])
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display = SimpleNamespace(shading=SimpleNamespace(light="STUDIO"))
        self.assertEqual(world_surface.lighting(scene), {"workbench_light": "STUDIO"})

    def test_a_full_temporary_folder_loses_only_the_display_reading(self):
        """Regression: an OSError from the reading's temporary folder failed a committed program."""
        import tempfile
        helper = SimpleNamespace(save_copy=lambda *args, **kwargs: None)
        full = OSError(28, "No space left on device")
        with patch.dict(sys.modules, {"angelo_agent.view_save": SimpleNamespace(native=lambda: helper)}), \
                patch.object(tempfile, "TemporaryDirectory", side_effect=full) as made:
            self.assertIsNone(world_light.displayed(SimpleNamespace(), [[0.5, 0.5, 0.5]]))
        made.assert_called_once()

    def test_a_flat_colour_alone_and_an_unreadable_scene(self):
        value, _, _ = default_world()
        lit = world_surface.lighting(scene_with(value))
        self.assertEqual(lit["shows"], "COLOR")
        self.assertIn("color", lit)
        self.assertFalse({"sun_lights", "directions", "other_lights", "sky_sun"} & set(lit))
        self.assertIsNone(world_surface.lighting(SimpleNamespace(world=value, render=None, objects=None)))


class Brightness(unittest.TestCase):
    def shows(self, value, objects=(), shown=None, **view):
        with patch.object(world_light, "displayed", lambda scene, values: shown and [shown] * len(values)):
            return world_surface.shows(value, "CYCLES", scene_with(value, objects, **view))

    def test_a_flat_colour_is_its_colour_times_strength_and_what_the_view_shows(self):
        value, _, bg = default_world((0.2, 0.4, 0.8, 1.0))
        bg.inputs["Strength"].default_value = 2.0
        brightness = self.shows(value, shown=[0.5, 0.6, 0.7], exposure=1.0)["brightness"]
        self.assertEqual(brightness["view"], {"view_transform": "AgX", "exposure": 1.0})
        self.assertEqual(brightness["samples"], [{"where": "every direction", "linear": [0.4, 0.8, 1.6],
                                                  "shown": [0.5, 0.6, 0.7]}])

    def test_a_default_sky_reads_the_measured_table_times_strength(self):
        value, node, bg = sky_world(elevation_deg=15.0)
        bg.inputs["Strength"].default_value = 0.5
        samples = self.shows(value)["brightness"]["samples"]
        rows, _ = world_light_table.reading("MULTIPLE_SCATTERING", 15.0, {})
        self.assertEqual([row["where"] for row in samples], list(world_light_table.DIRECTIONS))
        for row, (_, rgb) in zip(samples, rows):
            self.assertEqual(row["linear"], [round(v * 0.5, 5) for v in rgb])
            self.assertNotIn("shown", row, "no display reading without Blender's helper")

    def test_another_atmosphere_a_deep_night_or_an_indirect_sky_is_not_read(self):
        value, node, _ = sky_world(aerosol_density=3.0)
        self.assertIn("aerosol_density is 3", self.shows(value)["brightness"]["unread"])
        value, node, _ = sky_world(elevation_deg=-12.0)
        self.assertIn("below the horizon", self.shows(value)["brightness"]["unread"])
        value, out, bg = default_world()  # The sky reaches the Background through a Mix: not read directly.
        node = sky()
        mix = Node("Mix", "MIX", [("Factor", 0.5), ("A",), ("B",)], [("Result",)])
        value.node_tree.nodes += [node, mix]
        value.node_tree.links += [Link(node, "Color", mix, "A"), Link(mix, "Result", bg, "Color")]
        shown = self.shows(value)
        self.assertEqual(shown["shows"], "SKY")
        self.assertNotIn("brightness", shown)

    def test_a_linked_strength_is_not_read(self):
        value, node, bg = sky_world()
        value.node_tree.links.append(Link(node, "Color", bg, "Strength"))
        self.assertNotIn("brightness", self.shows(value))


class Fingerprint(unittest.TestCase):
    def test_the_sun_the_lights_and_the_view_transform_count_as_changes(self):
        value, node, _ = sky_world()
        light = sun("Sun", 90.0, 20.0)
        scene = scene_with(value, [light])
        before = world_surface.fingerprint(scene)
        for change, undo in ((lambda: setattr(node, "sun_rotation", 1.0), lambda: setattr(node, "sun_rotation", 0.0)),
                             (lambda: setattr(node, "aerosol_density", 2.0),
                              lambda: setattr(node, "aerosol_density", 1.0)),
                             (lambda: setattr(light, "matrix_world", sun("Sun", 100.0, 20.0).matrix_world),
                              lambda: setattr(light, "matrix_world", sun("Sun", 90.0, 20.0).matrix_world)),
                             (lambda: setattr(scene.view_settings, "exposure", 1.0),
                              lambda: setattr(scene.view_settings, "exposure", 0.0))):
            change()
            self.assertNotEqual(world_surface.fingerprint(scene), before)
            undo()
            self.assertEqual(world_surface.fingerprint(scene), before)


class Encoders(unittest.TestCase):
    def test_the_radiance_file_holds_the_values(self):
        values = [(0.4, 0.8, 1.6), (12.5, 3.0, 0.02), (0.0, 0.0, 0.0), (2e-5, 1e-5, 3e-5)]
        data = world_light.radiance_hdr(values)
        header, pixels = data.split(b"\n-Y 1 +X 4\n")
        self.assertTrue(header.startswith(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe"))
        self.assertEqual(len(pixels), 16)
        for index, rgb in enumerate(values):
            r, g, b, e = pixels[index * 4:index * 4 + 4]
            decoded = [0.0] * 3 if e == 0 else [(c + 0.5) * math.ldexp(1.0, e - 136) for c in (r, g, b)]
            for got, want in zip(decoded, rgb):
                self.assertAlmostEqual(got, want, delta=max(rgb) / 128 + 1e-12)

    def test_a_24_and_a_32_bit_bmp_row_is_read(self):
        for bits in (24, 32):
            with self.subTest(bits=bits):
                step = bits // 8
                row = bytes((30, 20, 10, 255)[:step] + (60, 50, 40, 255)[:step])
                row += b"\0" * ((-len(row)) % 4)
                header = struct.pack("<2sIHHI", b"BM", 54 + len(row), 0, 0, 54)
                info = struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, bits, 0, len(row), 2835, 2835, 0, 0)
                self.assertEqual(world_light.read_bmp(header + info + row, 2),
                                 [[round(10 / 255, 3), round(20 / 255, 3), round(30 / 255, 3)],
                                  [round(40 / 255, 3), round(50 / 255, 3), round(60 / 255, 3)]])

    def test_a_compressed_or_8_bit_bmp_gives_no_reading(self):
        header = struct.pack("<2sIHHI", b"BM", 58, 0, 0, 54)
        for bits, compression in ((8, 0), (24, 1)):
            info = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, bits, compression, 4, 2835, 2835, 0, 0)
            self.assertIsNone(world_light.read_bmp(header + info + b"\0" * 4, 1))

    def test_without_blenders_helper_there_is_no_display_reading(self):
        self.assertIsNone(world_light.displayed(SimpleNamespace(), [(1.0, 1.0, 1.0)]))


class Table(unittest.TestCase):
    def test_rows_ascend_and_readings_interpolate_between_them(self):
        for kind, rows in world_light_table.SKY_TABLE.items():
            self.assertEqual([row[0] for row in rows], sorted(row[0] for row in rows), kind)
        rows, reason = world_light_table.reading("MULTIPLE_SCATTERING", 12.0, {})
        low, high = world_light_table.SKY_TABLE["MULTIPLE_SCATTERING"][6:8]  # 11 and 15 degrees.
        expected = [a + (b - a) * 0.25 for a, b in zip(low[3], high[3])]
        self.assertIsNone(reason)
        self.assertEqual(rows[2][0], "zenith")
        for got, want in zip(rows[2][1], expected):
            self.assertAlmostEqual(got, want)


if __name__ == "__main__":
    unittest.main()
