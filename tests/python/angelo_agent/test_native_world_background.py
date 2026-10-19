# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The sky a World shows, as the scenario checks read it, against stand-in shader graphs.

``world_background`` evaluates a World's graph per view direction and
``scenario_checks.world_facts``/``_check_world`` grade what it shows near the
horizon and overhead. The stand-ins below carry the socket identifiers, types
and defaults of Blender 5.2's nodes. The compiled-app proof is the Cycles
panorama comparison over the recorded benchmark worlds
(``doc/angelo/oneshot_tests.rst``, "How the sky is read").

Each defect the evaluator replaced has a test: a Color Ramp read as the average
of its stops (``RampTests``), a Mix (or any other node) dropping the grader to
the viewport colour (``MixTests``), and a Sky texture that exists but is not
linked to the output counting as the sky (``SkyTests``).
"""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import scenario_checks as checks  # noqa: E402
import shader_fakes as fakes  # noqa: E402
import survey_checks  # noqa: E402
import world_background as wb  # noqa: E402

WARM, BLUE, GREY = (0.85, 0.55, 0.4, 1.0), (0.18, 0.3, 0.6, 1.0), (0.05, 0.05, 0.05, 1.0)
GOLDEN = {"sky_or_color": ["warm"], "sun_elevation_deg": [0, 25]}       # The bridge's golden sky.
CLEAR_BLUE = {"sky_or_color": ["blue"], "sun_elevation_deg": [20, 60]}  # The tree's clear blue sky.

Ramp, Tree, colour_mix, world = fakes.Ramp, fakes.Tree, fakes.colour_mix, fakes.world


def flat(colour, strength=1.0):
    def build(tree, background):
        background.inputs["Color"].default_value = colour
        background.inputs["Strength"].default_value = strength
    return build


def height_ramp(*stops):
    """Background colour from a Color Ramp over the view direction's height (Generated Z), as agents build skies."""
    def build(tree, background):
        coords, split = tree.add("TEX_COORD"), tree.add("SEPXYZ")
        ramp = tree.add("VALTORGB", color_ramp=Ramp(*stops))
        tree.link(coords, "Generated", split, "Vector")
        tree.link(split, "Z", ramp, "Fac")
        tree.link(ramp, "Color", background, "Color")
    return build


def grade(world_, check, engine="BLENDER_EEVEE"):
    return checks._check_world(None, NS(world=world_, render=NS(engine=engine)), None, check)


class FlatColourTests(unittest.TestCase):
    def test_a_flat_colour_is_what_every_direction_shows(self):
        facts = checks.world_facts(world(flat(BLUE)), "BLENDER_EEVEE")
        self.assertEqual(facts["horizon"], {"color": [0.18, 0.3, 0.6], "directions": 40, "families": {"blue": 1.0}})
        self.assertEqual(facts["zenith"]["families"], {"blue": 1.0})
        self.assertEqual(facts["color"], [0.18, 0.3, 0.6])

    def test_flat_blue_passes_the_tree_and_still_fails_the_golden_sky(self):
        self.assertTrue(grade(world(flat(BLUE)), CLEAR_BLUE)[0])
        passed, facts = grade(world(flat(BLUE)), GOLDEN)
        self.assertFalse(passed)
        self.assertEqual(facts["problems"], ["neither a low-sun Sky texture nor a warm background"])
        self.assertTrue(grade(world(flat(WARM)), GOLDEN)[0])
        self.assertFalse(grade(world(flat(GREY)), GOLDEN)[0])

    def test_strength_scales_what_is_shown_and_zero_is_black(self):
        facts = checks.world_facts(world(flat(BLUE, strength=2.0)), None)
        self.assertEqual(facts["horizon"]["color"], [0.36, 0.6, 1.2])
        self.assertEqual(facts["strength"], 2.0)
        self.assertFalse(grade(world(flat(BLUE, strength=0.0)), CLEAR_BLUE)[0])

    def test_nothing_linked_is_black_and_the_viewport_colour_never_counts(self):
        unlinked = world(None, color=(0.2, 0.4, 0.9))
        unlinked.node_tree.links.clear()
        facts = checks.world_facts(unlinked, None)
        self.assertEqual(facts["horizon"]["families"], {"grey": 1.0})
        self.assertFalse(grade(unlinked, CLEAR_BLUE)[0])
        self.assertFalse(grade(None if False else NS(name="None", node_tree=Tree(), color=(0.2, 0.4, 0.9)),
                               CLEAR_BLUE)[0])   # No output node: black too.
        self.assertTrue(grade(NS(name="Legacy", node_tree=None, color=(0.2, 0.4, 0.9)), CLEAR_BLUE)[0])
        self.assertFalse(grade(None, CLEAR_BLUE)[0])


