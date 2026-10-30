# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Solid view shows what a material's graph shows: the derivation, the write rule and every writer that calls it.

Recorded (released 1.0.6, real-GUI test): "make the seat dark wood and paint the legs glossy red" turned the legs
(``material_principled``) red in the default Solid view but left the seat (``shader_nodes``, a texture through a
Color Ramp) Blender's pale grey, because only the Principled path set ``Material.diffuse_color``. The class: any path
that creates, rewrites or brings in a material and leaves the display values Solid view draws behind. The cases below
use neutral graphs and colours, none of them the recorded one.
"""

import ast
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import material_display as md  # noqa: E402
from angelo_agent import material_display_sync as sync  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


class Socket:
    def __init__(self, name, value=None, kind="VALUE", identifier=None):
        self.name, self.identifier, self.default_value, self.type = name, identifier or name, value, kind
        self.links, self.enabled = [], True

    @property
    def is_linked(self):
        return bool(self.links)


class Link:
    def __init__(self, from_node, from_socket, to_socket, muted=False):
        self.from_node, self.from_socket, self.to_socket = from_node, from_socket, to_socket
        self.is_muted, self.is_valid = muted, True


class Node:
    def __init__(self, kind, inputs=(), outputs=(), **settings):
        self.type, self.mute, self.internal_links = kind, False, []
        self.inputs, self.outputs = list(inputs), list(outputs)
        self.__dict__.update(settings)

    def input(self, key):
        return next(s for s in self.inputs if key in (s.identifier, s.name))

    def output(self, key):
        return next(s for s in self.outputs if key in (s.identifier, s.name))


class Tree:
    def __init__(self, *nodes):
        self.nodes = list(nodes)


def link(node, out, to_node, into):
    """Link ``node``'s output ``out`` to ``to_node``'s input ``into``."""
    source, target = node.output(out), to_node.input(into)
    target.links.append(Link(node, source, target))
    return target


def principled(colour=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.5, alpha=1.0):
    return Node("BSDF_PRINCIPLED", [Socket("Base Color", colour, "RGBA"), Socket("Metallic", metallic),
                                    Socket("Roughness", roughness), Socket("Alpha", alpha)],
                [Socket("BSDF", kind="SHADER")])


def output(target="ALL"):
    return Node("OUTPUT_MATERIAL", [Socket("Surface", kind="SHADER")], [], is_active_output=True, target=target)


def rgb(colour):
    return Node("RGB", [], [Socket("Color", tuple(colour) + (1.0,), "RGBA")])


def shaded(shader, out="BSDF", *extra):
    result = output()
    link(shader, out, result, "Surface")
    return Tree(result, shader, *extra)


class Element:
    def __init__(self, position, colour):
        self.position, self.color = position, tuple(colour) + (1.0,)


def ramp(stops, interpolation="LINEAR", fac=0.5):
    return Node("VALTORGB", [Socket("Fac", fac)], [Socket("Color", kind="RGBA"), Socket("Alpha")],
                color_ramp=type("Ramp", (), {"elements": [Element(*s) for s in stops], "interpolation": interpolation})())


def texture(kind="TEX_NOISE"):
    return Node(kind, [Socket("Scale", 5.0)], [Socket("Fac"), Socket("Color", kind="RGBA")])


def mix_rgb(a, b, factor, blend_type="MIX"):
    node = Node("MIX", [Socket("Factor", factor, identifier="Factor_Float"),
                        Socket("A", tuple(a) + (1.0,), "RGBA", "A_Color"), Socket("B", tuple(b) + (1.0,), "RGBA", "B_Color")],
                [Socket("Result", kind="RGBA", identifier="Result_Color")], data_type="RGBA", blend_type=blend_type,
                clamp_result=False)
    return node


class Near(unittest.TestCase):
    def near(self, found, expected, places=4):
        self.assertIsNotNone(found)
        if isinstance(expected, tuple):
            for a, b in zip(found, expected):
                self.assertAlmostEqual(a, b, places=places)
        else:
            self.assertAlmostEqual(found, expected, places=places)


class DeriveTests(Near):
    def test_an_unlinked_base_colour_and_its_values(self):
        shown = md.derive(shaded(principled((0.1, 0.4, 0.7, 1), metallic=0.3, roughness=0.2, alpha=0.8)))
        self.near(shown["color"], (0.1, 0.4, 0.7))
        self.near(shown["metallic"], 0.3)
        self.near(shown["roughness"], 0.2)
        self.near(shown["alpha"], 0.8)

    def test_an_rgb_node_into_base_colour(self):
        shader, colour = principled(), rgb((0.9, 0.2, 0.05))
        link(colour, "Color", shader, "Base Color")
        self.near(md.derive(shaded(shader, "BSDF", colour))["color"], (0.9, 0.2, 0.05))

    def test_a_ramp_at_its_fixed_factor_and_its_mean_under_a_texture(self):
        stops = [(0.0, (0.0, 0.0, 0.0)), (1.0, (1.0, 0.5, 0.0))]
        shader, fixed = principled(), ramp(stops, fac=0.25)
        link(fixed, "Color", shader, "Base Color")
        self.near(md.derive(shaded(shader, "BSDF", fixed))["color"], (0.25, 0.125, 0.0))
        shader, spread, noise = principled(), ramp([(0.2, (0.1, 0.1, 0.1)), (0.6, (0.5, 0.3, 0.1))]), texture()
        link(noise, "Fac", spread, "Fac")
        link(spread, "Color", shader, "Base Color")
        # Mean over 0..1: 0.2 of the first stop, the segment's midpoint over 0.4, 0.4 of the last stop.
        expected = tuple(0.2 * a + 0.4 * (a + b) / 2 + 0.4 * b for a, b in zip((0.1, 0.1, 0.1), (0.5, 0.3, 0.1)))
        self.near(md.derive(shaded(shader, "BSDF", spread, noise))["color"], expected)

    def test_a_constant_ramp_holds_each_stop(self):
        stops = [(0.0, (1.0, 0.0, 0.0)), (0.5, (0.0, 0.0, 1.0))]
        self.near(md.ramp_mean(ramp(stops, "CONSTANT").color_ramp)[:3], (0.5, 0.0, 0.5))
        self.near(md.ramp_at(ramp(stops, "CONSTANT").color_ramp, 0.4)[:3], (1.0, 0.0, 0.0))

    def test_mix_blends_by_type_and_factor(self):
        for blend_type, expected in (("MIX", (0.25, 0.0, 0.75)), ("MULTIPLY", (0.25, 0.0, 0.0)),
                                     ("ADD", (1.0, 0.0, 0.75))):
            shader, mixed = principled(), mix_rgb((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.75, blend_type)
            link(mixed, "Result_Color", shader, "Base Color")
            with self.subTest(blend_type=blend_type):
                self.near(md.derive(shaded(shader, "BSDF", mixed))["color"], expected)

    def test_a_texture_through_a_mix_factor_is_taken_at_its_mean(self):
        shader, mixed, noise = principled(), mix_rgb((0.2, 0.2, 0.2), (0.6, 0.4, 0.2), 0.0), texture("TEX_VORONOI")
        link(noise, "Fac", mixed, "Factor_Float")
        link(mixed, "Result_Color", shader, "Base Color")
        self.near(md.derive(shaded(shader, "BSDF", mixed, noise))["color"], (0.4, 0.3, 0.2))

    def test_a_checker_shows_the_mean_of_its_colours(self):
        checker = Node("TEX_CHECKER", [Socket("Color1", (1, 1, 1, 1), "RGBA"), Socket("Color2", (0, 0, 0, 1), "RGBA")],
                       [Socket("Color", kind="RGBA"), Socket("Fac")])
        shader = principled()
        link(checker, "Color", shader, "Base Color")
        self.near(md.derive(shaded(shader, "BSDF", checker))["color"], (0.5, 0.5, 0.5))

    def test_no_output_or_nothing_on_its_surface_is_unknown(self):
        self.assertEqual(md.derive(Tree(principled())), dict.fromkeys(md.FIELDS))
        self.assertEqual(md.derive(Tree(output(), principled())), dict.fromkeys(md.FIELDS))
        self.assertEqual(md.derive(None), dict.fromkeys(md.FIELDS))

    def test_a_cycle_ends_unknown(self):
        shader, mixed = principled(), mix_rgb((0.1, 0.1, 0.1), (0.2, 0.2, 0.2), 0.5)
        link(mixed, "Result_Color", mixed, "A_Color")   # Its own result into its first input.
        link(mixed, "Result_Color", shader, "Base Color")
        shown = md.derive(shaded(shader, "BSDF", mixed))
        self.assertIsNone(shown["color"])
        self.near(shown["roughness"], 0.5)   # What the cycle does not reach is still known.

    def test_a_cycle_through_fresh_wrappers_still_ends(self):
        """Blender hands out a new Python wrapper for a socket on every access: identity is the struct's pointer."""
        class Wrapped(Socket):
            def as_pointer(self):
                return self.pointer
        shader, mixed = principled(), mix_rgb((0.1, 0.1, 0.1), (0.2, 0.2, 0.2), 0.5)
        result = mixed.output("Result_Color")
        result.__class__, result.pointer = Wrapped, 4242

        class FreshLink(Link):   # Every read of from_socket is another wrapper of the same output.
            @property
            def from_socket(self):
                fresh = Wrapped("Result", kind="RGBA", identifier="Result_Color")
                fresh.pointer = 4242
                return fresh

            @from_socket.setter
            def from_socket(self, value):
                pass
        mixed.input("A_Color").links.append(FreshLink(mixed, result, mixed.input("A_Color")))
        link(mixed, "Result_Color", shader, "Base Color")
        walk = md._Walk(None)
        self.assertIsNone(walk.surface(shaded(shader, "BSDF", mixed))["color"])
        self.assertLess(walk.visits, 12)   # Caught as a cycle at once, not only by the depth bound.

    def test_a_long_chain_is_bounded(self):
        nodes, previous = [], None
        for index in range(2000):
            node = Node("MATH", [Socket("Value", 0.5), Socket("Value_001", 0.0)], [Socket("Value")], operation="ADD",
                        use_clamp=False)
            if previous is not None:
                link(previous, "Value", node, "Value")
            nodes.append(previous := node)
        shader = principled()
        link(previous, "Value", shader, "Roughness")
        self.assertIsNone(md.derive(shaded(shader, "BSDF", *nodes))["roughness"])

    def test_emission_and_metallic_shaders(self):
        emission = Node("EMISSION", [Socket("Color", (1.0, 0.6, 0.1, 1), "RGBA"), Socket("Strength", 3.0)],
                        [Socket("Emission", kind="SHADER")])
        shown = md.derive(shaded(emission, "Emission"))
        self.near(shown["color"], (1.0, 0.6, 0.1))
        self.assertIsNone(shown["metallic"])
        metal = Node("BSDF_METALLIC", [Socket("Base Color", (0.9, 0.7, 0.3, 1), "RGBA"), Socket("Roughness", 0.15)],
                     [Socket("BSDF", kind="SHADER")])
        shown = md.derive(shaded(metal))
        self.near((shown["metallic"], shown["roughness"]), (1.0, 0.15))
        diffuse = Node("BSDF_DIFFUSE", [Socket("Color", (0.3, 0.3, 0.8, 1), "RGBA"), Socket("Roughness", 0.0)],
                       [Socket("BSDF", kind="SHADER")])
        self.near((md.derive(shaded(diffuse))["metallic"], md.derive(shaded(diffuse))["roughness"]), (0.0, 1.0))

    def test_a_mix_shader_weighs_its_shaders(self):
        red = principled((1, 0, 0, 1), roughness=0.2)
        blue = Node("BSDF_DIFFUSE", [Socket("Color", (0, 0, 1, 1), "RGBA")], [Socket("BSDF", kind="SHADER")])
        mixed = Node("MIX_SHADER", [Socket("Fac", 0.25), Socket("Shader", kind="SHADER"),
                                    Socket("Shader_001", kind="SHADER")], [Socket("Shader", kind="SHADER")])
        link(red, "BSDF", mixed, "Shader")
        link(blue, "BSDF", mixed, "Shader_001")
        shown = md.derive(shaded(mixed, "Shader", red, blue))
        self.near(shown["color"], (0.75, 0.0, 0.25))
        self.near(shown["roughness"], 0.75 * 0.2 + 0.25 * 1.0)
        self.near(shown["alpha"], 1.0)   # Only the Principled side states an alpha.

    def test_transparent_mixed_in_lowers_alpha(self):
        solid, clear = principled((0, 1, 0, 1)), Node("BSDF_TRANSPARENT", [Socket("Color", (1, 1, 1, 1), "RGBA")],
                                                      [Socket("BSDF", kind="SHADER")])
        mixed = Node("MIX_SHADER", [Socket("Fac", 0.5), Socket("Shader", kind="SHADER"),
                                    Socket("Shader_001", kind="SHADER")], [Socket("Shader", kind="SHADER")])
        link(solid, "BSDF", mixed, "Shader")
        link(clear, "BSDF", mixed, "Shader_001")
        shown = md.derive(shaded(mixed, "Shader", solid, clear))
        self.near(shown["alpha"], 0.5)
        self.near(shown["color"], (0.0, 1.0, 0.0))

    def test_reroutes_muted_nodes_and_groups_are_followed(self):
        shader, colour = principled(), rgb((0.3, 0.6, 0.9))
        reroute = Node("REROUTE", [Socket("Input", kind="RGBA")], [Socket("Output", kind="RGBA")])
        link(colour, "Color", reroute, "Input")
        link(reroute, "Output", shader, "Base Color")
        self.near(md.derive(shaded(shader, "BSDF", colour, reroute))["color"], (0.3, 0.6, 0.9))
        shader, muted = principled(), mix_rgb((0.1, 0.2, 0.3), (1, 1, 1), 1.0)
        muted.mute = True
        muted.internal_links = [Link(muted, muted.input("A_Color"), muted.output("Result_Color"))]
        link(muted, "Result_Color", shader, "Base Color")
        self.near(md.derive(shaded(shader, "BSDF", muted))["color"], (0.1, 0.2, 0.3))
        # A group whose output passes its input through: the group node's own input value.
        inner_in = Node("GROUP_INPUT", [], [Socket("Tint", kind="RGBA")])
        inner_out = Node("GROUP_OUTPUT", [Socket("Tint", kind="RGBA")], [], is_active_output=True)
        link(inner_in, "Tint", inner_out, "Tint")
        group = Node("GROUP", [Socket("Tint", (0.7, 0.1, 0.4, 1), "RGBA")], [Socket("Tint", kind="RGBA")],
                     node_tree=Tree(inner_in, inner_out))
        shader = principled()
        link(group, "Tint", shader, "Base Color")
        self.near(md.derive(shaded(shader, "BSDF", group))["color"], (0.7, 0.1, 0.4))

    def test_an_image_needs_a_reader(self):
        image = object()
        node = Node("TEX_IMAGE", [], [Socket("Color", kind="RGBA"), Socket("Alpha")], image=image)
        shader = principled()
        link(node, "Color", shader, "Base Color")
        tree = shaded(shader, "BSDF", node)
        self.assertIsNone(md.derive(tree)["color"])
        self.near(md.derive(tree, lambda found: (0.2, 0.3, 0.4) if found is image else None)["color"], (0.2, 0.3, 0.4))

    def test_an_eevee_output_wins_over_the_active_all_one(self):
        every, eevee = output("ALL"), output("EEVEE")
        red, green = principled((1, 0, 0, 1)), principled((0, 1, 0, 1))
        link(red, "BSDF", every, "Surface")
        link(green, "BSDF", eevee, "Surface")
        self.near(md.derive(Tree(every, red, eevee, green))["color"], (0.0, 1.0, 0.0))

    def test_the_eevee_output_wins_over_a_cycles_one(self):
        cycles, eevee = output("CYCLES"), output("EEVEE")
        red, green = principled((1, 0, 0, 1)), principled((0, 1, 0, 1))
        link(red, "BSDF", cycles, "Surface")
        link(green, "BSDF", eevee, "Surface")
        self.near(md.derive(Tree(cycles, red, eevee, green))["color"], (0.0, 1.0, 0.0))


