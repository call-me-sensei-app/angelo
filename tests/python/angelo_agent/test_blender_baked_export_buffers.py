# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A baked material is current exactly when a bake now reads the pixels its bake read, in the built app.

Run through ``run_native_suites.py --include test_blender_baked_export_buffers`` or an isolated Angelo with
--background --factory-startup --python-exit-code 1 --python <this file> -- <receipt.json>.

1.0.10 audit, re-check 2 (P2, since 1.0.9): Cycles copies some images from the pixels Blender holds (packed,
generated and movie images, unsaved edits, a UDIM image with a generated tile: ``image_is_builtin``) and opens every
other one from its files on disk. Held pixels can be older than the image's source: a file saved over by another
program while the image is open, then packed. The digest recorded the source, the bake read the old pixels, and after
a Reload or reopening the .blend the export called it current. Each case here binds a target as a ``BAKE_MAPS`` job
does (the digest, then a real Cycles bake reading the image), moves the scene through a Reload or a save and reopen,
bakes again, and requires ``export_baked.plan`` to call the binding stale exactly when the new bake's pixels differ
from the bound bake's. It also checks the place Cycles reads from: a plain file's file (the audit's own repro, which
was current rightly), a packed or generated-tile image's held pixels. ``test_native_baked_digest_buffers.py`` holds
the same property against stand-ins, with the cases Python cannot reach here (a buffer evicted under memory
pressure).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import bpy
import numpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_maps_fixtures as fx  # noqa: E402
from test_blender_baked_export import ensure_addon  # noqa: E402

RESULTS = {"cases": {}}
SIZE = 8
OLD, NEW = 0.1, 0.9   # The colour a file holds when the image is opened, and the one saved over it.


def depsgraph():
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()
    return graph


def save_png(path, value):
    """A small grey PNG at ``path``, with a later time than any before it (as a later save would have)."""
    image = bpy.data.images.new("Writer", SIZE, SIZE)
    image.generated_color = (value, value, value, 1.0)
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)
    later = time.time_ns() + 2 * 10 ** 9
    os.utime(path, ns=(later, later))


def scene(image):
    """A plane whose material emits ``image`` (each pixel as stored), with the UDIM tile 1001's UVs."""
    fx.scene_setup()
    ensure_addon()
    bpy.ops.mesh.primitive_plane_add()
    target = bpy.context.active_object
    target.name = "Target"
    material = bpy.data.materials.new("Emits")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    for node in list(nodes):
        if node.type != "OUTPUT_MATERIAL":
            nodes.remove(node)
    texture = nodes.new("ShaderNodeTexImage")
    texture.name, texture.interpolation = "Texture", "Closest"
    emission = nodes.new("ShaderNodeEmission")
    links.new(texture.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], nodes["Material Output"].inputs["Surface"])
    target.data.materials.append(material)
    if image is not None:
        texture.image = image(target)
    return target


def texture_image():
    return bpy.data.objects["Target"].data.materials[0].node_tree.nodes["Texture"].image


