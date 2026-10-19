# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a World shows a camera, read as the renderer reads it (world_surface), on stand-in node graphs.

The same rules as the benchmark grader's world_background: the active output by engine, links the renderer
follows, a muted node's pass-through, the Sky type and sun elevation when a Sky reaches the Surface, else a flat
colour and its family. ``test_blender_script_api_notes.py`` runs the module on real Blender 5.2 worlds.
"""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import world_surface  # noqa: E402
from angelo_agent.script_after import notes, world_before  # noqa: E402
from angelo_agent.script_store import script_id  # noqa: E402


class Socket(SimpleNamespace):
    def __init__(self, identifier, value=None, **kwargs):
        super().__init__(identifier=identifier, default_value=value, is_unavailable=False, **kwargs)


class Sockets(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next(socket for socket in self if socket.identifier == key)
        return list.__getitem__(self, key)


class Node(SimpleNamespace):
    def __init__(self, name, type, inputs=(), outputs=(), **kwargs):
        defaults = dict(mute=False, label="", bl_idname="ShaderNode" + type.title(), internal_links=[])
        defaults.update(kwargs)
        super().__init__(name=name, type=type, inputs=Sockets(Socket(*row) for row in inputs),
                         outputs=Sockets(Socket(*row) for row in outputs), **defaults)


class Link(SimpleNamespace):
    def __init__(self, from_node, from_socket, to_node, to_socket, **kwargs):
        super().__init__(from_node=from_node, from_socket=from_node.outputs[from_socket], to_node=to_node,
                         to_socket=to_node.inputs[to_socket], **{"is_muted": False, "is_valid": True, **kwargs})


def output(name="World Output", target="ALL", active=True):
    return Node(name, "OUTPUT_WORLD", [("Surface",), ("Volume",)], target=target, is_active_output=active)


def background(name="Background", colour=(0.05, 0.05, 0.05, 1.0), strength=1.0):
    return Node(name, "BACKGROUND", [("Color", colour), ("Strength", strength), ("Weight", 0.0)], [("Background",)])


def sky(sky_type="MULTIPLE_SCATTERING", elevation_deg=5.0, rotation_deg=0.0, **settings):
    values = dict(sun_rotation=math.radians(rotation_deg), sun_size=math.radians(0.545), sun_intensity=1.0,
                  sun_disc=True, altitude=100.0, air_density=1.0, aerosol_density=1.0, ozone_density=1.0)
    values.update(settings)
    return Node("Sky Texture", "TEX_SKY", [("Vector",)], [("Color",)], sky_type=sky_type,
                sun_elevation=math.radians(elevation_deg), **values)


def world(nodes, links, colour=(0.05, 0.05, 0.05)):
    return SimpleNamespace(name="World", color=colour, node_tree=SimpleNamespace(nodes=nodes, links=links))


def scene(value, engine="BLENDER_EEVEE"):
    return SimpleNamespace(world=value, render=SimpleNamespace(engine=engine))


def default_world(colour=(0.05, 0.05, 0.05, 1.0)):
    out, bg = output(), background(colour=colour)
    return world([out, bg], [Link(bg, "Background", out, "Surface")]), out, bg


class Shows(unittest.TestCase):
    def test_the_default_world_shows_its_flat_grey(self):
        shown = world_surface.shows(default_world()[0], "BLENDER_EEVEE")
        self.assertEqual(shown, {"world": "World", "strength": 1.0, "shows": "COLOR", "color": [0.05, 0.05, 0.05],
                                 "family": "grey"})

    def test_a_linked_sky_shows_its_type_and_sun_elevation(self):
        value, out, bg = default_world()
        node = sky("MULTIPLE_SCATTERING", 5.0)
        value.node_tree.nodes.append(node)
        value.node_tree.links.append(Link(node, "Color", bg, "Color"))
        shown = world_surface.shows(value, "CYCLES")
        self.assertEqual((shown["shows"], shown["sky"]), ("SKY", {
            "type": "MULTIPLE_SCATTERING", "sun_bearing_deg": 0.0, "sun_elevation_deg": 5.0, "sun_size_deg": 0.545,
            "sun_disc": True, "sun_intensity": 1.0, "altitude": 100.0, "air_density": 1.0, "aerosol_density": 1.0,
            "ozone_density": 1.0}))

    def test_a_sky_that_reaches_nothing_shows_nothing_of_it(self):
        # A script that made the node but never linked it, or linked it through a muted link, still shows the colour.
        value, out, bg = default_world((0.5, 0.5, 0.5, 1.0))
        node = sky()
        value.node_tree.nodes.append(node)
        self.assertEqual(world_surface.shows(value)["shows"], "COLOR")
        value.node_tree.links.append(Link(node, "Color", bg, "Color", is_muted=True))
        shown = world_surface.shows(value)
        self.assertEqual((shown["shows"], shown["family"]), ("COLOR", "grey"))

    def test_the_output_targeting_the_engine_wins(self):
        out_all, out_cycles = output("All"), output("Cycles", target="CYCLES", active=False)
        warm, blue = background("Warm", (0.8, 0.3, 0.05, 1)), background("Blue", (0.05, 0.2, 0.8, 1))
        value = world([out_all, out_cycles, warm, blue], [Link(warm, "Background", out_all, "Surface"),
                                                          Link(blue, "Background", out_cycles, "Surface")])
        self.assertEqual(world_surface.shows(value, "CYCLES")["family"], "blue")
        self.assertEqual(world_surface.shows(value, "BLENDER_EEVEE")["family"], "warm")

    def test_nothing_linked_shows_nothing(self):
        out, bg = output(), background()
        self.assertEqual(world_surface.shows(world([out, bg], []))["shows"], "NOTHING")
        self.assertEqual(world_surface.shows(None), {"world": None, "shows": "NOTHING"})
        self.assertEqual(world_surface.shows(world([background()], []))["shows"], "NOTHING")  # No output at all.

    def test_an_rgb_node_is_flat_and_other_nodes_are_named(self):
        value, out, bg = default_world()
        rgb = Node("RGB", "RGB", outputs=[("Color", (0.9, 0.5, 0.1, 1))])
        value.node_tree.nodes.append(rgb)
        value.node_tree.links.append(Link(rgb, "Color", bg, "Color"))
        self.assertEqual(world_surface.shows(value)["family"], "warm")
        ramp = Node("Ramp", "VALTORGB", [("Fac", 0.5)], [("Color",)], label="Sky ramp")
        value.node_tree.nodes.append(ramp)
        value.node_tree.links[-1] = Link(ramp, "Color", bg, "Color")
        self.assertEqual(world_surface.shows(value)["color_from"], ["Background", "Sky ramp"])

    def test_a_muted_node_passes_only_its_internal_link(self):
        value, out, bg = default_world()
        node = sky()
        mix = Node("Mix", "MIX_SHADER", [("Fac", 0.5), ("A",), ("B",)], [("Shader",)], mute=True)
        mix.internal_links = [SimpleNamespace(from_socket=mix.inputs["A"])]
        value.node_tree.nodes += [node, mix]
        value.node_tree.links = [Link(mix, "Shader", out, "Surface"), Link(bg, "Background", mix, "A"),
                                 Link(node, "Color", mix, "B")]
        shown = world_surface.shows(value)
        self.assertEqual(shown["shows"], "COLOR")  # The sky feeds the muted node's other input: not rendered.

    def test_a_world_without_a_node_tree_shows_its_colour(self):
        value = SimpleNamespace(name="Old", color=(0.1, 0.3, 0.9), node_tree=None)
        self.assertEqual(world_surface.shows(value)["family"], "blue")

    def test_colour_families_are_the_graders_bands(self):
        for rgb, family in (((0.5, 0.5, 0.5), "grey"), ((0, 0, 0), "grey"), ((1, 0.55, 0.2), "warm"),
                            ((1, 0.1, 0.4), "warm"), ((0.2, 0.8, 0.2), "green"), ((0.1, 0.4, 1), "blue"),
                            ((0.6, 0.1, 0.9), "other"), ((0.5, 0.5, 0.55), "grey")):
            self.assertEqual(world_surface.colour_family(rgb), family, rgb)


class ScriptResults(unittest.TestCase):
    """A script's result says what the World shows only when the script changed it."""

    def test_a_changed_world_is_reported_and_an_unchanged_one_is_not(self):
        value, out, bg = default_world((0.5, 0.5, 0.5, 1.0))
        window = SimpleNamespace(scene=scene(value))
        before = world_before(window)
        self.assertNotIn("world", notes(window, {}, "pass", before))
        bg.inputs["Color"].default_value = (0.8, 0.4, 0.1, 1.0)
        reported = notes(window, {}, "pass", before)["world"]
        self.assertEqual((reported["shows"], reported["family"]), ("COLOR", "warm"))

    def test_a_silent_sky_fallback_reads_as_the_grey_it_left_with_a_note(self):
        # Regression: try: sky.sky_type = 'NISHITA' / except: a grey background; the reply claimed a physical sky.
        value, out, bg = default_world()
        window = SimpleNamespace(scene=scene(value))
        before = world_before(window)
        value.node_tree.nodes.append(sky())  # Made, then the enum raised before it was linked.
        bg.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
        code = "try:\n    sky.sky_type = 'NISHITA'\nexcept Exception:\n    bg.inputs[0].default_value = (.5, .5, .5, 1)\n"
        result = notes(window, {"success": True}, code, before)
        self.assertEqual((result["world"]["shows"], result["world"]["family"]), ("COLOR", "grey"))
        self.assertIn("NISHITA", result["api_notes"][0])

    def test_an_unreadable_world_leaves_the_result_as_it_was(self):
        window = SimpleNamespace(scene=SimpleNamespace(world=SimpleNamespace(name="W"), render=None))
        self.assertIsNone(world_before(window))
        # Only the script's own id (script_store), which needs no World.
        self.assertEqual(notes(window, {"success": True}, "pass", None), {"success": True, "script": script_id("pass")})


if __name__ == "__main__":
    unittest.main()