class RampTests(unittest.TestCase):
    """Defect 1: a ramp was read as the average of its stops, whatever their positions."""

    def test_warm_horizon_to_blue_zenith_reads_warm_at_the_horizon(self):
        # The stops of dt-bridge-pyschema trial 1: their average is purple ("other"), the render warm then blue.
        sky = world(height_ramp((0.5, WARM), (0.62, BLUE)))
        facts = checks.world_facts(sky, "BLENDER_EEVEE")
        self.assertEqual(facts["horizon"]["families"], {"warm": 1.0})
        self.assertEqual(facts["zenith"]["families"], {"blue": 1.0})
        self.assertTrue(grade(sky, GOLDEN)[0])
        self.assertFalse(grade(sky, CLEAR_BLUE)[0])        # The horizon decides; it is warm.
        self.assertTrue(grade(sky, {"zenith_color": ["blue"]})[0])

    def test_the_same_stops_placed_elsewhere_read_differently(self):
        # Stop positions decide what is seen: warm only below the horizon leaves a blue sky.
        below = world(height_ramp((-0.2, WARM), (-0.05, BLUE)))
        self.assertEqual(checks.world_facts(below, None)["horizon"]["families"], {"blue": 1.0})
        self.assertFalse(grade(below, GOLDEN)[0])
        self.assertTrue(grade(below, CLEAR_BLUE)[0])

    def test_a_band_split_between_families_needs_half_its_directions(self):
        # Warm up to about 6 degrees, blue above: 2 of the horizon's 5 rings are warm.
        split = world(height_ramp((0.10, WARM), (0.1001, BLUE)))
        families = checks.world_facts(split, None)["horizon"]["families"]
        self.assertEqual(families, {"blue": 0.6, "warm": 0.4})
        self.assertFalse(grade(split, GOLDEN)[0])
        self.assertTrue(grade(split, CLEAR_BLUE)[0])
        self.assertTrue(grade(split, {"sky_or_color": ["warm", "blue"]})[0])