def bake():
    """A real Cycles bake of the target's emission into a new image, its centre pixel; the bake's node and image go
    again, so the material the digest reads is as it was."""
    target = bpy.data.objects["Target"]
    nodes = target.data.materials[0].node_tree.nodes
    result = bpy.data.images.new("Baked", SIZE, SIZE, float_buffer=True)
    node = nodes.new("ShaderNodeTexImage")
    node.image, nodes.active = result, node
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == target)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.bake(type="EMIT", margin=0)
    pixel = round(result.pixels[4 * (SIZE * SIZE // 2 + SIZE // 2)], 3)
    nodes.remove(node)
    bpy.data.images.remove(result)
    return pixel


def bind():
    """Bind the target as a ``BAKE_MAPS`` job does: the digest first, then the bake that reads the image."""
    from angelo_agent import export_baked
    target = bpy.data.objects["Target"]
    full, own = export_baked.digests(target, depsgraph(), (), None, True)
    pixel = bake()
    baked = bpy.data.materials.get("Baked Stand-in") or bpy.data.materials.new("Baked Stand-in")
    export_baked.bind(target, baked, full, "GLTF", (), None, True, own)
    return pixel


def stale():
    from angelo_agent import export_baked
    return export_baked.plan([bpy.data.objects["Target"]], depsgraph())["stale"] == ["Target"]


def reopen(folder):
    """Save and open the file again. Paths stay as they were stored: remapping them to relative ones changes the
    stored path, which the digest reads on its own (a separate question from the pixels)."""
    path = os.path.join(folder, "scene.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, relative_remap=False)
    bpy.ops.wm.open_mainfile(filepath=path)
    ensure_addon()


class HeldPixels(unittest.TestCase):
    def honest(self, name, bound, change, folder):
        """After ``change`` ("reload" or "reopen"), stale exactly when a bake now reads other pixels."""
        if change == "reload":
            texture_image().reload()
        else:
            reopen(folder)
        found = stale()
        now = bake()
        RESULTS["cases"]["%s, %s" % (name, change)] = {"bound_bake": bound, "bake_now": now, "stale": found,
                                                      "expected": now != bound}
        self.assertEqual(found, now != bound, "%s, %s: bound bake %s, a bake now %s" % (name, change, bound, now))
        return found

    def each_change(self, name, make, expect_bound):
        for change in ("reload", "reopen"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                scene(lambda target: make(folder))
                bound = bind()
                RESULTS["cases"]["%s, bound" % name] = {"bound_bake": bound}
                self.assertAlmostEqual(bound, expect_bound, delta=0.05, msg="%s: where Cycles read" % name)
                self.assertFalse(stale(), "%s: current right after its bake" % name)
                self.honest(name, bound, change, folder)

    def opened_then_saved_over(self, folder, name="a.png"):
        """An image opened (its pixels held) whose file another program then saves over, not reloaded."""
        path = os.path.join(folder, name)
        save_png(path, OLD)
        image = bpy.data.images.load(path)
        image.colorspace_settings.name = "Non-Color"
        self.assertAlmostEqual(image.pixels[0], OLD, delta=0.01)   # Held now.
        save_png(path, NEW)
        return image

    def test_a_plain_file_saved_over_reads_the_file(self):
        # The audit's repro: Cycles opens this file from disk whatever Blender holds, so the bake read the new file,
        # and after a Reload or a reopen the bake is rightly current.
        self.each_change("plain file saved over", self.opened_then_saved_over, NEW)

    def test_packed_after_the_file_was_saved_over(self):
        def make(folder):
            image = self.opened_then_saved_over(folder)
            image.pack()   # Packs the file's new bytes; Blender still holds the old pixels, and Cycles copies those.
            return image
        self.each_change("packed after saved over", make, OLD)

    def test_a_udim_image_with_a_generated_tile(self):
        def make(folder):
            path = os.path.join(folder, "u.1001.png")
            save_png(path, OLD)
            image = bpy.data.images.load(path)
            image.source, image.filepath = "TILED", os.path.join(folder, "u.<UDIM>.png")
            image.colorspace_settings.name = "Non-Color"
            with bpy.context.temp_override(edit_image=image):   # Add Tile, filled, never saved.
                bpy.ops.image.tile_add(number=1002, fill=True, width=SIZE, height=SIZE)
            self.assertAlmostEqual(image.pixels[0], OLD, delta=0.01)
            save_png(path, NEW)
            return image
        self.each_change("UDIM with a generated tile, tile 1001 saved over", make, OLD)

    def test_images_held_or_not_read_the_same(self):
        """No false stale: a packed image never loaded, a generated image, a plain file, across Reload and reopen."""
        def packed(folder):
            path = os.path.join(folder, "p.png")
            save_png(path, NEW)
            image = bpy.data.images.load(path)
            image.colorspace_settings.name = "Non-Color"
            image.pack()
            return image

        def generated(folder):
            image = bpy.data.images.new("Grey", SIZE, SIZE)
            image.generated_color = (NEW, NEW, NEW, 1.0)
            image.colorspace_settings.name = "Non-Color"
            return image

        def plain(folder):
            path = os.path.join(folder, "f.png")
            save_png(path, NEW)
            image = bpy.data.images.load(path)
            image.colorspace_settings.name = "Non-Color"
            return image

        for name, make in (("packed, never loaded", packed), ("generated", generated), ("plain file", plain)):
            with self.subTest(image=name):
                self.each_change(name, make, NEW)
                self.assertFalse(RESULTS["cases"]["%s, reopen" % name]["stale"], name)

    def test_unsaved_paint_is_what_the_bake_read(self):
        with tempfile.TemporaryDirectory() as folder:
            scene(lambda target: self.opened_then_saved_over(folder))
            image = texture_image()
            pixels = list(image.pixels)
            pixels[:4] = (0.5, 0.5, 0.5, 1.0)
            image.pixels = pixels   # An unsaved edit: Cycles now copies the held pixels.
            bound = bind()
            self.assertAlmostEqual(bound, OLD, delta=0.05, msg="the held (edited) pixels")
            self.assertFalse(stale(), "unchanged")
            self.honest("unsaved edit", bound, "reload", folder)


class SameEitherWay(unittest.TestCase):
    """An image Cycles copies reads the same digest whether its pixels are held, not held (decoded by the read), or
    decoded again, in every format and bit depth an image loads from: the native reader hashes what loading makes."""

    def image(self, folder, kind, depth, extension, linear):
        settings = bpy.context.scene.render.image_settings
        source = bpy.data.images.new("Noise", 16, 16, float_buffer=linear, alpha=True)
        pixels = numpy.random.default_rng(len(kind) + int(depth)).random(16 * 16 * 4, dtype=numpy.float32)
        source.pixels.foreach_set(pixels)
        settings.file_format, settings.color_depth = kind, depth
        if kind != "JPEG":
            settings.color_mode = "RGBA"
        path = os.path.join(folder, "%s%s.%s" % (kind, depth, extension))
        source.save_render(path, scene=bpy.context.scene)
        bpy.data.images.remove(source)
        image = bpy.data.images.load(path)
        image.pack()
        return image

    def test_held_or_not_in_every_format(self):
        from angelo_agent import export_baked_images as images
        fx.scene_setup()
        ensure_addon()
        with tempfile.TemporaryDirectory() as folder:
            for kind, depth, extension, linear in (("PNG", "8", "png", False), ("PNG", "16", "png", True),
                                                   ("JPEG", "8", "jpg", False), ("OPEN_EXR", "16", "exr", True),
                                                   ("OPEN_EXR", "32", "exr", True), ("TIFF", "16", "tif", True)):
                with self.subTest(kind=kind, depth=depth):
                    image = self.image(folder, kind, depth, extension, linear)
                    key = images.image_key(image, None, 1)
                    image.buffers_free()
                    images._DECODED.clear()
                    never = images.image_digest(image, key)
                    self.assertFalse(image.has_data, "a read of an image that held nothing keeps nothing")
                    self.assertEqual(images.image_digest(image, key), never, "remembered")
                    image.pixels[0]   # Held now.
                    self.assertTrue(image.has_data)
                    self.assertEqual(images.image_digest(image, key), never, "held")
                    image.buffers_free()
                    images._DECODED.clear()
                    self.assertEqual(images.image_digest(image, key), never, "decoded again")
                    RESULTS["cases"]["%s %s held or not" % (kind, depth)] = {"same": True}

    def test_a_udim_set_with_a_generated_tile_partly_held(self):
        from angelo_agent import export_baked_images as images
        fx.scene_setup()
        ensure_addon()
        with tempfile.TemporaryDirectory() as folder:
            for number in (1001, 1002):
                save_png(os.path.join(folder, "t.%d.png" % number), OLD if number == 1001 else NEW)
            image = bpy.data.images.load(os.path.join(folder, "t.1001.png"))
            image.source, image.filepath = "TILED", os.path.join(folder, "t.<UDIM>.png")
            image.tiles.new(1002)
            with bpy.context.temp_override(edit_image=image):
                bpy.ops.image.tile_add(number=1003, fill=True, width=SIZE, height=SIZE)
            key = images.image_key(image, None, 1)
            image.buffers_free()
            images._DECODED.clear()
            never = images.image_digest(image, key)
            image.pixels[0]   # Tile 1001 alone held.
            self.assertEqual(images.image_digest(image, key), never, "one tile held")
            image.tiles[1].size[0]   # Tile 1002 too.
            self.assertEqual(images.image_digest(image, key), never, "two tiles held")


class StoredPaths(unittest.TestCase):
    """A stored path's spelling is no input of a bake: Make Paths Relative, Make Paths Absolute and a save's relative
    remap rewrite it without changing what is read, so a binding stays current (1.0.10, coordinator's follow-up: the
    digest held ``filepath_raw``). Moving the file a relative path names does change what is read."""

    def test_rewriting_a_stored_path_keeps_a_binding_current(self):
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "tex"))
            image_file = os.path.join(folder, "tex", "a.png")
            save_png(image_file, NEW)

            def make(_target):
                image = bpy.data.images.load(image_file)
                image.colorspace_settings.name = "Non-Color"
                return image
            target = scene(make)
            tree = target.data.materials[0].node_tree
            ies = tree.nodes.new("ShaderNodeTexIES")   # An external file a node reads (a FILE_PATH value).
            ies.mode, ies.filepath = "EXTERNAL", os.path.join(folder, "tex", "lamp.ies")
            Path(ies.filepath).write_text("IESNA:LM-63-2002\nTILT=NONE\n")
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "scene.blend"), relative_remap=False)
            ensure_addon()
            bind()
            os.makedirs(os.path.join(folder, "moved"))
            moved = os.path.join(folder, "moved", "scene.blend")
            for label, step, spelling in (
                    ("Make Paths Relative", bpy.ops.file.make_paths_relative, "//tex/a.png"),
                    ("a save elsewhere with relative remap",   # The relative path is rewritten for the new folder.
                     lambda: bpy.ops.wm.save_as_mainfile(filepath=moved, relative_remap=True), "//../tex/a.png"),
                    ("reopened", lambda: bpy.ops.wm.open_mainfile(filepath=moved), "//../tex/a.png"),
                    ("Make Paths Absolute", bpy.ops.file.make_paths_absolute, image_file)):
                step()
                ensure_addon()
                found = stale()
                RESULTS["cases"]["stored path: %s" % label] = {"stale": found, "expected": False,
                                                               "path": texture_image().filepath_raw}
                self.assertEqual(os.path.normpath(texture_image().filepath_raw.replace("\\", "/")),
                                 os.path.normpath(spelling), label)
                self.assertFalse(found, label)

    def test_a_packed_image_stays_current_when_its_project_moves(self):
        """A bake copies a packed image's bytes wherever the project is, so its stored path, which names another
        folder once the .blend is saved elsewhere, is no input (break-it review of the follow-ups, Low 2)."""
        with tempfile.TemporaryDirectory() as folder:
            for name in ("project/tex", "elsewhere"):
                os.makedirs(os.path.join(folder, name))
            image_file = os.path.join(folder, "project", "tex", "p.png")
            save_png(image_file, NEW)
            scene(lambda _target: bpy.data.images.load(image_file))
            texture_image().pack()
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "project", "scene.blend"), relative_remap=False)
            texture_image().filepath_raw = "//tex/p.png"
            ensure_addon()
            bind()
            self.assertFalse(stale(), "bound")
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "elsewhere", "scene.blend"), relative_remap=False)
            ensure_addon()
            found = stale()
            RESULTS["cases"]["packed, project moved"] = {"stale": found, "expected": False}
            self.assertEqual(texture_image().filepath_raw, "//tex/p.png")
            self.assertFalse(found, "the packed bytes the bake reads are the same")

    def test_a_linked_trees_external_file_is_stamped_wherever_cycles_may_open_it(self):
        """A node's IES file stored as ``//lamp.ies`` in a tree linked from a library. Cycles 5.2 inlines the tree into
        a copy with no library before resolving it (``add_nodes``, ``blender_absolute_path``), so the bake opens the
        file beside the open .blend; Blender resolves the tree's paths beside the library. Saving over either is a
        change (break-it reviews of the follow-ups: the digest stamped only one of the two, and each reviewer found the
        other one; ``blend_file_paths.node_file_places``)."""
        with tempfile.TemporaryDirectory() as folder:
            for name in ("lib", "main"):
                os.makedirs(os.path.join(folder, name))
            library = os.path.join(folder, "lib", "lib.blend")
            lamps = {where: os.path.join(folder, where, "lamp.ies") for where in ("main", "lib")}
            for lamp in lamps.values():
                Path(lamp).write_text("IESNA:LM-63-2002\nTILT=NONE\n")
            group = bpy.data.node_groups.new("Lamp", "ShaderNodeTree")
            group.interface.new_socket(name="Fac", in_out="OUTPUT", socket_type="NodeSocketFloat")
            ies, out = group.nodes.new("ShaderNodeTexIES"), group.nodes.new("NodeGroupOutput")
            ies.mode, ies.filepath = "EXTERNAL", "//lamp.ies"
            group.links.new(ies.outputs["Fac"], out.inputs["Fac"])
            bpy.data.libraries.write(library, {group}, path_remap="NONE")
            target = scene(None)
            with bpy.data.libraries.load(library, link=True) as (_source, linked):
                linked.node_groups = ["Lamp"]
            tree = target.data.materials[0].node_tree
            node = tree.nodes.new("ShaderNodeGroup")
            node.node_tree = linked.node_groups[0]
            self.assertEqual(node.node_tree.nodes["IES Texture"].filepath, "//lamp.ies")
            tree.links.new(node.outputs["Fac"], tree.nodes["Emission"].inputs["Strength"])
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "main", "main.blend"), relative_remap=False)
            ensure_addon()
            for where, note in (("main", "where Cycles 5.2 opens it"), ("lib", "where Blender resolves the tree")):
                bind()
                self.assertFalse(stale(), "bound")
                Path(lamps[where]).write_text("IESNA:LM-63-2002\nTILT=NONE\n# saved over: %s\n" % where)
                found = stale()   # Its size changed, so its stamp did.
                RESULTS["cases"]["linked tree's IES saved over beside %s" % where] = {"stale": found, "expected": True}
                self.assertTrue(found, "the file %s changed" % note)

    def test_a_binding_made_before_this_update_is_named_as_the_updates_doing(self):
        from angelo_agent import export_baked
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "p.png")
            save_png(path, NEW)
            target = scene(lambda target: bpy.data.images.load(path))
            texture_image().pack()   # An image Cycles copies from memory: its 1.0.9 digest held the packed bytes.
            bind()
            self.assertEqual(target.angelo_baked.scheme, export_baked.SCHEME)
            self.assertEqual(export_baked.plan([target], depsgraph())["updated"], [])
            target.angelo_baked.scheme = 0   # As a 1.0.9 binding reads: no scheme.
            self.assertEqual(export_baked.plan([target], depsgraph())["updated"], [], "current: nothing to say")
            verified = target.angelo_baked.digest
            target.angelo_baked.digest = "bound again before the plan's timer ran"
            export_baked._record()   # The plan's timer: only the binding the plan verified is recorded.
            self.assertEqual(target.angelo_baked.scheme, 0, "another binding under the same object is not recorded")
            target.angelo_baked.digest = verified
            export_baked.plan([target], depsgraph())
            export_baked._record()   # Current under this scheme too, so it is recorded.
            self.assertEqual(target.angelo_baked.scheme, export_baked.SCHEME, "a current binding is brought up")
            target.angelo_baked.digest = "an edit since the bake"
            found = export_baked.plan([target], depsgraph())
            self.assertEqual((found["stale"], found["updated"]), (["Target"], []), "a later edit is an edit")
            target.angelo_baked.scheme = 0   # ``own`` stays as bound: only the full digest differs, so the update
            target.angelo_baked.digest = "a 1.0.9 digest of the packed bytes"   # is said before ``nearby``.
            found = export_baked.plan([target], depsgraph())
            RESULTS["cases"]["bound before the update"] = {"stale": found["stale"], "updated": found["updated"]}
            self.assertEqual((found["stale"], found["updated"], found["nearby"]), (["Target"], ["Target"], []))


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "baked-export-buffers.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(case) for case in (HeldPixels, SameEitherWay, StoredPaths)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "numbers": RESULTS, "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("A baked material read as current while a bake now reads other pixels; see the receipt")