class Material:
    """A material's display values and graph, as ``material_display_sync`` reads and writes them."""
    _uid = [1]

    def __init__(self, tree=None, colour=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.4, library=None, users=1):
        self.node_tree, self.diffuse_color, self.metallic, self.roughness = tree, colour, metallic, roughness
        self.library, self.users, self.session_uid = library, users, self._uid[0]
        self._uid[0] += 1


def blender(materials):
    """A ``bpy`` stand-in whose ``data.materials`` is ``materials``."""
    return type("Bpy", (), {"data": type("Data", (), {"materials": materials})()})()


class SyncTests(Near):
    def test_created_writes_every_known_value(self):
        material = Material(shaded(principled((0.2, 0.3, 0.4, 1), metallic=1.0, roughness=0.1)))
        self.assertEqual(sorted(sync.sync(material, sync.CREATED)), ["color", "metallic", "roughness"])
        self.near(tuple(material.diffuse_color), (0.2, 0.3, 0.4, 1.0))
        self.near((material.metallic, material.roughness), (1.0, 0.1))

    def test_an_edit_moves_only_what_its_result_changed(self):
        shader = principled((0.9, 0.1, 0.1, 1), roughness=0.5)
        material = Material(shaded(shader), colour=(0.3, 0.3, 0.9, 1.0))   # The person's own Solid colour.
        before = sync.derived(material)
        shader.input("Roughness").default_value = 0.05
        self.assertEqual(sync.sync(material, sync.EDITED, before=before), ["roughness"])
        self.near(tuple(material.diffuse_color), (0.3, 0.3, 0.9, 1.0))   # Kept: the edit did not change the colour.
        before = sync.derived(material)
        shader.input("Base Color").default_value = (0.1, 0.8, 0.1, 1)
        self.assertEqual(sync.sync(material, sync.EDITED, before=before), ["color"])
        self.near(tuple(material.diffuse_color), (0.1, 0.8, 0.1, 1.0))

    def test_an_import_fills_only_blender_defaults(self):
        material = Material(shaded(principled((0.1, 0.2, 0.3, 1), roughness=0.9)), colour=(0.5, 0.5, 0.5, 1.0))
        self.assertEqual(sync.sync(material, sync.ADDED), ["roughness"])
        self.near(tuple(material.diffuse_color), (0.5, 0.5, 0.5, 1.0))

    def test_unknown_values_and_library_materials_are_left(self):
        image = Node("TEX_IMAGE", [], [Socket("Color", kind="RGBA")], image=None)
        shader = principled()
        link(image, "Color", shader, "Base Color")
        material = Material(shaded(shader, "BSDF", image))
        self.assertEqual(sync.sync(material, sync.CREATED), ["roughness"])
        self.near(tuple(material.diffuse_color), (0.8, 0.8, 0.8, 1.0))
        linked = Material(shaded(principled((1, 0, 0, 1))), library=object())
        self.assertEqual(sync.sync(linked, sync.CREATED), [])

    def test_a_script_pass_follows_what_the_script_made_or_changed_and_nothing_else(self):
        stale = Material(shaded(principled((0.0, 0.0, 1.0, 1))))   # Never synced, and never touched by the script.
        edited_shader = principled((1.0, 0.0, 0.0, 1), roughness=0.5)
        edited = Material(shaded(edited_shader), colour=(1.0, 0.0, 0.0, 1.0), roughness=0.5)
        materials = [stale, edited]
        script = sync.ScriptPass(blender(materials))
        edited_shader.input("Base Color").default_value = (0.0, 1.0, 0.0, 1)
        edited_shader.input("Roughness").default_value = 0.1
        edited.roughness = 0.7   # The script set this display value itself.
        made = Material(shaded(principled((0.5, 0.25, 0.0, 1))))
        materials.append(made)
        self.assertEqual(script.finish(blender(materials)), 2)
        self.near(tuple(stale.diffuse_color), (0.8, 0.8, 0.8, 1.0))
        self.near(tuple(edited.diffuse_color), (0.0, 1.0, 0.0, 1.0))
        self.near(edited.roughness, 0.7)
        self.near(tuple(made.diffuse_color), (0.5, 0.25, 0.0, 1.0))

    def test_a_freed_material_never_fails_the_script(self):
        class Freed(Material):
            @property
            def node_tree(self):
                raise ReferenceError("freed")

            @node_tree.setter
            def node_tree(self, value):
                pass
        freed = Freed()
        script = sync.ScriptPass(blender([]))
        self.assertEqual(script.finish(blender([freed])), 0)
        # A module with no data (a stand-in) reads and writes nothing, and never raises.
        self.assertEqual(sync.ScriptPass(object()).finish(object()), 0)

    def textured(self, image):
        texture = Node("TEX_IMAGE", [], [Socket("Color", kind="RGBA")], image=image)
        shader = principled()
        link(texture, "Color", shader, "Base Color")
        return Material(shaded(shader, "BSDF", texture))

    def test_a_script_pass_reads_no_image(self):
        """It runs before and after every script: reading images there cost 12 s each way with 128 materials over
        eight 2048 x 2048 textures (measured, material_display_sync's docstring)."""
        image = Image([1.0] * 8)
        materials = [self.textured(image) for _ in range(3)]
        with mock.patch.object(sync, "image_mean", side_effect=AssertionError("an image was read")) as read:
            script = sync.ScriptPass(blender(materials))
            materials.append(self.textured(image))   # One the script made.
            script.finish(blender(materials))
        read.assert_not_called()
        self.near(tuple(materials[-1].diffuse_color), (0.8, 0.8, 0.8, 1.0))   # Image-driven: unknown, left.

    def test_an_import_reads_each_image_once_however_many_materials_use_it(self):
        image = Image([1.0] * 8)
        materials = [self.textured(image) for _ in range(5)]
        shared = {}
        with mock.patch.object(sync, "image_mean", wraps=sync.image_mean) as read:
            for material in materials:
                sync.sync(material, sync.ADDED, images=sync.LOAD, read=shared)
        self.assertEqual(read.call_count, 1)
        for material in materials:
            self.near(tuple(material.diffuse_color), (1.0, 1.0, 1.0, 1.0))


