# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Baked materials at export, in real Blender (doc/angelo/long_jobs.rst, "Baked materials at export").

Run through ``run_native_suites.py --include test_blender_baked_export`` or an isolated Angelo with --background
--factory-startup --python-exit-code 1 --python <this file> -- <receipt.json>. Real Cycles CPU bakes at 64 pixels.

A target with a procedural material is baked as the job bakes (``bake_maps_fixtures.bake``), its baked material made
and the target bound as the job binds it. Checked: the digest is the same after the bake (its temporary nodes are
gone); Angelo's web export, the engine link's live export and its background write-back write the target with the
baked material (base colour, one ORM image shared by occlusion and metallic-roughness, normal) while the person's
slots, materials and Undo stay as they were; an object sharing the target's mesh keeps its own material; a mesh, UV
or material edit makes the binding stale (own materials, a ``baked_stale`` finding; every other input, moves and AO
reach are in test_blender_baked_export_stale); the option off and an already assigned baked material; what each
packing preset carries into glTF. ``DEV`` targets were used while building this; ``HELD_OUT`` targets (other shapes,
node graphs and modifiers) never were.
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_maps_fixtures as fx  # noqa: E402

RESULTS = {"DEV": {}, "HELD_OUT": {}, "packings": {}}
FAST = {"meshes": "NONE", "textures": "KEEP", "merge": False, "selection_only": False}


def ensure_addon():
    """The add-on's property and glTF user extension (the exporter finds extensions among enabled add-ons)."""
    import addon_utils
    if not addon_utils.check("angelo_agent")[1]:
        addon_utils.enable("angelo_agent", default_set=True, persistent=True)
    from angelo_agent import export_baked
    if not hasattr(bpy.types.Object, "angelo_baked"):
        export_baked.register()


def glb_json(path):
    data = Path(path).read_bytes()
    length, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A, "the first chunk is JSON"
    return json.loads(data[20:20 + length])


def node_materials(gltf, name):
    """The material names a node's mesh is written with, one per primitive."""
    node = next(item for item in gltf["nodes"] if item.get("name") == name)
    mesh = gltf["meshes"][node["mesh"]]
    return [gltf["materials"][primitive["material"]]["name"] if "material" in primitive else None
            for primitive in mesh["primitives"]]


def material_named(gltf, name):
    return next(item for item in gltf["materials"] if item["name"] == name)


def texture_image(gltf, info):
    return None if info is None else gltf["textures"][info["index"]].get("source")