class MixTests(unittest.TestCase):
    """Defect 2: any other node (a Mix) returned nothing and the grader fell back to the viewport colour."""

    def test_a_mix_of_a_colour_and_a_ramp_is_evaluated_not_replaced_by_the_viewport_colour(self):
        # The shape of v-oneshot-tree-base trial 1: Mix(A colour, B ramp) by a factor from the height.
        def build(tree, background):
            coords, split = tree.add("TEX_COORD"), tree.add("SEPXYZ")
            scale = tree.add("MATH", operation="MULTIPLY", use_clamp=True)
            ramp = tree.add("VALTORGB", color_ramp=Ramp((0.0, (0.4, 0.62, 0.95, 1)), (0.55, (0.03, 0.16, 0.7, 1))))
            mix = colour_mix(tree)
            mix.inputs["A_Color"].default_value = (0.3, 0.4, 0.38, 1.0)
            scale.inputs["Value_001"].default_value = 4.0
            tree.link(coords, "Generated", split, "Vector")
            tree.link(split, "Z", scale, "Value")
            tree.link(split, "Z", ramp, "Fac")
            tree.link(scale, "Value", mix, "Factor_Float")
            tree.link(ramp, "Color", mix, "B_Color")
            tree.link(mix, "Result_Color", background, "Color")
        sky = world(build, color=(0.051, 0.051, 0.051))   # The viewport colour is the factory grey.
        facts = checks.world_facts(sky, "BLENDER_EEVEE")
        self.assertEqual(facts["horizon"]["families"], {"blue": 1.0})
        self.assertTrue(grade(sky, CLEAR_BLUE)[0])
        self.assertFalse(grade(sky, GOLDEN)[0])

    def test_light_path_shows_the_camera_branch(self):
        # Lighting from a warm colour, the camera sees blue: the camera's view is what is graded.
        def build(tree, background):
            output = tree.nodes[0]
            tree.links.clear()
            lit = tree.add("BACKGROUND", name="Lighting")
            lit.inputs["Color"].default_value = WARM
            background.inputs["Color"].default_value = BLUE
            path, mix = tree.add("LIGHT_PATH"), tree.add("MIX_SHADER")
            tree.link(path, "Is Camera Ray", mix, "Fac")
            tree.link(lit, "Background", mix, "Shader")
            tree.link(background, "Background", mix, "Shader_001")
            tree.link(mix, "Shader", output, "Surface")
        sky = world(build)
        self.assertTrue(grade(sky, CLEAR_BLUE)[0])
        self.assertFalse(grade(sky, GOLDEN)[0])

    def test_an_unreadable_node_is_inconclusive_never_passed(self):
        def build(tree, background):
            picture = tree.add("TEX_IMAGE", name="Clouds")
            tree.link(picture, "Color", background, "Color")
        passed, facts = grade(world(build), CLEAR_BLUE)
        self.assertFalse(passed)
        self.assertTrue(facts["inconclusive"])
        self.assertEqual(facts["horizon"]["families"], {"unknown": 1.0})
        self.assertIn("TEX_IMAGE 'Clouds'", facts["unread"])

    def test_a_ramp_after_an_unreadable_texture_is_judged_by_every_colour_it_can_show(self):
        def ramp(*stops):
            def build(tree, background):
                picture = tree.add("TEX_IMAGE", name="Clouds")
                node = tree.add("VALTORGB", color_ramp=Ramp(*stops))
                tree.link(picture, "Alpha", node, "Fac")
                tree.link(node, "Color", background, "Color")
            return world(build)
        self.assertTrue(grade(ramp((0.0, BLUE), (1.0, (0.3, 0.5, 0.9, 1))), CLEAR_BLUE)[0])   # Blue, whatever.
        self.assertFalse(grade(ramp((0.0, BLUE), (1.0, (0.3, 0.5, 0.9, 1))), GOLDEN)[0])
        self.assertNotIn("inconclusive", grade(ramp((0.0, BLUE), (1.0, (0.3, 0.5, 0.9, 1))), GOLDEN)[1])
        passed, facts = grade(ramp((0.0, WARM), (1.0, BLUE)), CLEAR_BLUE)                   # The image decides.
        self.assertEqual((passed, facts["inconclusive"]), (False, True))

    def test_a_noise_sky_is_computed_in_every_direction(self):
        def clouds(threshold):
            def build(tree, background):
                noise = tree.add("TEX_NOISE", name="Clouds")   # Its Vector is unlinked: the view direction.
                node = tree.add("VALTORGB", color_ramp=Ramp((threshold, WARM), (threshold + 0.0001, BLUE)))
                tree.link(noise, "Fac", node, "Fac")
                tree.link(node, "Color", background, "Color")
            return world(build)
        passed, facts = grade(clouds(0.1), CLEAR_BLUE)   # Noise above 0.1 nearly everywhere: blue.
        self.assertTrue(passed, facts)
        passed, facts = grade(clouds(0.9), CLEAR_BLUE)   # Below 0.9 nearly everywhere: warm, a measured fail.
        self.assertEqual((passed, "inconclusive" in facts), (False, False))


