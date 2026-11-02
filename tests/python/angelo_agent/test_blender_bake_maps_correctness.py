# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake every map at once: the baked numbers against references the bake does not produce itself.

Run through ``run_native_suites.py --include test_blender_bake_maps_correctness``
or an isolated Angelo with --background --factory-startup --python-exit-code 1
--python <this file> -- <receipt.json>. Real Cycles CPU bakes at 64 pixels.

Each channel is checked against a reference made another way than the bake's
emission graph: Cycles' own Diffuse-colour, Roughness and Emit bakes of the
untouched material, a film-transparent render for alpha, and closed forms from
the block images a material reads (``bake_maps_fixtures``) for metallic, mixes,
groups, UDIM tiles, displacement height and the high-to-low distance between
two spheres. The written files are read back by Angelo and compared with the
values they encode, per packing preset; the person's materials must be exactly
as they were and nothing temporary may stay. ``DEV`` cases were used while the
bake was developed; ``HELD_OUT`` cases never were, and their numbers are kept
apart in the receipt.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

import bpy
import numpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_maps_fixtures as fx  # noqa: E402
from bake_maps_fixtures import N, Tree, principled  # noqa: E402
from angelo_agent import bake_maps_encode, bake_maps_pack, bake_maps_write  # noqa: E402
from angelo_agent.bake_maps_plan import HIGH_POSITION, LOW_NORMAL, LOW_POSITION  # noqa: E402

RESULTS = {"DEV": {}, "HELD_OUT": {}}
FLOAT_TOL, NATIVE_MEAN, NATIVE_P99 = 2e-3, 3e-3, 2e-2
REQUEST = {"resolution": [N, N], "margin": 0, "packing": "SEPARATE", "format": "PNG", "output": "maps"}


