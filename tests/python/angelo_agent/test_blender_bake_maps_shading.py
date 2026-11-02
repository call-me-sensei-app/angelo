# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake every map at once: node-tree shapes and scene settings that change what Cycles shades.

Run through ``run_native_suites.py --include test_blender_bake_maps_shading`` or an isolated Angelo with
--background --factory-startup --python-exit-code 1 --python <this file> -- <receipt.json>. Real Cycles CPU bakes.

Written after an independent review of the bake (2026-10-31), each case against a reference the bake's graph
does not produce: closed forms from block images, and Cycles' own Emit and Roughness bakes of the untouched
material. Covered: a scene's film exposure and sample clamping change no baked value; shaders passed into a node
group (and through a nested one) are read as the closures they are; a colour linked straight into the Surface
socket is emission, as Cycles makes it; a Principled BSDF's Alpha weighs its emission; a colour linked into a
float input is read as Angelo converts it (luminance), not as its red channel; an OpenEXR set is read back by
Angelo's own OpenEXR reader (colour with alpha premultiplied); and a bake that wrote nothing is told apart from
one that did.
"""

import json
import os
import sys
from pathlib import Path
import unittest

import bpy
import numpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_maps_fixtures as fx  # noqa: E402
from bake_maps_fixtures import N, Tree, principled  # noqa: E402
from test_blender_bake_maps_correctness import RESULTS, Case  # noqa: E402

# Angelo's colour-to-float conversion: the scene-linear (Rec. 709) luminance.
LUMINANCE = numpy.array([0.2126, 0.7152, 0.0722], dtype=numpy.float32)


def mixer_group(name):
    """A group mixing two shader inputs by a factor input: the shape of layer and blend groups."""
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    for socket_name, kind in (("Fac", "NodeSocketFloat"), ("A", "NodeSocketShader"), ("B", "NodeSocketShader")):
        group.interface.new_socket(socket_name, in_out='INPUT', socket_type=kind)
    group.interface.new_socket("Shader", in_out='OUTPUT', socket_type='NodeSocketShader')
    inputs, outputs = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    mix = group.nodes.new("ShaderNodeMixShader")
    for index in range(3):
        group.links.new(inputs.outputs[index], mix.inputs[index])
    group.links.new(mix.outputs[0], outputs.inputs[0])
    return group


class ShadingCases(Case):
    group = "HELD_OUT"

    def test_film_exposure_and_clamping_change_nothing(self):
        scene = bpy.context.scene
        scene.cycles.film_exposure, scene.cycles.sample_clamp_direct = 2.5, 0.4
        base, glow = fx.blocks(self.rng), fx.blocks(self.rng)
        target = fx.plane("Lamp")
        tree = Tree("LampMat")
        target.data.materials.append(tree.surface(principled(
            tree, **{"Base Color": tree.texture(fx.image_of("B", base)),
                     "Emission Color": tree.texture(fx.image_of("E", glow)), "Emission Strength": 3.0})))
        result, = self.run_set([target], format="OPEN_EXR")
        inside = fx.interior()
        self.check("base", self.channel(result, "BASE_COLOR", "OPEN_EXR"), base, inside)
        self.check("emission", self.channel(result, "EMISSION", "OPEN_EXR"), glow * 3.0, inside)
        self.assertAlmostEqual(scene.cycles.film_exposure, 2.5, places=6)   # The person's own, put back.
        self.assertAlmostEqual(scene.cycles.sample_clamp_direct, 0.4, places=6)

    def test_shaders_passed_into_nested_groups(self):
        a, mask = fx.blocks(self.rng), fx.blocks(self.rng, 1)
        inner = mixer_group("Mixer")
        outer = bpy.data.node_groups.new("Layer", "ShaderNodeTree")   # Passes its shaders on to the mixer.
        for socket_name, kind in (("Fac", "NodeSocketFloat"), ("A", "NodeSocketShader"), ("B", "NodeSocketShader")):
            outer.interface.new_socket(socket_name, in_out='INPUT', socket_type=kind)
        outer.interface.new_socket("Shader", in_out='OUTPUT', socket_type='NodeSocketShader')
        inputs, outputs = outer.nodes.new("NodeGroupInput"), outer.nodes.new("NodeGroupOutput")
        nested = outer.nodes.new("ShaderNodeGroup")
        nested.node_tree = inner
        for index in range(3):
            outer.links.new(inputs.outputs[index], nested.inputs[index])
        outer.links.new(nested.outputs[0], outputs.inputs[0])
        target = fx.plane("Layered")
        tree = Tree("LayeredMat")
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = outer
        tree.links.new(tree.texture(fx.image_of("K", mask)), node.inputs[0])
        tree.links.new(principled(tree, **{"Base Color": tree.texture(fx.image_of("A", a)), "Roughness": 0.9}),
                       node.inputs[1])
        tree.links.new(principled(tree, **{"Base Color": (0.1, 0.2, 0.7, 1.0), "Metallic": 1.0,
                                           "Roughness": 0.2}), node.inputs[2])
        target.data.materials.append(tree.surface(node.outputs[0]))
        result, = self.run_set([target])
        inside = fx.interior()
        self.check("base", self.channel(result, "BASE_COLOR"), fx.lerp(mask, a, numpy.array([0.1, 0.2, 0.7])), inside)
        self.check("metallic", self.channel(result, "METALLIC")[..., :1], fx.lerp(mask, 0.0, 1.0), inside)
        self.check("roughness", self.channel(result, "ROUGHNESS")[..., :1], fx.lerp(mask, 0.9, 0.2), inside)

    def test_a_colour_linked_into_the_surface_is_emission(self):
        glow = fx.blocks(self.rng)
        target = fx.plane("Unlit")
        tree = Tree("UnlitMat")
        target.data.materials.append(tree.surface(tree.texture(fx.image_of("U", glow))))
        result, = self.run_set([target])
        self.assertIn("EMISSION", result[0].channels)
        self.check("emission", self.channel(result, "EMISSION"), glow, fx.interior())
        self.check("emission_vs_emit_bake", self.channel(result, "EMISSION"), fx.reference(target, "EMIT"),
                   mean=3e-3, p99=2e-2)

    def test_principled_alpha_weighs_its_emission(self):
        alpha = fx.blocks(self.rng, 1)
        target = fx.plane("Glass")
        tree = Tree("GlassMat")
        target.data.materials.append(tree.surface(principled(
            tree, **{"Alpha": tree.texture(fx.image_of("A", alpha)), "Emission Color": (1.0, 0.5, 0.2, 1.0),
                     "Emission Strength": 0.8})))
        result, = self.run_set([target])
        expected = alpha * numpy.array([1.0, 0.5, 0.2]) * 0.8
        self.check("emission", self.channel(result, "EMISSION"), expected, fx.interior())
        self.check("emission_vs_emit_bake", self.channel(result, "EMISSION"), fx.reference(target, "EMIT"),
                   mean=3e-3, p99=2e-2)

    def test_a_colour_in_a_float_input_is_its_luminance(self):
        colour = fx.blocks(self.rng)
        target = fx.plane("Tinted")
        tree = Tree("TintedMat")
        texture = tree.texture(fx.image_of("C", colour))
        target.data.materials.append(tree.surface(principled(tree, Roughness=texture, Metallic=texture)))
        result, = self.run_set([target])
        expected = (colour @ LUMINANCE)[..., None]
        self.check("metallic", self.channel(result, "METALLIC")[..., :1], expected, fx.interior(), largest=2e-3)
        self.check("roughness_vs_roughness_bake", self.channel(result, "ROUGHNESS")[..., :1],
                   fx.reference(target, "ROUGHNESS")[..., :1], mean=3e-3, p99=2e-2)

    def test_openexr_files_read_back_by_angelo(self):
        base, alpha = fx.blocks(self.rng), fx.blocks(self.rng, 1)
        target = fx.plane("Decal")
        tree = Tree("DecalMat")
        target.data.materials.append(tree.surface(principled(
            tree, **{"Base Color": tree.texture(fx.image_of("B", base)),
                     "Alpha": tree.texture(fx.image_of("A", alpha))})))
        result, = self.run_set([target], packing="GLTF", format="OPEN_EXR", depth=32)
        entries = result[1]
        stored = fx.read_file(self.folder / next(e["file"] for e in entries if e["map"] == "BaseColor"))
        inside = fx.interior() & (alpha[..., 0] > 0.05)
        # An EXR's RGBA is premultiplied: Angelo reads back colour times alpha, and alpha itself.
        self.check("exr_alpha", stored[..., 3:4], alpha, fx.interior(), largest=1e-6)
        self.check("exr_premultiplied_colour", stored[..., :3], base * alpha, inside, largest=1e-5)

    def test_ao_in_a_packed_bake_matches_cycles_ao_bake(self):
        """AO now comes from Cycles' AO node inside a packed Emit bake: against Cycles' own AO bake of the plane under
        a block resting on it (64 AO rays a texel both sides; the two differ by sampling noise only)."""
        target = fx.plane("Floor")
        tree = Tree("FloorMat")
        target.data.materials.append(tree.surface(principled(tree, Roughness=0.5)))
        bpy.ops.mesh.primitive_cube_add(size=0.8, location=(0.2, -0.1, 0.4))
        bpy.context.scene.world = bpy.data.worlds.new("World")
        result, = self.run_set([target], packing="ORM")
        plan = result[0]
        self.assertFalse(any(step.bake_type == "AO" for step in plan.steps), "AO rides in a packed Emit bake")
        self.assertEqual([step.key for step in plan.steps], [("AO",)], "a constant material folds; only AO bakes")
        native = fx.reference(target, "AO", samples=64)[..., :1]
        self.check("ao_vs_ao_bake", self.channel(result, "AO")[..., :1], native, mean=0.02, p99=0.15)

    def test_a_bake_that_wrote_nothing_is_told_apart(self):
        from angelo_agent import bake_maps_plan, bake_maps_steps
        target = fx.plane("Quiet")
        tree = Tree("QuietMat")
        target.data.materials.append(tree.surface(principled(
            tree, **{"Base Color": tree.nodes.new("ShaderNodeTexNoise").outputs["Color"]})))
        request = dict(fx_request(), resolution=[16, 16])
        plan = bake_maps_plan.plan(target, [], None, request)
        notes = bake_maps_steps.new_notes()
        run = bake_maps_steps.StepRun(plan, plan.steps[0], (16, 16), notes)
        try:
            run.prepare()
            self.assertFalse(run.wrote(), "a scratch image no bake wrote reads as written")
            with bpy.context.temp_override(active_object=target, object=target, selected_objects=[target],
                                           selected_editable_objects=[target]):
                bpy.ops.object.bake(**run.operator_properties(request, 0))
            self.assertTrue(run.wrote())
        finally:
            run.undo()
        self.assertFalse(any(image.name.startswith("Angelo Bake") for image in bpy.data.images))


def fx_request():
    from test_blender_bake_maps_correctness import REQUEST
    return dict(REQUEST, extrusion=0.0, ray_distance=0.0)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "bake-maps-shading.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ShadingCases))
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "numbers": RESULTS["HELD_OUT"], "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("Texture-set shading checks failed; see the receipt and process log")