class CameraRelativeTests(unittest.TestCase):
    """A sky drawn in the camera's frame (Texture Coordinate's Window output) is read where the camera looks."""

    @staticmethod
    def window_sky(*stops):
        def build(tree, background):
            coords, split = tree.add("TEX_COORD"), tree.add("SEPXYZ")
            ramp = tree.add("VALTORGB", color_ramp=Ramp(*stops))
            tree.link(coords, "Window", split, "Vector")
            tree.link(split, "Y", ramp, "Fac")
            tree.link(ramp, "Color", background, "Color")
        return world(build)

    @staticmethod
    def level_camera(view):
        """A camera at the origin looking along +Y, its frame 30 degrees tall: None behind it."""
        x, y, z = view
        if y <= 1e-9:
            return None
        return {"Camera": (x, z, y), "Window": (0.5 + x / y, 0.5 + (z / y) / (2 * math.tan(math.radians(15))), 0.0)}

    def test_window_coordinates_follow_the_scene_camera(self):
        # Warm up to 95% of the frame's height, blue above: a level camera's horizon sits mid-frame, so the
        # directions it sees up to about 13.5 degrees ahead (less to the sides) are warm, overhead is blue.
        sky = self.window_sky((0.95, WARM), (0.9501, BLUE))
        facts = checks.world_facts(sky, "BLENDER_EEVEE", self.level_camera)
        self.assertEqual(facts["horizon"]["directions"], 15)   # Three of eight azimuths face the camera's way.
        self.assertEqual(facts["horizon"]["families"], {"blue": 0.267, "warm": 0.733})
        self.assertEqual(facts["zenith"]["families"], {"blue": 1.0})
        self.assertTrue(checks.band_verdict(facts, "horizon", ["warm"]))

    def test_without_a_camera_they_are_unknown(self):
        sky = self.window_sky((0.0, BLUE), (0.5, (0.3, 0.5, 0.9, 1)))
        self.assertTrue(grade(sky, CLEAR_BLUE)[0])          # Any position on this ramp is blue.
        passed, facts = grade(self.window_sky((0.0, WARM), (1.0, BLUE)), CLEAR_BLUE)
        self.assertEqual((passed, facts["inconclusive"]), (False, True))


class SkyTests(unittest.TestCase):
    """Defect 3: a Sky texture that exists but is not linked to the output counted as the sky."""

    @staticmethod
    def sky_world(elevation_deg, linked=True, background_colour=(0.6, 0.55, 0.55, 1.0)):
        def build(tree, background):
            background.inputs["Color"].default_value = background_colour
            sky = tree.add("TEX_SKY", name="Sky Texture", sky_type="MULTIPLE_SCATTERING",
                           sun_elevation=math.radians(elevation_deg))
            if linked:
                tree.link(sky, "Color", background, "Color")
        return world(build, name="Golden Hour Sky")

    def test_a_linked_sky_counts_with_its_sun_elevation(self):
        passed, facts = grade(self.sky_world(15.0), GOLDEN)
        self.assertTrue(passed)
        self.assertEqual(facts["sky"], {"type": "MULTIPLE_SCATTERING", "sun_elevation_deg": 15.0})
        self.assertIn("TEX_SKY 'Sky Texture'", facts["unread"])   # Its colour is not evaluated; the sun decides.
        self.assertFalse(grade(self.sky_world(60.0), GOLDEN)[0])

    def test_an_unconnected_sky_shows_nothing_and_the_flat_background_decides(self):
        # dt-ab-bridge-lean-context trial 2: a sky node beside a flat grey background rendered flat grey.
        passed, facts = grade(self.sky_world(15.0, linked=False), GOLDEN)
        self.assertFalse(passed)
        self.assertIsNone(facts["sky"])
        self.assertEqual(facts["horizon"]["families"], {"grey": 1.0})
        self.assertTrue(grade(self.sky_world(15.0, linked=False, background_colour=WARM), GOLDEN)[0])

    def test_a_muted_link_or_a_muted_node_cuts_the_sky_off(self):
        sky = self.sky_world(15.0)
        sky.node_tree.links[-1].is_muted = True
        self.assertIsNone(checks.world_facts(sky, None)["sky"])
        def build(tree, background):
            sky_node = tree.add("TEX_SKY", sky_type="MULTIPLE_SCATTERING", sun_elevation=math.radians(15.0))
            mix = colour_mix(tree, name="Muted mix")
            mix.mute = True
            mix.internal_links = [NS(from_socket=mix.inputs["A_Color"], to_socket=mix.outputs["Result_Color"])]
            mix.inputs["A_Color"].default_value = BLUE
            tree.link(sky_node, "Color", mix, "B_Color")
            tree.link(mix, "Result_Color", background, "Color")
        muted = world(build)
        facts = checks.world_facts(muted, None)
        self.assertIsNone(facts["sky"])                         # B never reaches a muted Mix's result.
        self.assertEqual(facts["horizon"]["families"], {"blue": 1.0})

    def test_only_the_output_the_renderer_uses_counts(self):
        sky = self.sky_world(15.0)
        tree = sky.node_tree
        tree.nodes[0].is_active_output = False
        other = tree.add("OUTPUT_WORLD", name="Flat output", is_active_output=True)
        flat_bg = tree.add("BACKGROUND", name="Flat")
        flat_bg.inputs["Color"].default_value = GREY
        tree.link(flat_bg, "Background", other, "Surface")
        self.assertFalse(grade(sky, GOLDEN)[0])                 # The active output shows flat grey.
        other.target = "CYCLES"                                 # An output for Cycles only ...
        self.assertTrue(grade(sky, GOLDEN, engine="BLENDER_EEVEE")[0])   # ... is not EEVEE's.
        self.assertFalse(grade(sky, GOLDEN, engine="CYCLES")[0])