class Pixels:
    def __init__(self, values):
        self.values = values

    def foreach_get(self, out):
        out[:] = self.values


class Image:
    def __init__(self, values, size=(2, 1), has_data=True, is_float=False, space="sRGB"):
        self.pixels, self.size, self.channels, self.has_data, self.is_float = Pixels(values), size, 4, has_data, is_float
        self.colorspace_settings = type("Space", (), {"name": space})()


class ImageTests(Near):
    def setUp(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.fail("NumPy is a host-suite dependency (CI installs it)")

    def test_a_loaded_image_is_averaged_in_scene_linear(self):
        image = Image([1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0])
        self.near(sync.image_mean(image), (0.5, 0.5, 0.5))
        image = Image([0.5, 0.5, 0.5, 1.0, 0.5, 0.5, 0.5, 1.0])
        self.near(sync.image_mean(image), (0.21404,) * 3, places=4)   # sRGB 0.5 in linear.
        self.near(sync.image_mean(Image([0.5] * 8, space="Non-Color")), (0.5, 0.5, 0.5))

    def test_unloaded_or_large_images_are_not_read(self):
        self.assertIsNone(sync.image_mean(Image([1.0] * 8, has_data=False)))
        self.near(sync.image_mean(Image([1.0] * 8, has_data=False), load=True), (1.0, 1.0, 1.0))
        self.assertIsNone(sync.image_mean(Image([1.0] * 8, size=(4096, 4096))))
        self.assertIsNone(sync.image_mean(None))


def _calls(path):
    """The dotted names this module calls, e.g. ``display.sync``."""
    tree = ast.parse((ADDON / path).read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts, func = [], node.func
            while isinstance(func, ast.Attribute):
                parts.append(func.attr)
                func = func.value
            if isinstance(func, ast.Name):
                parts.append(func.id)
            names.append(".".join(reversed(parts)))
    return names


# Modules that bring materials in without a sync, and why (each a documented exemption, never silence).
NO_SYNC = {
    "worker_merge.py": "appends a worker's parts, whose materials that worker's own typed edits and scripts synced",
    "program_stage_bundle.py": "moves a stage between processes; it creates no material",
    "headless_preferences.py": "reads the person's preferences file; it brings no data in",
    "worker_part_counts.py": "lists a worker file's names; nothing is loaded",
    "script_rename_watch.py": "wraps a script's own libraries.load to watch names; the script pass syncs what it brings",
}


class WriterTests(unittest.TestCase):
    def test_every_material_writer_syncs(self):
        expected = {
            "action_nodes.py": ["display.sync", "display.derived"],
            "node_edit.py": ["display.sync", "display.derived"],
            "worker_materials.py": ["display.sync", "display.derived"],
            "blender_tools.py": ["ScriptPass", "display.finish"],
            "studio_assets_mesh.py": ["material_display_sync.sync"],
            "studio_assets_convert.py": ["material_display_sync.sync"],
            "media_place.py": ["display.sync"],
            "studio_host.py": ["display.known", "display.sync_added"],
            "studio_asset_ops.py": ["display.known", "display.sync_added"],
            "team_library_import.py": ["display.known", "display.sync_added"],
            "asset_start.py": ["display.sync"],
        }
        for path, names in expected.items():
            calls = _calls(path)
            for name in names:
                with self.subTest(module=path, call=name):
                    self.assertIn(name, calls)

    def test_new_materials_and_imports_are_all_covered(self):
        """A module that makes a material or imports/appends a file syncs it, or is a documented exemption."""
        for path in sorted(ADDON.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            writes = any(name.endswith(("materials.new", "libraries.load", "wm.obj_import", "wm.usd_import"))
                         or "import_scene." in name for name in _calls(path.name))
            if not writes or path.name == "material_display_sync.py":
                continue
            with self.subTest(module=path.name):
                self.assertTrue("material_display_sync" in text or path.name in NO_SYNC,
                                "%s makes or imports materials without material_display_sync" % path.name)

    def test_display_values_are_written_only_by_the_sync(self):
        for path in sorted(ADDON.glob("*.py")):
            if path.name == "material_display_sync.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                targets = node.targets if isinstance(node, ast.Assign) else []
                for target in targets:
                    with self.subTest(module=path.name, line=node.lineno):
                        self.assertFalse(isinstance(target, ast.Attribute) and target.attr == "diffuse_color",
                                         "write Solid-view values through material_display_sync.sync")

    def test_the_asset_writer_imports_the_sync_as_a_plain_module(self):
        """studio_assets_convert runs outside the add-on package, importing it beside itself."""
        import subprocess
        code = "import sys; sys.path.insert(0, %r); import material_display_sync as m; print(m.CREATED)" % str(ADDON)
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "CREATED"), done.stderr)

    def test_the_principled_path_no_longer_copies_the_stated_colour(self):
        source = (ADDON / "action_nodes.py").read_text(encoding="utf-8")
        self.assertNotIn("material.diffuse_color", source)


if __name__ == "__main__":
    unittest.main()