def procedural(name, variant):
    """A procedural material (nothing a glTF exporter writes as textures): noise or Voronoi colour through a ramp,
    a noise roughness and a bump normal; ``variant`` picks the graph."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    bsdf = nodes["Principled BSDF"]
    if variant == "noise":
        texture = nodes.new("ShaderNodeTexNoise")
        texture.inputs["Scale"].default_value = 6.0
    elif variant == "voronoi":
        texture = nodes.new("ShaderNodeTexVoronoi")
        texture.inputs["Scale"].default_value = 9.0
    else:   # wave
        texture = nodes.new("ShaderNodeTexWave")
        texture.inputs["Scale"].default_value = 4.0
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.05, 0.1, 0.4, 1.0)
    ramp.color_ramp.elements[1].color = (0.9, 0.6, 0.2, 1.0)
    links.new(texture.outputs[0] if variant != "noise" else texture.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(texture.outputs[0], bsdf.inputs["Roughness"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.4
    links.new(texture.outputs[0], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    if variant == "wave":   # A metallic stripe and a faint glow.
        links.new(texture.outputs[0], bsdf.inputs["Metallic"])
        bsdf.inputs["Emission Color"].default_value = (1.0, 0.3, 0.1, 1.0)
        links.new(texture.outputs[0], bsdf.inputs["Emission Strength"])
    return material


def shape(name, kind, material, modifier=None):
    """A mesh with UVs: a cube (its own unwrap), a UV sphere or a cylinder; ``modifier`` adds one to the stack."""
    before = set(bpy.data.objects)
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1.0)
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=0.6)
    else:
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.4, depth=1.2)
    obj, = set(bpy.data.objects) - before
    obj.name = obj.data.name = name
    obj.data.materials.append(material)
    if modifier == "BEVEL":
        obj.modifiers.new("Bevel", "BEVEL").width = 0.05
    elif modifier == "SUBSURF":
        obj.modifiers.new("Subdivision", "SUBSURF").levels = 1
    return obj


def depsgraph():
    return bpy.context.evaluated_depsgraph_get()


class Case(unittest.TestCase):
    group = "DEV"
    targets = (("Crate", "cube", "noise", None),)

    @classmethod
    def setUpClass(cls):
        fx.scene_setup()
        ensure_addon()
        cls.folder = Path(tempfile.mkdtemp(prefix="baked-export-"))
        objects = []   # All in place before the first bake: each is in the others' AO reach.
        for name, kind, variant, modifier in cls.targets:
            objects.append(shape(name, kind, procedural(name + " Paint", variant), modifier))
            objects[-1].location.x = 2.0 * (len(objects) - 1)
        cls.made = [cls.bake(obj, "GLTF") for obj in objects]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, True)

    @classmethod
    def bake(cls, obj, packing):
        """Bake ``obj``'s set as the job does and bind it: (object, baked material, receipt numbers)."""
        from angelo_agent import bake_maps_material, bake_maps_plan, export_baked
        request = {"resolution": [fx.N, fx.N], "margin": 2, "packing": packing, "format": "PNG",
                   "output": "maps", "material": True}
        if packing == "SEPARATE":   # A separate set bakes AO only when asked (packed presets always fill it).
            request["maps"] = ["BASE_COLOR", "METALLIC", "ROUGHNESS", "NORMAL", "EMISSION", "AO"]
        ao = "AO" in bake_maps_plan.plan(obj, [], None, request).channels   # As the job takes it, before the bake.
        before = export_baked.digest(obj, depsgraph(), ao=ao)
        folder = cls.folder / ("%s-%s" % (obj.name, packing))
        folder.mkdir()
        started = time.monotonic()
        (plan, entries, facts, _sums), = fx.bake([obj], request, folder)
        layer = obj.data.uv_layers.active
        material = bake_maps_material.create(obj.name, entries, str(folder), request, plan.channels, facts,
                                             layer.name if layer else None)
        after = export_baked.digest(obj, depsgraph(), ao=ao)
        export_baked.bind(obj, material, before, packing, ao=ao)
        return obj, material, {"seconds": round(time.monotonic() - started, 2), "channels": list(plan.channels),
                               "digest_kept": before == after}

    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="baked-glb-"))
        self.addCleanup(shutil.rmtree, self.out, True)
        self.record = RESULTS[self.group].setdefault(self.id().rsplit(".", 1)[-1], {})

    def web(self, name="scene.glb", **request):
        from angelo_agent import web_export
        path = self.out / name
        report = web_export.export(bpy.context.window, bpy.context.scene, bpy.context.view_layer, str(path),
                                   dict(FAST, **request))
        return report, glb_json(path)

    def slots(self):
        return {obj.name: [(slot.link, slot.material.name if slot.material else None) for slot in obj.material_slots]
                for obj in bpy.data.objects}

    def check_baked(self, gltf, obj, material):
        self.assertEqual(set(node_materials(gltf, obj.name)), {material.name}, obj.name)
        written = material_named(gltf, material.name)
        pbr = written["pbrMetallicRoughness"]
        self.assertIn("baseColorTexture", pbr)
        self.assertIn("normalTexture", written)
        self.assertIn("occlusionTexture", written, "baked AO reaches glTF")
        # One ORM image: occlusion (R) and metallic-roughness (G, B) read the same packed file.
        self.assertEqual(texture_image(gltf, written["occlusionTexture"]),
                         texture_image(gltf, pbr["metallicRoughnessTexture"]))

    # -- the tests ----------------------------------------------------------------------------------------------
    def test_the_bake_leaves_the_digest_as_it_was(self):
        self.record["digest_kept"] = [numbers["digest_kept"] for _obj, _material, numbers in self.made]
        self.assertTrue(all(self.record["digest_kept"]))

    def test_web_export_writes_each_target_with_its_baked_set_and_changes_nothing(self):
        slots = self.slots()
        report, gltf = self.web()
        for obj, material, _numbers in self.made:
            self.check_baked(gltf, obj, material)
        self.assertEqual(sorted(item["object"] for item in report["baked"]["used"]),
                         sorted(obj.name for obj, _m, _n in self.made))
        self.assertIn("baked_used", [item["code"] for item in report["findings"]])
        self.assertEqual(self.slots(), slots, "the person's slots never change")
        self.assertFalse(any(material.users > 1 + material.use_fake_user for _o, material, _n in self.made))
        self.record["glb_bytes"] = (self.out / "scene.glb").stat().st_size

    def test_the_option_off_writes_the_objects_own_materials(self):
        report, gltf = self.web(baked_materials=False)
        for obj, _material, _numbers in self.made:
            self.assertEqual(node_materials(gltf, obj.name), [obj.material_slots[0].material.name])
        self.assertEqual(report["baked"], {"used": [], "stale": [], "packing": []})

    def test_an_object_sharing_the_mesh_keeps_its_own_material(self):
        obj, material, _numbers = self.made[0]
        twin = bpy.data.objects.new(obj.name + " Twin", obj.data)
        bpy.context.scene.collection.objects.link(twin)
        twin.location.y = 100.0   # Out of the targets' AO reach (nearer, it would make them stale).
        for modifier in obj.modifiers:   # The same evaluated mesh, so the cache would share it if keyed wrongly.
            twin.modifiers.new(modifier.name, modifier.type)
        try:
            _report, gltf = self.web()
            self.check_baked(gltf, obj, material)
            self.assertEqual(node_materials(gltf, twin.name), [obj.data.materials[0].name])
        finally:
            bpy.data.objects.remove(twin)

    def test_a_mesh_uv_or_material_edit_is_stale(self):
        from angelo_agent import export_baked
        obj, material, _numbers = self.made[-1]
        mesh = obj.data
        edits = {   # Moves, AO reach and every other input: test_blender_baked_export_stale.
            "vertex": (lambda: setattr(mesh.vertices[0], "co", mesh.vertices[0].co + mesh.vertices[0].normal * 0.1),
                       True),
            "uv": (lambda: setattr(mesh.uv_layers.active.data[0], "uv", (0.123, 0.456)), True),
            "material": (lambda: self.nudge(mesh.materials[0]), True),
        }
        self.record["stale"] = {}
        for name, (edit, stale) in edits.items():
            copy = mesh.copy()
            tree = mesh.materials[0].node_tree
            saved = {node.name: [tuple(s.default_value) if hasattr(s.default_value, "__len__") else s.default_value
                                 for s in node.inputs if hasattr(s, "default_value")] for node in tree.nodes}
            location = tuple(obj.location)
            edit()
            mesh.update()
            found = export_baked.plan([obj], depsgraph())
            self.record["stale"][name] = found["stale"] == [obj.name]
            self.assertEqual(found["stale"] == [obj.name], stale, name)
            if stale:
                report, gltf = self.web()
                self.assertEqual(node_materials(gltf, obj.name), [mesh.materials[0].name])
                self.assertIn("baked_stale", [item["code"] for item in report["findings"]])
            # Put it back: the binding is current again.
            obj.location = location
            obj.data = copy
            for node in tree.nodes:
                for socket, value in zip([s for s in node.inputs if hasattr(s, "default_value")], saved[node.name]):
                    socket.default_value = value
            bpy.data.meshes.remove(mesh)
            mesh = copy
            mesh.name = obj.name
            self.assertEqual(export_baked.plan([obj], depsgraph())["stale"], [], "restored after " + name)

    def nudge(self, material):
        texture = next(node for node in material.node_tree.nodes if node.bl_idname.startswith("ShaderNodeTex"))
        texture.inputs["Scale"].default_value += 1.0

    def test_an_assigned_baked_material_is_written_as_it_is(self):
        obj, material, _numbers = self.made[0]
        own = [slot.material for slot in obj.material_slots]
        try:
            for slot in obj.material_slots:
                slot.material = material
            report, gltf = self.web()
            self.check_baked(gltf, obj, material)
            self.assertNotIn(obj.name, [item["object"] for item in report["baked"]["used"]])
            self.assertEqual(report["baked"]["stale"], [])
        finally:
            for slot, original in zip(obj.material_slots, own):
                slot.material = original

    def test_the_engine_links_live_export_and_write_back_use_it(self):
        from angelo_agent import engine_link_assets, engine_link_writeback
        live = self.out / "live.glb"
        engine_link_assets.export(None, str(live), engine_link_assets.live_gltf(engine_link_assets.GLTF_DURABLE))
        gltf = glb_json(live)
        for obj, material, _numbers in self.made:
            self.check_baked(gltf, obj, material)
        # The write-back: a background Angelo from the saved file assigns the planned materials in its own copy.
        found = engine_link_assets.baked_plan(None)
        self.assertEqual(found["stale"], [])
        blend, target = self.out / "work.blend", self.out / "asset.glb"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)
        writer = engine_link_writeback.WriteBack(bpy.app.binary_path)
        started = time.monotonic()
        writer.request("k", str(blend), str(target), "gltf", dict(engine_link_assets.GLTF_DURABLE),
                       baked=found["used"])
        done = []
        while not done and time.monotonic() - started < 120:
            done = writer.finished()
            time.sleep(0.05)
        self.assertEqual(done, [("k", None)])
        self.record["write_back_seconds"] = round(time.monotonic() - started, 2)
        gltf = glb_json(target)
        for obj, material, _numbers in self.made:
            self.check_baked(gltf, obj, material)
        self.assertEqual({obj.name: obj.material_slots[0].material.name for obj, _m, _n in self.made},
                         {obj.name: obj.name + " Paint" for obj, _m, _n in self.made}, "the session is untouched")