class EvaluatorTests(unittest.TestCase):
    def test_coordinates_in_a_world_are_the_view_direction(self):
        view = wb.direction(30.0, 90.0)
        self.assertAlmostEqual(view[2], 0.5)
        self.assertAlmostEqual(view[1], math.cos(math.radians(30.0)))

        def build(tree, background):
            coords, split = tree.add("TEX_COORD"), tree.add("SEPXYZ")
            tree.link(coords, "Normal", split, "Vector")          # Normal is the reversed direction.
            tree.link(split, "Z", background, "Strength")
        graph = wb.WorldGraph(world(build), None)
        self.assertAlmostEqual(graph.radiance(view).rgb[0], -0.5 * 0.051, places=6)

    def test_math_map_range_and_gradient(self):
        def build(tree, background):
            gradient = tree.add("TEX_GRADIENT", gradient_type="EASING", texture_mapping=None)
            remap = tree.add("MAP_RANGE", data_type="FLOAT", interpolation_type="LINEAR", clamp=True)
            remap.inputs["To Min"].default_value, remap.inputs["To Max"].default_value = 0.0, 2.0
            tree.link(gradient, "Fac", remap, "Value")
            tree.link(remap, "Result", background, "Strength")
        graph = wb.WorldGraph(world(build), None)
        # The gradient's unlinked Vector is the direction: X = 1 along +X, eased to 1, remapped to 2.
        self.assertAlmostEqual(graph.radiance((1.0, 0.0, 0.0)).rgb[0], 2.0 * 0.051, places=6)
        self.assertAlmostEqual(graph.radiance((0.5, 0.0, 0.866)).rgb[0], 2.0 * 0.5 * 0.051, places=6)

    def test_a_cycle_or_runaway_graph_is_unreadable_not_a_crash(self):
        def build(tree, background):
            first, second = tree.add("MATH", operation="ADD", use_clamp=False), tree.add("MATH", operation="ADD",
                                                                                       use_clamp=False)
            tree.link(first, "Value", second, "Value")
            tree.link(second, "Value", first, "Value")
            tree.link(second, "Value", background, "Strength")
        with self.assertRaises(wb.Unreadable):   # A loop runs into the depth bound, never forever.
            wb.WorldGraph(world(build), None).radiance((1.0, 0.0, 0.0))


class SurveyWorldLookTests(unittest.TestCase):
    def test_a_grey_background_brightness_is_colour_times_strength_as_before(self):
        rule = {"grey": True, "brightness": [0.005, 0.35]}
        scene = NS(world=world(flat((0.2, 0.2, 0.2, 1.0), strength=0.5)), render=NS(engine="BLENDER_EEVEE"))
        passed, details = survey_checks._check_world_look(None, scene, None, rule)
        self.assertTrue(passed, details)
        self.assertEqual(details["brightness"], 0.1)
        scene.world = world(flat((0.2, 0.45, 0.9, 1.0)))   # The harness's blue start fails.
        self.assertFalse(survey_checks._check_world_look(None, scene, None, rule)[0])


if __name__ == "__main__":
    unittest.main()