class Case(unittest.TestCase):
    group = "DEV"

    def setUp(self):
        fx.scene_setup()
        self.rng = numpy.random.default_rng(zlib.crc32(self.id().encode()))   # The same blocks on every run.
        self.folder = Path(tempfile.mkdtemp(prefix="bake-maps-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.record = RESULTS[self.group].setdefault(self.id().rsplit(".", 1)[-1], {})

    def run_set(self, targets, sources=(), **request):
        states = {material.name: fx.tree_state(material) for material in bpy.data.materials}
        before = (len(bpy.data.images), len(bpy.data.node_groups))
        started = time.monotonic()
        results = fx.bake(targets, dict(REQUEST, **request), self.folder, sources)
        self.record["seconds"] = round(time.monotonic() - started, 3)
        self.record["bakes"] = sum(len(plan.steps) for plan, *_ in results)
        for name, state in states.items():
            self.assertEqual(fx.tree_state(bpy.data.materials[name]), state, "material %s changed" % name)
        self.assertEqual((len(bpy.data.images), len(bpy.data.node_groups)), before, "temporary data left behind")
        return results

    def channel(self, result, channel, image_format="PNG"):
        plan, _entries, _facts, sums = result
        sums = {key: (value[0][1] if isinstance(value, list) else value) for key, value in sums.items()}
        if HIGH_POSITION in sums:   # As bake_maps_write.write_set combines them.
            sums["HEIGHT"] = bake_maps_write.geometric_height(sums.pop(HIGH_POSITION), sums.pop(LOW_POSITION),
                                                              sums.pop(LOW_NORMAL))
        channels, _facts = bake_maps_pack.finish(sums, (N, N), image_format)
        value = channels[channel]
        return value if isinstance(value, numpy.ndarray) else numpy.broadcast_to(
            numpy.asarray(value[:3], dtype=numpy.float32), (N, N, 3))

    def check(self, label, got, expected, mask=None, mean=FLOAT_TOL, largest=FLOAT_TOL, p99=None):
        width = min(got.shape[-1], expected.shape[-1])
        found = fx.errors(got[..., :width], expected[..., :width], mask)
        self.record[label] = found
        self.assertLessEqual(found["mean"], mean, label)
        if p99 is not None:
            self.assertLessEqual(found["p99"], p99, label)
        else:
            self.assertLessEqual(found["max"], largest, label)

    def check_file(self, entries, name, expected, colour=False, depth=8):
        """A written file read back against the values it should encode."""
        entry = next(entry for entry in entries if entry["map"] == name)
        got = fx.read_file(self.folder / entry["file"])
        encoded = bake_maps_encode.srgb_encode(expected) if colour else numpy.clip(expected, 0, 1)
        step = 1.0 / (255 if depth == 8 else 65535)
        width = min(got.shape[-1], encoded.shape[-1])
        self.check("file_" + name, got[..., :width], encoded[..., :width], largest=step * 1.01, mean=step)


class DevCases(Case):
    group = "DEV"

    def test_principled_textures(self):
        base, rough = fx.blocks(self.rng), fx.blocks(self.rng, 1)
        target = fx.plane("Plain")
        tree = Tree("PlainMat")
        target.data.materials.append(tree.surface(principled(
            tree, **{"Base Color": tree.texture(fx.image_of("A", base)),
                     "Roughness": tree.texture(fx.image_of("B", rough))})))
        result, = self.run_set([target])
        self.assertEqual(result[0].channels, ["BASE_COLOR", "ROUGHNESS"])   # Metallic 0 everywhere: no map.
        inside = fx.interior()
        self.check("base_vs_analytic", self.channel(result, "BASE_COLOR"), base, inside)
        self.check("base_vs_diffuse_colour_bake", self.channel(result, "BASE_COLOR"),
                   fx.reference(target, "DIFFUSE", {'COLOR'}), mean=NATIVE_MEAN, p99=NATIVE_P99)
        self.check("roughness_vs_roughness_bake", self.channel(result, "ROUGHNESS"),
                   fx.reference(target, "ROUGHNESS"), mean=NATIVE_MEAN, p99=NATIVE_P99)
        self.check_file(result[1], "BaseColor", self.channel(result, "BASE_COLOR"), colour=True)

    def test_mix_shader_by_mask(self):
        a, metal = fx.blocks(self.rng), fx.blocks(self.rng, 1)
        rough, mask = fx.blocks(self.rng, 1), fx.blocks(self.rng, 1)
        target = fx.plane("Mixed")
        tree = Tree("MixedMat")
        first = principled(tree, **{"Base Color": tree.texture(fx.image_of("A", a)),
                                    "Metallic": tree.texture(fx.image_of("M", metal)), "Roughness": 0.2})
        second = principled(tree, **{"Base Color": (0.1, 0.5, 0.9, 1.0), "Metallic": 1.0,
                                     "Roughness": tree.texture(fx.image_of("R", rough))})
        target.data.materials.append(tree.surface(fx.mix_shader(tree, tree.texture(fx.image_of("K", mask)),
                                                                first, second)))
        result, = self.run_set([target], packing="ORM")
        inside = fx.interior()
        self.check("base", self.channel(result, "BASE_COLOR"), fx.lerp(mask, a, numpy.array([0.1, 0.5, 0.9])), inside)
        self.check("metallic", self.channel(result, "METALLIC"), fx.lerp(mask, metal, 1.0), inside)
        self.check("roughness", self.channel(result, "ROUGHNESS"), fx.lerp(mask, 0.2, rough), inside)
        orm = numpy.concatenate([numpy.ones((N, N, 1), numpy.float32), self.channel(result, "ROUGHNESS")[..., :1],
                                 self.channel(result, "METALLIC")[..., :1]], axis=-1)
        orm[..., 0] = self.channel(result, "AO")[..., 0]
        self.check_file(result[1], "ORM", orm)


class HeldOutCases(Case):
    group = "HELD_OUT"

    def test_node_group_with_procedurals(self):
        group = bpy.data.node_groups.new("Rusty", "ShaderNodeTree")
        group.interface.new_socket("Scale", in_out='INPUT', socket_type='NodeSocketFloat')
        group.interface.new_socket("Shader", in_out='OUTPUT', socket_type='NodeSocketShader')
        nodes, links = group.nodes, group.links
        inputs, outputs = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
        noise, wave = nodes.new("ShaderNodeTexNoise"), nodes.new("ShaderNodeTexWave")
        ramp, maths = nodes.new("ShaderNodeValToRGB"), nodes.new("ShaderNodeMath")
        maths.operation, maths.inputs[0].default_value = 'SUBTRACT', 1.0
        ramp.color_ramp.elements[0].color, ramp.color_ramp.elements[1].color = (0.3, 0.1, 0.02, 1), (0.8, 0.5, 0.3, 1)
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(inputs.outputs[0], noise.inputs["Scale"])
        links.new(noise.outputs["Fac"], ramp.inputs[0])
        links.new(ramp.outputs[0], bsdf.inputs["Base Color"])
        links.new(wave.outputs["Fac"], maths.inputs[1])
        links.new(maths.outputs[0], bsdf.inputs["Roughness"])
        links.new(bsdf.outputs[0], outputs.inputs[0])
        target = fx.plane("Grouped")
        tree = Tree("GroupedMat")
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        node.inputs[0].default_value = 7.0
        target.data.materials.append(tree.surface(node.outputs[0]))
        result, = self.run_set([target])
        self.check("base_vs_diffuse_colour_bake", self.channel(result, "BASE_COLOR"),
                   fx.reference(target, "DIFFUSE", {'COLOR'}), mean=NATIVE_MEAN, p99=NATIVE_P99)
        self.check("roughness_vs_roughness_bake", self.channel(result, "ROUGHNESS"),
                   fx.reference(target, "ROUGHNESS"), mean=NATIVE_MEAN, p99=NATIVE_P99)

    def test_add_shader_hdr_emission(self):
        target = fx.plane("Glowing")
        tree = Tree("GlowingMat")
        checker, voronoi = tree.nodes.new("ShaderNodeTexChecker"), tree.nodes.new("ShaderNodeTexVoronoi")
        surface = principled(tree, **{"Base Color": checker.outputs["Color"], "Roughness": 0.6})
        glow = tree.node("ShaderNodeEmission", Color=voronoi.outputs["Color"], Strength=4.0).outputs[0]
        add = tree.nodes.new("ShaderNodeAddShader")
        tree.links.new(surface, add.inputs[0])
        tree.links.new(glow, add.inputs[1])
        target.data.materials.append(tree.surface(add.outputs[0]))
        result, = self.run_set([target], packing="GLTF")
        plan, entries, facts, _sums = result
        self.assertIn("EMISSION", plan.channels)
        strength = facts["emission_strength"]
        self.record["emission_strength"] = strength
        emitted = fx.reference(target, "EMIT")
        self.check("emission_vs_emit_bake", self.channel(result, "EMISSION") * strength, emitted, mean=NATIVE_MEAN * 4,
                   p99=NATIVE_P99 * 4)
        self.check("base_vs_diffuse_colour_bake", self.channel(result, "BASE_COLOR"),
                   fx.reference(target, "DIFFUSE", {'COLOR'}), mean=NATIVE_MEAN, p99=NATIVE_P99)
        self.check("roughness_constant", self.channel(result, "ROUGHNESS"), numpy.full((N, N, 1), 0.6))
        self.check_file(entries, "Emission", self.channel(result, "EMISSION"), colour=True)

    def test_transparent_cutout(self):
        base, mask, alpha = fx.blocks(self.rng), fx.blocks(self.rng, 1), fx.blocks(self.rng, 1)
        target = fx.plane("Leaf")
        tree = Tree("LeafMat")
        leaf = principled(tree, **{"Base Color": tree.texture(fx.image_of("B", base)),
                                   "Alpha": tree.texture(fx.image_of("A", alpha))})
        hole = tree.nodes.new("ShaderNodeBsdfTransparent").outputs[0]
        target.data.materials.append(tree.surface(fx.mix_shader(tree, tree.texture(fx.image_of("M", mask)),
                                                                hole, leaf)))
        result, = self.run_set([target], packing="UNITY")
        inside = fx.interior()
        covered = inside & (mask[..., 0] > 0.02)
        self.check("alpha", self.channel(result, "ALPHA"), mask * alpha, inside)
        self.check("base_where_covered", self.channel(result, "BASE_COLOR"), base, covered, largest=5e-3)
        self.check("alpha_vs_render", self.channel(result, "ALPHA"), render_alpha(target), inside,
                   mean=NATIVE_MEAN * 2, p99=NATIVE_P99 * 2)
        mask_map = numpy.stack([self.channel(result, "METALLIC")[..., 0], self.channel(result, "AO")[..., 0],
                                numpy.zeros((N, N)), 1.0 - self.channel(result, "ROUGHNESS")[..., 0]], axis=-1)
        self.check_file(result[1], "MaskMap", mask_map)

    def test_baked_material_renders_like_the_original(self):
        """The whole set: the baked material, lit and rendered, against the original material's render.

        Maps hold four texels per rendered pixel: at one texel a pixel, the baked image's bilinear lookup blurs what
        the original's procedurals give exactly (0.038 mean error at 64 texels, 0.003 at 256, measured 2026-10-31
        with the bake unchanged), which is the map's resolution, not its values."""
        target = fx.plane("Panel")
        tree = Tree("PanelMat")
        wave, noise = tree.nodes.new("ShaderNodeTexWave"), tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value, wave.inputs["Scale"].default_value = 3.0, 2.0
        bump = tree.node("ShaderNodeBump", Height=noise.outputs["Fac"], Strength=0.3)
        paint = principled(tree, **{"Base Color": wave.outputs["Color"], "Roughness": noise.outputs["Fac"],
                                    "Normal": bump.outputs[0]})
        steel = principled(tree, **{"Base Color": (0.55, 0.56, 0.6, 1.0), "Metallic": 1.0, "Roughness": 0.3})
        mask = tree.node("ShaderNodeMath", Value=noise.outputs["Fac"])
        mask.operation, mask.inputs[1].default_value = 'GREATER_THAN', 0.6
        target.data.materials.append(tree.surface(fx.mix_shader(tree, mask.outputs[0], paint, steel)))
        sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN'))
        sun.rotation_euler = (0.5, 0.3, 0.0)
        bpy.context.scene.collection.objects.link(sun)
        bpy.context.scene.world = bpy.data.worlds.new("Grey")
        bpy.context.scene.world.color = (0.3, 0.3, 0.3)
        original = render_top(target)
        request = dict(REQUEST, packing="GLTF", format="PNG", resolution=[4 * N, 4 * N])
        result, = self.run_set([target], packing="GLTF", resolution=[4 * N, 4 * N])
        plan, entries, facts, _sums = result
        from angelo_agent import bake_maps_material
        name = bake_maps_material.create(target.name, entries, self.folder, request, plan.channels, facts)
        target.material_slots[0].material = bpy.data.materials[name]
        baked = render_top(target)
        self.check("render_rgb", baked[..., :3], original[..., :3], mean=0.01, p99=0.12)

    def test_two_materials_and_metallic_bsdf(self):
        image = fx.blocks(self.rng)
        target = fx.split_plane("Halves", ((0, 0), (0.5, 0), (0.5, 1), (0, 1)), ((0.5, 0), (1, 0), (1, 1), (0.5, 1)))
        metal = Tree("MetalMat")
        metal.surface(metal.node("ShaderNodeBsdfMetallic", **{"Base Color": metal.texture(fx.image_of("C", image)),
                                                               "Roughness": 0.3}).outputs[0])
        paint = Tree("PaintMat")
        paint.surface(paint.node("ShaderNodeBsdfDiffuse", Color=(0.7, 0.2, 0.2, 1.0)).outputs[0])
        target.data.materials.append(metal.material)
        target.data.materials.append(paint.material)
        result, = self.run_set([target])
        left = numpy.zeros((N, N), bool)
        left[:, :N // 2] = True
        inside = fx.interior()
        self.check("metallic", self.channel(result, "METALLIC")[..., 0], numpy.where(left, 1.0, 0.0), inside)
        self.check("roughness", self.channel(result, "ROUGHNESS")[..., 0], numpy.where(left, 0.3, 1.0), inside)
        expected = numpy.where(left[..., None], image, numpy.array([0.7, 0.2, 0.2]))
        self.check("base", self.channel(result, "BASE_COLOR"), expected, inside)

    def test_udim_tiles(self):
        target = fx.split_plane("Tiled", ((0, 0), (1, 0), (1, 1), (0, 1)), ((1, 0), (2, 0), (2, 1), (1, 1)))
        tree = Tree("TiledMat")
        uv = tree.nodes.new("ShaderNodeTexCoord").outputs["UV"]
        split = tree.node("ShaderNodeSeparateXYZ", Vector=uv)
        combine = tree.node("ShaderNodeCombineColor", Red=split.outputs[0], Green=split.outputs[1], Blue=0.5)
        half = tree.node("ShaderNodeMath", Value=split.outputs[0])
        half.operation, half.inputs[1].default_value = 'MULTIPLY', 0.25
        tree.surface(principled(tree, **{"Base Color": combine.outputs[0], "Roughness": half.outputs[0]}))
        target.data.materials.append(tree.material)
        target.data.materials.append(tree.material)
        result, = self.run_set([target])
        plan, entries, _facts, sums = result
        self.assertEqual(plan.tiles, [1001, 1002])
        centres = (numpy.arange(N) + 0.5) / N
        for tile, values in sums["BASE_COLOR"]:
            u = centres[None, :] + (tile - 1001)
            expected = numpy.stack([numpy.broadcast_to(u, (N, N)), numpy.broadcast_to(centres[:, None], (N, N)),
                                    numpy.full((N, N), 0.5)], axis=-1)
            self.check("base_tile_%d" % tile, values, expected, largest=0.02)
        self.assertEqual(sorted(entry["tile"] for entry in entries if entry["map"] == "BaseColor"), [1001, 1002])

    def test_displacement_height(self):
        height = fx.blocks(self.rng, 1)
        target = fx.plane("Bumpy")
        tree = Tree("BumpyMat")
        tree.surface(principled(tree, **{"Base Color": (0.5, 0.5, 0.5, 1.0)}))
        displace = tree.node("ShaderNodeDisplacement", Height=tree.texture(fx.image_of("H", height)), Midlevel=0.3,
                             Scale=0.2)
        tree.links.new(displace.outputs[0], tree.output.inputs["Displacement"])
        target.data.materials.append(tree.material)
        result, = self.run_set([target])
        plan, entries, facts, _sums = result
        self.assertIn("HEIGHT", plan.channels)
        self.assertIn("NORMAL", plan.channels)   # Bump displacement shades the normal.
        stored = fx.read_file(self.folder / next(e["file"] for e in entries if e["map"] == "Height"))[..., :1]
        settings = facts["height"]
        rebuilt = (stored - settings["midlevel"]) * settings["scale"]
        self.check("height_rebuilt_from_file", rebuilt, (height - 0.3) * 0.2, fx.interior(), largest=1e-3)

    def test_high_to_low_spheres(self):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0)
        low = bpy.context.active_object
        low.name = "Low"
        bpy.ops.object.shade_smooth()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64, radius=1.05)
        high = bpy.context.active_object
        high.name = "High"
        bpy.ops.object.shade_smooth()
        for obj, colour in ((low, (0.2, 0.2, 0.2, 1)), (high, (0.9, 0.1, 0.1, 1))):
            tree = Tree(obj.name + "Mat")
            obj.data.materials.append(tree.surface(principled(tree, **{"Base Color": colour})))
        result, = self.run_set([low], sources=[high], ray_distance=0.2, extrusion=0.1)
        plan, _entries, _facts, sums = result
        coverage = sums["LOW_POSITION"][0][1][..., 3] > 0.5
        height = self.channel(result, "HEIGHT")[..., 0]
        settings = result[2]["height"]
        units = (height - settings["midlevel"]) * settings["scale"]
        self.check("height_units", units[..., None], numpy.full((N, N, 1), 0.0505), coverage, largest=2e-3)
        self.check("base_from_high", self.channel(result, "BASE_COLOR"), numpy.array([0.9, 0.1, 0.1]), coverage)
        normal = self.channel(result, "NORMAL")
        self.check("normal_flat", normal, numpy.array([0.5, 0.5, 1.0]), coverage, mean=5e-3, largest=0.05)


def render_top(target, samples=64, transparent=False):
    """A Cycles render of the plane seen square-on, one pixel per texel: (N, N, 4) linear values."""
    scene = bpy.context.scene
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(camera)
    camera.data.type, camera.data.ortho_scale = 'ORTHO', 2.0
    camera.location = (target.location.x, target.location.y, 5.0)
    scene.camera = camera
    scene.render.film_transparent = transparent
    scene.render.resolution_x = scene.render.resolution_y = N
    scene.render.resolution_percentage = 100
    scene.cycles.samples = samples
    path = Path(tempfile.mkdtemp(prefix="bake-maps-render-")) / "render.exr"
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = 'OPEN_EXR'
    bpy.ops.render.render(write_still=True)
    pixels = fx.read_file(path)
    shutil.rmtree(path.parent, True)
    bpy.data.objects.remove(camera)
    return pixels


def render_alpha(target):
    """The reference alpha: a film-transparent render (a Principled BSDF's alpha is sampled per path, so many)."""
    return render_top(target, samples=512, transparent=True)[..., 3:4]


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "bake-maps-correctness.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(DevCases),
                                unittest.defaultTestLoader.loadTestsFromTestCase(HeldOutCases)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "numbers": RESULTS, "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("Texture-set bake correctness failed; see the receipt and process log")