class HeldOutCases(Case):
    group = "HELD_OUT"
    targets = (("Barrel", "cylinder", "voronoi", "BEVEL"), ("Orb", "sphere", "wave", "SUBSURF"))


class PackingCases(unittest.TestCase):
    """Each packing preset: GLTF, ORM and SEPARATE export with every map; UNITY and UNREAL keep their own materials
    (``packing``), and what their baked material would carry if assigned is recorded (why they are refused)."""

    def test_each_packing(self):
        fx.scene_setup()
        ensure_addon()
        Case.folder = Path(tempfile.mkdtemp(prefix="baked-packing-"))
        self.addCleanup(shutil.rmtree, Case.folder, True)
        from angelo_agent import export_baked, web_export
        for index, packing in enumerate(("GLTF", "ORM", "SEPARATE", "UNITY", "UNREAL")):
            obj = shape("Box " + packing, "cube", procedural("Box %s Paint" % packing, "wave"))
            obj.location.x = 2.0 * index
            _obj, material, _numbers = Case.bake(obj, packing)
            found = export_baked.plan([obj], depsgraph())
            assigned = packing not in export_baked.GLTF_PACKINGS
            if assigned:   # Refused for glTF: record what the material itself would carry (Use Baked Material).
                self.assertEqual(found["packing"], [(obj.name, packing)])
                for slot in obj.material_slots:
                    slot.material = material
            else:
                self.assertEqual(found["used"], [(obj.name, material.name)])
            path = Case.folder / ("%s.glb" % packing)
            web_export.export(None, bpy.context.scene, bpy.context.view_layer, str(path), FAST)
            gltf = glb_json(path)
            self.assertEqual(set(node_materials(gltf, obj.name)), {material.name})
            written = material_named(gltf, material.name)
            pbr = written.get("pbrMetallicRoughness", {})
            RESULTS["packings"][packing] = {
                "baseColor": "baseColorTexture" in pbr, "metallicRoughness": "metallicRoughnessTexture" in pbr,
                "occlusion": "occlusionTexture" in written, "normal": "normalTexture" in written,
                "emissive": "emissiveTexture" in written, "assigned_for_the_record": assigned,
                "orm_shared": "occlusionTexture" in written and "metallicRoughnessTexture" in pbr and
                texture_image(gltf, written["occlusionTexture"]) == texture_image(gltf, pbr["metallicRoughnessTexture"]),
                "images": len(gltf.get("images", ()))}
            obj.hide_viewport = True   # The next export holds the next box only.
        for packing in sorted(export_baked.GLTF_PACKINGS):
            carried = RESULTS["packings"][packing]
            self.assertTrue(all(carried[key] for key in ("baseColor", "metallicRoughness", "occlusion", "normal",
                                                         "emissive")), (packing, carried))
        self.assertTrue(RESULTS["packings"]["GLTF"]["orm_shared"])


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "baked-export.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(Case), loader.loadTestsFromTestCase(HeldOutCases),
                                loader.loadTestsFromTestCase(PackingCases)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "numbers": RESULTS, "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("Baked materials at export failed; see the receipt and process log")
